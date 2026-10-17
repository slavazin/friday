"""The request text the worker, the eval rubric and the judge read (SPEC §§ 2, 10).

The drift constraint's one home is `run.yml` `drift.constraint`. Stored request text never carries it; it is
appended here, once, from the drift round on, as plain request text with no label marking it as a change.
"""

from __future__ import annotations

from friday.domain.types import Regime, Request


def regime_of(round_: int, drift_round: int) -> Regime:
    """The regime of training round `round_`: R1 before `drift_round`, R2 from it on."""
    if round_ < 1:
        raise ValueError(f"rounds start at 1, got {round_}")
    return Regime.R2 if round_ >= drift_round else Regime.R1


def request_text(request: Request, regime: Regime, drift_constraint: str) -> str:
    """The request's text, with the drift constraint appended once when `regime` is R2.

    Raises when the stored text already carries the constraint: that would compose it twice in R2 and leak
    it into R1.
    """
    constraint = " ".join(drift_constraint.split())
    if not constraint:
        raise ValueError("drift.constraint is empty")
    text = request.text.strip()
    if constraint in " ".join(text.split()):
        raise ValueError(f"{request.id}: stored request text carries the drift constraint")
    if regime is Regime.R2:
        return f"{text}\n{constraint}"
    return text
