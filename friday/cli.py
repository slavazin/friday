"""`friday` command line: doctor, steer, run."""

from __future__ import annotations

import argparse
import os
import sys

from friday.config import load_spec


def _doctor(args: argparse.Namespace) -> int:
    """Reports key presence (never values) and how each role's pin resolves against /v1/models."""
    from friday.adapters.crusoe import CrusoeClient
    from friday.adapters.memory import MemorySpendLog
    from friday.app.pins import resolve

    spec = load_spec(args.spec)
    for env in (spec.models.endpoint.api_key_env, spec.search.api_key_env):
        print(f"{env}: {'present' if os.environ.get(env) else 'MISSING'}")
    client = CrusoeClient(spec.models, spec.transport, MemorySpendLog())
    served = client.list_models()
    for role, res in resolve(spec.models.roles, served).items():
        note = f"  (fallback; wanted {', '.join(res.wanted)})" if res.replaced else ""
        print(f"{role:12s} -> {res.model}{note}")
    print(f"run.yml hash {spec.spec_hash}")
    return 0


def _steer(args: argparse.Namespace) -> int:
    raise NotImplementedError("steer: writes a Steer event applied at the next round close")


def _run(args: argparse.Namespace) -> int:
    raise NotImplementedError("run: the coordinator loop (block 12:30)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="friday")
    parser.add_argument("--spec", default="run.yml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=_doctor)
    st = sub.add_parser("steer")
    st.add_argument("decision_id")
    st.add_argument("action", choices=["retire", "veto", "note"])
    st.add_argument("reason")
    st.set_defaults(fn=_steer)
    sub.add_parser("run").set_defaults(fn=_run)
    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
