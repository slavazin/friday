"""Lens curation at the round close (SPEC § 6): window from the stores, stateless strikes, rendered lens commits,
the minter every `lens.mint_every` closes, cap evictions."""

import random
import shutil
from pathlib import Path

import pytest

from friday.adapters.git import RecordingVcs
from friday.adapters.lens_md import MarkdownLensSet
from friday.adapters.memory import MemoryEventLog, MemoryJudgeStore, MemoryObservationStore, MemoryPassStore
from friday.adapters.telemetry import MemoryTelemetry
from friday.app.lens_close import LensClose
from friday.app.render import FlipRenderer
from friday.app.wiring import build_mechanics, flip_constants
from friday.config import load_spec
from friday.domain.lifecycle import FlipCause
from friday.domain.types import Arm, Composition, EvalReport, Lens, LensAnswer, LensStatus, Pass, Regime
from friday.ports.telemetry import Scope
from friday.roles.lens_minter import LensMinter, LensProposal
from friday.roles.prompts import PromptBook
from tests.fakes import ScriptedClient

ROOT = Path(__file__).resolve().parents[2]
SPEC = load_spec(ROOT / "run.yml")
RENDERER = FlipRenderer.load(ROOT / "templates/flips.yml")


class World:
    def __init__(self, tmp_path, minter_replies=None):
        shutil.copytree(ROOT / "lenses", tmp_path / "lenses")
        self.vcs = RecordingVcs()
        self.lens_set = MarkdownLensSet(tmp_path / "lenses", self.vcs)
        self.events, self.passes = MemoryEventLog(), MemoryPassStore()
        self.judge, self.obs = MemoryJudgeStore(), MemoryObservationStore()
        self.minter = LensMinter(ScriptedClient([]), PromptBook(ROOT / "prompts"))
        self.minter_calls: list[tuple] = []
        replies = list(minter_replies or [])

        def propose(residual, lenses, limit):
            self.minter_calls.append((list(residual), [x.id for x in lenses], limit))
            return replies.pop(0) if replies else []

        self.minter.propose = propose

    def lens_close(self):
        """A fresh LensClose each time: nothing carries between closes but the stores."""
        m = build_mechanics(SPEC)
        ln = SPEC.lens
        return LensClose(
            lens_set=self.lens_set, events=self.events, passes=self.passes, judge_store=self.judge,
            observations=self.obs, renderer=RENDERER, params=m.curation, scorer=m.lens_value,
            correlation=m.fold_correlation, seed=SPEC.run.seed, window=ln.window, mint_every=ln.mint_every,
            residual_min_passes=ln.residual_min_passes, mint_max=ln.mint_max, minter=self.minter,
            telemetry=MemoryTelemetry(Scope("main")),
            residual_source=lambda p: (f"request {p.category}", "<canvas id=board>"), constants=flip_constants(SPEC),
        )

    def add(self, round_, n, ps, arm=Arm.JOURNAL, model="judge/a", seed=0, prefix=None):
        rng = random.Random(seed)
        ids = []
        for i in range(n):
            pid = f"{prefix or 'r' + str(round_)}-{arm.value}-{i:03d}"
            loss = rng.random()
            self.passes.add_pass(Pass(pid, round_, f"c{i}", arm, "w", Composition(f"c{i}", ()), Regime.R1,
                                      pair_slot=0))
            self.passes.add_eval(EvalReport(pid, Regime.R1, (), (), 0.5, 0.5, (), 3, loss))
            if arm is Arm.JOURNAL:
                self.judge.add_lens_answers([LensAnswer(pid, lid, f(loss, rng), "", model) for lid, f in ps.items()])
            ids.append(pid)
        return ids


PREDICTIVE = {"L-01": lambda loss, rng: loss, "L-02": lambda loss, rng: 1.0 - loss}


def test_lens_below_its_floor_at_two_consecutive_closes_retires_with_a_rendered_commit(tmp_path):
    w = World(tmp_path)
    w.add(1, SPEC.lens.window, PREDICTIVE)
    first = w.lens_close().close(1)
    assert first.flips == () and first.curation.strikes["L-02"] == 1
    second = w.lens_close().close(3)        # odd round: no minting; strikes recomputed from the stores
    assert [(f.subject_id, f.cause) for f in second.flips] == [("L-02", FlipCause.LENS_FLOOR)]
    msg = w.vcs.commits[-1][1]
    assert msg.startswith("L-02 active → retired: AUC 0.00 below floor ") and msg.endswith("at 2 consecutive closes")
    assert w.events.flips()[-1].subject_id == "L-02"
    assert next(x for x in w.lens_set.lenses() if x.id == "L-02").status is LensStatus.RETIRED


