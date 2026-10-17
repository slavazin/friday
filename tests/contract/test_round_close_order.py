"""The whole round close over in-memory stores (SPEC § 11): steers, lens curation, observation writes and expiry,
pooling, nomination, gate, detector and pings, in order, every flip a rendered commit."""

import dataclasses
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from friday.adapters.git import RecordingVcs
from friday.adapters.lens_md import MarkdownLensSet
from friday.adapters.memory import (
    MemoryEventLog,
    MemoryJournal,
    MemoryJudgeStore,
    MemoryLedger,
    MemoryObservationStore,
    MemoryPassStore,
    MemoryPoolStore,
    MemorySpendLog,
)
from friday.algorithms.halt import HaltReason
from friday.algorithms.recurrence import RecurrenceBar
from friday.app.render import FlipRenderer
from friday.app.round_close import build_round_close
from friday.app.wiring import flip_constants
from friday.config import load_spec
from friday.domain.events import Steer
from friday.domain.lifecycle import FlipCause, IllegalFlip
from friday.domain.types import (
    Arm,
    Composition,
    DecisionStatus,
    DeltaKind,
    Disposition,
    EvalReport,
    Fire,
    Hook,
    LedgerDelta,
    Lens,
    LensStatus,
    Observation,
    ObservationStatus,
    Pass,
    Pool,
    PoolStatus,
    Regime,
)
from friday.ports.models import Citation
from friday.roles.judge import DraftObservation
from friday.roles.lens_minter import LensMinter
from friday.roles.nominator import Draft, NoDecision, Nominator
from friday.roles.pooling import Assignment, NewPool, PoolingCall, PoolingResult
from friday.roles.prompts import PromptBook
from tests.fakes import ScriptedClient
from tests.unit.builders import dec

ROOT = Path(__file__).resolve().parents[2]
SPEC = load_spec(ROOT / "run.yml")
RENDERER = FlipRenderer.load(ROOT / "templates/flips.yml")
SHAPE = "keyboard handler bound to an element that never takes focus"
LABEL = "bind handlers on `window`"


class FastTrackBar(RecurrenceBar):
    """Scripted stand-in for the fast-track rule (built in another slice): three open observations under one
    label send a pool to the nominator in its first round."""

    def label_at_bar(self, pool, observations):
        hit = super().label_at_bar(pool, observations)
        if hit is not None or pool.status is PoolStatus.MERGED:
            return hit
        after = pool.refused_at_round if pool.status is PoolStatus.REFUSED else None
        mine = [o for o in observations if o.pool_id == pool.id and o.status is ObservationStatus.OPEN
                and (after is None or o.round > after)]
        labels = [o.direction_label for o in mine]
        top = max(set(labels), key=labels.count) if labels else None
        return top if len(mine) >= self.min_observations and labels.count(top) * 3 >= 2 * len(mine) else None


def by_shape_pooling(lens_id, pools, new):
    """Scripted pooling: an observation joins the pool of its shape, else opens one; its direction is its label."""
    known = {p.shape: p.id for p in pools}
    fresh: dict[str, NewPool] = {}
    out = []
    for o in new:
        if o.shape in known:
            out.append(Assignment(o.id, known[o.shape], o.direction))
        else:
            fresh.setdefault(o.shape, NewPool(f"k{len(fresh)}", o.shape, (o.direction,)))
            out.append(Assignment(o.id, "new:" + fresh[o.shape].key, o.direction))
    return PoolingResult(tuple(out), tuple(fresh.values()), ())


def stand_in_nominator(book):
    """A Nominator whose `draft` the test replaces; built from its declared fields, so it survives a field added
    with no default (the scripted `draft` reads none of them)."""
    given = {"client": ScriptedClient([]), "search": None, "book": book, "tag_vocabulary": frozenset()}
    return Nominator(**{f.name: given.get(f.name, ()) for f in dataclasses.fields(Nominator)})


