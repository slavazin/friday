"""The runner, the coordinator loop and the readout builds (SPEC §§ 11, 12) on scripted clients and a fake browser."""

from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest

from friday.adapters.crusoe import TransportFailure
from friday.adapters.git import RecordingVcs
from friday.adapters.lens_md import MarkdownLensSet
from friday.adapters.memory import (
    MemoryEventLog,
    MemoryExposureStore,
    MemoryGoalSource,
    MemoryJournal,
    MemoryJudgeStore,
    MemoryLedger,
    MemoryObservationStore,
    MemoryPassStore,
    MemoryPoolStore,
    MemorySpendLog,
    MemoryTagVocabulary,
)
from friday.adapters.telemetry import MemoryTelemetry
from friday.algorithms.language import blinding_hits
from friday.app import runner as runner_mod
from friday.app.pipeline import PassEnv, PassSettings
from friday.app.render import FlipRenderer
from friday.app.round_close import RoundClose, build_round_close
from friday.app.runner import DraftRecordingJudge, MemoryCloseLog, RoundTools, Runner, Stores
from friday.app.wiring import build_mechanics, evaluator_settings
from friday.domain.types import Arm, DecisionStatus, DeltaKind, Regime, TagGloss
from friday.ports.telemetry import Scope
from friday.roles.ask import Rejected
from friday.roles.coordinator import TOOL_NAMES, Coordinator, RoundState, parse_tool_call
from friday.roles.nominator import NoDecision
from friday.roles.pooling import PoolingCall
from friday.roles.prompts import PromptBook
from friday.roles.tagger import Tagger
from friday.roles.worker import Worker
from tests.contract.test_round_close_order import by_shape_pooling, stand_in_nominator
from tests.fakes import ScriptedClient
from tests.unit.test_pipeline import (
    SPEC,
    FileBrowser,
    ProcessJudge,
    decision,
    page_ids_missing,
    replies,
    request,
    scripted_eval,
    spec_with,
)

ROOT = Path(__file__).resolve().parents[2]
BOOK = PromptBook(ROOT / "prompts")
RENDERER = FlipRenderer.load(ROOT / "templates/flips.yml")


class World:
    """One run over in-memory stores, a fake browser and scripted model replies."""

    def __init__(self, tmp_path: Path, spec=None, coordinator_replies=None):
        self.spec = spec or spec_with(detect={"ablate_p": 1.0})
        self.tmp = tmp_path
        if not (tmp_path / "lenses").exists():
            shutil.copytree(ROOT / "lenses", tmp_path / "lenses")
        self.stores = Stores(MemoryPassStore(), MemoryJudgeStore(), MemoryObservationStore(), MemoryPoolStore(),
                             MemoryExposureStore(), MemoryLedger(), MemoryEventLog(), MemorySpendLog())
        self.journal = MemoryJournal(_decisions={"D-0001": decision("D-0001", DecisionStatus.CANDIDATE, "canvas")})
        self.lens_set = MarkdownLensSet(tmp_path / "lenses", RecordingVcs())
        reqs = [request("Q-0001", "canvas", round_=1), request("Q-0002", "timer", round_=1),
                request("Q-0003", "canvas", round_=2), request("H-0001", "canvas", held_out=True)]
        self.goals = MemoryGoalSource(reqs)
        self.vocab = MemoryTagVocabulary()
        for t in ("canvas", "timer"):
            self.vocab.add(TagGloss(t, f"apps with {t}", 1))
        self.vocab.set_tags({r.id: r.tags for r in reqs})
        self.telemetry = MemoryTelemetry(Scope("main"))
        self.close_log = MemoryCloseLog()
        self.coordinator_client = ScriptedClient(list(coordinator_replies or []))

    def env(self) -> PassEnv:
        client = ScriptedClient(replies())
        return PassEnv(
            worker=Worker(client, BOOK), book=BOOK, browser=FileBrowser(), evaluate=scripted_eval,
            judge=DraftRecordingJudge(ProcessJudge(client, BOOK, 0.5, 3, 1, self.telemetry), self.tmp / "artifacts"),
            passes=self.stores.passes, judge_store=self.stores.judge, artifacts=self.tmp / "artifacts",
            settings=PassSettings.from_spec(self.spec), transport_errors=(TransportFailure,),
            missing_ids=page_ids_missing, telemetry=self.telemetry,
        )

    def build_close(self) -> RoundClose:
        pooling = PoolingCall(ScriptedClient([]), BOOK, self.spec.pool.max_direction_labels, self.spec.json.reasks)
        pooling.assign = by_shape_pooling
        nominator = stand_in_nominator(BOOK)
        nominator.draft = lambda pool, label, obs: NoDecision("scripted: no draft")
        s = self.stores
        return build_round_close(
            self.spec, journal=self.journal, ledger=s.ledger, events=s.events, judge_store=s.judge, pools=s.pools,
            passes=s.passes, observations=s.observations, lens_set=self.lens_set, spend=s.spend, pooling=pooling,
            nominator=nominator, minter=None, renderer=RENDERER, bar=build_mechanics(self.spec).bar,
            telemetry=self.telemetry)

    def runner(self, coordinator=True) -> Runner:
        @contextmanager
        def factory():
            yield self.env()

        coord = Coordinator(self.coordinator_client, BOOK, self.spec.json.reasks) if coordinator else None
        return Runner(
            spec=self.spec, stores=self.stores, journal=self.journal, lens_set=self.lens_set, goals=self.goals,
            vocabulary=self.vocab, tagger=Tagger(ScriptedClient([]), BOOK, (2, 4), 1, ()), coordinator=coord,
            build_close=self.build_close, env_factory=factory, close_log=self.close_log, telemetry=self.telemetry,
            root=self.tmp, transport_errors=(TransportFailure,), manifest={"spec_hash": self.spec.spec_hash},
            in_process=True, say=lambda line: None,
        )


