"""Pin resolution against the served model list (SPEC § 5)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from friday.config import RolePin
from friday.ports.models import ModelInfo


class NoServedModel(RuntimeError):
    pass


@dataclass(frozen=True)
class Resolution:
    role: str
    model: str
    replaced: bool             # True when no pin was served and the fallback was taken
    wanted: tuple[str, ...]


def resolve(roles: Mapping[str, RolePin], served: Sequence[ModelInfo]) -> dict[str, Resolution]:
    """Each role's model: the first served pin (image-capable where required), else the fallback."""
    by_id = {m.id: m for m in served}
    out: dict[str, Resolution] = {}
    for role, pin in roles.items():
        ok = [p for p in pin.pins if p in by_id and (not pin.requires_image or by_id[p].image_input)]
        if ok:
            out[role] = Resolution(role, ok[0], False, pin.pins)
        elif pin.fallback in by_id:
            out[role] = Resolution(role, pin.fallback, True, pin.pins)
        else:
            raise NoServedModel(f"{role}: none of {pin.pins} nor fallback {pin.fallback} is served")
    return out


def demote_group(
    resolved: Mapping[str, Resolution], roles: Mapping[str, RolePin], group: str,
) -> dict[str, Resolution]:
    """Moves every role in `group` to its fallback (the slow-first-call rule)."""
    out = dict(resolved)
    for role, pin in roles.items():
        if pin.slow_first_call_group == group:
            out[role] = Resolution(role, pin.fallback, True, pin.pins)
    return out


class CriticNotIndependent(RuntimeError):
    """The critic is bound to a model another role reads the pass with (SPEC § 17 Independence)."""


CRITIC_INDEPENDENT_OF = ("rubric", "judge", "nominator")


def check_critic_independence(bound: Mapping[str, str]) -> None:
    """Raises CriticNotIndependent when the critic's bound model equals the bound rubric, judge or nominator
    model. The rubric's model would steer revisions toward the scorer's taste and bias the critic run's loss
    comparison; the judge's and the nominator's would read their own reaction back (SPEC § 17)."""
    critic = bound.get("critic")
    if critic is None:
        raise KeyError("the critic role is not bound")
    clash = [r for r in CRITIC_INDEPENDENT_OF if bound.get(r) == critic]
    if clash:
        raise CriticNotIndependent(f"critic bound to {critic}, the same model as {', '.join(clash)}")