class World:
    def __init__(self, tmp_path, bar=None, minter=None):
        shutil.copytree(ROOT / "lenses", tmp_path / "lenses")
        self.vcs = RecordingVcs()
        self.journal, self.ledger, self.events = MemoryJournal(), MemoryLedger(), MemoryEventLog()
        self.judge, self.pools, self.passes = MemoryJudgeStore(), MemoryPoolStore(), MemoryPassStore()
        self.obs, self.spend = MemoryObservationStore(), MemorySpendLog()
        self.lens_set = MarkdownLensSet(tmp_path / "lenses", self.vcs)
        book = PromptBook(ROOT / "prompts")
        self.pooling = PoolingCall(ScriptedClient([]), book, SPEC.pool.max_direction_labels, SPEC.json.reasks)
        self.pool_calls: list[tuple[str, list[str]]] = []

        def assign(lens_id, pools, new):
            self.pool_calls.append((lens_id, [o.id for o in new]))
            return by_shape_pooling(lens_id, pools, new)

        self.pooling.assign = assign
        self.nominator = stand_in_nominator(book)
        self.drafts: list[object] = []
        self.nominator_calls: list[tuple] = []

        def draft(pool, label, observations):
            self.nominator_calls.append((pool, label, list(observations)))
            nxt = self.drafts.pop(0) if self.drafts else Draft(Hook(frozenset({"canvas"})),
                                                               "Bind keyboard handlers on `window`.", ())
            return nxt

        self.nominator.draft = draft
        self.close = build_round_close(
            SPEC, journal=self.journal, ledger=self.ledger, events=self.events, judge_store=self.judge,
            pools=self.pools, passes=self.passes, observations=self.obs, lens_set=self.lens_set, spend=self.spend,
            pooling=self.pooling, nominator=self.nominator, minter=minter, renderer=RENDERER,
            bar=bar or FastTrackBar(SPEC.pool.recurrence_min_observations, SPEC.pool.recurrence_min_rounds),
        )

    def add_passes(self, round_, n, arm=Arm.JOURNAL, failed=0):
        ids = []
        for i in range(n):
            pid = f"r{round_}-{arm.value}-{i}"
            self.passes.add_pass(Pass(pid, round_, f"cat{i}", arm, "w", Composition(f"cat{i}", ()), Regime.R1,
                                      pair_slot=0 if arm is Arm.JOURNAL else None, transport_failed=i < failed))
            self.passes.add_eval(EvalReport(pid, Regime.R1, (), (), 0.5, 0.5, (), 3, 0.4))
            ids.append(pid)
        return ids

    def deltas(self, did, kind, round_, ds):
        for d in ds:
            self.ledger.append(LedgerDelta(did, round_, "cat", kind, d, 0.0))


def seed_status(journal, did, status):
    """Test setup: a drafted decision placed directly in `status` (its earlier flips are not under test)."""
    journal.draft(dec(did, DecisionStatus.CANDIDATE))
    journal._decisions[did] = replace(journal.get(did), status=status)


def obs_drafts(pass_ids, lens_id="L-03", shape=SHAPE, label=LABEL):
    return {pid: [DraftObservation(lens_id, shape, label, "canvas.addEventListener")] for pid in pass_ids}


