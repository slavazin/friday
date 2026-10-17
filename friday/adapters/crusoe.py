"""Crusoe Managed Inference: an OpenAI-compatible endpoint, metered per call.

Retries 429/5xx with full-jitter exponential backoff (a Retry-After header wins), records one spend row
per call (failed calls included), and echoes the served model string. Prices come from GET /v1/models unless
`models.price_overrides` names the model.

With a `ResponseCache`, a call at temperature 0 is looked up by `request_key(body)` before it is sent; a hit
returns the stored reply (with the model string the service echoed when it was stored) and records a
zero-cost spend row marked cached. A call at a temperature above 0 is never cached: a sampled role (the
worker, the drafting roles) must draw a fresh reply each time.

Every chat opens one `model.call` span (docs/telemetry.md) around the whole call, cache lookup and retries
included: the attempts, the retry statuses, the backoff slept, tokens, cost, the served model and whether a
response_format was sent. A failed call closes the span failed and raises.
"""

from __future__ import annotations

import base64
import json
import os
import random
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx

from friday.adapters.cache import request_key
from friday.config import ModelsSection, RolePin, TransportSection
from friday.ports.models import ChatResult, Message, ModelInfo, ResponseCache
from friday.ports.stores import SpendLog
from friday.ports.telemetry import SpanHandle, Telemetry


class CallDeadline(RuntimeError):
    """One attempt that did not complete within `transport.request_timeout_s` in total; retried like a 5xx."""


class TransportFailure(RuntimeError):
    """A call that exhausted its retries; the pass is recorded as failed for transport."""