def plan_reply(_messages=None) -> str:
    return json.dumps({"tool": "plan_round", "args": {}})


def test_one_round_runs_end_to_end_and_writes_passes_ledger_close_and_telemetry(tmp_path):
    w = World(tmp_path, coordinator_replies=[json.dumps({"tool": "read_journal", "args": {}}), plan_reply()])
    assert w.runner().run(stop_after=1) is None
    passes = w.stores.passes.passes(1)
    assert len(passes) == 6                                     # 2 requests x (2 journal + 1 notes)
    assert all(w.stores.passes.eval_of(p.id) is not None for p in passes)
    rows = w.stores.ledger.deltas("D-0001", DeltaKind.EXPOSURE)
    assert [(r.round, r.category) for r in rows] == [(1, "Q-0001")]
    assert [c.summary.round for c in w.close_log.closed()] == [1]
    assert {o.pass_id for o in w.stores.observations.observations()} == {p.id for p in passes if p.arm is Arm.JOURNAL}
    assert (tmp_path / "plans" / "round-01.md").exists()
    assert (tmp_path / "notes.md").read_text().count("- shape of") == 4
    names = {r.name for r in w.telemetry.rows()}
    assert {"run", "run.manifest", "run.spend", "round", "round.passes", "round.pass_count", "round.concurrency",
            "round.spend"} <= names
    outcomes = {r.attrs["outcome"]: r.value for r in w.telemetry.rows(name_prefix="round.pass_count")}
    assert outcomes == {"scored": 6, "transport_failed": 0, "crashed": 0, "rerun": 0}


def test_a_restart_before_the_close_resumes_without_repeating_a_pass_or_a_delta(tmp_path, monkeypatch):
    w = World(tmp_path, coordinator_replies=[plan_reply()])
    real_close = RoundClose.close

    def crash(self, *a, **k):
        raise RuntimeError("the process died before the close")

    monkeypatch.setattr(RoundClose, "close", crash)
    with pytest.raises(RuntimeError, match="died"):
        w.runner().run(stop_after=1)
    ran = sorted(p.id for p in w.stores.passes.passes(1))
    rows = list(w.stores.ledger.deltas("D-0001"))
    obs = sorted(o.id for o in w.stores.observations.observations())
    assert rows and obs                                         # persisted before the close ran
    monkeypatch.setattr(RoundClose, "close", real_close)

    def no_pass(*a, **k):
        raise AssertionError("a resumed round re-ran its passes")

    monkeypatch.setattr(runner_mod.pipeline, "run_round", no_pass)
    w.runner(coordinator=False).run(stop_after=1)
    assert sorted(p.id for p in w.stores.passes.passes(1)) == ran
    assert list(w.stores.ledger.deltas("D-0001")) == rows          # no delta appended twice
    assert sorted(o.id for o in w.stores.observations.observations()) == obs
    assert [c.summary.round for c in w.close_log.closed()] == [1]


