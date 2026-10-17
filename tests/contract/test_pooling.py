"""The pooling call (SPEC § 7): every observation once, labels from the pool, no split, no unknown id."""

import json
from pathlib import Path

import pytest

from friday.domain.ids import IdMinter
from friday.domain.types import Observation, ObservationStatus, Pool, PoolStatus
from friday.roles.ask import Rejected
from friday.roles.pooling import PoolingCall, PoolingFailed, apply_pooling, validate_pooling
from friday.roles.prompts import PromptBook
from tests.fakes import ScriptedClient

ROOT = Path(__file__).resolve().parents[2]
BOOK = PromptBook(ROOT / "prompts")

P1 = Pool("P-0001", "L-03", "keyboard handler bound to an element that never takes focus",
          ("bind handlers on window",))
P2 = Pool("P-0002", "L-03", "countdown driven by interval tick counting", ("derive time from Date.now()",))
P3 = Pool("P-0003", "L-03", "timer derived from tick counts", ("derive time from Date.now()",))
PR = Pool("P-0004", "L-03", "controls placed below the fold", ("place controls first",), PoolStatus.REFUSED,
          refused_at_round=3)
POOLS = [P1, P2, P3, PR]


def o(i, pool=None, label=None, rnd=5, status=ObservationStatus.OPEN):
    return Observation(f"O-{i:04d}", f"x{i}", rnd, "L-03", "cat", f"shape {i}", f"dir {i}", "q", status, pool, label)


NEW = [o(11), o(12), o(13)]


def reply(assignments, new_pools=(), merges=()):
    return {"assignments": list(assignments), "new_pools": list(new_pools), "merges": list(merges)}


def a(oid, pool, label):
    return {"observation": oid, "pool": pool, "label": label}


GOOD = reply(
    [a("O-0011", "P-0001", "bind handlers on window"), a("O-0012", "new:k", "call focus() on start"),
     a("O-0013", "P-0004", "place controls first")],
    [{"key": "k", "shape": "canvas that never receives focus", "labels": ["call focus() on start"]}],
    [{"keep": "P-0002", "merge": "P-0003"}],
)


def v(raw, pools=POOLS, new=NEW):
    return validate_pooling(raw, pools, new, 2)


def test_a_valid_reply_routes_to_existing_new_and_refused_pools():
    got = v(GOOD)
    assert [x.pool_ref for x in got.assignments] == ["P-0001", "new:k", "P-0004"]
    assert got.merges == (("P-0002", "P-0003"),)


def test_a_reply_that_drops_an_observation_is_rejected():
    with pytest.raises(Rejected, match="not assigned"):
        v(reply(GOOD["assignments"][:2], GOOD["new_pools"]))


def test_an_observation_assigned_twice_is_rejected():
    dup = [*GOOD["assignments"], a("O-0011", "P-0002", "derive time from Date.now()")]
    with pytest.raises(Rejected, match="twice"):
        v(reply(dup, GOOD["new_pools"]))


def test_a_reply_that_splits_a_pool_is_rejected():
    with pytest.raises(Rejected, match="never splits"):
        v({**GOOD, "splits": [{"pool": "P-0001", "into": ["new:x", "new:y"]}]})
    # moving an earlier observation of P-0001 into a new pool is a split by another name
    old = o(2, "P-0001", "bind handlers on window", rnd=2)
    moved = [*GOOD["assignments"], a(old.id, "new:k", "call focus() on start")]
    with pytest.raises(Rejected, match="earlier ones never move"):
        v(reply(moved, GOOD["new_pools"]))


def test_reassigning_a_closed_observation_is_rejected():
    closed = o(3, "P-0001", "bind handlers on window", status=ObservationStatus.CLOSED)
    with pytest.raises(Rejected, match="never move"):
        v(reply([*GOOD["assignments"], a(closed.id, "P-0002", "derive time from Date.now()")], GOOD["new_pools"]))


def test_a_label_outside_the_pools_labels_is_rejected():
    bad = [a("O-0011", "P-0001", "bind handlers on the window object"), *GOOD["assignments"][1:]]
    with pytest.raises(Rejected, match="not one of P-0001's labels"):
        v(reply(bad, GOOD["new_pools"]))


def test_a_reply_cannot_rename_a_pool_or_add_a_label():
    with pytest.raises(Rejected, match="unknown keys"):
        v({**GOOD, "pools": [{"id": "P-0001", "shape": "renamed", "labels": ["a", "b", "c"]}]})
    with pytest.raises(Rejected, match="key, shape and labels only"):
        v(reply(GOOD["assignments"], [{**GOOD["new_pools"][0], "id": "P-0001"}]))


def test_a_new_pool_over_the_label_cap_is_rejected():
    three = [{"key": "k", "shape": "canvas that never receives focus", "labels": ["x", "y", "call focus() on start"]}]
    with pytest.raises(Rejected, match="1 to 2 labels"):
        v(reply(GOOD["assignments"], three))


