"""The rubric adapter (SPEC § 5 `R`): frozen questions per viewport, the screenshot sent as an image, one
re-ask, never a defaulted score, cached by artifact hash and viewport."""

import base64
import dataclasses
import json
from pathlib import Path

import httpx
import pytest

from friday.adapters.cache import FileResponseCache
from friday.adapters.crusoe import CrusoeClient
from friday.adapters.memory import MemorySpendLog
from friday.adapters.rubric import ModelRubric, RubricUnreadable
from friday.algorithms.language import blinding_hits
from friday.algorithms.loss import LossModel
from friday.config import load_spec
from friday.roles.ask import Rejected, ask_json
from friday.roles.prompts import PromptBook
from tests.fakes import ScriptedClient

ROOT = Path(__file__).resolve().parents[2]
SPEC = load_spec(ROOT / "run.yml")
BOOK = PromptBook(ROOT / "prompts")
VP = (1280, 800)
GOOD = '{"resembles": 4, "controls_visible": 5, "legible": 3}'
SHOT = b"\x89PNG fake screenshot"
REQUEST = "A timer with #start and #time. Costs $5."


def rubric(client, cache=None, model="fake/model", spend=None):
    if cache is not None and spend is None:
        spend = MemorySpendLog()
    return ModelRubric.from_spec(SPEC, client, ask_json, Rejected, model=model, prompts=ROOT / "prompts", cache=cache,
                                 spend=spend)


def test_template_asks_exactly_the_configured_number_of_questions():
    r = rubric(ScriptedClient([]))
    assert r.questions == ("resembles", "controls_visible", "legible")
    assert len(r.questions) == SPEC.eval.rubric_questions


def test_a_template_with_the_wrong_question_count_is_refused():
    text = (ROOT / "prompts/rubric.md").read_text()
    with pytest.raises(ValueError, match="rubric_questions"):
        ModelRubric(ScriptedClient([]), ask_json, Rejected, text, model="m", scale=(1, 5), questions=4, reasks=1)


def test_prompt_names_no_blinded_word():
    # The judge's lens row plus the words the rubric reader must never see (no rubric row in blinding.yml yet).
    words = set(BOOK.blinding["judge_lens"]) | {"check", "checks", "loss", "pass", "fail", "regime", "drift",
                                                "candidate", "arm", "judge", "exposure", "decision", "journal"}
    words |= set(SPEC.stream.families)
    template = (ROOT / "prompts/rubric.md").read_text()
    assert blinding_hits(template, words) == []
    assert blinding_hits(template, BOOK.hedges) == []


def test_scores_come_back_in_question_order_and_map_through_the_loss():
    client = ScriptedClient([GOOD])
    scores = rubric(client).score(SHOT, REQUEST, "a" * 64, VP)
    assert scores == (4, 5, 3)
    assert LossModel(SPEC.eval.w_a, SPEC.eval.w_r, SPEC.eval.rubric_scale, SPEC.eval.rubric_questions).rubric(scores) \
        == pytest.approx((0.75 + 1.0 + 0.5) / 3)


def test_screenshot_is_attached_and_request_text_is_verbatim():
    client = ScriptedClient([GOOD])
    rubric(client).score(SHOT, REQUEST, "a" * 64, (375, 812))
    role, msgs, tag = client.calls[0]
    assert role == "rubric" and tag.startswith("rubric:") and "375x812" in tag
    assert msgs[-1].images == (SHOT,)
    assert REQUEST in msgs[-1].content                                # `$5` survives the template
    assert "375 × 812" in msgs[0].content and "from 1 (not at all) to 5 (fully)" in msgs[0].content


def test_unreadable_twice_raises_and_never_defaults():
    client = ScriptedClient(["no json here", '{"resembles": 4}'])
    with pytest.raises(RubricUnreadable):
        rubric(client).score(SHOT, REQUEST, "a" * 64, VP)
    assert len(client.calls) == 1 + SPEC.json.reasks


@pytest.mark.parametrize("bad", [
    '{"resembles": 6, "controls_visible": 5, "legible": 3}',
    '{"resembles": 0, "controls_visible": 5, "legible": 3}',
    '{"resembles": 4.5, "controls_visible": 5, "legible": 3}',
    '{"resembles": "4", "controls_visible": 5, "legible": 3}',
    '{"resembles": true, "controls_visible": 5, "legible": 3}',
    '[4, 5, 3]',
])
def test_an_off_scale_or_off_form_answer_is_re_asked_with_the_reason(bad):
    client = ScriptedClient([bad, GOOD])
    assert rubric(client).score(SHOT, REQUEST, "a" * 64, VP) == (4, 5, 3)
    reask = client.calls[1][1][-1]
    assert reask.role == "user" and "not usable" in reask.content and client.calls[1][2].endswith(":reask1")