def test_four_round_simulation_admits_suspects_and_retires_with_rendered_commits_in_order(tmp_path):
    w = World(tmp_path)
    # round 1: three observations agree -> one pool, fast-tracked, drafted as D-0001
    s1 = w.close.close(1, obs_drafts(w.add_passes(1, 3)))
    assert s1.observations == ("O-0001", "O-0002", "O-0003") and s1.pools == ("P-0001",)
    assert s1.nominations == ("D-0001",)
    d = w.journal.get("D-0001")
    assert d.status is DecisionStatus.CANDIDATE and d.pool_id == "P-0001" and d.drafted_round == 1
    assert d.warrant["observations"] == ["O-0001", "O-0002", "O-0003"] and d.warrant["lens"] == "L-03"
    assert all(o.status is ObservationStatus.CLOSED for o in w.obs.observations())
    assert w.pools.pools()[0].status is PoolStatus.NOMINATED
    # round 2: exposure pairs help -> admitted
    w.add_passes(2, 3)
    w.deltas("D-0001", DeltaKind.EXPOSURE, 2, [0.3, 0.25, 0.35, 0.3, 0.3, 0.3])
    s2 = w.close.close(2, {})
    assert [f.cause for f in s2.flips] == [FlipCause.GATE_ADMIT]
    # round 3: the planted drift -> ablation pairs show harm -> suspect, successor drafting invited
    w.add_passes(3, 3)
    w.deltas("D-0001", DeltaKind.ABLATION, 3, [-0.2, -0.25, -0.2, -0.3])
    s3 = w.close.close(3, {})
    assert [f.cause for f in s3.flips] == [FlipCause.DETECT_SUSPECT] and s3.invitations == ("D-0001",)
    # round 4: confirmation pairs hold -> retired
    w.add_passes(4, 3)
    w.deltas("D-0001", DeltaKind.ABLATION, 4, [-0.2, -0.3, -0.25])
    s4 = w.close.close(4, {}, history=[s1.summary, s2.summary, s3.summary])
    assert [f.cause for f in s4.flips] == [FlipCause.DETECT_RETIRE]
    assert w.journal.get("D-0001").status is DecisionStatus.RETIRED

    flips = w.events.flips()
    assert [(f.old, f.new, f.round) for f in flips] == [
        ("candidate", "admitted", 2), ("admitted", "suspect", 3), ("suspect", "retired", 4)]
    constants = flip_constants(SPEC)
    assert w.journal.commits == [RENDERER.render("decision", f, constants) for f in flips]
    assert w.journal.commits[0].startswith("D-0001 candidate → admitted: W ")
    assert w.journal.commits[1].startswith("D-0001 admitted → suspect: short_window")
    assert w.journal.commits[2].startswith("D-0001 suspect → retired: 3 confirmation pairs")
    assert s4.halt is None and s4.summary.suspects == 0


def test_no_decision_burns_no_id_and_minted_ids_resume_past_gaps(tmp_path):
    w = World(tmp_path)
    seed_status(w.journal, "D-0001", DecisionStatus.RETIRED)
    seed_status(w.journal, "D-0003", DecisionStatus.EXPIRED)
    w.pools.put_pool(Pool("P-0002", "L-05", "other", ("other",), PoolStatus.OPEN))
    w.obs.add_observations([Observation("O-0005", "x", 1, "L-05", "c", "other", "other", "q",
                                        ObservationStatus.CLOSED, "P-0002", "other")])
    w.drafts = [NoDecision("payload restates the baseline")]
    s1 = w.close.close(2, obs_drafts(w.add_passes(2, 3)))
    assert s1.observations == ("O-0006", "O-0007", "O-0008") and s1.pools == ("P-0003",)
    assert s1.nominations == () and [r.pool_id for r in s1.no_decisions] == ["P-0003"]
    assert {d.id for d in w.journal.decisions()} == {"D-0001", "D-0003"}
    reopened = [o for o in w.obs.observations() if o.pool_id == "P-0003"]
    assert all(o.status is ObservationStatus.OPEN for o in reopened)
    assert next(p for p in w.pools.pools() if p.id == "P-0003").status is PoolStatus.OPEN
    # next close: the same pool drafts; the id is past every id ever written, gaps included
    s2 = w.close.close(3, obs_drafts(w.add_passes(3, 1)))
    assert s2.observations == ("O-0009",) and s2.nominations == ("D-0004",)
    assert w.journal.get("D-0004").warrant["observations"] == ["O-0006", "O-0007", "O-0008", "O-0009"]
    # a third draft never reissues an id, whatever state earlier decisions are in
    w.pools.put_pool(Pool("P-0009", "L-03", "x", ("y",), PoolStatus.OPEN))
    s3 = w.close.close(4, obs_drafts(w.add_passes(4, 3), shape="x", label="y"))
    assert s3.pools == () and s3.nominations == ("D-0005",)
    assert s3.observations == ("O-0010", "O-0011", "O-0012")


