"""The notes file (SPEC § 12) and the request text with the drift constraint (SPEC § 10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from friday.app.notes import append_free, approx_tokens, note_line, notes_text
from friday.app.request_text import regime_of, request_text
from friday.config import load_spec
from friday.domain.types import Arm, Check, Observation, Regime, Request

ROOT = Path(__file__).resolve().parents[2]
SPEC = load_spec(ROOT / "run.yml")


def obs(id_: str, pass_id: str, shape: str, lens_id: str = "free", direction: str = "Bind the click on #start.",
        ) -> Observation:
    return Observation(id_, pass_id, 1, lens_id, "Q-0001", shape, direction, "<p>")


def req(text: str) -> Request:
    return Request("Q-0001", text, frozenset(), {"#start": "starts"}, {Regime.R1: (Check("c", (), {}),)}, round=1)


ARMS = {"j0": Arm.JOURNAL, "j1": Arm.JOURNAL, "n0": Arm.NOTES}


def test_notes_take_free_observations_deduplicated_by_exact_text(tmp_path):
    path = tmp_path / "notes.md"
    first = append_free(path, [obs("O-0001", "j0", "the start button did nothing"),
                               obs("O-0002", "j1", "the start button did nothing"),
                               obs("O-0003", "j1", "the canvas stayed blank", lens_id="L-01")], ARMS)
    assert first == [note_line(obs("O-0001", "j0", "the start button did nothing"))]
    again = append_free(path, [obs("O-0004", "j0", "the start button did nothing"),
                               obs("O-0005", "j0", "the score never moved")], ARMS)
    assert again == [note_line(obs("O-0005", "j0", "the score never moved"))]
    assert path.read_text().splitlines() == first + again


def test_a_free_observation_from_a_non_journal_pass_raises(tmp_path):
    with pytest.raises(ValueError, match="journal only"):
        append_free(tmp_path / "n.md", [obs("O-0001", "n0", "x")], ARMS)
    with pytest.raises(ValueError, match="journal only"):
        append_free(tmp_path / "n.md", [obs("O-0001", "unknown", "x")], ARMS)
    assert not (tmp_path / "n.md").exists()


def test_notes_text_keeps_the_most_recent_whole_lines_within_the_budget(tmp_path):
    path = tmp_path / "notes.md"
    lines = [f"- observation number {i:03d} with some words" for i in range(50)]
    path.write_text("\n".join(lines) + "\n")
    per_line = approx_tokens(lines[0] + "\n")
    text = notes_text(path, per_line * 3 + 1)
    assert text == "\n".join(lines[-3:])
    assert notes_text(path, per_line * 100) == "\n".join(lines)
    assert notes_text(tmp_path / "absent.md", SPEC.notes.max_tokens) is None
    assert notes_text(path, 1) is None


def test_r2_appends_the_constraint_once_and_r1_never():
    c = SPEC.drift.constraint
    r = req("Build a timer with #start.")
    assert request_text(r, Regime.R2, c).count(c) == 1
    assert c not in request_text(r, Regime.R1, c)
    assert request_text(r, Regime.R1, c) == "Build a timer with #start."


def test_stored_request_text_carrying_the_constraint_raises():
    c = SPEC.drift.constraint
    with pytest.raises(ValueError, match="carries the drift constraint"):
        request_text(req("Build a timer. " + c), Regime.R1, c)


def test_regime_switches_at_the_drift_round():
    d = SPEC.drift.round
    assert regime_of(d - 1, d) is Regime.R1 and regime_of(d, d) is Regime.R2
    assert regime_of(SPEC.run.rounds, d) is Regime.R2
    with pytest.raises(ValueError):
        regime_of(0, d)
