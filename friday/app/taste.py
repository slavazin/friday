"""The manager's taste on the critic run (SPEC § 17 Taste): the planted quirks in `critic/taste.yml`, which of them
are in scope for a request, the acceptance checks they add to its eval, and the loss without them (`loss_base`).

The quirks are generator-side. Only the critic (as its own habits) and the critic run's eval read them; a quirk is
in scope for a request by the request's cell, which `goals/cells.json` holds and no agent of the loop reads.
"""

from __future__ import annotations

import dataclasses
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from friday.algorithms.loss import LossModel
from friday.domain.types import Check, EvalReport, Regime, Request
from friday.eval import dsl

# Pending a run.yml knob (seam request: critic.taste_path): the quirks file, relative to the repository.
TASTE_PATH = "critic/taste.yml"
CELLS_FILE = "cells.json"           # beside stream.path: the grid as minted, with each request's cell
TASTE_FILE = "taste.json"           # artifacts/<pass_id>/: the in-scope quirks, their check results, loss_base
ANY = "any"


@dataclass(frozen=True)
class Quirk:
    """One planted habit: `scope` is ("family", name), ("mode", name) or ("any", None)."""

    id: str
    scope: tuple[str, str | None]
    preference: str
    check: Check

    def covers(self, cell: tuple[str, str]) -> bool:
        """Whether a request of `cell` (family, mode) is in this quirk's scope."""
        kind, name = self.scope
        if kind == ANY:
            return True
        return cell[0] == name if kind == "family" else cell[1] == name

    @property
    def scope_text(self) -> str:
        kind, name = self.scope
        return ANY if kind == ANY else f"{kind}: {name}"


def _scope(raw: Any, families: Sequence[str], modes: Sequence[str], where: str) -> tuple[str, str | None]:
    if raw == ANY:
        return (ANY, None)
    if not isinstance(raw, dict) or len(raw) != 1:
        raise ValueError(f"{where}: scope is {{family: ...}}, {{mode: ...}} or any")
    (kind, name), = raw.items()
    known = {"family": families, "mode": modes}.get(kind)
    if known is None or name not in known:
        raise ValueError(f"{where}: scope {kind}: {name!r} names no stream.{kind}s entry")
    return (str(kind), str(name))


def load_quirks(path: str | Path, families: Sequence[str], modes: Sequence[str], max_wait_ms: int) -> tuple[Quirk, ...]:
    """The quirks in `path`; raises ValueError (dsl.InvalidCheck for a check outside the DSL) on a malformed file,
    a repeated quirk or check id, or a preference that names a goal family or mode."""
    raw = yaml.safe_load(Path(path).read_text())
    out: list[Quirk] = []
    for i, q in enumerate(raw["quirks"]):
        where = f"{path}: quirks[{i}]"
        c = q["check"]
        check = Check(str(c["id"]), tuple(dict(s) for s in c["steps"]), dict(c["expect"]))
        dsl.validate(check, max_wait_ms)
        pref = " ".join(str(q["preference"]).split())
        named = [w for w in (*families, *modes) if w.lower() in pref.lower()]
        if named:
            raise ValueError(f"{where}: the preference names {named}")
        out.append(Quirk(str(q["id"]), _scope(q["scope"], families, modes, where), pref, check))
    for attr in ("id", "check"):
        seen = [q.id if attr == "id" else q.check.id for q in out]
        if len(set(seen)) != len(seen):
            raise ValueError(f"{path}: repeated quirk {attr} ids {seen}")
    return tuple(out)


def load_cells(stream_path: str | Path) -> dict[str, tuple[str, str]]:
    """Request id -> (family, mode), from the `cells.json` beside the goal stream."""
    raw = json.loads((Path(stream_path).parent / CELLS_FILE).read_text())
    return {str(k): (str(v[0]), str(v[1])) for k, v in raw["requests"].items()}


@dataclass(frozen=True)
class QuirkResult:
    quirk: str
    check_id: str
    passed: bool            # the check passed at every viewport the eval read
    rate: float             # the share of viewports at which it passed


