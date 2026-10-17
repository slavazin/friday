"""The manager's taste on the critic run (SPEC § 17 Taste): the planted quirks, their reach (the critic and the
eval only), loss_base, and the taste readings."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from friday.adapters.memory import MemoryEventLog, MemoryLedger, MemoryPassStore
from friday.algorithms.language import blinding_hits
from friday.app import pipeline
from friday.app.readout import critic_readings, taste_lines, taste_readings
from friday.app.runner import critic_taste_weight
from friday.app.taste import TASTE_PATH, Taste, load_cells, load_quirks
from friday.app.wiring import build_mechanics
from friday.domain.events import StatusFlip
from friday.domain.lifecycle import FlipCause
from friday.domain.types import (
    Arm,
    CheckResult,
    Composition,
    DeltaKind,
    EvalReport,
    LedgerDelta,
    Observation,
    Pass,
    Pool,
    Regime,
)
from friday.roles.critic import Critic, validate_reply
from friday.roles.nominator import render_nominator
from friday.roles.pooling import render_pooling
from friday.roles.tagger import Tagger, TaggingRefused
from tests.fakes import ScriptedClient
from tests.unit.test_critic import REVISED, is_critic, is_revision
from tests.unit.test_pipeline import BOOK, SPEC, TEL, JudgeLog, factory, html_for, scripted_judge, two_request_round
from tests.unit.test_pipeline import env as pass_env

ROOT = Path(__file__).resolve().parents[2]
S = SPEC.stream
QUIRKS = load_quirks(ROOT / TASTE_PATH, S.families, S.modes, SPEC.eval.max_wait_ms)
BY_ID = {q.id: q for q in QUIRKS}
LOSS = build_mechanics(SPEC).loss
W = critic_taste_weight(ROOT / "run-critic.yml")
CRITIC_ROUNDS = 4                                       # run-critic.yml run.rounds
CELLS = {"Q-0001": ("canvas game", "keyboard-driven"), "Q-0002": ("timer or tracker", "click or tap")}


def stream_rounds(rounds: int = CRITIC_ROUNDS) -> dict[str, int]:
    rows = json.loads((ROOT / S.path).read_text())
    return {r["id"]: r["round"] for r in rows if not r["held_out"] and r["round"] <= rounds}


# --- the quirks file ---------------------------------------------------------------------------------------


def test_six_quirks_span_a_dense_family_a_drift_dense_family_two_sparse_families_a_mode_and_a_control():
    assert len(QUIRKS) == 6
    assert [q.scope for q in QUIRKS] == [
        ("family", "canvas game"), ("family", "data tool"), ("family", "text tool"),
        ("family", "form or calculator"), ("mode", "keyboard-driven"), ("any", None)]
    cells, rounds = load_cells(ROOT / S.path), stream_rounds()
    share = {q.id: sum(q.covers(cells[r]) for r in rounds) / len(rounds) for q in QUIRKS}
    assert share["app-title"] == 1.0
    assert share["pause"] >= 0.4                                           # dense in the stream
    assert share["dark-mode"] <= 0.1 and share["clear-all"] <= 0.1          # sparse
    per_round = Counter(rounds[r] for r in rounds if BY_ID["row-count"].covers(cells[r]))
    assert per_round[SPEC.drift.round] > max(per_round[n] for n in range(1, SPEC.drift.round))  # dense from the drift


def test_each_preference_is_one_sentence_naming_no_family_or_mode_and_passes_as_a_critic_request():
    for q in QUIRKS:
        assert blinding_hits(q.preference, (*S.families, *S.modes)) == [], q.id
        assert validate_reply({"criticism": "x", "requests": [q.preference]}, 2).requests == (q.preference,)
        sels = [v for s in q.check.steps for k, v in s.items() if k in ("click", "focus", "type")]
        sels += [q.check.expect[k] for k in q.check.expect if k != "regex"]
        for sel in sels:                                    # the preference names every id its check reads
            assert sel.lstrip("#") in q.preference, (q.id, sel)


def test_no_quirk_check_id_or_element_id_clashes_with_a_request_of_the_stream():
    rows = json.loads((ROOT / S.path).read_text()) + json.loads((ROOT / S.held_out_path).read_text())
    for r in rows:
        own = {c["id"] for cs in r["checks"].values() for c in cs}
        assert own.isdisjoint({q.check.id for q in QUIRKS}), r["id"]
        for q in QUIRKS:
            if q.covers(load_cells(ROOT / S.path)[r["id"]]):
                assert q.check.expect.get("text_matches", q.check.expect.get("visible")) not in r["contract"]


def test_every_stream_request_of_the_critic_run_has_a_cell():
    cells = load_cells(ROOT / S.path)
    assert set(stream_rounds()) <= set(cells)


def test_a_malformed_quirk_file_is_refused(tmp_path):
    good = (ROOT / TASTE_PATH).read_text()
    for bad, match in ((good.replace("{family: text tool}", "{family: poem tool}"), "names no"),
                       (good.replace("id: c12", "id: c11"), "repeated"),
                       (good.replace("{wait: 300}]\n      expect: {text_matches: \"#pause-note\"",
                                     "{wait: 9000}]\n      expect: {text_matches: \"#pause-note\""), "wait"),
                       (good.replace("I always want a line", "I always want a data tool line"), "names")):
        p = tmp_path / "taste.yml"
        p.write_text(bad)
        with pytest.raises(ValueError, match=match):
            load_quirks(p, S.families, S.modes, SPEC.eval.max_wait_ms)


# --- one scripted critic round with the taste on -----------------------------------------------------------


class CheckEval:
    """An evaluator that reads the checks it is handed: each passes when its expectation's id is in the file."""

    def __init__(self) -> None:
        self.seen: dict[str, list[str]] = {}

    def __call__(self, pass_, request_, regime, text) -> EvalReport:
        html = Path(pass_.artifact_path).read_text()
        checks = request_.checks_for(regime)
        self.seen[pass_.id] = [c.id for c in checks]
        vp = "@1280x800"
        results = tuple(CheckResult(c.id + vp, f'id="{next(iter(c.expect.values())).lstrip("#")}"' in html)
                        for c in checks)
        c = sum(r.passed for r in results) / len(results)
        p, h, r = 0.5, 0.5, 0.5
        a = LOSS.a_mix(p, h, c)
        return EvalReport(pass_.id, regime, results, (), a, r, (3, 3, 3), 1, LOSS.loss(a, r), P=p, H=h, C=c)