def test_a_new_pool_twinning_a_refused_shape_is_rejected():
    twin = [{"key": "k", "shape": "Controls placed  below the fold", "labels": ["call focus() on start"]}]
    with pytest.raises(Rejected, match="repeats the shape of P-0004"):
        v(reply(GOOD["assignments"], twin))


def test_unknown_pool_and_unknown_new_key_are_rejected():
    with pytest.raises(Rejected, match="unknown pool P-0099"):
        v(reply([a("O-0011", "P-0099", "x"), *GOOD["assignments"][1:]], GOOD["new_pools"]))
    with pytest.raises(Rejected, match="names no entry"):
        v(reply([a("O-0011", "new:zz", "x"), *GOOD["assignments"][1:]], GOOD["new_pools"]))


def test_a_new_pool_with_no_observation_is_rejected():
    extra = [*GOOD["new_pools"], {"key": "e", "shape": "an empty idea", "labels": ["do it"]}]
    with pytest.raises(Rejected, match="no observation"):
        v(reply(GOOD["assignments"], extra))


def test_merges_keep_the_lower_id_and_only_open_pools():
    with pytest.raises(Rejected, match="lower id survives"):
        v(reply(GOOD["assignments"], GOOD["new_pools"], [{"keep": "P-0003", "merge": "P-0002"}]))
    with pytest.raises(Rejected, match="only open pools merge"):
        v(reply(GOOD["assignments"], GOOD["new_pools"], [{"keep": "P-0001", "merge": "P-0004"}]))
    with pytest.raises(Rejected, match="at most one merge"):
        v(reply(GOOD["assignments"], GOOD["new_pools"],
                [{"keep": "P-0001", "merge": "P-0002"}, {"keep": "P-0002", "merge": "P-0003"}]))
    to_gone = [*GOOD["assignments"][:2], a("O-0013", "P-0003", "derive time from Date.now()")]
    with pytest.raises(Rejected, match="merged in this reply"):
        v(reply(to_gone, GOOD["new_pools"], GOOD["merges"]))


def call(script):
    client = ScriptedClient(script)
    return client, PoolingCall(client, BOOK, max_direction_labels=2, reasks=1)


def test_the_call_reasks_once_then_raises():
    bad = json.dumps(reply(GOOD["assignments"][:1]))
    client, pc = call([bad, bad])
    with pytest.raises(PoolingFailed) as err:
        pc.assign("L-03", POOLS, NEW)
    assert len(client.calls) == 2 and len(err.value.failures) == 2
    assert "not assigned" in client.calls[1][1][-1].content


def test_the_call_recovers_on_the_reask():
    client, pc = call(["{not json", json.dumps(GOOD)])
    got = pc.assign("L-03", POOLS, NEW)
    assert len(got.assignments) == 3 and client.calls[0][0] == "pooling"


def test_the_prompt_lists_pools_refused_pools_and_new_observations_without_rounds_or_categories():
    client, pc = call([json.dumps(GOOD)])
    pc.assign("L-03", POOLS, NEW)
    user = client.calls[0][1][1].content
    live, refused = user.split("## Closed-off pools")
    assert "P-0001" in live and "P-0004" not in live and "P-0004" in refused
    assert "O-0011" in user and "shape 11" in user
    assert '"cat"' not in user and "round" not in user


def test_empty_new_makes_no_call_and_stray_lenses_raise():
    client, pc = call([])
    assert pc.assign("L-03", POOLS, []).assignments == ()
    assert client.calls == []
    with pytest.raises(ValueError, match="not under lens"):
        pc.assign("L-07", POOLS, NEW)


def test_apply_mints_ids_in_the_runner_and_moves_merged_open_observations():
    result = v(GOOD)
    existing = [o(5, "P-0003", "derive time from Date.now()", rnd=4),
                o(6, "P-0003", "derive time from Date.now()", rnd=1, status=ObservationStatus.CLOSED)]
    minter = IdMinter.resume("pool", [p.id for p in POOLS])
    got = apply_pooling(result, "L-03", POOLS, NEW, existing, minter)
    by_id = {p.id: p for p in got.pools}
    assert by_id["P-0005"].shape == "canvas that never receives focus"
    assert by_id["P-0003"].status is PoolStatus.MERGED and by_id["P-0003"].merged_into == "P-0002"
    obs = {x.id: x for x in got.observations}
    assert obs["O-0012"].pool_id == "P-0005" and obs["O-0013"].pool_id == "P-0004"
    assert obs["O-0005"].pool_id == "P-0002" and "O-0006" not in obs   # closed history stays put
    assert obs["O-0013"].status is ObservationStatus.OPEN               # routed to a refused pool, stays open