def test_refused_pool_is_not_renominated_from_pre_refusal_observations(tmp_path):
    w = World(tmp_path, bar=RecurrenceBar(SPEC.pool.recurrence_min_observations, SPEC.pool.recurrence_min_rounds))
    w.pools.put_pool(Pool("P-0001", "L-03", SHAPE, (LABEL,), PoolStatus.REFUSED, refused_at_round=3))
    w.obs.add_observations([Observation(f"O-000{i}", f"old{i}", r, "L-03", "c", SHAPE, LABEL, "q",
                                        pool_id="P-0001", direction_label=LABEL)
                            for i, r in ((1, 2), (2, 3), (3, 3))])
    s4 = w.close.close(4, obs_drafts(w.add_passes(4, 2)))
    assert s4.nominations == () and w.nominator_calls == []          # 5 open, only 2 dated after the refusal
    s5 = w.close.close(5, obs_drafts(w.add_passes(5, 1)))
    assert s5.nominations == ("D-0001",)
    _, _, seen = w.nominator_calls[0]
    assert [o.id for o in seen] == ["O-0004", "O-0005", "O-0006"]   # post-refusal only
    assert w.journal.get("D-0001").warrant["observations"] == ["O-0004", "O-0005", "O-0006"]


def test_hidden_lens_observations_never_reach_a_pool(tmp_path):
    w = World(tmp_path)
    w.lens_set.add(Lens("L-09", "angle", "counter", LensStatus.HIDDEN, minted_round=0))
    for r in range(1, 4):
        w.close.close(r, obs_drafts(w.add_passes(r, 3), lens_id="L-09"))
    assert all(lens_id != "L-09" for lens_id, _ in w.pool_calls)
    assert w.pools.pools() == [] and w.nominator_calls == []
    hidden = [o for o in w.obs.observations() if o.lens_id == "L-09"]
    assert len(hidden) == 9 and all(o.pool_id is None for o in hidden)


def test_illegal_steer_raises_and_writes_nothing(tmp_path):
    w = World(tmp_path)
    for did in ("D-0001", "D-0002"):
        seed_status(w.journal, did, DecisionStatus.ADMITTED)
    w.events.append_steer(Steer("D-0002", "retire", "demo", 3))     # legal, but listed before the illegal one
    w.events.append_steer(Steer("D-0001", "veto", "demo", 3))       # a veto on an admitted decision
    with pytest.raises(IllegalFlip):
        w.close.close(3, {})
    assert w.events.flips() == [] and w.journal.commits == [] and len(w.events.pending_steers()) == 2
    assert {d.status for d in w.journal.decisions()} == {DecisionStatus.ADMITTED}


def test_retire_and_veto_steers_apply_as_one_commit_each_and_a_note_changes_no_status_and_writes_no_commit(tmp_path):
    w = World(tmp_path)
    seed_status(w.journal, "D-0001", DecisionStatus.ADMITTED)
    w.pools.put_pool(Pool("P-0001", "L-03", SHAPE, (LABEL,), PoolStatus.NOMINATED))
    w.journal.draft(dec("D-0002", DecisionStatus.CANDIDATE))
    w.events.append_steer(Steer("D-0001", "note", "watch the phone layout", 2))
    w.events.append_steer(Steer("D-0001", "retire", "demo retire", 2))
    w.events.append_steer(Steer("D-0002", "veto", "demo veto", 2))
    s = w.close.close(2, {})
    assert w.journal.commits == ['D-0001 admitted → retired: steer, "demo retire"',
                                 'D-0002 candidate → refused: steer veto, "demo veto"']
    assert [x.reason for x in s.steers.notes] == ["watch the phone layout"]
    assert w.events.pending_steers() == []
    assert w.pools.pools()[0].status is PoolStatus.REFUSED and w.pools.pools()[0].refused_at_round == 2