def voicing(messages) -> str:
    if is_critic(messages):
        return json.dumps({"criticism": "No way to stop it.", "requests": [BY_ID["pause"].preference]})
    if is_revision(messages):
        return REVISED
    return html_for(messages)


def taste_round(tmp_path, monkeypatch):
    log = JudgeLog([], [])
    scripted_judge(monkeypatch, log)
    reqs, _, _, store, _, tasks = two_request_round(tmp_path)
    client = ScriptedClient([voicing] * 200)
    ev = CheckEval()
    e = pass_env(tmp_path, client, evaluate=ev)
    e.critic = Critic(client, BOOK, SPEC.critic.max_requests, SPEC.json.reasks)
    e.taste = Taste(QUIRKS, CELLS, LOSS, W)
    result = pipeline.run_round(1, store.plans(1), tasks, factory(e), parallel=1, reruns=0, in_process=True,
                                telemetry=TEL)
    assert result.crashed == {}
    return reqs, tasks, client, ev, log, result


def _texts(messages) -> str:
    return "\n".join(m.content for m in messages)


def test_an_in_scope_quirk_reaches_the_critic_and_the_checks_and_an_out_of_scope_one_reaches_neither(
        tmp_path, monkeypatch):
    _, tasks, client, ev, _, _ = taste_round(tmp_path, monkeypatch)
    want = {"Q-0001": {"pause", "key-help", "app-title"}, "Q-0002": {"app-title"}}
    for t in tasks:
        critic = [c[1] for c in client.calls if c[0] == "critic" and c[2] == f"{t.pass_id}:critic"]
        assert len(critic) == 1
        text = _texts(critic[0])
        for q in QUIRKS:
            assert (q.preference in text) == (q.id in want[t.request.id]), (t.pass_id, q.id)
            assert (q.check.id in ev.seen[t.pass_id]) == (q.id in want[t.request.id]), (t.pass_id, q.id)
        assert ev.seen[t.pass_id][: len(t.request.checks_for(t.regime))] == [c.id for c in
                                                                              t.request.checks_for(t.regime)]
        rec = json.loads((tmp_path / "artifacts" / t.pass_id / pipeline.TASTE_FILE).read_text())
        assert {r["quirk"] for r in rec["quirks"]} == want[t.request.id]


