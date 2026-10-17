import pytest

from friday.algorithms.composition import compose_pair, select_composed
from friday.algorithms.exposure import PairKind, assign_exposures
from friday.domain.hooks import fires, reach
from friday.domain.types import DecisionStatus, Hook

from .builders import dec, req


def test_hook_fires_on_any_fires_when_tag_unless_a_not_this_tag():
    h = Hook(frozenset({"canvas", "keyboard"}), frozenset({"form"}))
    assert fires(h, {"canvas"})
    assert not fires(h, {"canvas", "form"})
    assert not fires(h, {"timer"})
    assert reach(h, [req("a", "canvas"), req("b", "timer"), req("c", "keyboard")]) == ("a", "c")


def test_select_composed_respects_status_hook_and_cap():
    ds = [dec("D-0001"), dec("D-0002"), dec("D-0003"), dec("D-0004", DecisionStatus.CANDIDATE),
          dec("D-0005", DecisionStatus.RETIRED), dec("D-0006", fires=("timer",)), dec("D-0007", DecisionStatus.SUSPECT)]
    rank = {"D-0001": 9.0, "D-0002": 1.0, "D-0003": 5.0, "D-0007": 7.0}
    assert select_composed(req("r", "canvas"), ds, rank, cap=3) == ("D-0001", "D-0003", "D-0007")
    assert select_composed(req("r", "canvas"), ds, rank, cap=10) == ("D-0001", "D-0002", "D-0003", "D-0007")


def test_cap_ties_drop_the_higher_id():
    ds = [dec("D-0001"), dec("D-0002")]
    assert select_composed(req("r", "canvas"), ds, {}, cap=1) == ("D-0001",)


def test_pairs_differ_by_exactly_one_decision():
    r = req("r", "canvas")
    a, b = compose_pair(r, ("D-0001",), exposed="D-0009", treated_slot=1)
    assert a.decision_ids == ("D-0001",) and b.decision_ids == ("D-0001", "D-0009") and b.exposed == "D-0009"
    a, b = compose_pair(r, ("D-0001", "D-0002"), ablated="D-0002", treated_slot=0)
    assert a.decision_ids == ("D-0001", "D-0002") and b.decision_ids == ("D-0001",) and b.ablated == "D-0002"
    a, b = compose_pair(r, ("D-0001",))
    assert a == b


def test_pair_rejects_two_exposures_and_absent_ablation():
    with pytest.raises(ValueError):
        compose_pair(req("r"), ("D-0001",), exposed="D-0002", ablated="D-0001")
    with pytest.raises(ValueError):
        compose_pair(req("r"), ("D-0001",), ablated="D-0003")


def test_candidate_beats_ablation_and_fewest_pairs_wins():
    reqs = [req("a", "canvas"), req("b", "canvas"), req("c", "timer")]
    c1 = dec("D-0010", DecisionStatus.CANDIDATE)
    c2 = dec("D-0011", DecisionStatus.CANDIDATE)
    plans = assign_exposures(1, reqs, [c1, c2], {"c": ("D-0001",)}, {"D-0010": 0, "D-0011": 0}, {}, 42, 1.0)
    by = {p.category: p for p in plans}
    assert by["a"].kind is PairKind.EXPOSURE and by["a"].decision_id == "D-0010"
    assert by["b"].decision_id == "D-0011"          # counts update within the round
    assert by["c"].kind is PairKind.ABLATION and by["c"].decision_id == "D-0001"


def test_ablation_probability_zero_leaves_pairs_unexposed():
    plans = assign_exposures(1, [req("a", "canvas")], [], {"a": ("D-0001",)}, {}, {}, 42, 0.0)
    assert plans[0].kind is PairKind.NONE


def test_ablation_picks_the_decision_with_fewest_ablations():
    plans = assign_exposures(1, [req("a")], [], {"a": ("D-0001", "D-0002")}, {}, {"D-0001": 3}, 42, 1.0)
    assert plans[0].decision_id == "D-0002"


def test_assignment_is_a_pure_function_of_seed_and_keys():
    args = ([req("a", "canvas"), req("b", "canvas")], [dec("D-0010", DecisionStatus.CANDIDATE)], {}, {}, {}, 7, 0.75)
    assert assign_exposures(3, *args) == assign_exposures(3, *args)
    slots = {assign_exposures(r, *args)[0].treated_slot for r in range(1, 30)}
    assert slots == {0, 1}
