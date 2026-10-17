"""Fakes and adapters conform to their ports (same public methods, same parameter names)."""

import inspect

import pytest

from friday.adapters import memory
from friday.adapters.journal_md import MarkdownJournal
from friday.adapters.lens_md import MarkdownLensSet
from friday.adapters.sqlite import stores as sq
from friday.ports import browser, models, stores
from friday.roles.coordinator import TOOL_NAMES, CoordinatorTools
from tests.fakes import FakePage, ScriptedClient


def methods(proto):
    return {n: inspect.signature(f) for n, f in vars(proto).items() if callable(f) and not n.startswith("_")}


def params(sig):
    return [p for p in sig.parameters if p != "self"]


@pytest.mark.parametrize(("impl", "port"), [
    (memory.MemoryPassStore, stores.PassStore), (memory.MemoryJudgeStore, stores.JudgeStore),
    (memory.MemoryObservationStore, stores.ObservationStore), (memory.MemoryPoolStore, stores.PoolStore),
    (memory.MemoryLedger, stores.Ledger), (memory.MemoryEventLog, stores.EventLog),
    (memory.MemorySpendLog, stores.SpendLog), (memory.MemoryJournal, stores.Journal),
    (MarkdownJournal, stores.Journal), (MarkdownLensSet, stores.LensSet),
    (sq.SqliteLedger, stores.Ledger), (sq.SqliteEventLog, stores.EventLog), (sq.SqliteSpendLog, stores.SpendLog),
    (ScriptedClient, models.ModelClient), (FakePage, browser.Page),
])
def test_implementation_matches_port(impl, port):
    for name, sig in methods(port).items():
        assert hasattr(impl, name), f"{impl.__name__} lacks {name}"
        assert params(inspect.signature(getattr(impl, name))) == params(sig), f"{impl.__name__}.{name}"


def test_coordinator_has_no_tool_that_moves_the_bar():
    names = set(methods(CoordinatorTools))
    assert names == set(TOOL_NAMES)
    forbidden = ("expos", "status", "flip", "admit", "retire", "constant", "set_", "gate", "write")
    assert not [n for n in names for f in forbidden if f in n]