def test_cache_hit_skips_the_call_and_returns_the_same_scores(tmp_path):
    cache = FileResponseCache(tmp_path)
    client = ScriptedClient([GOOD])
    assert rubric(client, cache).score(SHOT, REQUEST, "a" * 64, VP) == (4, 5, 3)
    assert rubric(client, cache).score(b"other bytes", REQUEST, "a" * 64, VP) == (4, 5, 3)
    assert len(client.calls) == 1


@pytest.mark.parametrize("change", ["viewport", "artifact", "model", "request", "prompt"])
def test_the_cache_key_moves_with_everything_that_changes_the_answer(tmp_path, change):
    cache = FileResponseCache(tmp_path)
    client = ScriptedClient([GOOD, GOOD])
    rubric(client, cache).score(SHOT, REQUEST, "a" * 64, VP)
    vp, art, req = VP, "a" * 64, REQUEST
    r = rubric(client, cache)
    if change == "viewport":
        vp = (375, 812)
    elif change == "artifact":
        art = "b" * 64
    elif change == "model":
        r = rubric(client, cache, model="other/model")
        client.model = "other/model"                                   # the service answers from the new binding
    elif change == "request":
        req = REQUEST + " It must work on a phone screen."
    else:
        text = (ROOT / "prompts/rubric.md").read_text().replace("Rate only", "Rate strictly only")
        r = ModelRubric(client, ask_json, Rejected, text, model="fake/model", scale=(1, 5), questions=3, reasks=1,
                        cache=cache, spend=MemorySpendLog())
    r.score(SHOT, req, art, vp)
    assert len(client.calls) == 2


def test_a_corrupt_cached_answer_is_revalidated_and_raises(tmp_path):
    cache = FileResponseCache(tmp_path)
    r = rubric(ScriptedClient([]), cache)
    key = r.cache_key("a" * 64, VP, REQUEST)
    cache.put(key, json.dumps({"scores": {"resembles": 9, "controls_visible": 5, "legible": 3},
                               "echoed_model": "fake/model"}))
    with pytest.raises(RubricUnreadable, match="cached"):
        r.score(SHOT, REQUEST, "a" * 64, VP)
    cache.put(key, json.dumps({"other": 1}))
    with pytest.raises(RubricUnreadable, match="cached"):
        r.score(SHOT, REQUEST, "a" * 64, VP)


def test_a_rubric_level_cache_hit_records_a_zero_cost_spend_row_with_the_echoed_model(tmp_path):
    cache, spend = FileResponseCache(tmp_path), MemorySpendLog()
    rubric(ScriptedClient([GOOD]), cache, spend=spend).score(SHOT, REQUEST, "a" * 64, VP)
    assert spend.rows == []                                            # the live call's row is the client's to write
    client = ScriptedClient([])
    assert rubric(client, cache, spend=spend).score(SHOT, REQUEST, "a" * 64, VP) == (4, 5, 3)
    assert client.calls == []
    [(role, tag, result, ok, _)] = spend.rows
    assert role == "rubric" and ok and tag.startswith("rubric:") and "1280x800" in tag
    assert result.cached and result.cost_usd == 0.0 and result.model == "fake/model"


def test_a_cache_without_a_spend_log_is_refused():
    with pytest.raises(ValueError, match="spend"):
        ModelRubric.from_spec(SPEC, ScriptedClient([]), ask_json, Rejected, model="m", prompts=ROOT / "prompts",
                              cache=FileResponseCache(Path("/nonexistent")))


def test_a_cached_entry_answered_by_another_model_raises(tmp_path):
    cache = FileResponseCache(tmp_path)
    r = rubric(ScriptedClient([]), cache)
    cache.put(r.cache_key("a" * 64, VP, REQUEST), json.dumps({
        "scores": {"resembles": 4, "controls_visible": 5, "legible": 3}, "echoed_model": "other/model"}))
    with pytest.raises(RubricUnreadable, match="other/model"):
        r.score(SHOT, REQUEST, "a" * 64, VP)


def test_the_routing_check_compares_the_echoed_model_and_never_caches_a_differing_echo(monkeypatch, tmp_path):
    client, _, posts = _crusoe(monkeypatch, [GOOD])                    # the mock service always echoes "m"
    client.bind({"rubric": "n"})
    cache = FileResponseCache(tmp_path)
    r = rubric(client, cache, model="n")
    with pytest.raises(RuntimeError, match="echoed 'm'"):
        r.score(SHOT, REQUEST, "a" * 64, VP)
    assert posts[0]["model"] == "n"                                    # the request itself went to the bound id
    assert cache.get(r.cache_key("a" * 64, VP, REQUEST)) is None


def test_an_empty_screenshot_is_refused_without_a_call():
    client = ScriptedClient([GOOD])
    with pytest.raises(ValueError):
        rubric(client).score(b"", REQUEST, "a" * 64, VP)
    assert client.calls == []


