"""JSON codecs for the nested values the SQLite stores keep in TEXT columns.

Each `decode_*` is the exact inverse of its `encode_*`: tuples come back as tuples, enums as enums, and a
missing key raises (a row that does not decode is a defect, never a default).
"""

from __future__ import annotations

import json
from typing import Any

from friday.domain.types import (
    Check,
    CheckResult,
    Composition,
    HowToParse,
    HowToReading,
    HowToRung,
    ProbeReading,
)


def dumps(value: Any) -> str:
    """`value` as JSON text; NaN and infinities are refused (SQLite would read them back as NULL)."""
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


def loads(text: str) -> Any:
    """The value of JSON text this module wrote."""
    return json.loads(text)


def encode_composition(c: Composition) -> str:
    return dumps({"request_id": c.request_id, "decision_ids": list(c.decision_ids), "exposed": c.exposed,
                  "ablated": c.ablated, "notes": c.notes})


def decode_composition(text: str) -> Composition:
    d = loads(text)
    return Composition(d["request_id"], tuple(d["decision_ids"]), d["exposed"], d["ablated"], d["notes"])


def check_result_json(r: CheckResult) -> dict[str, Any]:
    return {"check_id": r.check_id, "passed": r.passed, "failing_step": r.failing_step}


def check_result_of(d: dict[str, Any]) -> CheckResult:
    return CheckResult(d["check_id"], d["passed"], d["failing_step"])


def encode_check_results(results: tuple[CheckResult, ...]) -> str:
    return dumps([check_result_json(r) for r in results])


def decode_check_results(text: str) -> tuple[CheckResult, ...]:
    return tuple(check_result_of(d) for d in loads(text))


def _check_json(c: Check) -> dict[str, Any]:
    return {"id": c.id, "steps": list(c.steps), "expect": c.expect}


def _check_of(d: dict[str, Any]) -> Check:
    return Check(d["id"], tuple(d["steps"]), d["expect"])


def _probe_json(p: ProbeReading) -> dict[str, Any]:
    return {"pass_id": p.pass_id, "viewport": list(p.viewport), "loaded": p.loaded,
            "console_errors": list(p.console_errors), "controls": p.controls, "responsive": p.responsive,
            "responsive_ids": list(p.responsive_ids), "silent": p.silent, "P": p.P}


def _probe_of(d: dict[str, Any]) -> ProbeReading:
    w, h = d["viewport"]
    return ProbeReading(d["pass_id"], (w, h), d["loaded"], tuple(d["console_errors"]), d["controls"],
                        d["responsive"], tuple(d["responsive_ids"]), d["silent"], d["P"])


def _parse_json(p: HowToParse) -> dict[str, Any]:
    return {"rung": int(p.rung), "declared": [_check_json(c) for c in p.declared],
            "invalid_entries": p.invalid_entries, "declared_ids": list(p.declared_ids),
            "undeclared_contract_ids": list(p.undeclared_contract_ids), "summary": p.summary}


def _parse_of(d: dict[str, Any]) -> HowToParse:
    return HowToParse(HowToRung(d["rung"]), tuple(_check_of(c) for c in d["declared"]), d["invalid_entries"],
                      tuple(d["declared_ids"]), tuple(d["undeclared_contract_ids"]), d["summary"])


def _how_to_json(h: HowToReading) -> dict[str, Any]:
    return {"pass_id": h.pass_id, "viewport": list(h.viewport), "parse": _parse_json(h.parse),
            "results": [check_result_json(r) for r in h.results], "H": h.H}


def _how_to_of(d: dict[str, Any]) -> HowToReading:
    w, h = d["viewport"]
    return HowToReading(d["pass_id"], (w, h), _parse_of(d["parse"]),
                        tuple(check_result_of(r) for r in d["results"]), d["H"])


def encode_probes(probes: tuple[ProbeReading, ...]) -> str:
    return dumps([_probe_json(p) for p in probes])


def decode_probes(text: str) -> tuple[ProbeReading, ...]:
    return tuple(_probe_of(d) for d in loads(text))


def encode_how_to(readings: tuple[HowToReading, ...]) -> str:
    return dumps([_how_to_json(h) for h in readings])


def decode_how_to(text: str) -> tuple[HowToReading, ...]:
    return tuple(_how_to_of(d) for d in loads(text))


def encode_strings(values: tuple[str, ...]) -> str:
    return dumps(list(values))


def decode_strings(text: str) -> tuple[str, ...]:
    return tuple(loads(text))


def encode_ints(values: tuple[int, ...]) -> str:
    return dumps(list(values))


def decode_ints(text: str) -> tuple[int, ...]:
    return tuple(loads(text))
