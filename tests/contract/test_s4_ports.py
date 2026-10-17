"""The rubric adapter, the quality probe and the response cache conform to their ports (same public
methods, same parameter names)."""

import inspect

import pytest

from friday.adapters.cache import FileResponseCache
from friday.adapters.quality import NodeQualityProbe
from friday.adapters.rubric import ModelRubric
from friday.ports import browser, models, scoring


def _params(fn):
    return [p for p in inspect.signature(fn).parameters if p != "self"]


@pytest.mark.parametrize(("impl", "port"), [
    (ModelRubric, scoring.Rubric), (NodeQualityProbe, browser.QualityProbe), (FileResponseCache, models.ResponseCache),
])
def test_adapter_matches_its_port(impl, port):
    for name, fn in vars(port).items():
        if callable(fn) and not name.startswith("_"):
            assert hasattr(impl, name), f"{impl.__name__} lacks {name}"
            assert _params(getattr(impl, name)) == _params(fn), f"{impl.__name__}.{name}"
