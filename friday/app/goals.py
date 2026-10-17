"""Minting and tagging orchestration (SPEC § 4): wires the goal minter to the check DSL and a real-browser
stub run, and applies the tagger's output to the tag vocabulary under the per-round minting bar."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from friday.config import RunSpec
from friday.domain.types import Check, Regime, TagGloss, Viewport
from friday.eval import dsl
from friday.ports.browser import Browser
from friday.ports.goals import GoalSource, TagVocabulary
from friday.ports.models import ModelClient
from friday.ports.telemetry import Telemetry
from friday.roles.goal_minter import DslCheck, GoalMinter, MintedGoals, MintParams, StubReading, stub_html
from friday.roles.prompts import PromptBook
from friday.roles.tagger import Tagger


def mint_params(spec: RunSpec, incoherent: Sequence[tuple[str, str]] = ()) -> MintParams:
    """The goal minter's figures from run.yml. A whole request is regenerated at most
    `stream.check_regenerations` times, the one regeneration bound run.yml holds."""
    s = spec.stream
    return MintParams(
        seed=spec.run.seed, rounds=spec.run.rounds, per_round=s.per_round, held_out=s.held_out,
        drift_round=spec.drift.round, families=tuple(s.families), mix=dict(s.mix),
        checks_per_regime=tuple(s.checks_per_regime), check_regenerations=s.check_regenerations,
        request_regenerations=s.check_regenerations, constraint=spec.drift.constraint,
        test_query=spec.eval.test_query, max_wait_ms=spec.eval.max_wait_ms, reasks=spec.json.reasks,
        expect_verbs=frozenset(dsl.EXPECT_KEYS), modes=tuple(s.modes), held_out_cells=s.held_out_cells,
        split_max_draws=s.split_max_draws, incoherent=frozenset(incoherent),
    )


def dsl_validator(max_wait_ms: int) -> DslCheck:
    """Validates a check set against the DSL; raises dsl.InvalidCheck."""
    return lambda checks: dsl.validate_set(checks, max_wait_ms)


@dataclass
class StubBench:
    """Runs a check on its request's contract stub in the browser, at every viewport of the check's regime."""

    browser: Browser
    write_stub: Callable[[str, str], str]           # (request id, html) -> the stub's path
    viewports: Mapping[str, Sequence[Viewport]]

    def __call__(self, request_id: str, contract: Mapping[str, str], check: Check, regime: Regime) -> StubReading:
        """passed=True when the check passes on the stub at any viewport; the detail names every step that
        failed before the expectation (a load error, a timeout, a missing element)."""
        path = self.write_stub(request_id, stub_html(contract))
        passed, detail = False, []
        for vp in self.viewports[regime.value]:
            res = dsl.run_check(lambda vp=vp: self.browser.page(path, vp), check)
            passed = passed or res.passed
            if not res.passed and not res.failing_step.startswith("expect"):
                detail.append(f"{vp[0]}x{vp[1]}: {res.failing_step}")
        return StubReading(passed, "; ".join(detail))


def build_minter(spec: RunSpec, client: ModelClient, book: PromptBook, browser: Browser,
                 write_stub: Callable[[str, str], str], sink: Callable[[MintedGoals], None],
                 incoherent: Sequence[tuple[str, str]] = ()) -> GoalMinter:
    """The goal minter over the real check DSL and a browser stub run, on the grid less `incoherent`."""
    bench = StubBench(browser, write_stub, spec.eval.viewports)
    return GoalMinter(client, book, mint_params(spec, incoherent), bench, dsl_validator(spec.eval.max_wait_ms),
                      sink)


def build_tagger(spec: RunSpec, client: ModelClient, book: PromptBook) -> Tagger:
    s = spec.stream
    return Tagger(client, book, tuple(s.tags_per_request), spec.json.reasks, tuple(s.families))


def tag_round(tagger: Tagger, source: GoalSource, vocabulary: TagVocabulary, n: int,
              max_new_tags: int, *, telemetry: Telemetry) -> tuple[Mapping[str, frozenset[str]], list[TagGloss]]:
    """Tags round `n`'s requests and stores the new glosses and the tags. The round's minting budget is
    `max_new_tags` less the tags already minted in round `n`, so a second call cannot exceed the bar.
    The step is one `goals.tag` span on `telemetry`."""
    with telemetry.span("goals.tag", round=n) as span:
        tags, new = _tag_round(tagger, source, vocabulary, n, max_new_tags)
        span.set(requests=len(tags), new_tags=len(new))
        return tags, new


def _tag_round(tagger: Tagger, source: GoalSource, vocabulary: TagVocabulary, n: int,
               max_new_tags: int) -> tuple[Mapping[str, frozenset[str]], list[TagGloss]]:
    requests = list(source.round(n))
    if not requests:
        raise ValueError(f"round {n} has no requests")
    tagged = sorted(r.id for r in requests if vocabulary.tags_of(r.id) is not None)
    if tagged:
        raise ValueError(f"round {n} requests already tagged: {tagged}")
    vocab = list(vocabulary.glosses())
    budget = max_new_tags - sum(g.minted_round == n for g in vocab)
    if budget < 0:
        raise ValueError(f"round {n} already minted past the bar of {max_new_tags}")
    tags, new = tagger.tag(requests, vocab, n, budget)
    for g in new:
        vocabulary.add(g)
    vocabulary.set_tags(tags)
    return tags, new


def tag_held_out(tagger: Tagger, source: GoalSource, vocabulary: TagVocabulary,
                 close_round: int, *, telemetry: Telemetry) -> Mapping[str, frozenset[str]]:
    """Tags the held-out set with the frozen vocabulary, minting nothing. Raises ValueError before the
    close: the held-out set is read only once every stream request of rounds 1..`close_round` is tagged.
    The step is one `goals.tag` span on `telemetry` (`held_out` true)."""
    with telemetry.span("goals.tag", held_out=True) as span:
        tags = _tag_held_out(tagger, source, vocabulary, close_round)
        span.set(requests=len(tags), new_tags=0)
        return tags


def _tag_held_out(tagger: Tagger, source: GoalSource, vocabulary: TagVocabulary,
                  close_round: int) -> Mapping[str, frozenset[str]]:
    for n in range(1, close_round + 1):
        stream = list(source.round(n))
        if not stream:
            raise ValueError(f"round {n} has no requests; the held-out set is tagged at the close only")
        untagged = sorted(r.id for r in stream if vocabulary.tags_of(r.id) is None)
        if untagged:
            raise ValueError(f"round {n} requests not yet tagged: {untagged}; "
                             "the held-out set is tagged at the close only")
    requests = list(source.held_out())
    tagged = sorted(r.id for r in requests if vocabulary.tags_of(r.id) is not None)
    if tagged:
        raise ValueError(f"held-out requests already tagged: {tagged}")
    tags, new = tagger.tag(requests, list(vocabulary.glosses()), close_round, 0)
    if new:
        raise AssertionError("held-out tagging minted a tag")
    vocabulary.set_tags(tags)
    return tags
