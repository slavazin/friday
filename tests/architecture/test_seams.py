"""Every seam a surface calls exists with its declared signature (stubs raise until built)."""

import inspect

import pytest

from friday.eval import how_to, probe
from friday.roles import judge, lens_minter, nominator, pooling, tagger

SEAMS = [
    (probe.run, ["page", "viewport", "pass_id", "declared_ids", "max_controls", "settle_ms", "sample_text"]),
    (probe.missing_contract_ids, ["page", "contract"]),
    (how_to.parse, ["html", "contract", "max_controls", "test_query", "settle_ms"]),
    (how_to.score, ["parse_", "results", "contract", "repair_penalty", "pass_id", "viewport"]),
    (judge.Judge.lens_call, ["self", "pass_id", "request_text", "composed", "source", "lenses"]),
    (judge.Judge.report_call, ["self", "pass_id", "request_text", "composed", "source", "answers",
                               "eval_report_text", "lens_ids"]),
    (tagger.Tagger.tag, ["self", "requests", "vocabulary", "round_", "max_new"]),
    (pooling.PoolingCall.assign, ["self", "lens_id", "pools", "new"]),
    (nominator.Nominator.draft, ["self", "pool", "label", "observations"]),
    (lens_minter.LensMinter.propose, ["self", "residual", "lenses", "limit"]),
]


@pytest.mark.parametrize(("fn", "params"), SEAMS, ids=lambda x: getattr(x, "__qualname__", ""))
def test_seam_signature(fn, params):
    assert list(inspect.signature(fn).parameters) == params