def test_a_restart_recovers_observations_journaled_by_passes_that_never_reported_back(tmp_path, monkeypatch):
    w = World(tmp_path, coordinator_replies=[plan_reply()])
    real_run_round = runner_mod.pipeline.run_round

    def run_then_die(*a, **k):
        real_run_round(*a, **k)                                  # passes finished and wrote their rows
        raise RuntimeError("parent died before reading the outcomes")

    monkeypatch.setattr(runner_mod.pipeline, "run_round", run_then_die)
    with pytest.raises(RuntimeError, match="parent died"):
        w.runner().run(stop_after=1)
    assert w.stores.observations.observations() == []
    monkeypatch.setattr(runner_mod.pipeline, "run_round", real_run_round)
    w.runner(coordinator=False).run(stop_after=1)
    journal_passes = {p.id for p in w.stores.passes.passes(1) if p.arm is Arm.JOURNAL}
    assert {o.pass_id for o in w.stores.observations.observations()} == journal_passes
    assert len(w.stores.ledger.deltas("D-0001")) == 1


def test_the_runner_resumes_at_the_first_round_without_a_close(tmp_path):
    w = World(tmp_path, coordinator_replies=[plan_reply()])
    w.runner().run(stop_after=1)
    w.coordinator_client.script.append(plan_reply())
    w.runner().run(stop_after=2)
    assert [c.summary.round for c in w.close_log.closed()] == [1, 2]
    assert {p.round for p in w.stores.passes.passes()} == {1, 2}


def test_the_coordinator_cannot_reach_the_plan_a_status_or_a_figure(tmp_path):
    for bad in ({"tool": "set_exposure", "args": {}}, {"tool": "plan_round", "args": {"kind": "exposure"}},
                {"tool": "read_ledger", "args": {"decision_id": "D-0001", "status": "admitted"}},
                {"tool": "spawn_pass", "args": {"category": "Q-0001", "arm": "empty", "worker": 0}},
                {"tool": "halt", "args": {"reason": {"rounds": 1}}}):
        with pytest.raises(Rejected):
            parse_tool_call(bad)
    w = World(tmp_path, coordinator_replies=[
        json.dumps({"tool": "spawn_pass", "args": {"category": "Q-0001", "arm": "journal", "worker": 3}}),
        json.dumps({"tool": "halt", "args": {"reason": "enough"}}), plan_reply()])
    w.runner().run(stop_after=1)
    assert len(w.stores.passes.passes(1)) == 6                   # the pair plan alone set the passes
    assert w.close_log.closed()[-1].halt is None                 # a coordinator halt is a request, not a halt
    plan = (tmp_path / "plans" / "round-01.md").read_text()
    assert "spawn_pass Q-0001 journal 3" in plan and "halt requested: enough" in plan
    assert any(r.attrs.get("reason", "").startswith("coordinator asked to halt")
               for r in w.telemetry.rows(name_prefix="round.ping"))


def test_a_coordinator_that_never_plans_still_leaves_the_round_plan(tmp_path):
    w = World(tmp_path, coordinator_replies=["not json"] * 2)
    w.runner().run(stop_after=1)
    assert (tmp_path / "plans" / "round-01.md").exists()
    assert len(w.stores.passes.passes(1)) == 6


def test_coordinator_stops_after_max_turns_without_plan_round():
    client = ScriptedClient([json.dumps({"tool": "read_journal", "args": {}})] * 10)

    class Tools:
        def read_journal(self):
            return {}

    turns = Coordinator(client, BOOK, 1).run_round(RoundState(1, 14, ("Q-0001",), "", (), ()), Tools())
    assert len(turns) == 4 and len(client.calls) == 4


def test_read_ledger_on_an_unknown_decision_in_a_markdown_journal_is_fed_back_not_raised(tmp_path):
    from friday.adapters.journal_md import MarkdownJournal
    journal = MarkdownJournal(tmp_path / "journal", RecordingVcs())
    tools = RoundTools(1, runner_mod.pipeline.RoundPlan(1, (), {}, {}, (), {}), (), journal, MemoryLedger(),
                       tmp_path / "plans", (), lambda reason: None)
    client = ScriptedClient([json.dumps({"tool": "read_ledger", "args": {"decision_id": "D-0001"}}),
                             plan_reply()])
    turns = Coordinator(client, BOOK, 1).run_round(RoundState(1, 14, ("Q-0001",), "", (), ()), tools)
    assert turns[0].result == "error: D-0001 is not in the Journal"
    assert turns[-1].call.tool == "plan_round" and (tmp_path / "plans" / "round-01.md").exists()