def test_no_quirk_text_reaches_the_build_prompt_the_judge_the_pooling_call_the_nominator_or_the_tagger(
        tmp_path, monkeypatch):
    reqs, tasks, client, _, log, result = taste_round(tmp_path, monkeypatch)
    prefs = [q.preference for q in QUIRKS]

    def clean(text: str, who: str) -> None:
        assert [p for p in prefs if p in text] == [], who
        assert blinding_hits(text, ["I always want"]) == [], who

    revisions = 0
    for role, messages, tag in client.calls:
        if role == "critic":
            continue
        if tag.endswith(":revision"):                       # the manager's request, in the user's words: allowed
            revisions += 1
            continue
        clean(_texts(messages), f"{role} {tag}")
    assert revisions == len(tasks)
    for row in log.lens:
        clean(json.dumps(row), "judge lens call")
    for row in log.report:
        clean(json.dumps(row, default=str), "judge report call")
    obs = [Observation(f"O-{i:04d}", o.pass_id, 1, "L-01", o.category, o.draft.shape, o.draft.direction,
                       o.draft.quote) for i, o in enumerate(x for out in result.outcomes for x in out.observations)]
    assert obs
    clean(_texts(render_pooling(BOOK, [], obs, SPEC.pool.max_direction_labels)), "pooling")
    pool = Pool("P-0001", "L-01", obs[0].shape, ("do the direction",))
    clean(_texts(render_nominator(BOOK, pool, "do the direction", obs, [], frozenset({"canvas"}),
                                  SPEC.nominator.payload_max_words)), "nominator")
    tagger_client = ScriptedClient(["{}"] * 5)
    with pytest.raises(TaggingRefused):
        Tagger(tagger_client, BOOK, (2, 4), 1, tuple(S.families)).tag(
            [r.__class__(r.id, r.text, frozenset(), r.contract, r.checks, round=1) for r in reqs], [], 1, 4)
    for _, messages, _ in tagger_client.calls:
        clean(_texts(messages), "tagger")


def test_taste_is_its_own_component_of_a_and_loss_base_leaves_it_out(tmp_path, monkeypatch):
    _, tasks, _, ev, _, result = taste_round(tmp_path, monkeypatch)
    for out in result.outcomes:
        rep = out.report
        rec = json.loads((tmp_path / "artifacts" / out.pass_.id / pipeline.TASTE_FILE).read_text())
        taste_ids = {q.check.id for q in QUIRKS}
        own = [r.passed for r in rep.checks if r.check_id.split("@")[0] not in taste_ids]
        mine = [r.passed for r in rep.checks if r.check_id.split("@")[0] in taste_ids]
        assert mine                                          # the eval still lists each taste check
        c_base = sum(own) / len(own)
        a_main = LOSS.a_mix(rep.P, rep.H, c_base)
        t = sum(mine) / len(mine)
        assert pytest.approx(c_base) == rep.C                # the taste checks are out of C: counted in T alone
        assert pytest.approx((1 - W) * a_main + W * t) == rep.A
        assert rec["loss"] == rep.loss == pytest.approx(LOSS.loss(rep.A, rep.R))
        assert rec["loss_base"] == pytest.approx(LOSS.loss(a_main, rep.R))
        assert rec["C_base"] == pytest.approx(c_base) and rec["T"] == pytest.approx(t)
        assert rec["loss_base"] != rec["loss"]               # no artifact here builds any taste: T is 0
        stored = json.loads((tmp_path / "artifacts" / out.pass_.id / "eval.json").read_text())
        assert stored["loss"] == rep.loss and stored["C"] == rep.C   # the store and the gate read the same loss
    assert TEL.rows(name_prefix="pass.loss_base") and TEL.rows(name_prefix="pass.T")