def test_successor_drafted_from_a_pool_opened_since_suspicion_supersedes_without_seeing_suspect(tmp_path):
    w = World(tmp_path)
    w.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    w.deltas("D-0001", DeltaKind.EXPOSURE, 1, [0.3, 0.25, 0.35, 0.3, 0.3, 0.3])
    w.close.close(1, {})
    w.add_passes(2, 1)
    w.deltas("D-0001", DeltaKind.ABLATION, 2, [-0.2, -0.25, -0.2, -0.3])
    w.judge.add_fires([Fire(f"f{i}", "D-0001", 2, Disposition.CONTRADICTED, "q") for i in range(2)])
    s2 = w.close.close(2, {})
    assert w.journal.get("D-0001").status is DecisionStatus.SUSPECT
    assert any("contradicted on 2 of its last 2" in p.reason for p in s2.pings)
    s3 = w.close.close(3, obs_drafts(w.add_passes(3, 3), lens_id="L-08", shape="control off-screen at 375 px"))
    assert s3.nominations == ("D-0002",)
    assert w.journal.get("D-0002").supersedes == ("D-0001",)
    pool, label, seen = w.nominator_calls[-1]
    shown = repr((pool, label, seen)).lower()
    assert "suspect" not in shown and "d-0001" not in shown and "ablation" not in shown


def test_newly_suspect_decision_invites_a_successor_once_the_gate_frees_room(tmp_path):
    w = World(tmp_path)
    w.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    w.deltas("D-0001", DeltaKind.EXPOSURE, 1, [0.3, 0.25, 0.35, 0.3, 0.3, 0.3])
    w.close.close(1, {})
    w.pools.put_pool(Pool("P-0001", "L-01", "earlier", ("earlier",), PoolStatus.NOMINATED))
    for did in ("D-0002", "D-0003"):                       # two candidates fill gate.in_flight
        w.journal.draft(dec(did, DecisionStatus.CANDIDATE, drafted_round=1))
    w.deltas("D-0002", DeltaKind.EXPOSURE, 2, [-0.5] * 6)  # refused at the gate this close
    w.deltas("D-0001", DeltaKind.ABLATION, 2, [-0.2, -0.25, -0.2, -0.3])
    s = w.close.close(2, obs_drafts(w.add_passes(2, 3), lens_id="L-08", shape="control off-screen at 375 px"))
    assert s.invitations == ("D-0001",) and s.nominations == ("D-0004",)
    assert w.journal.get("D-0004").supersedes == ("D-0001",)
    causes = [f.cause for f in s.flips]
    assert causes == [FlipCause.GATE_REFUSE, FlipCause.DETECT_SUSPECT]


def test_pooling_result_is_validated_before_any_write(tmp_path):
    w = World(tmp_path)

    def bad(lens_id, pools, new):
        return PoolingResult((Assignment(new[0].id, "new:k0", "a label the pool lacks"),),
                             (NewPool("k0", "shape", ("bind on window",)),), ())

    w.pooling.assign = bad
    with pytest.raises(ValueError, match="not one of"):
        w.close.close(1, obs_drafts(w.add_passes(1, 1)))
    assert w.pools.pools() == [] and all(o.pool_id is None for o in w.obs.observations())


def test_merge_keeps_the_lower_id_and_moves_open_observations(tmp_path):
    w = World(tmp_path)
    w.pools.put_pool(Pool("P-0001", "L-03", "a", ("x",)))
    w.pools.put_pool(Pool("P-0002", "L-03", "b", ("x",)))
    w.obs.add_observations([Observation("O-0001", "p", 1, "L-03", "c", "b", "x", "q", pool_id="P-0002",
                                        direction_label="x")])

    def merge(lens_id, pools, new):
        return PoolingResult((Assignment(new[0].id, "P-0001", "x"),), (), (("P-0001", "P-0002"),))

    w.pooling.assign = merge
    w.close.close(1, obs_drafts(w.add_passes(1, 1)))
    gone = next(p for p in w.pools.pools() if p.id == "P-0002")
    assert gone.status is PoolStatus.MERGED and gone.merged_into == "P-0001"
    assert {o.pool_id for o in w.obs.observations()} == {"P-0001"}