def test_coordinator_templates_name_no_blinded_word():
    words = BOOK.blinding["coordinator"]
    assert {"check", "loss", "score", "regime", "drift"} <= set(words)
    for name in ("system.md", "user.md"):
        assert blinding_hits((ROOT / "prompts/coordinator" / name).read_text(), words) == []
    system = (ROOT / "prompts/coordinator/system.md").read_text()
    assert all(f"- {n} " in system for n in TOOL_NAMES)


def test_round_tools_read_only_views(tmp_path):
    w = World(tmp_path)
    from friday.app.pipeline import plan_round
    plan = plan_round(1, w.goals.round(1), w.journal, w.stores.ledger, 1, w.spec, MemoryExposureStore())
    t = RoundTools(1, plan, w.goals.round(1), w.journal, w.stores.ledger, tmp_path / "plans", (), lambda r: None)
    assert t.read_journal() == {"D-0001": "candidate"}
    assert t.read_ledger("D-0001")["exposure_pairs"] == 0
    with pytest.raises(LookupError):
        t.spawn_pass("Q-9999", Arm.JOURNAL, 0)


def test_readout_builds_held_out_requests_only_in_three_arms_and_never_reach_the_ledger(tmp_path):
    w = World(tmp_path, coordinator_replies=[plan_reply()])
    r = w.runner()
    r.run(stop_after=1)
    before = {d: list(w.stores.ledger.deltas(d)) for d in ("D-0001",)}
    fires_before = list(w.stores.judge.fires_of("D-0001"))
    tasks = r.readout_tasks()
    assert {t.request.id for t in tasks} == {"H-0001"}
    assert len(tasks) == w.spec.readout.reps * 3
    assert {t.arm for t in tasks} == {Arm.EMPTY, Arm.NOTES, Arm.JOURNAL}
    assert {t.regime for t in tasks} == {Regime(w.spec.readout.regime)}
    r.readout()
    v = [p for p in w.stores.passes.passes() if p.category == "H-0001"]
    assert len(v) == len(tasks) and all(p.pair_slot is None for p in v)
    assert all(w.stores.passes.eval_of(p.id) is not None for p in v)
    spans = {r.scope.pass_id: r for r in w.telemetry.spans(name_prefix="pass") if r.name == "pass"}
    for p in v:                                       # the live view and replay read readout passes from these
        assert spans[p.id].scope.round == p.round and spans[p.id].attrs["readout"] is True
    assert {r.scope.pass_id for r in w.telemetry.rows(name_prefix="pass.loss")} >= {p.id for p in v}
    assert {d: list(w.stores.ledger.deltas(d)) for d in before} == before
    assert list(w.stores.judge.fires_of("D-0001")) == fires_before
    notes = [p for p in v if p.arm is Arm.NOTES]
    assert notes and all(p.composition.notes for p in notes)
    tokens = r.composed_tokens()
    assert all(tokens[p.id] == 0 for p in v if p.arm is Arm.EMPTY)
    assert all(tokens[p.id] > 0 for p in notes)
    lines = r.readout_lines()
    assert lines[0].startswith("Readout over 1 held-out categories")
    path = r.write_export()
    assert json.loads(path.read_text())["schema_version"] == 1


def test_wiring_reads_every_evaluator_and_loss_figure_from_the_spec():
    m = build_mechanics(SPEC)
    assert m.loss.a_weights == dict(SPEC.eval.a_weights)
    s = evaluator_settings(SPEC)
    assert s["viewports"] == {k: tuple(tuple(v) for v in vs) for k, vs in SPEC.eval.viewports.items()}
    assert (s["test_query"], s["probe_max_controls"], s["settle_ms"], s["sample_text"]) == (
        SPEC.eval.test_query, SPEC.probe.max_controls, SPEC.probe.settle_ms, SPEC.probe.sample_text)
    assert (s["how_to_max_controls"], s["repair_penalty"]) == (SPEC.how_to.max_controls, SPEC.how_to.repair_penalty)
