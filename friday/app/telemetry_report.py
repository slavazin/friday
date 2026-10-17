"""Text views over the telemetry log: a round's span tree and its per-step time table (`friday telemetry`).

The pilot (CARRY_FORWARD S5) reads the time table to fit the run to the deadline.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence

from friday.ports.telemetry import TelemetryReader, TelemetryRow


def _fmt_s(v: float | None) -> str:
    return "in flight" if v is None else f"{v:.2f}s"


def span_tree(reader: TelemetryReader, *, run: str, round_: int | None = None, pass_id: str | None = None) -> str:
    """The spans in scope as an indented tree, each line `name  duration  [failed: error]`."""
    spans = reader.spans(run=run, round=round_, pass_id=pass_id)
    children: dict[str | None, list[TelemetryRow]] = defaultdict(list)
    ids = {s.span_id for s in spans}
    for s in spans:
        children[s.parent_id if s.parent_id in ids else None].append(s)
    lines: list[str] = []

    def walk(parent: str | None, depth: int) -> None:
        for s in children.get(parent, []):
            mark = "" if s.ok is not False else f"  [failed: {s.error}]"
            who = f" ({s.scope.pass_id})" if s.scope.pass_id and depth == 0 else ""
            lines.append(f"{'  ' * depth}{s.name}{who}  {_fmt_s(s.duration_s)}{mark}")
            walk(s.span_id, depth + 1)

    walk(None, 0)
    return "\n".join(lines)


def step_times(spans: Sequence[TelemetryRow]) -> dict[str, dict[str, float | int | None]]:
    """Per span name: count, failed, median, p90 and max duration in seconds over the closed spans."""
    by: dict[str, list[TelemetryRow]] = defaultdict(list)
    for s in spans:
        by[s.name].append(s)
    out: dict[str, dict[str, float | int | None]] = {}
    for name, rows in sorted(by.items()):
        d = sorted(r.duration_s for r in rows if r.duration_s is not None)
        out[name] = {
            "count": len(rows),
            "failed": sum(r.ok is False for r in rows),
            "in_flight": sum(r.ended_at is None for r in rows),
            "median_s": statistics.median(d) if d else None,
            "p90_s": d[min(len(d) - 1, int(0.9 * len(d)))] if d else None,
            "max_s": d[-1] if d else None,
        }
    return out


def time_table(reader: TelemetryReader, *, run: str, round_: int | None = None) -> str:
    """`step_times` over the scope as a fixed-width table."""
    rows = step_times(reader.spans(run=run, round=round_))
    head = f"{'step':<28}{'n':>5}{'fail':>6}{'median':>10}{'p90':>10}{'max':>10}"
    cell = lambda v: "-" if v is None else f"{v:.2f}"  # noqa: E731
    body = [f"{k:<28}{v['count']:>5}{v['failed']:>6}{cell(v['median_s']):>10}{cell(v['p90_s']):>10}"
            f"{cell(v['max_s']):>10}" for k, v in rows.items()]
    return "\n".join([head, *body])
