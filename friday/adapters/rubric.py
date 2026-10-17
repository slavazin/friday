"""The rubric (SPEC § 5 `R`): the frozen questions of `prompts/rubric.md`, answered over one viewport's
screenshot by the `rubric` role's model. Implements `friday.ports.scoring.Rubric`.

The reply is decoded through the one JSON path (`ask_json`, injected by the composition root because
adapters do not import roles) with `json.reasks` re-asks; an answer still unreadable after that raises
`RubricUnreadable`. No score is ever defaulted.

Scores are cached by (artifact hash, viewport, prompt hash, model id, request text). A cached answer is
validated again on every read, so a corrupt or off-scale entry raises instead of scoring.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from string import Template
from typing import Any, Protocol

from friday.adapters.cache import request_key
from friday.config import RunSpec
from friday.domain.types import Viewport
from friday.ports.models import ChatResult, Message, ModelClient, ResponseCache

ROLE = "rubric"
TEMPLATE = "rubric.md"
_QUESTION = re.compile(r"^- (\w+): ", flags=re.MULTILINE)


class RubricUnreadable(RuntimeError):
    """The rubric model's answer could not be read after the re-asks; the pass has no R."""


class _Asked(Protocol):
    value: Any
    results: tuple[ChatResult, ...]
    failures: tuple[str, ...]


class AskJson(Protocol):
    """The signature of `friday.roles.ask.ask_json`."""

    def __call__(
        self, client: ModelClient, role: str, messages: Sequence[Message], validate: Callable[[Any], Any], *,
        reasks: int, tag: str, schema: dict[str, Any] | None = None,
    ) -> _Asked: ...


class ModelRubric:
    def __init__(
        self,
        client: ModelClient,
        ask: AskJson,
        rejected: type[Exception],
        template: str,
        *,
        model: str,
        scale: tuple[int, int],
        questions: int,
        reasks: int,
        cache: ResponseCache | None = None,
    ) -> None:
        """`ask` and `rejected` are `friday.roles.ask.ask_json` and `friday.roles.ask.Rejected`; `model` is the
        id the `rubric` role is bound to (after pin resolution), so a fallback model never reads a pin's cache."""
        system, sep, user = template.partition("\n---\n")
        if not sep:
            raise ValueError("rubric template needs a '---' line between the instructions and the request part")
        self._keys = tuple(_QUESTION.findall(system))
        if len(self._keys) != questions or len(set(self._keys)) != questions:
            raise ValueError(f"rubric template asks {list(self._keys)}; eval.rubric_questions is {questions}")
        self._client, self._ask, self._rejected = client, ask, rejected
        self._system, self._user = Template(system.strip()), Template(user.strip())
        self._model, self._scale, self._reasks, self._cache = model, scale, reasks, cache
        self.prompt_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()

    @classmethod
    def from_spec(
        cls, spec: RunSpec, client: ModelClient, ask: AskJson, rejected: type[Exception], *, model: str,
        prompts: Path, cache: ResponseCache | None = None,
    ) -> ModelRubric:
        """The rubric on `eval.rubric_scale`, `eval.rubric_questions` and `json.reasks`, template from `prompts`."""
        return cls(
            client, ask, rejected, (prompts / TEMPLATE).read_text(encoding="utf-8"), model=model,
            scale=spec.eval.rubric_scale, questions=spec.eval.rubric_questions, reasks=spec.json.reasks,
            cache=cache,
        )

    @property
    def questions(self) -> tuple[str, ...]:
        return self._keys

    def messages(self, screenshot: bytes, request_text: str, viewport: Viewport) -> list[Message]:
        """The rubric call's messages: the instructions, then the request text with the screenshot attached."""
        lo, hi = self._scale
        system = self._system.substitute(width=viewport[0], height=viewport[1], lo=lo, hi=hi)
        user = self._user.substitute(request_text=request_text.strip())
        return [Message("system", system), Message("user", user, images=(screenshot,))]

    def schema(self) -> dict[str, Any]:
        lo, hi = self._scale
        return {
            "type": "object",
            "properties": {k: {"type": "integer", "minimum": lo, "maximum": hi} for k in self._keys},
            "required": list(self._keys),
        }

    def validate(self, value: Any) -> tuple[int, ...]:
        """The scores in question order; raises `rejected` with the reason when any is missing or off-scale."""
        lo, hi = self._scale
        if not isinstance(value, dict):
            raise self._rejected(f"expected a JSON object with keys {list(self._keys)}")
        out = []
        for k in self._keys:
            if k not in value:
                raise self._rejected(f"missing {k!r}")
            v = value[k]
            if isinstance(v, bool) or not isinstance(v, int):
                raise self._rejected(f"{k!r} must be a whole number from {lo} to {hi}, got {v!r}")
            if not lo <= v <= hi:
                raise self._rejected(f"{k!r} must lie from {lo} to {hi}, got {v}")
            out.append(v)
        return tuple(out)

    def cache_key(self, artifact_hash: str, viewport: Viewport, request_text: str) -> str:
        return request_key({
            "kind": "rubric", "artifact_hash": artifact_hash, "viewport": list(viewport),
            "prompt_hash": self.prompt_hash, "model": self._model, "request_text": request_text,
        })

    def score(self, screenshot: bytes, request_text: str, artifact_hash: str, viewport: Viewport) -> tuple[int, ...]:
        """The frozen questions' scores on the rubric scale for one viewport's screenshot.

        Raises `RubricUnreadable` when the answer is unreadable after the re-asks, and RuntimeError when the
        client sent the call to a model other than the one this rubric is bound to.
        """
        if not screenshot:
            raise ValueError("empty screenshot: an artifact that cannot render scores the floor in the evaluator")
        key = self.cache_key(artifact_hash, tuple(viewport), request_text)
        if self._cache is not None:
            stored = self._cache.get(key)
            if stored is not None:
                # Our own cache entry (written below), not model output.
                return self._cached_scores(json.loads(stored))
        w, h = viewport
        asked = self._ask(
            self._client, ROLE, self.messages(screenshot, request_text, viewport), self.validate,
            reasks=self._reasks, tag=f"rubric:{artifact_hash[:12]}:{w}x{h}", schema=self.schema(),
        )
        for res in asked.results:
            sent = res.raw.get("sent") or res.raw.get("requested") or {}
            if "model" in sent and sent["model"] != self._model:
                raise RuntimeError(f"rubric bound to {self._model} but the call went to {sent['model']}")
        if asked.value is None:
            raise RubricUnreadable(f"rubric answer unreadable after {len(asked.results)} calls: {asked.failures}")
        scores: tuple[int, ...] = asked.value
        if self._cache is not None:
            self._cache.put(key, json.dumps({
                "scores": dict(zip(self._keys, scores, strict=True)), "echoed_model": asked.results[-1].model,
                "model": self._model, "prompt_hash": self.prompt_hash, "artifact_hash": artifact_hash,
                "viewport": list(viewport),
            }, sort_keys=True))
        return scores

    def _cached_scores(self, entry: Any) -> tuple[int, ...]:
        try:
            return self.validate(entry["scores"])
        except (self._rejected, KeyError, TypeError) as e:
            raise RubricUnreadable(f"cached rubric entry is unreadable: {e}") from e
