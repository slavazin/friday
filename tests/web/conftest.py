"""Shared fixtures for the demo page tests: the fixture run's paths and a live server over any export."""

import socket
import threading
import time
from pathlib import Path

import pytest

from friday.config import load_spec

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "web" / "fixtures"


def fixture_paths():
    return {"spec": FIX / "run.yml", "db": FIX / "telemetry.db", "export": FIX / "export.json",
            "artifacts": FIX / "artifacts"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Served:
    """A uvicorn server in a thread over `create_app(...)`; `url` is its base URL."""

    def __init__(self, spec_path, db, export, artifacts):
        import uvicorn

        from web.app import create_app

        self.port = _free_port()
        app = create_app(load_spec(spec_path), db, export, artifacts)
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.05)
        self.url = f"http://127.0.0.1:{self.port}"

    def close(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture(scope="session")
def served():
    p = fixture_paths()
    s = Served(p["spec"], p["db"], p["export"], p["artifacts"])
    yield s
    s.close()
