"""The probe suite (SPEC § 5): the request-independent share of the deterministic loss."""

from __future__ import annotations

from collections.abc import Collection

from friday.domain.types import ProbeReading, Viewport
from friday.ports.browser import Page


def run(page: Page, viewport: Viewport, pass_id: str, declared_ids: Collection[str],
        max_controls: int, settle_ms: int, sample_text: str) -> ProbeReading:
    """Operates each control on a loaded page and reads which change visible state."""
    raise NotImplementedError("slice S1: probe suite")


def missing_contract_ids(page: Page, contract: Collection[str]) -> list[str]:
    """Contract ids (with '#') absent from the loaded page; drives the self-repair turn."""
    raise NotImplementedError("slice S1: page-based contract id check")
