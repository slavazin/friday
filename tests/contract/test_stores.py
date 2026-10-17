import sqlite3

import pytest

from friday.adapters.git import RecordingVcs
from friday.adapters.journal_md import FrozenTextViolation, MarkdownJournal, parse_decision, render_decision
from friday.adapters.lens_md import MarkdownLensSet, parse_lens, render_lens
from friday.adapters.sqlite.stores import SqliteEventLog, SqliteLedger, SqliteSpendLog, connect
from friday.domain.events import StatusFlip, Steer
from friday.domain.lifecycle import FlipCause, IllegalFlip
from friday.domain.types import DecisionStatus, DeltaKind, LedgerDelta, LensStatus
from friday.ports.models import ChatResult
from tests.unit.builders import dec, lens


def test_decision_file_round_trips():
    d = dec("D-0007", DecisionStatus.CANDIDATE, fires=("canvas", "keyboard"), not_this=("form",),
            payload="Bind keyboard handlers on `window`.\nCall `focus()` on start.", supersedes=("D-0003",))
    assert parse_decision(render_decision(d)) == d


def test_journal_flip_commits_once_and_freezes_text(tmp_path):
    vcs = RecordingVcs()
    j = MarkdownJournal(tmp_path, vcs)
    j.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    with pytest.raises(FrozenTextViolation):
        j.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    flip = StatusFlip("D-0001", "candidate", "admitted", FlipCause.GATE_ADMIT, 3, {"ledger": "5 pairs"})
    new = j.flip(flip, "D-0001 candidate → admitted")
    assert new.status is DecisionStatus.ADMITTED and new.warrant["ledger"] == "5 pairs"
    assert j.get("D-0001").payload == "Do the thing."
    assert vcs.commits == [((str(tmp_path / "D-0001.md"),), "D-0001 candidate → admitted")]
    with pytest.raises(IllegalFlip):
        j.flip(StatusFlip("D-0001", "admitted", "expired", FlipCause.GATE_EXPIRE, 4), "x")


def test_lens_file_round_trips_and_flips(tmp_path):
    lz = lens("L-09", LensStatus.HIDDEN)
    assert parse_lens(render_lens(lz)) == lz
    s = MarkdownLensSet(tmp_path, RecordingVcs())
    s.add(lz)
    got = s.flip(StatusFlip("L-09", "hidden", "active", FlipCause.LENS_CLEAR_FLOOR, 4), "m")
    assert got.status is LensStatus.ACTIVE and got.has_cleared_floor


def test_baseline_lens_files_parse():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "lenses"
    ls = MarkdownLensSet(root, RecordingVcs()).lenses()
    assert [x.id for x in ls] == [f"L-0{i}" for i in range(1, 9)] and all(x.status is LensStatus.ACTIVE for x in ls)


def test_sqlite_ledger_is_append_only(tmp_path):
    conn = connect(tmp_path / "f.db")
    led = SqliteLedger(conn)
    led.append(LedgerDelta("D-0001", 1, "a", DeltaKind.EXPOSURE, 0.2, 0.0))
    led.append(LedgerDelta("D-0001", 2, "a", DeltaKind.ABLATION, -0.1, 1.0))
    assert [r.d for r in led.deltas("D-0001")] == [0.2, -0.1]
    assert [r.kind for r in led.deltas("D-0001", DeltaKind.ABLATION)] == [DeltaKind.ABLATION]
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE ledger_deltas SET d = 1")
    with pytest.raises(sqlite3.IntegrityError):
        led.append(LedgerDelta("D-0001", 2, "a", DeltaKind.EXPOSURE, 1.5, 0.0))


def test_sqlite_events_and_steers_round_trip(tmp_path):
    conn = connect(tmp_path / "f.db")
    ev = SqliteEventLog(conn)
    flip = StatusFlip("D-0001", "admitted", "retired", FlipCause.SUPERSEDE, 5, {"successor": "D-0004"}, ("D-0004",))
    ev.append_flip(flip)
    assert ev.flips("D-0001") == [flip]
    s = Steer("D-0002", "veto", "off-topic", 3)
    ev.append_steer(s)
    assert ev.pending_steers() == [s]
    ev.mark_steer_applied(s, 4)
    assert ev.pending_steers() == []


def test_sqlite_spend_records_failures_and_totals(tmp_path):
    sp = SqliteSpendLog(connect(tmp_path / "f.db"))
    sp.record("worker", "x1", ChatResult("t", "m", 100, 50, 0.002, raw={"sent": {"temperature": 0.7}}), ok=True)
    sp.record("judge", "x1", None, ok=False, error="HTTP 503")
    assert sp.total_usd() == pytest.approx(0.002)
