"""Regression tests, one per finding of the scaffold's cold review."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from friday.adapters.git import RecordingVcs
from friday.adapters.journal_md import FrozenTextViolation, MarkdownJournal
from friday.adapters.sqlite.stores import SqliteEventLog, connect
from friday.algorithms.exposure import PairKind, PairPlan
from friday.algorithms.language import check_payload
from friday.algorithms.loss import LossModel
from friday.app.ledger import pair_deltas
from friday.app.wiring import build_mechanics
from friday.config import ConfigError, parse_spec
from friday.domain.events import StatusFlip, Steer
from friday.domain.lifecycle import FlipCause
from friday.domain.types import Arm, Check, Composition, DecisionStatus, EvalReport, Pass, Regime
from friday.eval.dsl import InvalidCheck, run_check, validate
from friday.roles.judge import validate_lens_answers
from friday.stats.eprocess import FixedBet
from tests.unit.builders import dec, lens

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / "run.yml").read_text()


def test_unloadable_page_fails_the_check_instead_of_raising():
    @contextmanager
    def broken():
        raise TimeoutError("navigation timeout")
        yield

    r = run_check(broken, Check("c1", (), {"exists": "#a"}))
    assert not r.passed and r.failing_step.startswith("load: TimeoutError")


@pytest.mark.parametrize("bad", [
    Check("c", (), {"text_matches": "#a"}),
    Check("c", ({"key": "Tab", "times": "x"},), {"exists": "#a"}),
    Check("c", ({"wait": True},), {"exists": "#a"}),
    Check("c", ({"click": 3},), {"exists": "#a"}),
    Check("c", (), {"exists": ["#a"]}),
])
def test_dsl_rejects_mistyped_arguments(bad):
    with pytest.raises(InvalidCheck):
        validate(bad, 3000)


def test_tampered_payload_is_refused_at_flip(tmp_path):
    j = MarkdownJournal(tmp_path, RecordingVcs())
    j.draft(dec("D-0002", DecisionStatus.CANDIDATE, payload="Bind keys on `window`."))
    path = tmp_path / "D-0002.md"
    path.write_text(path.read_text().replace("Bind keys on `window`.", "Bind keys on the canvas."))
    with pytest.raises(FrozenTextViolation):
        j.flip(StatusFlip("D-0002", "candidate", "admitted", FlipCause.GATE_ADMIT, 5), "m")


def test_betting_strategy_swaps_by_config_alone():
    text = TEXT.replace("name: agrapa", "name: fixed").replace(
        "      lambda_first: 0.25\n      lambda_max: 0.9\n      eps: 1.0e-9", "      lam: 0.3")
    m = build_mechanics(parse_spec(text))
    assert isinstance(m.gate.betting, FixedBet) and m.gate.betting.lam == 0.3


def test_payload_may_name_a_contract_id():
    got = check_payload("Increment `#score` inside the collision handler.", 60, ["score"], [])
    assert not got.refusable
    assert check_payload("Raise the score shown on screen.", 60, ["score"], []).refusable


def test_discarded_low_p_quote_needs_no_verbatim_match():
    got = validate_lens_answers({"lenses": [{"id": "L-01", "p": 0.1, "quote": "paraphrase"}]},
                                "x", [lens("L-01")], "<html>", 0.5, "m")
    assert got[0].quote == ""


def test_successful_rerun_replaces_a_failed_original_in_either_order():
    def p(i, slot, failed=False):
        return Pass(i, 1, "cat", Arm.JOURNAL, "w", Composition("cat", ()), Regime.R1, pair_slot=slot,
                    transport_failed=failed)

    evals = {i: EvalReport(i, Regime.R1, (), (), 0, 0, (), 0, loss) for i, loss in [("a", 0.6), ("b2", 0.4)]}
    plan = [PairPlan("cat", PairKind.EXPOSURE, "D-0001", treated_slot=1)]
    for order in ([p("a", 0), p("b2", 1), p("b", 1, True)], [p("a", 0), p("b", 1, True), p("b2", 1)]):
        assert len(pair_deltas(1, plan, order, evals)) == 1


def test_loss_refuses_missing_readings():
    lm = LossModel(0.75, 0.25, (1, 5), 3)
    with pytest.raises(ValueError):
        lm.acceptance([])
    with pytest.raises(ValueError):
        lm.rubric([4, 4])


@pytest.mark.parametrize(("old", "new"), [
    ("level: 0.80", "level: 80"),
    ("confirm_pairs: 3", "confirm_pairs: 1"),
    ("rank_by: e_value", "rank_by: banana"),
])
def test_config_refuses_values_that_break_the_arithmetic(old, new):
    with pytest.raises(ConfigError):
        parse_spec(TEXT.replace(old, new))


def test_lambda_first_above_lambda_max_is_refused():
    with pytest.raises(ValueError):
        build_mechanics(parse_spec(TEXT.replace("lambda_first: 0.25", "lambda_first: 0.95")))


def test_event_log_is_append_only(tmp_path):
    conn = connect(tmp_path / "f.db")
    ev = SqliteEventLog(conn)
    ev.append_flip(StatusFlip("D-0001", "candidate", "admitted", FlipCause.GATE_ADMIT, 3))
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE events SET body = '{}'")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM events")
    s = Steer("D-0001", "note", "n", 3)
    ev.append_steer(s)
    ev.mark_steer_applied(s, 4)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE events SET applied_round = 9 WHERE type = 'steer'")