def _price(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_models(payload: Mapping[str, Any]) -> list[ModelInfo]:
    """ModelInfo rows from a /v1/models response body."""
    out = []
    for m in payload.get("data", []):
        tags = m.get("tags", [])
        modality = m.get("architecture", {}).get("modality", "")
        pricing = m.get("pricing", {})
        out.append(ModelInfo(
            id=m["id"],
            image_input=modality == "multimodal" or "image text to text" in tags,
            structured_output="response_format" in m.get("supported_parameters", []),
            price_in_per_mtok=_price(pricing.get("prompt")),
            price_out_per_mtok=_price(pricing.get("completion")),
        ))
    return out


def _content(msg: Message) -> Any:
    if not msg.images:
        return msg.content
    parts: list[dict[str, Any]] = [{"type": "text", "text": msg.content}]
    for img in msg.images:
        url = "data:image/png;base64," + base64.b64encode(img).decode()
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


class CrusoeClient:
    def __init__(
        self,
        models: ModelsSection,
        transport: TransportSection,
        spend: SpendLog,
        *,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        cache: ResponseCache | None = None,
        telemetry: Telemetry,
    ) -> None:
        """`telemetry` is the process's log; every chat writes its `model.call` span there."""
        key = os.environ.get(models.endpoint.api_key_env)
        if not key:
            raise RuntimeError(f"environment variable {models.endpoint.api_key_env} is not set")
        self._models, self._transport, self._spend = models, transport, spend
        self._http = http or httpx.Client(
            base_url=models.endpoint.base_url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=transport.request_timeout_s,
        )
        self._sleep, self._rng = sleep, rng or random.Random()
        self._cache = cache
        self._telemetry = telemetry
        self._role_models: dict[str, str] = {}
        self._catalog: dict[str, ModelInfo] = {}

    # ModelCatalog
    def list_models(self) -> Sequence[ModelInfo]:
        resp = self._request("GET", "/models", None)
        rows = parse_models(resp)
        self._catalog = {m.id: m for m in rows}
        return rows

    def bind(self, role_models: Mapping[str, str]) -> None:
        """Fixes the model each role calls (role -> model id) for the rest of the run.

        Raises when a bound model has no price: an unpriced call would read as $0 and the spend cap
        could never trigger. Call `list_models` first, or name the model in `models.price_overrides`.
        """
        unpriced = sorted({m for m in role_models.values() if self._cost(m, 1, 1) is None})
        if unpriced:
            raise RuntimeError(f"no price for {unpriced}: call list_models() first or set models.price_overrides")
        self._role_models = dict(role_models)

    def model_for(self, role: str) -> str:
        return self._role_models[role]

    # ModelClient
    def chat(
        self, role: str, messages: Sequence[Message], *, json_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None, tag: str = "",
    ) -> ChatResult:
        """The reply to `messages` on `role`'s bound model, inside one `model.call` span."""
        with self._telemetry.span("model.call", role=role, tag=tag) as span:
            result = self._chat(role, messages, json_schema, max_tokens, tag, span)
            span.set(model=result.model, tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                     cost_usd=result.cost_usd, cached=result.cached)
            return result

    def _chat(
        self, role: str, messages: Sequence[Message], json_schema: dict[str, Any] | None, max_tokens: int | None,
        tag: str, span: SpanHandle,
    ) -> ChatResult:
        pin: RolePin = self._models.roles[role]
        model = self.model_for(role)
        span.set(requested=model, structured=False, attempts=0, retry_statuses=[], backoff_s=0.0)
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": _content(m)} for m in messages],
            "temperature": pin.temperature,
        }
        if pin.seed is not None:
            body["seed"] = pin.seed
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        info = self._catalog.get(model)
        if json_schema is not None and info is not None and info.structured_output:
            schema = {"name": (tag or role).replace(":", "_"), "schema": json_schema}
            body["response_format"] = {"type": "json_schema", "json_schema": schema}
        span.set(structured="response_format" in body)
        key = request_key(body) if self._cache is not None and body["temperature"] == 0 else None
        if key is not None:
            hit = self._cache_hit(role, tag, key, body)
            if hit is not None:
                return hit
        try:
            resp = self._request("POST", "/chat/completions", body, span)
        except TransportFailure as e:
            self._spend.record(role, tag, None, ok=False, error=str(e))
            raise
        usage = resp.get("usage") or {}
        served = resp.get("model", model)
        t_in, t_out = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        try:
            text = resp["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            self._spend.record(role, tag, None, ok=False, error=f"malformed reply: {type(e).__name__}")
            raise TransportFailure(f"malformed reply from {served}") from e
        result = ChatResult(
            text=text,
            model=served,
            tokens_in=t_in,
            tokens_out=t_out,
            cost_usd=self._cost(model, t_in, t_out),
            raw={"id": resp.get("id"), "sent": {k: v for k, v in body.items() if k != "messages"}},
        )
        self._spend.record(role, tag, result, ok=True)
        if key is not None:
            entry = {"text": text, "model": served, "id": resp.get("id"), "tokens_in": t_in, "tokens_out": t_out}
            self._cache.put(key, json.dumps(entry, sort_keys=True))
        return result

    def _cache_hit(self, role: str, tag: str, key: str, body: dict[str, Any]) -> ChatResult | None:
        """The stored reply for `key` as a zero-cost cached result (its spend row recorded), or None on a miss."""
        stored = self._cache.get(key)
        if stored is None:
            return None
        # The cache holds this client's own JSON (written above), never model output, so plain json applies.
        entry = json.loads(stored)
        result = ChatResult(
            text=entry["text"],
            model=entry["model"],
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            cached=True,
            raw={"id": entry["id"], "cache_key": key, "stored_tokens": [entry["tokens_in"], entry["tokens_out"]],
                 "requested": {k: v for k, v in body.items() if k != "messages"}},
        )
        self._spend.record(role, tag, result, ok=True)
        return result

    def _cost(self, model: str, t_in: int, t_out: int) -> float | None:
        override = self._models.price_overrides.get(model)
        if override:
            p_in, p_out = override.get("prompt"), override.get("completion")
        else:
            info = self._catalog.get(model)
            p_in, p_out = (info.price_in_per_mtok, info.price_out_per_mtok) if info else (None, None)
        if p_in is None or p_out is None:
            return None
        return (t_in * p_in + t_out * p_out) / 1e6

    def _deadline_request(self, method: str, path: str, body: dict[str, Any] | None,
                          deadline_s: float) -> httpx.Response:
        """One HTTP attempt that returns within `deadline_s` in total, or raises CallDeadline.

        httpx's timeout bounds each read, not the call: a reply the server keeps open without finishing hung
        judge calls for ten minutes. The attempt runs on a daemon thread; past the deadline it is abandoned
        (its connection closes when the thread's read times out) and the call counts as a retriable attempt.
        """
        box: dict[str, Any] = {}

        def go() -> None:
            try:
                box["r"] = self._http.request(method, path, json=body)
            except BaseException as e:  # noqa: BLE001 - re-raised in the caller's thread below
                box["e"] = e

        th = threading.Thread(target=go, daemon=True, name=f"crusoe-{path}")
        th.start()
        th.join(deadline_s)
        if th.is_alive():
            raise CallDeadline(f"no complete reply within {deadline_s:g} s (transport.request_timeout_s)")
        if "e" in box:
            raise box["e"]
        return box["r"]

    def _request(self, method: str, path: str, body: dict[str, Any] | None,
                 span: SpanHandle | None = None) -> dict[str, Any]:
        """The JSON reply, after retries; with `span`, records `attempts`, `retry_statuses` and `backoff_s` on it
        as each attempt ends, so a call that fails still carries what it tried."""
        t = self._transport
        last = ""
        statuses: list[int | str] = []
        slept = 0.0

        def note(attempt: int) -> None:
            if span is not None:
                span.set(attempts=attempt + 1, retry_statuses=list(statuses), backoff_s=slept)

        def pause(s: float) -> None:
            nonlocal slept
            self._sleep(s)
            slept += s

        for attempt in range(t.max_tries):
            note(attempt)
            try:
                r = self._deadline_request(method, path, body, t.request_timeout_s)
            except (httpx.TransportError, CallDeadline) as e:
                last = f"{type(e).__name__}: {e}"
                statuses.append(type(e).__name__)
            else:
                if r.status_code < 400:
                    try:
                        return r.json()
                    except ValueError:
                        last = f"HTTP {r.status_code}: body is not JSON"
                        raise TransportFailure(last) from None
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                statuses.append(r.status_code)
                note(attempt)
                if r.status_code not in t.retry_status:
                    raise TransportFailure(last)
                retry_after = _price(r.headers.get("retry-after"))
                if retry_after is not None and attempt + 1 < t.max_tries:
                    pause(min(retry_after, t.backoff_cap_s))
                    continue
            if attempt + 1 < t.max_tries:
                pause(self._rng.uniform(0, min(t.backoff_cap_s, t.backoff_base_s * 2**attempt)))
        note(t.max_tries - 1)
        raise TransportFailure(f"{t.max_tries} tries: {last}")
