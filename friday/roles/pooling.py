"""The pooling call (SPEC § 7): assigns a lens's new observations to stable pools. No embeddings.

The model routes and labels; it never mints an id, renames a pool, adds a label to an existing pool, splits a
pool or touches an observation outside the round's new ones. The runner mints `P-` ids (`apply_pooling`).
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from friday.domain.ids import IdMinter
from friday.domain.types import Observation, ObservationStatus, Pool, PoolStatus
from friday.ports.models import Message, ModelClient
from friday.roles.ask import Rejected, ask_json
from friday.roles.prompts import PromptBook

ROLE = "pooling"
NEW = "new:"
_TOP_KEYS = {"assignments", "new_pools", "merges"}


@dataclass(frozen=True)
class Assignment:
    observation_id: str
    pool_ref: str          # an existing pool id, or "new:<k>" naming an entry of `new_pools`
    direction_label: str


@dataclass(frozen=True)
class NewPool:
    key: str
    shape: str
    direction_labels: tuple[str, ...]


@dataclass(frozen=True)
class PoolingResult:
    assignments: tuple[Assignment, ...]
    new_pools: tuple[NewPool, ...]
    merges: tuple[tuple[str, str], ...]   # (surviving lower id, merged id)


class PoolingFailed(RuntimeError):
    """The pooling reply stayed invalid after its re-ask; the round close must not guess an assignment."""

    def __init__(self, lens_id: str, failures: Sequence[str]) -> None:
        super().__init__(f"pooling call for {lens_id} failed: {'; '.join(failures)}")
        self.lens_id = lens_id
        self.failures = tuple(failures)


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Rejected(f"{what} must be a non-empty string")
    return value.strip()


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def validate_pooling(
    raw: Any, pools: Sequence[Pool], new: Sequence[Observation], max_direction_labels: int,
) -> PoolingResult:
    """The PoolingResult `raw` encodes; raises Rejected on any reply outside the pooling form.

    `pools` are the lens's live pools (open, nominated and refused). Every new observation is assigned
    exactly once, to a live pool or a new pool of this reply, under one of that pool's labels.
    """
    if not isinstance(raw, dict):
        raise Rejected("expected an object with assignments, new_pools and merges")
    extra = set(raw) - _TOP_KEYS
    if extra:
        raise Rejected(f"unknown keys {sorted(extra)}: a reply only assigns, mints new pools and merges; "
                       "it never splits a pool or edits an existing pool")
    by_id = {p.id: p for p in pools}

    new_raw = raw.get("new_pools", [])
    if not isinstance(new_raw, list):
        raise Rejected("new_pools must be a list")
    new_pools: dict[str, NewPool] = {}
    known_shapes = {_norm(p.shape): p.id for p in pools}
    for item in new_raw:
        if not isinstance(item, dict) or set(item) - {"key", "shape", "labels"}:
            raise Rejected("each new pool is an object with key, shape and labels only")
        key = _text(item.get("key"), "a new pool key")
        if key in new_pools:
            raise Rejected(f"new pool key {key!r} is used twice")
        shape = _text(item.get("shape"), f"new pool {key} shape")
        if _norm(shape) in known_shapes:
            raise Rejected(f"new pool {key} repeats the shape of {known_shapes[_norm(shape)]}; assign to it")
        known_shapes[_norm(shape)] = f"new pool {key}"
        labels_raw = item.get("labels")
        if not isinstance(labels_raw, list) or not 1 <= len(labels_raw) <= max_direction_labels:
            raise Rejected(f"new pool {key} needs 1 to {max_direction_labels} labels")
        labels = tuple(_text(x, f"a label of new pool {key}") for x in labels_raw)
        if len({_norm(x) for x in labels}) != len(labels):
            raise Rejected(f"new pool {key} repeats a label")
        new_pools[key] = NewPool(key, shape, labels)

    merges_raw = raw.get("merges", [])
    if not isinstance(merges_raw, list):
        raise Rejected("merges must be a list")
    merges: list[tuple[str, str]] = []
    touched: set[str] = set()
    for item in merges_raw:
        if not isinstance(item, dict) or set(item) != {"keep", "merge"}:
            raise Rejected("each merge is an object with keep and merge")
        keep, gone = str(item["keep"]), str(item["merge"])
        for pid in (keep, gone):
            if pid not in by_id:
                raise Rejected(f"merge names unknown pool {pid}")
            if by_id[pid].status is not PoolStatus.OPEN:
                raise Rejected(f"{pid} is {by_id[pid].status.value}; only open pools merge")
        if keep == gone or keep in touched or gone in touched:
            raise Rejected("a pool takes part in at most one merge per reply")
        if keep > gone:
            raise Rejected(f"the lower id survives a merge: keep {gone}, merge {keep}")
        if len(set(by_id[keep].direction_labels) | set(by_id[gone].direction_labels)) > max_direction_labels:
            raise Rejected(f"merging {gone} into {keep} would exceed {max_direction_labels} labels")
        touched |= {keep, gone}
        merges.append((keep, gone))
    merged_away = {g for _, g in merges}

    asg_raw = raw.get("assignments")
    if not isinstance(asg_raw, list):
        raise Rejected("assignments must be a list")
    wanted = {o.id for o in new}
    seen: set[str] = set()
    assignments: list[Assignment] = []
    used_new: set[str] = set()
    for item in asg_raw:
        if not isinstance(item, dict) or set(item) != {"observation", "pool", "label"}:
            raise Rejected("each assignment is an object with observation, pool and label")
        oid, ref = str(item["observation"]), str(item["pool"])
        label = _text(item["label"], f"the label of {oid}")
        if oid not in wanted:
            raise Rejected(f"{oid} is not one of this round's new observations; earlier ones never move")
        if oid in seen:
            raise Rejected(f"{oid} is assigned twice")
        seen.add(oid)
        if ref.startswith(NEW):
            target = new_pools.get(ref[len(NEW):])
            if target is None:
                raise Rejected(f"{oid}: {ref} names no entry of new_pools")
            used_new.add(target.key)
            labels = target.direction_labels
        elif ref in by_id:
            if ref in merged_away:
                raise Rejected(f"{oid}: {ref} is merged in this reply; assign to the surviving pool")
            labels = by_id[ref].direction_labels
        else:
            raise Rejected(f"{oid}: unknown pool {ref}")
        if label not in labels:
            raise Rejected(f"{oid}: label {label!r} is not one of {ref}'s labels {list(labels)}")
        assignments.append(Assignment(oid, ref, label))
    missing = sorted(wanted - seen)
    if missing:
        raise Rejected(f"observations not assigned: {missing}")
    unused = sorted(set(new_pools) - used_new)
    if unused:
        raise Rejected(f"new pools with no observation: {unused}")
    return PoolingResult(tuple(assignments), tuple(new_pools.values()), tuple(merges))


def _pool_row(p: Pool) -> dict[str, Any]:
    return {"id": p.id, "shape": p.shape, "labels": list(p.direction_labels)}


def render_pooling(book: PromptBook, pools: Sequence[Pool], new: Sequence[Observation],
                   max_direction_labels: int) -> list[Message]:
    """The pooling call's messages: the template with the lens's pools, refused pools and new observations."""
    live = [_pool_row(p) for p in pools if p.status is not PoolStatus.REFUSED]
    refused = [_pool_row(p) for p in pools if p.status is PoolStatus.REFUSED]
    obs = [{"id": o.id, "shape": o.shape, "direction": o.direction} for o in new]
    user = (book.text("pooling/user.md")
            .replace("{{pools}}", json.dumps(live, indent=1))
            .replace("{{refused}}", json.dumps(refused, indent=1))
            .replace("{{observations}}", json.dumps(obs, indent=1)))
    system = book.text("pooling/system.md").replace("{{max_labels}}", str(max_direction_labels))
    return [Message("system", system), Message("user", user)]


@dataclass
class PoolingCall:
    client: ModelClient
    book: PromptBook
    max_direction_labels: int     # run.yml pool.max_direction_labels
    reasks: int                   # run.yml json.reasks

    def assign(self, lens_id: str, pools: Sequence[Pool], new: Sequence[Observation]) -> PoolingResult:
        """The validated routing of `new` under `lens_id`; raises PoolingFailed after the re-ask.

        `pools` are the lens's pools; merged ones are left out of the prompt. An empty `new` makes no call.
        """
        stray = [p.id for p in pools if p.lens_id != lens_id] + [o.id for o in new if o.lens_id != lens_id]
        if stray:
            raise ValueError(f"not under lens {lens_id}: {stray}")
        if any(o.pool_id is not None for o in new):
            raise ValueError("new observations are unpooled")
        if not new:
            return PoolingResult((), (), ())
        live = [p for p in pools if p.status is not PoolStatus.MERGED]
        asked = ask_json(
            self.client, ROLE, render_pooling(self.book, live, new, self.max_direction_labels),
            lambda raw: validate_pooling(raw, live, new, self.max_direction_labels),
            reasks=self.reasks, tag=f"pooling:{lens_id}",
        )
        if asked.value is None:
            raise PoolingFailed(lens_id, asked.failures)
        return asked.value


@dataclass(frozen=True)
class PoolingApplied:
    pools: tuple[Pool, ...]                 # new and changed pools, to store
    observations: tuple[Observation, ...]   # assigned and re-pointed observations, to store


def apply_pooling(
    result: PoolingResult, lens_id: str, pools: Sequence[Pool], new: Sequence[Observation],
    existing: Sequence[Observation], minter: IdMinter,
) -> PoolingApplied:
    """Pools and observations after `result`: new pools get runner-minted `P-` ids in reply order; a merged
    pool records `merged_into`, its labels join the survivor's, and its open observations move to the survivor.
    """
    by_id = {p.id: p for p in pools}
    minted = {np.key: Pool(minter.next(), lens_id, np.shape, np.direction_labels) for np in result.new_pools}
    changed: dict[str, Pool] = {}
    moved: list[Observation] = []
    for keep, gone in result.merges:
        survivor = changed.get(keep, by_id[keep])
        labels = survivor.direction_labels + tuple(
            x for x in by_id[gone].direction_labels if x not in survivor.direction_labels)
        changed[keep] = dataclasses.replace(survivor, direction_labels=labels)
        changed[gone] = dataclasses.replace(by_id[gone], status=PoolStatus.MERGED, merged_into=keep)
        moved += [dataclasses.replace(o, pool_id=keep) for o in existing
                  if o.pool_id == gone and o.status is ObservationStatus.OPEN]
    new_by_id = {o.id: o for o in new}
    assigned = [
        dataclasses.replace(
            new_by_id[a.observation_id],
            pool_id=minted[a.pool_ref[len(NEW):]].id if a.pool_ref.startswith(NEW) else a.pool_ref,
            direction_label=a.direction_label,
        )
        for a in result.assignments
    ]
    return PoolingApplied((*minted.values(), *changed.values()), (*assigned, *moved))