@dataclass(frozen=True)
class TasteReading:
    quirks: tuple[QuirkResult, ...]
    loss: float             # the loss the gate reads (the taste checks inside C)
    loss_base: float        # the same eval with the taste checks left out of C
    C_base: float


def _viewport_of(check_id: str) -> tuple[str, str]:
    base, sep, vp = check_id.rpartition("@")
    if not sep:
        raise ValueError(f"check result {check_id!r} carries no viewport")
    return base, vp


@dataclass(frozen=True)
class Taste:
    """The critic run's taste: the quirks, each request's cell and the loss model `loss_base` is read with."""

    quirks: tuple[Quirk, ...]
    cells: Mapping[str, tuple[str, str]]
    loss_model: LossModel

    def in_scope(self, request_id: str) -> tuple[Quirk, ...]:
        """The quirks whose scope covers the request; raises KeyError for a request with no recorded cell."""
        cell = self.cells[request_id]
        return tuple(q for q in self.quirks if q.covers(cell))

    def scoped_request(self, request: Request, regime: Regime, quirks: Sequence[Quirk]) -> Request:
        """`request` with each quirk's check appended to its `regime` acceptance checks; raises on an id clash."""
        own = request.checks_for(regime)
        clash = {c.id for c in own} & {q.check.id for q in quirks}
        if clash:
            raise ValueError(f"{request.id}: taste check ids {sorted(clash)} clash with its own checks")
        return dataclasses.replace(request, checks={**request.checks,
                                                    regime: (*own, *(q.check for q in quirks))})

    def reading(self, report: EvalReport, quirks: Sequence[Quirk]) -> TasteReading:
        """Each quirk check's result, and `loss_base`: the report's loss with C read over the request's own checks.

        C is re-read per viewport from the report's check results and must equal the report's C (the taste checks
        are inside it); a mismatch raises, so `loss_base` is never read off a C the eval did not compute."""
        taste_ids = {q.check.id for q in quirks}
        by_vp: dict[str, list[tuple[str, bool]]] = defaultdict(list)
        for r in report.checks:
            base, vp = _viewport_of(r.check_id)
            by_vp[vp].append((base, r.passed))
        if not by_vp:
            raise ValueError(f"{report.pass_id}: no acceptance check results to read")
        c_all, c_base = [], []
        for vp, rows in by_vp.items():
            own = [p for cid, p in rows if cid not in taste_ids]
            if not own:
                raise ValueError(f"{report.pass_id}@{vp}: no acceptance check of the request's own")
            c_all.append(sum(p for _, p in rows) / len(rows))
            c_base.append(sum(own) / len(own))
        C = sum(c_all) / len(c_all)
        if abs(C - report.C) > 1e-9:
            raise ValueError(f"{report.pass_id}: C re-read as {C}, the report holds {report.C}")
        C_base = sum(c_base) / len(c_base)
        a_base = self.loss_model.a_mix(report.P, report.H, C_base)
        results = []
        for q in quirks:
            got = [p for rows in by_vp.values() for cid, p in rows if cid == q.check.id]
            if len(got) != len(by_vp):
                raise ValueError(f"{report.pass_id}: {q.check.id} read at {len(got)} of {len(by_vp)} viewports")
            results.append(QuirkResult(q.id, q.check.id, all(got), sum(got) / len(got)))
        return TasteReading(tuple(results), report.loss, self.loss_model.loss(a_base, report.R), C_base)


def build_taste(repo: str | Path, spec: Any, loss_model: LossModel) -> Taste:
    """The taste for the critic run under `spec` (a RunSpec), its quirks read from `TASTE_PATH` under `repo`."""
    s = spec.stream
    quirks = load_quirks(Path(repo) / TASTE_PATH, s.families, s.modes, spec.eval.max_wait_ms)
    return Taste(quirks, load_cells(Path(repo) / s.path), loss_model)
