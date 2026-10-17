"""The showcase fixtures: deterministic, built through `friday.app.export.build`, and covering the telemetry
catalogue's pass, eval, judge, model-call, round and round-close rows."""

import json
import re
import sqlite3
from pathlib import Path

from tests.web.conftest import FIX, ROOT
from web.fixtures.make import make


def _rows(db: Path):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT kind, name, run, round, pass_id, at, span_id, parent_id, ended_at, ok, error, "
                            "value, unit, attrs, pid FROM telemetry ORDER BY seq").fetchall()
    finally:
        conn.close()


def test_make_is_deterministic_and_the_committed_fixtures_are_current(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    make(a)
    make(b)
    assert (a / "export.json").read_text() == (b / "export.json").read_text()
    assert _rows(a / "telemetry.db") == _rows(b / "telemetry.db")
    assert (a / "export.json").read_text() == (FIX / "export.json").read_text(), "rerun web/fixtures/make.py"
    assert _rows(a / "telemetry.db") == _rows(FIX / "telemetry.db"), "rerun web/fixtures/make.py"
    assert (a / "run.yml").read_text() == (FIX / "run.yml").read_text()


def test_fixture_run_has_three_rounds_a_drift_round_and_both_regimes():
    doc = json.loads((FIX / "export.json").read_text())
    assert doc["run"]["rounds"] == 3 and 1 < doc["run"]["drift_round"] <= 3
    regimes = {p["regime"] for p in doc["passes"] if not p["readout"]}
    assert regimes == {"R1", "R2"}
    statuses = {d["status"] for d in doc["decisions"]}
    assert {"admitted", "suspect", "candidate"} <= statuses
    assert any(p["transport_failed"] for p in doc["passes"])           # a failed pass to show as failed


def test_every_admission_in_the_fixture_clears_one_over_alpha_on_its_recomputed_wealth():
    doc = json.loads((FIX / "export.json").read_text())
    from friday.config import load_spec

    bar = 1 / load_spec(FIX / "run.yml").gate.alpha
    for d in doc["decisions"]:
        for f in d["status_history"]:
            if f["new"] == "admitted":
                path = d["ledger"]["exposure"]["wealth_path"]
                upto = sum(x["round"] <= f["round"] for x in d["ledger"]["exposure"]["deltas"])
                assert path[upto - 1] >= bar, d["id"]


def test_telemetry_holds_ten_passes_a_round_and_every_round_close_phase():
    conn = sqlite3.connect(FIX / "telemetry.db")
    per_round = dict(conn.execute("SELECT round, COUNT(DISTINCT pass_id) FROM telemetry WHERE name='pass' "
                                  "AND round <= 3 GROUP BY round"))
    assert all(n >= 10 for n in per_round.values()) and set(per_round) == {1, 2, 3}
    names = {r[0] for r in conn.execute("SELECT DISTINCT name FROM telemetry")}
    catalogue = (ROOT / "docs" / "telemetry.md").read_text()
    listed = set(re.findall(r"^\| `([a-z_.]+)` \|", catalogue, flags=re.M))
    listed |= {f"close.{p}" for p in ("steers", "lens_curation", "observations", "pooling", "nomination", "gate",
                                      "detector", "pings")}
    listed |= {f"pass.{x}" for x in "ARPHCQ"}                          # named in the pass.loss row's text
    listed -= {"close.<phase>"}
    assert names <= listed, names - listed                          # nothing outside the catalogue
    # Not planted: no fallback taken, goals minted before the run, no critic run.
    missing = listed - names - {"run.fallback", "goals.mint", "critic.pass",   # emitted by the real run only
                                "round.tags", "round.coordinator", "round.settled", "round.observations_unread"}
    assert missing == set(), missing
    # Spans close after they open, and every open row has its close row.
    bad = conn.execute("SELECT COUNT(*) FROM telemetry WHERE ended_at IS NOT NULL AND ended_at < at").fetchone()[0]
    unclosed = conn.execute("SELECT COUNT(*) FROM (SELECT span_id FROM telemetry WHERE kind='span' GROUP BY span_id "
                            "HAVING COUNT(*) != 2)").fetchone()[0]
    assert bad == 0 and unclosed == 0
