"""The eval report as the judge's report call reads it (SPEC § 6, Report call).

Renders each acceptance check with pass or fail and its failing step, the probe results, each declared how-to
control with pass or fail, the uncovered contract ids, the repair rung, the silent controls, the console
errors, Q and the rubric scores. The regime is never rendered (blinding row judge_report); neither is the loss.
The text is a pure function of the report: the same report always renders the same text.
"""

from __future__ import annotations

from collections.abc import Sequence

from friday.domain.types import CheckResult, EvalReport, HowToReading, ProbeReading


def _result(r: CheckResult) -> str:
    if r.passed:
        return f"- {r.check_id}: pass"
    step = r.failing_step.strip() or "(no step recorded)"
    return f"- {r.check_id}: fail at step: {step}"


def _viewport(v: tuple[int, int]) -> str:
    return f"{v[0]}x{v[1]}"


def _checks(checks: Sequence[CheckResult]) -> list[str]:
    if not checks:
        return ["Acceptance checks: none"]
    passed = sum(c.passed for c in checks)
    return [f"Acceptance checks ({passed} of {len(checks)} pass):", *(_result(c) for c in checks)]


def _probes(probes: Sequence[ProbeReading]) -> list[str]:
    if not probes:
        return ["Probe results: not read"]
    lines = ["Probe results:"]
    for p in probes:
        lines.append(
            f"- at {_viewport(p.viewport)}: loaded {'yes' if p.loaded else 'no'}; "
            f"console errors {len(p.console_errors)}; controls operated {p.controls}; "
            f"responsive {p.responsive}; silent {p.silent}")
    return lines


def _how_to(readings: Sequence[HowToReading]) -> list[str]:
    if not readings:
        return ["How-to controls: not read"]
    lines = ["How-to controls:"]
    for h in readings:
        parse = h.parse
        lines.append(f"- at {_viewport(h.viewport)}: repair rung {parse.rung.value} ({parse.rung.name.lower()}); "
                     f"declared controls {len(parse.declared)}; invalid entries {parse.invalid_entries}")
        lines += [f"  {_result(r)}" for r in h.results]
        uncovered = ", ".join(parse.undeclared_contract_ids) or "none"
        lines.append(f"  uncovered contract ids: {uncovered}")
    return lines


def render(report: EvalReport) -> str:
    """The report call's eval-report text for `report`, deterministic in its input."""
    lines = [*_checks(report.checks), "", *_probes(report.probes), "", *_how_to(report.how_to), ""]
    lines.append(f"Silent controls (responsive, not declared in the how-to): {report.silent_controls}")
    lines.append("")
    if report.console_errors:
        lines += ["Console errors:", *(f"- {e}" for e in report.console_errors)]
    else:
        lines.append("Console errors: none")
    lines.append("")
    lines.append(f"Quality reading Q: {report.Q} (ESLint errors {report.eslint_errors}, "
                 f"axe violations {report.axe_violations})")
    scores = ", ".join(str(s) for s in report.rubric) or "none"
    lines.append(f"Rubric scores: {scores}")
    return "\n".join(lines)
