"""The critic run's tempo (SPEC § 17 Tempo): earlier nomination and more candidates under test on the critic run's
overlay, the gate's bar unchanged, and the `decision.turnaround` telemetry and readings."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from friday.adapters.memory import MemoryExposureStore, MemoryLedger
from friday.adapters.telemetry import MemoryTelemetry
from friday.algorithms.recurrence import Route, nominations
from friday.app import tempo
from friday.app.pipeline import plan_round
from friday.app.runner import load_run_spec
from friday.app.wiring import build_mechanics
from friday.domain.events import StatusFlip
from friday.domain.lifecycle import FlipCause
from friday.domain.types import DecisionStatus, Disposition, Hook, Observation, Pool
from friday.ports.telemetry import Scope
from friday.roles.judge import DraftObservation
from friday.roles.nominator import Draft
from tests.unit.test_pipeline import SPEC, ProcessJudge, decision, journal, request
from tests.unit.test_runner import World, plan_reply

ROOT = Path(__file__).resolve().parents[2]
CRIT = load_run_spec(ROOT / "run-critic.yml")


def _obs(i: int, category: str, round_: int = 1, pool: str = "P-0001") -> Observation:
    return Observation(f"O-{i:04d}", f"p{i}", round_, "L-01", category, "the shape", "do it", "<p>",
                       pool_id=pool, direction_label="do it")


# --- earlier nomination, more candidates, the same bar -------------------------------------------------------


def test_a_pool_seen_on_two_requests_in_round_one_is_nominated_at_the_round_one_close_on_the_critic_run_only():
    pool = Pool("P-0001", "L-01", "the shape", ("do it",))
    seen = [_obs(1, "Q-0001"), _obs(2, "Q-0001"), _obs(3, "Q-0002")]     # one pair's two passes, then another
    (n,) = nominations([pool], seen, build_mechanics(CRIT).bar, room=CRIT.gate.in_flight)
    assert n.pool.id == "P-0001" and n.route is Route.FAST_TRACK
    assert nominations([pool], seen, build_mechanics(SPEC).bar, room=SPEC.gate.in_flight) == []   # main: round 2+
    one_request = [_obs(1, "Q-0001"), _obs(2, "Q-0001"), _obs(3, "Q-0001")]
    assert nominations([pool], one_request, build_mechanics(CRIT).bar, room=4) == []   # 2 distinct requests needed


def test_gate_in_flight_four_puts_four_candidates_under_test_and_the_gate_reads_the_main_runs_bar():
    cands = [decision(f"D-000{i}", DecisionStatus.CANDIDATE, "canvas") for i in range(1, 6)]
    reqs = [request(f"Q-000{i}", "canvas") for i in range(1, 7)]
    for spec, k in ((CRIT, 4), (SPEC, SPEC.gate.in_flight)):
        plan = plan_round(1, reqs, journal(*cands), MemoryLedger(), spec.run.seed, spec, MemoryExposureStore())
        assert len(plan.in_flight) == k
        assert len({p.decision_id for p in plan.plans if p.decision_id}) == k
    crit, main = build_mechanics(CRIT).gate, build_mechanics(SPEC).gate
    assert (crit.alpha, crit.n_min, crit.n_max) == (main.alpha, main.n_min, main.n_max)


# --- the turnaround events -------------------------------------------------------------------------------------


def test_turnaround_events_carry_rounds_and_wall_seconds_from_the_pools_first_observation():
    obs = [_obs(1, "Q-0001", 1), _obs(2, "Q-0002", 2), _obs(3, "Q-0003", 1, pool="P-0002")]
    flips = [StatusFlip("D-0001", "candidate", "admitted", FlipCause.GATE_ADMIT, 3, {"n": 5}),
             StatusFlip("D-0002", "candidate", "refused", FlipCause.GATE_REFUSE, 3, {"n": 12}),
             StatusFlip("D-0003", "admitted", "suspect", FlipCause.DETECT_SUSPECT, 3, {"n": 4}),
             StatusFlip("D-0004", "admitted", "retired", FlipCause.SUPERSEDE, 3),
             StatusFlip("L-01", "hidden", "active", FlipCause.LENS_CLEAR_FLOOR, 3)]
    pool_of = {"D-0001": "P-0001", "D-0002": "P-0002", "D-0003": "P-0001", "D-0004": None, "D-0005": "P-0009"}
    at = {"p1": 100.0, "p3": 40.0}
    rows = tempo.turnaround_events(3, flips, ["D-0005"], pool_of, obs,
                                   lambda ids: min((at[i] for i in ids if i in at), default=None), 400.0)
    by = {(r["decision_id"], r["step"]): r for r in rows}
    assert set(by) == {("D-0005", "drafted"), ("D-0001", "admitted"), ("D-0002", "rejected"),
                       ("D-0004", "retired")}                               # a suspect flip is no step
    a = by[("D-0001", "admitted")]
    assert (a["first_observation_round"], a["rounds"], a["wall_s"], a["n"]) == (1, 2, 300.0, 5)
    assert by[("D-0002", "rejected")]["wall_s"] == 360.0
    for k in (("D-0004", "retired"), ("D-0005", "drafted")):                 # no pool / no observation: unread
        r = by[k]
        assert r["first_observation_round"] is None and r["rounds"] is None and r["wall_s"] is None


def test_turnaround_readings_read_the_events_alone_and_a_quirk_not_learned_reads_so():
    t = MemoryTelemetry(Scope("critic"))
    t.event(tempo.TURNAROUND, decision_id="D-0001", pool_id="P-0001", step="drafted", status="candidate",
            cause="nominate", round=1, first_observation_round=1, rounds=0, wall_s=60.0, n=None)
    t.event(tempo.TURNAROUND, decision_id="D-0001", pool_id="P-0001", step="admitted", status="admitted",
            cause="gate.admit", round=3, first_observation_round=1, rounds=2, wall_s=1800.0, n=5)
    (r,) = tempo.turnaround_readings(t.rows(name_prefix=tempo.TURNAROUND))
    assert (r.first_observation_round, r.nominated_round, r.admitted_round, r.n_at_admission) == (1, 1, 3, 5)
    assert r.minutes == {"drafted": 1.0, "admitted": 30.0} and r.rejected_round is None
    lines = tempo.turnaround_lines([r], {"pause": "D-0001", "dark-mode": None})
    assert any("D-0001" in x and "admitted round 3" in x and "n 5" in x for x in lines)
    assert any(x.strip() == "quirk dark-mode: not learned" for x in lines)
    (q, d) = tempo.quirk_turnaround([r], {"pause": "D-0001", "dark-mode": None})
    assert (q["admitted_round"], q["minutes"]["admitted"]) == (3, 30.0)
    assert (d["learned"], d["admitted_round"], d["minutes"]) == (False, None, {})


# --- a scripted 2-round critic run -----------------------------------------------------------------------------


def one_shape_report(self, pass_id, request_text, composed, source, answers, eval_report_text, lens_ids):
    """Every journal pass observes the same shape, so the pooling call pools them into one pool."""
    return [DraftObservation("free", "the one shape", "dir", "<p>")], {d: (Disposition.APPLIED, "") for d in composed}


def _critic_world(tmp_path, scope: str) -> World:
    spec = dataclasses.replace(CRIT, detect=dataclasses.replace(CRIT.detect, ablate_p=1.0))
    w = World(tmp_path, spec=spec, coordinator_replies=[plan_reply()] * 4)
    w.telemetry = MemoryTelemetry(Scope(scope))
    build = w.build_close

    def build_close():
        close = build()
        close.nominator.draft = lambda pool, label, obs: Draft(Hook(frozenset({"canvas"})),
                                                               "Show the state in #status.", ())
        return close

    w.build_close = build_close
    return w


def test_a_scripted_two_round_critic_run_writes_turnaround_rows_and_the_readout_prints_them(tmp_path, monkeypatch):
    monkeypatch.setattr(ProcessJudge, "report_call", one_shape_report)
    w = _critic_world(tmp_path, "critic")
    w.runner().run(stop_after=2)
    rows = w.telemetry.rows(kind="event", name_prefix=tempo.TURNAROUND)
    assert rows and all(r.scope.run == "critic" for r in rows)
    drafted = [r for r in rows if r.attrs["step"] == "drafted"]
    first = drafted[0]
    assert first.scope.round == 1 and first.attrs["round"] == 1                  # nominated at the round 1 close
    assert first.attrs["first_observation_round"] == 1 and first.attrs["rounds"] == 0
    assert first.attrs["wall_s"] is not None and first.attrs["wall_s"] >= 0.0
    readings = tempo.turnaround_readings(rows)
    assert readings[0].nominated_round == 1 and readings[0].first_observation_round == 1
    lines = tempo.turnaround_lines(readings, {})
    assert any(readings[0].decision_id in x and "nominated round 1" in x for x in lines)
    json.dumps([dataclasses.asdict(x) for x in readings])                        # the export serialises


def test_the_main_run_writes_no_turnaround_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(ProcessJudge, "report_call", one_shape_report)
    w = _critic_world(tmp_path, "main")
    w.runner().run(stop_after=1)
    assert w.telemetry.rows(name_prefix=tempo.TURNAROUND) == []


def test_a_turnaround_row_with_an_unknown_step_is_refused():
    t = MemoryTelemetry(Scope("critic"))
    t.event(tempo.TURNAROUND, decision_id="D-0001", step="sped up", round=1)
    with pytest.raises(ValueError, match="unknown step"):
        tempo.turnaround_readings(t.rows())
