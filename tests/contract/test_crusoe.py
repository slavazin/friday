import dataclasses
import json
from pathlib import Path

import httpx
import pytest

from friday.adapters.crusoe import CrusoeClient, TransportFailure, parse_models
from friday.adapters.memory import MemorySpendLog
from friday.adapters.telemetry import MemoryTelemetry
from friday.app.pins import NoServedModel, demote_group, resolve
from friday.config import load_spec
from friday.ports.models import Message
from friday.ports.telemetry import Scope

TEL = MemoryTelemetry(Scope("main"))

ROOT = Path(__file__).resolve().parents[2]
SPEC = load_spec(ROOT / "run.yml")
MODELS = json.loads((ROOT / "tests/data/models_2026-10-17.json").read_text())


def test_pins_resolve_against_the_served_list():
    served = parse_models(MODELS)
    res = resolve(SPEC.models.roles, served)
    assert res["worker"].model == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B" and not res["worker"].replaced
    assert res["rubric"].model == "nvidia/NVIDIA-Nemotron-3.5-Super-VL"
    assert not res["nominator"].replaced and res["nominator"].model == "zai-org/GLM-5.3"
    assert res["nominator"].model != res["judge"].model
    assert res["critic"].model not in (res["judge"].model, res["nominator"].model)
    demoted = demote_group(res, SPEC.models.roles, "drafting")
    assert all(demoted[r].replaced for r in ("nominator", "lens_minter", "goal_minter", "tagger", "pooling"))
    assert not demoted["judge"].replaced


def test_image_role_skips_text_only_pins():
    image_first = ("nvidia/NVIDIA-Nemotron-3.5-Super-VL", "google/gemma-4-31b-it")
    served = [m for m in parse_models(MODELS) if m.id not in image_first]
    assert resolve(SPEC.models.roles, served)["rubric"].model == "zai-org/GLM-5.3-Flash"


def test_missing_fallback_raises():
    with pytest.raises(NoServedModel):
        resolve(SPEC.models.roles, [])


def make_client(monkeypatch, handler):
    monkeypatch.setenv(SPEC.models.endpoint.api_key_env, "test-key")
    spend = MemorySpendLog()
    http = httpx.Client(base_url="https://x/v1", transport=httpx.MockTransport(handler))
    sleeps: list[float] = []
    models = dataclasses.replace(SPEC.models, price_overrides={"m": {"prompt": 0.1, "completion": 0.2}})
    c = CrusoeClient(models, SPEC.transport, spend, http=http, sleep=sleeps.append, telemetry=TEL)
    return c, spend, sleeps


def reply(model="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"):
    body = {"id": "r1", "model": model, "choices": [{"message": {"content": "<!doctype html>"}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 2000}}
    return httpx.Response(200, json=body)


def test_chat_retries_on_429_and_meters_the_call(monkeypatch):
    calls = []

    def handler(req: httpx.Request):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json=MODELS)
        calls.append(json.loads(req.content))
        return httpx.Response(429, headers={"retry-after": "2"}) if len(calls) == 1 else reply()

    c, spend, sleeps = make_client(monkeypatch, handler)
    resolve_map = {r: v.model for r, v in resolve(SPEC.models.roles, c.list_models()).items()}
    c.bind(resolve_map)
    res = c.chat("worker", [Message("user", "hi")], tag="x1")
    assert sleeps == [2.0] and res.model == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    assert calls[-1]["temperature"] == 0.7 and "seed" not in calls[-1]
    assert res.cost_usd == pytest.approx((1000 * 0.05 + 2000 * 0.20) / 1e6)
    assert spend.rows[-1][3] is True and spend.rows[-1][2].raw["sent"]["temperature"] == 0.7


def test_chat_gives_up_after_max_tries_and_records_the_failure(monkeypatch):
    def handler(req):
        return httpx.Response(503, text="busy")

    c, spend, sleeps = make_client(monkeypatch, handler)
    c.bind({"worker": "m"})
    with pytest.raises(TransportFailure):
        c.chat("worker", [Message("user", "hi")])
    assert len(sleeps) == SPEC.transport.max_tries - 1 and spend.rows[-1][3] is False


def test_client_error_is_not_retried(monkeypatch):
    c, _, sleeps = make_client(monkeypatch, lambda req: httpx.Response(400, text="bad"))
    c.bind({"worker": "m"})
    with pytest.raises(TransportFailure):
        c.chat("worker", [Message("user", "hi")])
    assert sleeps == []


def test_bind_refuses_unpriced_models(monkeypatch):
    c, _, _ = make_client(monkeypatch, lambda req: httpx.Response(200, json=MODELS))
    with pytest.raises(RuntimeError, match="no price"):
        c.bind({"worker": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"})      # catalog not loaded yet
    c.list_models()
    c.bind({"worker": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"})


def test_malformed_reply_is_a_recorded_transport_failure(monkeypatch):
    c, spend, _ = make_client(monkeypatch, lambda req: httpx.Response(200, json={"model": "m", "choices": []}))
    c.bind({"worker": "m"})
    with pytest.raises(TransportFailure, match="malformed"):
        c.chat("worker", [Message("user", "hi")])
    assert spend.rows[-1][3] is False


@pytest.mark.live
def test_live_models_list_serves_every_role():
    from friday.adapters.crusoe import CrusoeClient as Live

    served = Live(SPEC.models, SPEC.transport, MemorySpendLog()).list_models()
    resolve(SPEC.models.roles, served)
