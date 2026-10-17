"""Hook firing and reach (SPEC § 2 hook, reach)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from friday.domain.types import Hook, Request


def fires(hook: Hook, tags: Iterable[str]) -> bool:
    """True iff `tags` holds at least one `fires_when` tag and no `not_this` tag."""
    tag_set = set(tags)
    return bool(hook.fires_when & tag_set) and not (hook.not_this & tag_set)


def text_hash(hook: Hook, payload: str) -> str:
    """A digest of a decision's frozen text (hook and payload), written at drafting and checked at every flip."""
    material = "\x1f".join([",".join(sorted(hook.fires_when)), ",".join(sorted(hook.not_this)), payload.strip()])
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def reach(hook: Hook, requests: Iterable[Request]) -> tuple[str, ...]:
    """The ids of the given requests the hook fires on, in input order."""
    return tuple(r.id for r in requests if fires(hook, r.tags))
