"""The response cache (SPEC § 5 eval pins; CARRY_FORWARD S4): content-addressed, atomic, and only for
deterministic calls; a hit records a zero-cost spend row."""

import dataclasses
import json
from pathlib import Path

import httpx
import pytest

from friday.adapters.cache import FileResponseCache, request_key
from friday.adapters.crusoe import CrusoeClient
from friday.adapters.memory import MemorySpendLog
from friday.adapters.telemetry import MemoryTelemetry
from friday.config import load_spec
from friday.ports.models import Message
from friday.ports.telemetry import Scope

TEL = MemoryTelemetry(Scope("main"))

ROOT = Path(__file__).resolve().parents[2]
SPEC = load_spec(ROOT / "run.yml")
PRICES = {"m": {"prompt": 0.1, "completion": 0.2}, "m-fallback": {"prompt": 0.1, "completion": 0.2}}

BODY = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.0, "max_tokens": 10}


def test_put_then_get_returns_the_value_and_a_miss_reads_none(tmp_path):
    cache = FileResponseCache(tmp_path)
    key = request_key(BODY)
    assert cache.get(key) is None
    cache.put(key, "réponse")
    assert cache.get(key) == "réponse"
    assert FileResponseCache(tmp_path).get(key) == "réponse"         # persisted on disk


def test_put_leaves_no_temporary_file_and_overwrites_whole(tmp_path):
    cache = FileResponseCache(tmp_path)
    key = request_key(BODY)
    cache.put(key, "a" * 10_000)
    cache.put(key, "b")
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert [p.name for p in files] == [key] and cache.get(key) == "b"


def test_a_key_that_is_not_a_digest_is_refused(tmp_path):
    cache = FileResponseCache(tmp_path)
    with pytest.raises(ValueError):
        cache.get("../../etc/passwd")
    with pytest.raises(ValueError):
        cache.put("ABC", "x")


def test_request_key_ignores_key_order_and_moves_with_every_field():
    reordered = dict(reversed(list(BODY.items())))
    assert request_key(reordered) == request_key(BODY)
    variants = [
        {**BODY, "model": "m2"},
        {**BODY, "temperature": 0.1},
        {**BODY, "max_tokens": 11},
        {**BODY, "seed": 1},
        {**BODY, "response_format": {"type": "json_object"}},
        {**BODY, "messages": [{"role": "user", "content": "hi!"}]},
        {**BODY, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "hi"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]}]},
    ]
    keys = {request_key(v) for v in variants}
    assert len(keys) == len(variants) and request_key(BODY) not in keys


def _client(monkeypatch, cache, served_model="m-served"):
    monkeypatch.setenv(SPEC.models.endpoint.api_key_env, "test-key")
    posts: list[dict] = []

    def handler(req: httpx.Request):
        posts.append(json.loads(req.content))
        return httpx.Response(200, json={
            "id": f"r{len(posts)}", "model": served_model, "choices": [{"message": {"content": f"reply {len(posts)}"}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100}})

    spend = MemorySpendLog()
    http = httpx.Client(base_url="https://x/v1", transport=httpx.MockTransport(handler))
    models = dataclasses.replace(SPEC.models, price_overrides=PRICES)
    client = CrusoeClient(models, SPEC.transport, spend, http=http, sleep=lambda s: None, cache=cache,
                          telemetry=TEL)
    return client, spend, posts


def test_second_identical_temperature_zero_call_is_a_zero_cost_cached_hit(monkeypatch, tmp_path):
    client, spend, posts = _client(monkeypatch, FileResponseCache(tmp_path))
    client.bind({"rubric": "m"})
    first = client.chat("rubric", [Message("user", "rate", images=(b"\x89PNG",))], tag="t")
    second = client.chat("rubric", [Message("user", "rate", images=(b"\x89PNG",))], tag="t")
    assert len(posts) == 1
    assert not first.cached and first.cost_usd and first.cost_usd > 0
    assert second.cached and second.cost_usd == 0.0 and second.text == first.text == "reply 1"
    assert second.model == "m-served"                                  # the echoed model, as stored
    assert "sent" not in second.raw and second.raw["requested"]["model"] == "m"
    assert [r[3] for r in spend.rows] == [True, True] and spend.rows[1][2].cached
    assert spend.total_usd() == pytest.approx(first.cost_usd)


def test_a_changed_image_misses_the_cache(monkeypatch, tmp_path):
    client, _, posts = _client(monkeypatch, FileResponseCache(tmp_path))
    client.bind({"rubric": "m"})
    client.chat("rubric", [Message("user", "rate", images=(b"one",))])
    client.chat("rubric", [Message("user", "rate", images=(b"two",))])
    assert len(posts) == 2


def test_a_fallback_model_never_reads_the_pins_cache_entry(monkeypatch, tmp_path):
    cache = FileResponseCache(tmp_path)
    client, _, posts = _client(monkeypatch, cache)
    client.bind({"rubric": "m"})
    client.chat("rubric", [Message("user", "rate")])
    client.bind({"rubric": "m-fallback"})
    res = client.chat("rubric", [Message("user", "rate")])
    assert len(posts) == 2 and not res.cached and posts[1]["model"] == "m-fallback"


def test_a_sampled_role_is_never_cached(monkeypatch, tmp_path):
    client, spend, posts = _client(monkeypatch, FileResponseCache(tmp_path))
    client.bind({"worker": "m"})
    assert SPEC.models.roles["worker"].temperature > 0
    a = client.chat("worker", [Message("user", "build")])
    b = client.chat("worker", [Message("user", "build")])
    assert len(posts) == 2 and a.text != b.text and not b.cached
    assert list(tmp_path.rglob("*")) == []


def test_without_a_cache_every_call_is_sent(monkeypatch):
    client, _, posts = _client(monkeypatch, None)
    client.bind({"rubric": "m"})
    client.chat("rubric", [Message("user", "rate")])
    client.chat("rubric", [Message("user", "rate")])
    assert len(posts) == 2