def _crusoe(monkeypatch, replies, cache=None):
    monkeypatch.setenv(SPEC.models.endpoint.api_key_env, "test-key")
    posts: list[dict] = []

    def handler(req: httpx.Request):
        posts.append(json.loads(req.content))
        return httpx.Response(200, json={"id": "r", "model": "m", "choices": [{"message": {"content": replies.pop(0)}}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 10}})

    models = dataclasses.replace(SPEC.models, price_overrides={"m": {"prompt": 0.1, "completion": 0.2},
                                                               "n": {"prompt": 0.1, "completion": 0.2}})
    spend = MemorySpendLog()
    http = httpx.Client(base_url="https://x/v1", transport=httpx.MockTransport(handler))
    return CrusoeClient(models, SPEC.transport, spend, http=http, sleep=lambda s: None, cache=cache), spend, posts


def test_the_request_body_carries_the_image_as_a_content_part_at_temperature_zero(monkeypatch):
    client, spend, posts = _crusoe(monkeypatch, [GOOD])
    client.bind({"rubric": "m"})
    rubric(client, model="m").score(SHOT, REQUEST, "a" * 64, VP)
    user = posts[0]["messages"][-1]["content"]
    assert user[0]["type"] == "text" and REQUEST in user[0]["text"]
    assert user[1] == {"type": "image_url",
                       "image_url": {"url": "data:image/png;base64," + base64.b64encode(SHOT).decode()}}
    assert posts[0]["temperature"] == 0.0 == SPEC.models.roles["rubric"].temperature
    assert spend.rows[0][2].raw["sent"]["temperature"] == 0.0 and spend.rows[0][2].raw["sent"]["model"] == "m"


def test_a_call_routed_to_another_model_than_the_bound_one_raises(monkeypatch):
    client, _, _ = _crusoe(monkeypatch, [GOOD])
    client.bind({"rubric": "n"})
    with pytest.raises(RuntimeError, match="bound to m"):
        rubric(client, model="m").score(SHOT, REQUEST, "a" * 64, VP)


def test_a_cached_unreadable_reply_still_raises_rather_than_scoring(monkeypatch, tmp_path):
    client, _, posts = _crusoe(monkeypatch, ["garbage", "garbage"], cache=FileResponseCache(tmp_path))
    client.bind({"rubric": "m"})
    with pytest.raises(RubricUnreadable):
        rubric(client, model="m").score(SHOT, REQUEST, "a" * 64, VP)
    with pytest.raises(RubricUnreadable):                              # replayed from the response cache
        rubric(client, model="m").score(SHOT, REQUEST, "a" * 64, VP)
    assert len(posts) == 2


@pytest.mark.live
def test_live_fixture_screenshot_scores_and_the_second_call_is_a_cache_hit(tmp_path):
    pytest.importorskip("playwright")
    from friday.adapters.playwright_browser import PlaywrightBrowser
    from friday.app.pins import resolve

    spend = MemorySpendLog()
    cache = FileResponseCache(tmp_path / "cache")
    client = CrusoeClient(SPEC.models, SPEC.transport, spend, cache=cache)
    resolved = resolve(SPEC.models.roles, client.list_models())
    client.bind({"rubric": resolved["rubric"].model})
    fixture = ROOT / "tests/data/quality/clean.html"
    browser = PlaywrightBrowser(SPEC.eval.action_timeout_ms, SPEC.eval.load_timeout_ms)
    try:
        with browser.page(str(fixture), VP) as page:
            shot = page.screenshot()
    finally:
        browser.close()
    request = "A counter: #out shows a number and the #go button adds one; #name takes a name."
    art = "fixture-clean"
    first = rubric(client, model=resolved["rubric"].model).score(shot, request, art, VP)
    # A rubric without its own cache reaches the client; the identical body is a response-cache hit.
    second = ModelRubric.from_spec(SPEC, client, ask_json, Rejected, model=resolved["rubric"].model,
                                   prompts=ROOT / "prompts").score(shot, request, art, VP)
    assert first == second and len(first) == SPEC.eval.rubric_questions
    live_rows = [r for r in spend.rows if not r[2].cached]
    hits = [r for r in spend.rows if r[2].cached]
    assert hits and all(r[2].cost_usd == 0.0 for r in hits) and len(live_rows) >= 1
    print(f"rubric model {resolved['rubric'].model} echoed {live_rows[0][2].model}; scores {first}; "
          f"cost ${spend.total_usd():.6f}; rows {[(r[1], r[2].cached, r[2].cost_usd) for r in spend.rows]}")


def test_recorded_live_replies_decode_to_their_recorded_scores_under_the_frozen_prompt():
    # tests/data/rubric_live_replies.json: two live rubric calls on the quality fixtures' 1280x800 screenshots.
    recorded = json.loads((ROOT / "tests/data/rubric_live_replies.json").read_text())
    for name, rec in recorded.items():
        r = rubric(ScriptedClient([rec["text"]], model=rec["model"]), model=rec["model"])
        assert rec["prompt_hash"] == r.prompt_hash, "prompts/rubric.md changed since the live recording"
        assert list(r.score(SHOT, REQUEST, name, VP)) == rec["scores"]
    assert recorded["clean"]["scores"] != recorded["flawed"]["scores"]