def _one_pass(tmp_path, monkeypatch, revision: str, taste: Taste, request_id: str = "Q-0002"):
    scripted_judge(monkeypatch, JudgeLog([], []))
    _, _, _, _, _, tasks = two_request_round(tmp_path)
    task = next(t for t in tasks if t.request.id == request_id)

    def answer(messages) -> str:
        if is_critic(messages):
            return json.dumps({"criticism": "Fine.", "requests": []})
        return revision if is_revision(messages) else html_for(messages)

    e = pass_env(tmp_path, ScriptedClient([answer] * 20), evaluate=CheckEval())
    e.critic = Critic(e.worker.client, BOOK, SPEC.critic.max_requests, SPEC.json.reasks)
    e.taste = taste
    out = pipeline.run_pass(task, e)
    return out, json.loads((tmp_path / "artifacts" / task.pass_id / pipeline.TASTE_FILE).read_text())


def test_a_missed_quirk_moves_the_pass_loss_by_about_a_quarter(tmp_path, monkeypatch):
    titled = REVISED.replace("<body>", '<body><h1 id="app-title">Tally</h1>')
    met, met_rec = _one_pass(tmp_path / "met", monkeypatch, titled, Taste(QUIRKS, CELLS, LOSS, W))
    missed, missed_rec = _one_pass(tmp_path / "missed", monkeypatch, REVISED, Taste(QUIRKS, CELLS, LOSS, W))
    assert [r["quirk"] for r in met_rec["quirks"]] == ["app-title"]            # Q-0002: the one quirk in scope
    assert (met_rec["T"], missed_rec["T"]) == (1.0, 0.0)
    assert met.report.C == missed.report.C                                     # C reads the request's own checks
    assert met_rec["loss_base"] == pytest.approx(missed_rec["loss_base"])
    moved = missed.report.loss - met.report.loss
    assert moved == pytest.approx(SPEC.eval.w_a * W) and abs(moved - 0.25) < 0.02


def test_t_reads_unread_when_no_quirk_is_in_scope_and_a_is_then_a_main(tmp_path, monkeypatch):
    scoped = tuple(q for q in QUIRKS if q.scope[0] != "any")                   # Q-0002's cell: none in scope
    out, rec = _one_pass(tmp_path, monkeypatch, REVISED, Taste(scoped, CELLS, LOSS, W))
    assert rec["quirks"] == [] and rec["T"] is None                            # unread, never 0
    assert rec["A"] == rec["A_main"] == pytest.approx(out.report.A)
    assert out.report.loss == rec["loss_base"] == pytest.approx(LOSS.loss(out.report.A, out.report.R))
    t_rows = [r for r in TEL.rows(name_prefix="pass.T") if r.scope.pass_id == out.pass_.id]
    assert t_rows and t_rows[-1].value is None


def test_a_taste_weight_outside_the_open_unit_interval_is_refused():
    for w in (0.0, 1.0, -0.1):
        with pytest.raises(ValueError, match="taste_weight"):
            Taste(QUIRKS, CELLS, LOSS, w)