def test_above_floor_close_resets_the_recomputed_strikes(tmp_path):
    w = World(tmp_path)
    w.add(1, SPEC.lens.window, {"L-02": lambda loss, rng: 1.0 - loss})
    w.add(2, SPEC.lens.window, {"L-02": lambda loss, rng: loss}, seed=1)   # round 2 alone fills the window
    w.add(3, SPEC.lens.window, {"L-02": lambda loss, rng: 1.0 - loss}, seed=2)
    lc = w.lens_close()
    assert lc.strikes_before(w.lens_set.lenses(), 3)["L-02"] == 0         # close 2 read above its floor
    assert lc.close(3).flips == ()


def test_window_reads_the_last_journal_arm_passes_only(tmp_path):
    w = World(tmp_path)
    w.add(1, 10, PREDICTIVE)
    journal = w.add(2, SPEC.lens.window, PREDICTIVE, seed=3)
    w.add(3, 20, {}, arm=Arm.NOTES)
    window, _ = w.lens_close().window_at(3)
    assert [x.pass_id for x in window] == journal


def test_values_are_read_per_judge_model(tmp_path):
    w = World(tmp_path)
    w.add(1, 32, PREDICTIVE, model="judge/a")
    later = w.add(2, 32, PREDICTIVE, model="judge/b", seed=4)
    window, model = w.lens_close().window_at(2)
    assert model == "judge/b"
    assert [x.pass_id for x in window] == later          # judge/a's passes are left out, not read as unanswered


def test_passes_another_judge_model_answered_never_enter_the_residual_after_a_model_switch(tmp_path):
    w = World(tmp_path)
    flag_all = {"L-01": lambda loss, rng: 0.5 + 0.5 * loss}        # L-01 flags every pass, under either model
    w.add(1, 32, flag_all, model="judge/a")
    w.add(2, 32, flag_all, model="judge/b", seed=4)
    res = w.lens_close().close(2)                                   # a minting close
    assert res.window_model == "judge/b"
    assert res.curation.residual == () and w.minter_calls == []


def test_folded_lens_is_superseded_by_the_higher_value_one(tmp_path):
    w = World(tmp_path)
    w.add(1, SPEC.lens.window, {"L-01": lambda loss, rng: loss,
                                "L-03": lambda loss, rng: min(1.0, loss * 0.9 + 0.05 * rng.random())})
    res = w.lens_close().close(1)
    assert [(f.subject_id, f.cause, f.successors) for f in res.flips] == [("L-03", FlipCause.LENS_FOLD, ("L-01",))]
    assert w.vcs.commits[-1][1].startswith("L-03 active → superseded by L-01: Spearman rho ")
    assert next(x for x in w.lens_set.lenses() if x.id == "L-03").superseded_by == "L-01"


def test_minted_lens_enters_hidden_and_clears_its_floor_with_a_rendered_commit(tmp_path):
    w = World(tmp_path, minter_replies=[[LensProposal("Which control is off-screen at 375 px?", "Which is not?")]])
    low = {"L-01": lambda loss, rng: 0.4 * loss}              # predictive but never flags: residual is high-loss
    w.add(2, SPEC.lens.window, low)
    res = w.lens_close().close(2)
    assert [x.id for x in res.minted] == ["L-09"] and res.minted[0].status is LensStatus.HIDDEN
    residual, live, limit = w.minter_calls[0]
    assert limit == SPEC.lens.mint_max and len(residual) >= SPEC.lens.residual_min_passes
    assert residual[0].request_text.startswith("request ")
    w.add(3, SPEC.lens.window, {"L-01": lambda loss, rng: 0.4 * rng.random(), "L-09": lambda loss, rng: loss},
          seed=5)
    res = w.lens_close().close(3)
    assert [(f.subject_id, f.cause) for f in res.flips] == [("L-09", FlipCause.LENS_CLEAR_FLOOR)]
    assert w.vcs.commits[-1][1].startswith("L-09 minted lens clears its floor: AUC 1.00 over 64 passes, floor ")
    assert next(x for x in w.lens_set.lenses() if x.id == "L-09").has_cleared_floor


def test_a_lens_that_clears_and_folds_in_one_close_records_each_flip_from_its_current_status(tmp_path):
    w = World(tmp_path)
    w.lens_set.add(Lens("L-09", "a9", "c9", LensStatus.HIDDEN, minted_round=0))
    w.add(1, SPEC.lens.window, {"L-01": lambda loss, rng: loss, "L-09": lambda loss, rng: min(1.0, loss + 0.01)})
    res = w.lens_close().close(1)
    assert [(f.subject_id, f.old, f.new) for f in res.flips] == [
        ("L-09", "hidden", "active"), ("L-09", "active", "superseded")]
    assert [m for _, m in w.vcs.commits][-1].startswith("L-09 active → superseded by L-01")


