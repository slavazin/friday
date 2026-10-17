"""The demo server: endpoints read the fixture export and telemetry log; model-written apps are served
only by known pass id, from inside their folder, under the strict CSP; no prompt, reply or key reaches a response."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from friday.adapters.telemetry import SqliteTelemetry, connect_telemetry
from friday.config import load_spec
from friday.ports.telemetry import Scope
from tests.web.conftest import fixture_paths
from web.app import APP_CSP, create_app
from web.fixtures.make import KEY_SENTINEL, PROMPT_SENTINEL, REPLY_SENTINEL

P = fixture_paths()
SPEC = load_spec(P["spec"])
DOC = json.loads(P["export"].read_text())


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(SPEC, P["db"], P["export"], P["artifacts"]))


def test_export_endpoint_serves_the_export_file_unchanged(client):
    assert client.get("/api/export").json() == DOC


def test_config_reads_the_run_spec(client):
    cfg = client.get("/api/config").json()
    assert cfg == {"live_poll_ms": SPEC.telemetry.live_poll_ms, "alpha": SPEC.gate.alpha,
                   "admit_wealth": 1 / SPEC.gate.alpha, "drift_round": SPEC.drift.round, "rounds": SPEC.run.rounds,
                   "spend_cap_usd": SPEC.run.spend_cap_usd}


def test_telemetry_pages_by_after_seq_without_gaps_or_repeats(client):
    seqs, after = [], 0
    while True:
        page = client.get("/api/telemetry", params={"after_seq": after, "limit": 700}).json()
        seqs += [r["seq"] for r in page["rows"]]
        after = page["last_seq"]
        if not page["more"]:
            break
    conn = connect_telemetry(P["db"], busy_timeout_ms=SPEC.telemetry.busy_timeout_ms)
    assert seqs == [r.seq for r in SqliteTelemetry(conn, Scope("main")).rows()]
    assert len(seqs) > 700


def test_telemetry_filters_by_round_pass_and_name(client):
    pid = next(p["id"] for p in DOC["passes"] if p["round"] == 1 and p["loss"] is not None)
    rows = client.get("/api/telemetry", params={"pass_id": pid, "name_prefix": "pass."}).json()["rows"]
    assert rows and all(r["pass_id"] == pid and r["name"].startswith("pass.") for r in rows)
    loss = [r["value"] for r in rows if r["name"] == "pass.loss"]
    assert loss == [next(p["loss"] for p in DOC["passes"] if p["id"] == pid)]


def test_spans_and_times_cover_every_close_phase(client):
    spans = client.get("/api/spans", params={"round": 1, "name_prefix": "close."}).json()["spans"]
    assert {s["name"] for s in spans} == {f"close.{p}" for p in ("steers", "lens_curation", "observations", "pooling",
                                                                 "nomination", "gate", "detector", "pings")}
    assert all(s["ended_at"] is not None for s in spans)
    times = client.get("/api/times", params={"round": 1}).json()["times"]
    assert times["pass"]["count"] > 0 and times["pass"]["median_s"] > 0


@pytest.mark.parametrize("path", [
    "/apps/../../etc/passwd", "/apps/..%2F..%2Fetc%2Fpasswd", "/apps/%2e%2e",
    "/apps/r99-C-999-journal-0", "/apps/r01-C-103-journal-1",          # unknown; known but failed (no artifact)
    "/apps/r01-C-101-journal-0%2Fprompt.txt", "/apps/r01-C-101-journal-0/prompt.txt",
    "/static/../fixtures/export.json", "/static/%2e%2e/fixtures/export.json", "/static/../app.py",
])
def test_apps_and_static_refuse_traversal_and_unknown_pass_ids(client, path):
    r = client.get(path)
    assert r.status_code == 404, (path, r.status_code)
    assert PROMPT_SENTINEL not in r.text and "spec_hash" not in r.text


def test_failed_pass_is_refused_even_with_a_folder_on_disk(tmp_path):
    # A pass whose export row has no artifact is refused, whatever the folder holds.
    pid = "r01-C-103-journal-1"
    (tmp_path / pid).mkdir()
    (tmp_path / pid / "index.html").write_text("<p>planted</p>")
    r = TestClient(create_app(SPEC, P["db"], P["export"], tmp_path)).get(f"/apps/{pid}")
    assert r.status_code == 404


def test_symlinked_artifact_outside_its_folder_is_refused(tmp_path):
    pid = next(p["id"] for p in DOC["passes"] if p["artifact_path"])
    (tmp_path / pid).mkdir()
    secret = tmp_path.parent / "outside.html"
    secret.write_text(PROMPT_SENTINEL)
    (tmp_path / pid / "index.html").symlink_to(secret)
    r = TestClient(create_app(SPEC, P["db"], P["export"], tmp_path)).get(f"/apps/{pid}")
    assert r.status_code == 404 and PROMPT_SENTINEL not in r.text


def test_app_response_carries_the_strict_csp(client):
    pid = next(p["id"] for p in DOC["passes"] if p["artifact_path"])
    r = client.get(f"/apps/{pid}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert csp == APP_CSP
    for directive in ("default-src 'none'", "script-src 'unsafe-inline'", "style-src 'unsafe-inline'", "img-src data:",
                      "sandbox allow-scripts"):
        assert directive in csp
    assert "allow-same-origin" not in csp and "connect-src" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"


def test_page_csp_allows_only_its_own_files(client):
    csp = client.get("/").headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "http" not in csp


def _every_response(client):
    yield client.get("/")
    for path in ("/api/export", "/api/config", "/api/spans", "/api/times", "/static/index.html", "/static/app.css"):
        yield client.get(path)
    after = 0
    while True:
        r = client.get("/api/telemetry", params={"after_seq": after})
        yield r
        page = r.json()
        after = page["last_seq"]
        if not page["more"]:
            break
    for p in DOC["passes"]:
        if p["artifact_path"]:
            yield client.get(f"/apps/{p['id']}")


def test_no_prompt_reply_or_key_from_the_fixtures_reaches_any_response(client, monkeypatch):
    monkeypatch.setenv(SPEC.models.endpoint.api_key_env, KEY_SENTINEL)
    n = 0
    for r in _every_response(client):
        n += 1
        for planted in (PROMPT_SENTINEL, REPLY_SENTINEL, KEY_SENTINEL, SPEC.models.endpoint.api_key_env):
            assert planted not in r.text, (r.url, planted)
    assert n > 50


def test_the_fixture_stores_do_hold_the_planted_secrets():
    # The leak test above means something only if the secrets were in the stores the export read.
    from friday.config import parse_spec
    from web.fixtures.make import build_world, fixture_spec_text

    world = build_world(parse_spec(fixture_spec_text()))
    assert any(PROMPT_SENTINEL in r.text for r in world.stores.goals.held_out())
    assert any(row[2] is not None and row[2].text == REPLY_SENTINEL for row in world.stores.spend.rows)
    assert (P["artifacts"] / next(p["id"] for p in DOC["passes"] if p["artifact_path"]) / "prompt.txt").exists()


def test_missing_export_and_log_read_as_not_written_not_as_empty_figures(tmp_path):
    c = TestClient(create_app(SPEC, tmp_path / "none.db", tmp_path / "none.json", tmp_path))
    assert c.get("/api/export").status_code == 404
    t = c.get("/api/telemetry").json()
    assert t["available"] is False and t["rows"] == []
    assert c.get("/apps/r01-C-101-journal-0").status_code == 404


def _resolve(argv):
    import argparse

    from web.app import resolve_paths
    ap = argparse.ArgumentParser()
    for flag in ("--spec", "--root", "--db", "--export", "--artifacts"):
        ap.add_argument(flag, type=Path, default=None)
    return resolve_paths(ap.parse_args(argv))


def test_a_real_run_root_reads_the_repository_run_spec_not_the_fixtures(tmp_path):
    from web.app import FIXTURES, REPO_SPEC
    spec, db, export, artifacts = _resolve(["--root", str(tmp_path)])
    paths = load_spec(REPO_SPEC).paths
    assert spec == REPO_SPEC and db == tmp_path / paths.db and export == tmp_path / "export.json"
    assert artifacts == tmp_path / paths.artifacts
    assert _resolve(["--db", str(tmp_path / "x.db"), "--export", "e", "--artifacts", "a"])[0] == REPO_SPEC
    assert _resolve([])[0] == FIXTURES / "run.yml"


def test_friday_web_forwards_the_spec_when_a_run_path_is_given(monkeypatch):
    import web.app
    from friday import cli
    seen = []
    monkeypatch.setattr(web.app, "main", lambda argv: seen.append(argv))
    cli.main(["--spec", "run.yml", "web", "--root", "runs/x"])
    cli.main(["web"])
    assert seen == [["--spec", "run.yml", "--root", "runs/x"], []]
