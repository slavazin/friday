"""The eval service: probes, how-to check, acceptance checks, rubric and quality reading -> EvalReport.

Every reading runs at each of the regime's viewports (SPEC § 5, § 10) and P, H, C and R are averaged over them.

Each step opens its `eval.*` span (docs/telemetry.md) on the injected telemetry log.

Only a browser failure is scored: the Browser port's pages raise TimeoutError (the page did not answer in time)
or ConnectionError (it failed to load or crashed). Any other exception is a programming error and raises.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from friday.algorithms.loss import LossModel
from friday.domain.types import CheckResult, EvalReport, Pass, ProbeReading, Regime, Request, Viewport
from friday.eval import how_to, probe
from friday.eval.dsl import run_check
from friday.ports.browser import Browser, QualityProbe
from friday.ports.scoring import Rubric
from friday.ports.telemetry import Telemetry

BROWSER_FAILURES = probe.BROWSER_FAILURES   # TimeoutError, ConnectionError


@dataclass
class Evaluator:
    browser: Browser
    rubric: Rubric
    quality: QualityProbe
    loss_model: LossModel                          # carries eval.a_weights
    viewports: Mapping[str, Sequence[Viewport]]    # run.yml eval.viewports, keyed by regime
    test_query: str                                # eval.test_query
    probe_max_controls: int                        # probe.max_controls
    settle_ms: int                                 # probe.settle_ms
    sample_text: str                               # probe.sample_text
    how_to_max_controls: int                       # how_to.max_controls
    repair_penalty: float                          # how_to.repair_penalty
    telemetry: Telemetry                           # the process's log; every step's eval.* span

    def _probe(self, path: str, vp: Viewport, pass_id: str, declared_ids: Sequence[str]) -> ProbeReading:
        loaded = False
        try:
            with self.browser.page(path, vp) as page:
                page.goto(self.test_query)
                loaded = True   # the probes read the test-query load: a page that fails it did not load
                return probe.run(page, vp, pass_id, declared_ids, self.probe_max_controls, self.settle_ms,
                                 self.sample_text)
        except BROWSER_FAILURES as e:  # a page that fails to load or stops answering is scored, not dropped
            msg = f"{'stopped answering' if loaded else 'load failed'}: {type(e).__name__}: {e}"
            return probe.failed(pass_id, vp, msg) if loaded else probe.unloaded(pass_id, vp, msg)

    def _screenshot(self, open_page, tag: str, read_axe: bool) -> tuple[bytes | None, list[str], int | None]:
        """One plain load: its console errors, its screenshot and its axe count; no screenshot when it fails.

        A browser failure during the axe read (the page hangs or crashes) is recorded in the errors and leaves
        axe unread (None: the next viewport reads it); any other error there is the quality probe's and raises.
        """
        with ExitStack() as stack:
            try:
                page = stack.enter_context(open_page())
                errors = list(page.console_errors())
                with self.telemetry.span("eval.screenshot") as span:
                    shot = page.screenshot()
                    span.set(bytes=len(shot))
            except BROWSER_FAILURES as e:  # an artifact that cannot load is scored, not dropped
                # Every check has already failed on load; the rubric's floor is the true reading of a page
                # nothing renders on. Recorded in console_errors so the report call sees why.
                return None, [f"load failed{tag}: {type(e).__name__}: {e}"], None
            if not read_axe:
                return shot, errors, None
            try:
                return shot, errors, self.quality.axe_violations(page)
            except BROWSER_FAILURES as e:  # a page that hangs or crashes under axe is a browser failure, scored
                return shot, [*errors, f"axe read failed{tag}: {type(e).__name__}: {e}"], None

    def run(self, pass_: Pass, request: Request, regime: Regime, request_text: str) -> EvalReport:
        """Scores one pass's artifact under `regime`. `request_text` is the text the worker read."""
        with self.telemetry.span("eval", viewports=len(self.viewports.get(regime.value, ()))):
            return self._run(pass_, request, regime, request_text)

    def _run(self, pass_: Pass, request: Request, regime: Regime, request_text: str) -> EvalReport:
        t = self.telemetry
        if pass_.artifact_path is None:
            raise ValueError(f"{pass_.id} has no artifact")
        path = pass_.artifact_path
        html = Path(path).read_text()
        viewports = tuple(self.viewports[regime.value])
        if not viewports:
            raise ValueError(f"no viewports for {regime}")
        checks = request.checks_for(regime)
        contract = list(request.contract)
        parsed = how_to.parse(html, contract, self.how_to_max_controls, self.test_query, self.settle_ms)
        artifact_hash = hashlib.sha256(html.encode()).hexdigest()
        lo, _ = self.loss_model.rubric_scale

        probes, readings, results, rubric, r_parts, c_parts = [], [], [], [], [], []
        errors: list[str] = []
        axe: int | None = None
        for vp in viewports:
            def open_page(vp: Viewport = vp):
                return self.browser.page(path, vp)

            with t.span("eval.viewport", width=vp[0], height=vp[1]):
                tag = f"@{vp[0]}x{vp[1]}"
                with t.span("eval.checks", checks=len(checks)) as span:
                    vp_results = [run_check(open_page, c) for c in checks]
                    span.set(checks_passed=sum(r.passed for r in vp_results))
                results += [CheckResult(r.check_id + tag, r.passed, r.failing_step) for r in vp_results]
                c_parts.append(self.loss_model.acceptance(vp_results))
                with t.span("eval.how_to", rung=int(parsed.rung)) as span:
                    steps = how_to.run(open_page, parsed)
                    span.set(steps=len(steps), steps_passed=sum(r.passed for r in steps))
                    readings.append(how_to.score(parsed, steps, contract, self.repair_penalty, pass_.id, vp))
                with t.span("eval.probe") as span:
                    probes.append(self._probe(path, vp, pass_.id, parsed.declared_ids))
                    span.set(controls_driven=probes[-1].controls, responsive=probes[-1].responsive,
                             silent_controls=probes[-1].silent, loaded=probes[-1].loaded)
                with t.span("eval.load") as span:
                    shot, vp_errors, axe_page = self._screenshot(open_page, tag, read_axe=axe is None)
                    span.set(console_errors=len(vp_errors), loaded=shot is not None)
                if axe is None and axe_page is not None:
                    axe = axe_page
                errors += [e for e in vp_errors if e not in errors]
                scores = (
                    (lo,) * self.loss_model.rubric_questions if shot is None
                    else tuple(self.rubric.score(shot, request_text, artifact_hash, vp))
                )
                rubric += scores
                r_parts.append(self.loss_model.rubric(scores))

        n = len(viewports)
        p = sum(x.P for x in probes) / n
        h = sum(x.H for x in readings) / n
        c = sum(c_parts) / n
        r = sum(r_parts) / n
        a = self.loss_model.a_mix(p, h, c)
        with t.span("eval.quality") as span:
            eslint = self.quality.eslint_errors(html)
            span.set(eslint_errors=eslint, axe_violations=axe)   # axe None: no page loaded for axe to read
        axe_n = axe if axe is not None else 0   # never loaded: no page for axe to read; Q counts ESLint alone
        return EvalReport(
            pass_id=pass_.id, regime=regime, checks=tuple(results), console_errors=tuple(errors), A=a, R=r,
            rubric=tuple(rubric), Q=eslint + axe_n, loss=self.loss_model.loss(a, r),
            eslint_errors=eslint, axe_violations=axe_n, P=p, H=h, C=c, probes=tuple(probes),
            how_to=tuple(readings), silent_controls=max(x.silent for x in probes),
        )