def test_minter_runs_only_every_mint_every_closes(tmp_path):
    w = World(tmp_path)
    w.add(1, SPEC.lens.window, {"L-01": lambda loss, rng: 0.4 * loss})
    w.lens_close().close(1)
    assert w.minter_calls == []
    w.lens_close().close(2)
    assert len(w.minter_calls) == 1


def test_over_the_cap_the_lowest_value_lens_past_n_min_is_retired(tmp_path):
    w = World(tmp_path, minter_replies=[[LensProposal("angle new", "counter new")]])
    w.lens_set.add(Lens("L-09", "a9", "c9", LensStatus.HIDDEN, minted_round=0))
    w.lens_set.add(Lens("L-10", "a10", "c10", LensStatus.HIDDEN, minted_round=0))
    ps = {"L-01": lambda loss, rng: 0.4 * loss,                          # AUC 1
          "L-02": lambda loss, rng: 0.4 * rng.random() if loss < 0.8 else 0.45}   # weaker, still above floor
    w.add(2, SPEC.lens.window, ps, seed=7)
    res = w.lens_close().close(2)
    v = res.curation.values
    assert v["L-02"] is not None and v["L-02"] < v["L-01"] and v["L-02"] > res.curation.floors["L-02"]
    assert [(f.subject_id, f.cause) for f in res.flips] == [("L-02", FlipCause.LENS_CAP)]
    assert w.vcs.commits[-1][1].startswith("L-02 active → retired: lowest value ")
    assert [x.id for x in res.minted] == ["L-11"]
    live = [x for x in w.lens_set.lenses() if x.status in (LensStatus.ACTIVE, LensStatus.HIDDEN)]
    assert len(live) == SPEC.lens.cap


def test_proposal_is_dropped_when_no_lens_past_n_min_can_make_room(tmp_path):
    w = World(tmp_path, minter_replies=[[LensProposal("angle new", "counter new")]])
    w.lens_set.add(Lens("L-09", "a9", "c9", LensStatus.HIDDEN, minted_round=0))
    w.lens_set.add(Lens("L-10", "a10", "c10", LensStatus.HIDDEN, minted_round=0))
    w.add(2, SPEC.lens.window, {})                                       # no lens answered: none past n_min
    res = w.lens_close().close(2)
    assert res.minted == () and res.flips == ()


def test_minter_proposing_above_mint_max_raises(tmp_path):
    w = World(tmp_path, minter_replies=[[LensProposal(f"a{i}", f"c{i}") for i in range(SPEC.lens.mint_max + 1)]])
    w.add(2, SPEC.lens.window, {"L-01": lambda loss, rng: 0.4 * loss})
    with pytest.raises(ValueError, match="mint_max"):
        w.lens_close().close(2)


def test_rerunning_a_minting_close_mints_no_second_set_of_lenses(tmp_path):
    prop = LensProposal("Which control is off-screen at 375 px?", "Which is not?")
    w = World(tmp_path, minter_replies=[[prop], [prop]])
    w.add(2, SPEC.lens.window, {"L-01": lambda loss, rng: 0.4 * loss})
    first = w.lens_close().close(2)
    rerun = w.lens_close().close(2)          # the close of round 2 re-run after a later step raised
    assert [x.id for x in first.minted] == ["L-09"] and [x.id for x in rerun.minted] == ["L-09"]
    assert len(w.minter_calls) == 1
    assert [x.id for x in w.lens_set.lenses() if x.minted_round == 2] == ["L-09"]


def test_pass_whose_lens_call_was_dropped_never_enters_the_residual_view(tmp_path):
    w = World(tmp_path)
    w.add(1, 56, {"L-01": lambda loss, rng: 0.5 + 0.5 * loss})        # L-01 flags every answered pass
    dropped = w.add(2, 8, {}, seed=3, prefix="dropped")                # no lens answers: the call was dropped
    window, _ = w.lens_close().window_at(2)
    assert not {p.pass_id for p in window} & set(dropped)
    res = w.lens_close().close(2)
    assert res.curation.residual == () and w.minter_calls == []


def test_lens_flip_whose_lens_file_write_failed_is_not_appended_to_the_event_log_again_on_the_rerun(tmp_path):
    w = World(tmp_path)
    w.add(1, SPEC.lens.window, PREDICTIVE)
    w.lens_close().close(1)
    real = w.lens_set.flip

    def fail_once(flip, message):
        w.lens_set.flip = real
        raise RuntimeError("git commit failed")

    w.lens_set.flip = fail_once
    with pytest.raises(RuntimeError):
        w.lens_close().close(3)
    w.lens_close().close(3)
    assert [(f.subject_id, f.new) for f in w.events.flips()] == [("L-02", "retired")]
    assert next(x for x in w.lens_set.lenses() if x.id == "L-02").status is LensStatus.RETIRED
    assert len([c for c in w.vcs.commits if c[1].startswith("L-02 ")]) == 1