def test_open_observations_expire_after_expiry_rounds(tmp_path):
    w = World(tmp_path)
    w.lens_set.add(Lens("L-09", "angle", "counter", LensStatus.HIDDEN, minted_round=0))
    w.close.close(1, obs_drafts(w.add_passes(1, 1), lens_id="L-09"))
    s = w.close.close(1 + SPEC.pool.expiry_rounds, {})
    assert s.expired == ()
    s = w.close.close(2 + SPEC.pool.expiry_rounds, {})
    assert s.expired == ("O-0001",) and w.obs.observations()[0].status is ObservationStatus.CLOSED


def test_observations_from_a_notes_arm_pass_raise(tmp_path):
    w = World(tmp_path)
    ids = w.add_passes(1, 1, arm=Arm.NOTES)
    with pytest.raises(ValueError, match="journal-arm"):
        w.close.close(1, obs_drafts(ids))
    assert w.obs.observations() == []


def test_transport_and_halt_pings(tmp_path):
    w = World(tmp_path)
    w.add_passes(SPEC.run.rounds, 3, failed=2)
    s = w.close.close(SPEC.run.rounds, {})
    assert s.halt is HaltReason.ROUNDS
    reasons = [p.reason for p in s.pings]
    assert reasons == [f"round {SPEC.run.rounds}: 2 of 3 passes failed for transport after re-runs, above the "
                       f"share {SPEC.run.ping_transport_share:.2f}", f"run halts after round {SPEC.run.rounds}: rounds"]


def test_a_successful_rerun_clears_the_transport_failure(tmp_path):
    w = World(tmp_path)
    w.add_passes(1, 3, failed=2)
    for i in range(2):
        w.passes.add_pass(Pass(f"rerun{i}", 1, f"cat{i}", Arm.JOURNAL, "w", Composition(f"cat{i}", ()), Regime.R1,
                               pair_slot=0))
    assert w.close.close(1, {}).pings == ()


def test_nominator_sources_land_in_the_warrant(tmp_path):
    w = World(tmp_path)
    w.drafts = [Draft(Hook(frozenset({"canvas"})), "Bind keyboard handlers on `window`.",
                      (Citation("https://example.org/focus", "t", "s"),))]
    w.close.close(1, obs_drafts(w.add_passes(1, 3)))
    assert w.journal.get("D-0001").warrant["sources"] == ["https://example.org/focus"]


def test_merged_pool_takes_both_pools_labels_so_the_nominator_gets_a_label_of_its_pool(tmp_path):
    w = World(tmp_path, bar=RecurrenceBar(SPEC.pool.recurrence_min_observations, SPEC.pool.recurrence_min_rounds))
    w.pools.put_pool(Pool("P-0001", "L-03", "a", ("x",)))
    w.pools.put_pool(Pool("P-0002", "L-03", "b", ("y",)))
    w.obs.add_observations([Observation(f"O-000{i}", f"p{i}", r, "L-03", "c", "b", "y", "q", pool_id="P-0002",
                                        direction_label="y") for i, r in ((1, 1), (2, 2))])

    def merge(lens_id, pools, new):
        return PoolingResult((Assignment(new[0].id, "P-0002", "y"),), (), (("P-0001", "P-0002"),))

    w.pooling.assign = merge
    s = w.close.close(3, obs_drafts(w.add_passes(3, 1), shape="b", label="y"))
    keep = next(p for p in w.pools.pools() if p.id == "P-0001")
    assert keep.direction_labels == ("x", "y")
    pool, label, _ = w.nominator_calls[0]
    assert pool.id == "P-0001" and label in pool.direction_labels and s.nominations == ("D-0001",)
    assert all(o.direction_label in keep.direction_labels for o in w.obs.observations() if o.pool_id == "P-0001")


