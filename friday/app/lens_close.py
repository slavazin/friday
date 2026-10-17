"""Lens curation at the round close (SPEC § 6 steps 1-5): reads the window from the stores, applies the
curation flips to the LensSet as rendered commits, runs the lens minter every `lens.mint_every` closes, and
applies cap evictions.

Stateless across closes: a lens's floor strikes are recomputed from the stores by re-reading the previous
closes' windows (each floor is a pure function of the run seed, the close round and the lens id), so no
strike count is held anywhere between closes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from friday.algorithms.lens_curation import CurationParams, CurationResult, WindowPass, cap_evictions, curate
from friday.app.render import FlipRenderer
from friday.domain.events import StatusFlip
from friday.domain.ids import IdMinter
from friday.domain.lifecycle import LIVE_LENS, FlipCause, check_lens_flip
from friday.domain.types import Arm, Lens, LensStatus, Pass
from friday.ports.stores import EventLog, JudgeStore, LensSet, ObservationStore, PassStore
from friday.roles.lens_minter import LensMinter, ResidualItem
from friday.stats.ranks import BinaryScore, Correlation

# pass -> (request text, artifact source excerpt) for the lens minter's residual view
ResidualSource = Callable[[Pass], tuple[str, str]]


@dataclass(frozen=True)
class LensCloseResult:
    flips: tuple[StatusFlip, ...]           # in the order they were committed
    minted: tuple[Lens, ...]
    curation: CurationResult
    window_model: str | None                # the judge model whose answers the values were read from


@dataclass
class LensClose:
    """Lens steps of one round close, over the stores and the LensSet."""

    lens_set: LensSet
    events: EventLog
    passes: PassStore
    judge_store: JudgeStore
    observations: ObservationStore
    renderer: FlipRenderer
    params: CurationParams
    scorer: BinaryScore
    correlation: Correlation
    seed: int
    window: int
    mint_every: int
    residual_min_passes: int
    mint_max: int
    minter: LensMinter | None               # None when the minter is cut (SPEC § 13)
    residual_source: ResidualSource | None = None
    constants: Mapping[str, Any] = field(default_factory=dict)

    # -- the window, from the stores ------------------------------------------------------------------

    def window_at(self, round_: int) -> tuple[list[WindowPass], str | None]:
        """The last `window` journal-arm passes with a loss at the close of `round_`, and the judge model read.

        Lens values are read per judge model: when the window's answers come from several judge models, only
        the answers of the model that answered the latest answered pass are read; the other passes keep their
        loss (labels) and read as unanswered.
        """
        rows = []
        for p in self.passes.passes():
            if p.arm is not Arm.JOURNAL or p.transport_failed or p.round > round_:
                continue
            ev = self.passes.eval_of(p.id)
            if ev is not None:
                rows.append((p, ev.loss))
        rows.sort(key=lambda r: (r[0].round, r[0].id))
        rows = rows[-self.window:] if self.window > 0 else []
        answers = self.judge_store.lens_answers([p.id for p, _ in rows])
        order = {p.id: i for i, (p, _) in enumerate(rows)}
        model = None
        if answers:
            model = max(answers, key=lambda a: order[a.pass_id]).judge_model
        by_pass: dict[str, dict[str, float]] = {p.id: {} for p, _ in rows}
        for a in answers:
            if a.judge_model == model:
                by_pass[a.pass_id][a.lens_id] = a.p
        return [WindowPass(p.id, loss, by_pass[p.id]) for p, loss in rows], model

    def _curate(self, lenses: Sequence[Lens], round_: int, strikes: Mapping[str, int]) -> CurationResult:
        window, _ = self.window_at(round_)
        return curate(lenses, window, strikes, self.params, self.scorer, self.correlation, self.seed, round_)

    def strikes_before(self, lenses: Sequence[Lens], round_: int) -> dict[str, int]:
        """Consecutive below-floor readings each live lens carries into the close of `round_`.

        Re-reads the closes before `round_`, newest first: a reading at or below the floor adds a strike, a
        reading above it ends the count, and a close with no value (fewer than `n_min` answers) is skipped,
        exactly as `curate` carries strikes. Counting stops at `floor_strikes - 1`, the most a live lens can
        carry (one more retires it).
        """
        live = [x for x in lenses if x.status in LIVE_LENS]
        need = self.params.floor_strikes - 1
        strikes = {x.id: 0 for x in live}
        open_ids = {x.id for x in live} if need > 0 else set()
        for c in range(round_ - 1, 0, -1):
            if not open_ids:
                break
            reading = self._curate([x for x in live if x.id in open_ids], c, {})
            for lens_id in sorted(open_ids):
                value, floor = reading.values.get(lens_id), reading.floors.get(lens_id)
                if value is None or floor is None:
                    continue
                if value > floor:
                    open_ids.discard(lens_id)
                    continue
                strikes[lens_id] += 1
                if strikes[lens_id] >= need:
                    open_ids.discard(lens_id)
        return strikes

    # -- writes -----------------------------------------------------------------------------------------

    def _flip(self, lens: Lens, new: LensStatus, cause: FlipCause, round_: int,
              evidence: Mapping[str, Any], successors: Sequence[str] = ()) -> StatusFlip:
        """Validates, renders, appends to the event log, then writes the lens file and its commit."""
        check_lens_flip(lens.status, new, cause)
        flip = StatusFlip(lens.id, lens.status.value, new.value, cause, round_, dict(evidence), tuple(successors))
        message = self.renderer.render("lens", flip, self.constants)
        self.events.append_flip(flip)
        self.lens_set.flip(flip, message)
        return flip

    def close(self, round_: int) -> LensCloseResult:
        """Curation flips, then (every `mint_every` closes) the minter and the cap; returns what was written."""
        lenses = {x.id: x for x in self.lens_set.lenses()}
        live = [x for x in lenses.values() if x.status in LIVE_LENS]
        strikes = self.strikes_before(live, round_)
        window, model = self.window_at(round_)
        result = curate(live, window, strikes, self.params, self.scorer, self.correlation, self.seed, round_)
        flips: list[StatusFlip] = []
        for lf in result.flips:
            evidence: dict[str, Any] = dict(lf.figures)
            if lf.cause is FlipCause.LENS_FLOOR:
                evidence["strikes"] = result.strikes[lf.lens_id]
            successors: tuple[str, ...] = ()
            if lf.superseded_by is not None:
                evidence["superseded_by"] = lf.superseded_by
                successors = (lf.superseded_by,)
            flips.append(self._flip(lenses[lf.lens_id], lf.new, lf.cause, round_, evidence, successors))
            lenses[lf.lens_id] = replace(lenses[lf.lens_id], status=lf.new)   # a later flip reads the new status

        minted: list[Lens] = []
        if self.minter is not None and self.mint_every > 0 and round_ % self.mint_every == 0 \
                and len(result.residual) >= self.residual_min_passes:
            flips_, minted = self._mint(round_, result, window)
            flips.extend(flips_)
        return LensCloseResult(tuple(flips), tuple(minted), result, model)

    def _mint(self, round_: int, result: CurationResult, window: Sequence[WindowPass],
              ) -> tuple[list[StatusFlip], list[Lens]]:
        if self.residual_source is None or self.minter is None:
            raise ValueError("minting needs the lens minter and a residual source (request text, source excerpt)")
        by_id = {p.id: p for p in self.passes.passes()}
        obs = self.observations.observations()
        items = []
        for pid in result.residual:
            request_text, excerpt = self.residual_source(by_id[pid])
            items.append(ResidualItem(request_text, excerpt, tuple(o for o in obs if o.pass_id == pid)))
        current = list(self.lens_set.lenses())
        live = [x for x in current if x.status in LIVE_LENS]
        proposals = self.minter.propose(items, live, self.mint_max)
        if len(proposals) > self.mint_max:
            raise ValueError(f"lens minter proposed {len(proposals)} lenses, above lens.mint_max {self.mint_max}")
        answered = {x.id: sum(1 for w in window if x.id in w.p) for x in live}
        evict, room = cap_evictions(live, result.values, answered, len(proposals), self.params.cap,
                                    self.params.n_min)
        by_lens = {x.id: x for x in live}
        flips = [self._flip(by_lens[i], LensStatus.RETIRED, FlipCause.LENS_CAP, round_,
                            {"value": result.values[i], "n": answered[i]}) for i in evict]
        minter = IdMinter.resume("lens", [x.id for x in current])
        minted = []
        for prop in proposals[:room]:
            lens = Lens(minter.next(), prop.angle, prop.counterfactual, LensStatus.HIDDEN, minted_round=round_)
            self.lens_set.add(lens)
            minted.append(lens)
        return flips, minted
