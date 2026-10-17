"""Small builders for domain values in tests."""

from friday.domain.types import Check, Decision, DecisionStatus, Hook, Lens, LensStatus, Regime, Request


def req(id_: str, *tags: str) -> Request:
    return Request(id_, f"request {id_}", frozenset(tags), {"#start": "starts"},
                   {Regime.R1: (Check("c1", (), {"exists": "#start"}),), Regime.R2: ()})


def dec(id_: str, status=DecisionStatus.ADMITTED, fires=("canvas",), not_this=(), payload="Do the thing.",
        supersedes=(), drafted_round=1) -> Decision:
    return Decision(id_, status, "P-0001", Hook(frozenset(fires), frozenset(not_this)), payload,
                    supersedes=tuple(supersedes), drafted_round=drafted_round)


def lens(id_: str, status=LensStatus.ACTIVE) -> Lens:
    return Lens(id_, f"angle {id_}", f"counter {id_}", status)