def test_merge_whose_label_union_exceeds_max_direction_labels_is_rejected_before_any_write(tmp_path):
    w = World(tmp_path)
    n = SPEC.pool.max_direction_labels
    w.pools.put_pool(Pool("P-0001", "L-03", "a", tuple(f"a{i}" for i in range(n))))
    w.pools.put_pool(Pool("P-0002", "L-03", "b", ("b0",)))

    def merge(lens_id, pools, new):
        return PoolingResult((Assignment(new[0].id, "P-0001", "a0"),), (), (("P-0001", "P-0002"),))

    w.pooling.assign = merge
    with pytest.raises(ValueError, match="max_direction_labels"):
        w.close.close(1, obs_drafts(w.add_passes(1, 1)))
    assert {p.id: p.status for p in w.pools.pools()} == {"P-0001": PoolStatus.OPEN, "P-0002": PoolStatus.OPEN}
    assert all(o.pool_id is None for o in w.obs.observations())


def test_successor_from_a_pool_opened_since_suspicion_with_a_disjoint_hook_supersedes_nothing(tmp_path):
    w = World(tmp_path)
    w.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE))                  # hook fires on "canvas"
    w.deltas("D-0001", DeltaKind.EXPOSURE, 1, [0.3, 0.25, 0.35, 0.3, 0.3, 0.3])
    w.close.close(1, {})
    w.deltas("D-0001", DeltaKind.ABLATION, 2, [-0.2, -0.25, -0.2, -0.3])
    w.close.close(2, {})
    assert w.journal.get("D-0001").status is DecisionStatus.SUSPECT
    w.drafts = [Draft(Hook(frozenset({"form"})), "Keep every control on-screen at 375 px.", ())]
    s3 = w.close.close(3, obs_drafts(w.add_passes(3, 3), lens_id="L-08", shape="control off-screen at 375 px"))
    assert s3.nominations == ("D-0002",) and w.journal.get("D-0002").supersedes == ()


def test_rerunning_a_close_after_a_mid_close_failure_writes_no_duplicate_observations(tmp_path):
    w = World(tmp_path)
    calls = {"n": 0}

    def flaky(lens_id, pools, new):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("pooling call failed after its re-ask")
        return by_shape_pooling(lens_id, pools, new)

    w.pooling.assign = flaky
    drafts = obs_drafts(w.add_passes(1, 3))
    with pytest.raises(RuntimeError):
        w.close.close(1, drafts)
    s = w.close.close(1, drafts)
    assert sorted(o.id for o in w.obs.observations()) == ["O-0001", "O-0002", "O-0003"]
    assert s.observations == () and s.pools == ("P-0001",) and s.nominations == ("D-0001",)
    assert {o.pool_id for o in w.obs.observations()} == {"P-0001"}


def test_rerunning_a_close_after_a_steer_flip_landed_unmarked_applies_it_once(tmp_path):
    w = World(tmp_path)
    seed_status(w.journal, "D-0001", DecisionStatus.ADMITTED)
    w.events.append_steer(Steer("D-0001", "retire", "demo retire", 2))
    real = w.events.mark_steer_applied

    def fail_once(steer, round_):
        w.events.mark_steer_applied = real
        raise RuntimeError("event log write failed")

    w.events.mark_steer_applied = fail_once
    with pytest.raises(RuntimeError):
        w.close.close(2, {})
    w.close.close(2, {})
    assert w.events.pending_steers() == [] and len(w.journal.commits) == 1
    assert w.journal.get("D-0001").status is DecisionStatus.RETIRED