def test_a_taste_without_a_critic_and_a_request_with_no_cell_are_refused(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    _, _, _, _, _, tasks = two_request_round(tmp_path)
    e = pass_env(tmp_path, ScriptedClient([voicing] * 50), evaluate=CheckEval())
    e.taste = Taste(QUIRKS, CELLS, LOSS, W)
    with pytest.raises(ValueError, match="critic"):
        pipeline.run_pass(tasks[0], e)
    with pytest.raises(KeyError):
        Taste(QUIRKS, {}, LOSS, W).in_scope("Q-0001")


def test_loss_base_refuses_a_c_the_report_did_not_compute():
    q = (BY_ID["app-title"],)
    rep = EvalReport("p", Regime.R1, (CheckResult("c1@1280x800", True), CheckResult("c16@1280x800", False)), (),
                     0.5, 0.5, (3, 3, 3), 1, 0.5, P=0.5, H=0.5, C=0.9)
    with pytest.raises(ValueError, match="re-read"):
        Taste(QUIRKS, CELLS, LOSS, W).reading(rep, q)
    only_taste = EvalReport("p", Regime.R1, (CheckResult("c16@1280x800", False),), (), 0.5, 0.5, (3, 3, 3), 1, 0.5,
                            C=0.0)
    with pytest.raises(ValueError, match="own"):
        Taste(QUIRKS, CELLS, LOSS, W).reading(only_taste, q)


# --- the taste readings ------------------------------------------------------------------------------------


def _pass(store: MemoryPassStore, records: dict, pid: str, round_: int, cat: str, slot: int | None,
          exposed: str | None, passed: dict[str, bool], loss: float = 0.5) -> None:
    arm = Arm.JOURNAL if slot is not None else Arm.NOTES
    store.add_pass(Pass(pid, round_, cat, arm, "w", Composition(cat, (exposed,) if exposed else (), exposed),
                        Regime.R1, pair_slot=slot))
    store.add_eval(EvalReport(pid, Regime.R1, (), (), 0.5, 0.5, (3,), 1, loss))
    records[pid] = {"quirks": [{"quirk": k, "passed": v} for k, v in passed.items()], "loss_base": loss - 0.1}


def planted():
    """Rounds 1-3. G-A (dense, canvas) and G-T (sparse, text). D-0001 is exposed on G-A in rounds 1-2 and its
    exposed slots pass the pause check; admitted at round 2. Nothing teaches dark-mode."""
    cells = {"G-A1": ("canvas game", "real-time"), "G-A2": ("canvas game", "real-time"),
             "G-A3": ("canvas game", "real-time"), "G-T1": ("text tool", "typing")}
    rounds = {"G-A1": 1, "G-A2": 2, "G-A3": 3, "G-T1": 2}
    store, rec = MemoryPassStore(), {}
    on = {"pause": True, "app-title": False}
    off = {"pause": False, "app-title": False}
    _pass(store, rec, "p1a", 1, "G-A1", 0, "D-0001", on)
    _pass(store, rec, "p1b", 1, "G-A1", 1, None, off)
    _pass(store, rec, "p2a", 2, "G-A2", 0, None, off)
    _pass(store, rec, "p2b", 2, "G-A2", 1, "D-0001", on)
    _pass(store, rec, "p3a", 3, "G-A3", 0, None, on)
    _pass(store, rec, "p3b", 3, "G-A3", 1, None, on)
    _pass(store, rec, "t2a", 2, "G-T1", 0, None, {"dark-mode": False, "app-title": False})
    _pass(store, rec, "t2b", 2, "G-T1", 1, None, {"dark-mode": False, "app-title": False})
    store.add_pass(Pass("x2", 2, "G-T1", Arm.NOTES, "w", Composition("G-T1", ()), Regime.R1))
    store.add_eval(EvalReport("x2", Regime.R1, (), (), 0.5, 0.5, (3,), 1, 0.5))          # no taste record
    events, ledger = MemoryEventLog(), MemoryLedger()
    events.append_flip(StatusFlip("D-0001", "candidate", "admitted", FlipCause.GATE_ADMIT, 2))
    for n, cat in ((1, "G-A1"), (2, "G-A2"), (2, "G-T1"), (3, "G-A3")):
        ledger.append(LedgerDelta("D-0001", n, cat, DeltaKind.EXPOSURE, 0.1, 0.0))
    return cells, rounds, store, rec, events, ledger


def test_taste_readings_name_the_teaching_decision_its_evidence_and_a_not_learned_quirk():
    cells, rounds, store, rec, events, ledger = planted()
    t = taste_readings(QUIRKS, cells, rounds, store, rec, events, ledger, frozenset())
    by = {r.quirk: r for r in t.quirks}
    pause = by["pause"]
    assert pause.stream_share == pytest.approx(0.75) and pause.in_scope_passes == 6 and pause.first_round == 1
    (x,) = pause.learned
    assert (x.decision_id, x.admitted_round, x.pairs, x.exposed_rate, x.unexposed_rate) == ("D-0001", 2, 2, 1.0, 0.0)
    assert (x.n_at_admission, x.n_in_scope, x.rounds_to_admission) == (3, 2, 2)
    assert pause.rate_before == pytest.approx(0.5) and pause.rate_after == 1.0
    dark = by["dark-mode"]
    assert not dark.is_learned and dark.rate_after is None and dark.rate_before == 0.0
    assert by["row-count"].in_scope_passes == 0 and by["row-count"].rate_before is None   # unread, never 0
    assert not by["app-title"].is_learned                    # D-0001's exposed slots do not pass it more often
    assert t.learned == 1 and t.of == 6 and t.unread == ("x2",)
    text = taste_lines(t)
    assert sum("not learned" in line for line in text[:-1]) == 5
    assert text[-1].startswith("learned 1 of 6 quirks; n at admission by density: app-title (1.00) not learned, "
                               "pause (0.75) n 3 in 2 rounds")


def test_a_decision_not_admitted_by_the_gate_teaches_no_quirk():
    cells, rounds, store, rec, _, ledger = planted()
    events = MemoryEventLog()
    events.append_flip(StatusFlip("D-0001", "candidate", "admitted", FlipCause.STEER_VETO, 2))
    t = taste_readings(QUIRKS, cells, rounds, store, rec, events, ledger, frozenset())
    assert t.learned == 0


def test_the_critic_side_of_the_loss_comparison_reads_loss_base_and_refuses_a_gap():
    from friday.adapters.memory import MemoryJournal

    _, _, store, rec, _, _ = planted()
    base = {**{pid: r["loss_base"] for pid, r in rec.items()}, "x2": 0.4}
    r = critic_readings(store, store, {}, MemoryJournal(), MemoryJournal(), frozenset(), base)
    assert r.mean_main == pytest.approx(0.5) and r.mean_critic == pytest.approx(0.4)
    assert r.mean_diff == pytest.approx(-0.1)
    with pytest.raises(ValueError, match="loss_base"):
        critic_readings(store, store, {}, MemoryJournal(), MemoryJournal(), frozenset(),
                        {k: v for k, v in base.items() if k != "p1a"})


def test_the_critic_prompt_with_habits_names_no_word_blinded_for_the_critic():
    words = (*BOOK.blinding["critic"], *S.families, *S.modes)
    assert blinding_hits(BOOK.text("critic/habits.md"), words) == []
    c = Critic(ScriptedClient([]), BOOK, 2, 1)
    system = c.messages("a timer", "<!doctype html>", None, [q.preference for q in QUIRKS])[0].content
    assert blinding_hits(system, words) == []
    assert all(q.preference in system for q in QUIRKS)
    assert "{habits}" not in system
    assert BOOK.text("critic/habits.md").split("\n", 1)[0] not in c.messages("a timer", "x", None)[0].content