def test_build_round_close_with_a_minter_and_no_residual_source_raises(tmp_path):
    w = World(tmp_path)
    minter = LensMinter(ScriptedClient([]), PromptBook(ROOT / "prompts"))
    with pytest.raises(ValueError, match="residual source"):
        build_round_close(
            SPEC, journal=w.journal, ledger=w.ledger, events=w.events, judge_store=w.judge, pools=w.pools,
            passes=w.passes, observations=w.obs, lens_set=w.lens_set, spend=w.spend, pooling=w.pooling,
            nominator=w.nominator, minter=minter, renderer=RENDERER)


def _fail_journal_flip_once(w):
    real = w.journal.flip

    def fail_once(flip, message):
        w.journal.flip = real
        raise RuntimeError("git commit failed")

    w.journal.flip = fail_once


def test_rerun_after_a_steer_flip_reached_the_event_log_but_the_journal_write_failed_writes_the_journal_once(
        tmp_path):
    w = World(tmp_path)
    seed_status(w.journal, "D-0001", DecisionStatus.ADMITTED)
    w.events.append_steer(Steer("D-0001", "retire", "demo retire", 2))
    _fail_journal_flip_once(w)
    with pytest.raises(RuntimeError):
        w.close.close(2, {})
    assert w.journal.get("D-0001").status is DecisionStatus.ADMITTED and len(w.events.flips()) == 1
    w.close.close(2, {})
    assert [(f.subject_id, f.old, f.new, f.cause) for f in w.events.flips()] == [
        ("D-0001", "admitted", "retired", FlipCause.STEER_RETIRE)]
    assert w.journal.get("D-0001").status is DecisionStatus.RETIRED
    assert w.journal.commits == ['D-0001 admitted → retired: steer, "demo retire"']
    assert w.events.pending_steers() == []


def test_gate_flip_whose_journal_write_failed_is_not_appended_to_the_event_log_again_on_the_rerun(tmp_path):
    w = World(tmp_path)
    w.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    w.deltas("D-0001", DeltaKind.EXPOSURE, 1, [0.3, 0.25, 0.35, 0.3, 0.3, 0.3])
    _fail_journal_flip_once(w)
    with pytest.raises(RuntimeError):
        w.close.close(1, {})
    w.close.close(1, {})
    assert [(f.subject_id, f.old, f.new, f.round) for f in w.events.flips()] == [
        ("D-0001", "candidate", "admitted", 1)]
    assert w.journal.get("D-0001").status is DecisionStatus.ADMITTED and len(w.journal.commits) == 1


def test_assignment_to_a_merge_survivor_is_validated_against_its_post_merge_label_union(tmp_path):
    w = World(tmp_path)
    w.pools.put_pool(Pool("P-0001", "L-03", "a", ("x",)))
    w.pools.put_pool(Pool("P-0002", "L-03", "b", ("y",)))
    w.pooling.assign = lambda lens_id, pools, new: PoolingResult(
        (Assignment(new[0].id, "P-0001", "y"),), (), (("P-0001", "P-0002"),))
    w.close.close(1, obs_drafts(w.add_passes(1, 1), shape="b", label="y"))
    pools = {p.id: p for p in w.pools.pools()}
    assert pools["P-0001"].direction_labels == ("x", "y") and pools["P-0002"].merged_into == "P-0001"
    (o,) = w.obs.observations()
    assert o.pool_id == "P-0001" and o.direction_label == "y"


def test_assignment_label_outside_the_post_merge_union_still_raises_before_any_write(tmp_path):
    w = World(tmp_path)
    w.pools.put_pool(Pool("P-0001", "L-03", "a", ("x",)))
    w.pools.put_pool(Pool("P-0002", "L-03", "b", ("y",)))
    w.pooling.assign = lambda lens_id, pools, new: PoolingResult(
        (Assignment(new[0].id, "P-0001", "z"),), (), (("P-0001", "P-0002"),))
    with pytest.raises(ValueError, match="label 'z'"):
        w.close.close(1, obs_drafts(w.add_passes(1, 1), shape="b", label="z"))
    assert {p.id: p.direction_labels for p in w.pools.pools()} == {"P-0001": ("x",), "P-0002": ("y",)}
