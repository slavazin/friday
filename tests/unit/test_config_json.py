from pathlib import Path

import pytest

from friday.app.wiring import build_mechanics, flip_constants
from friday.config import ConfigError, load_spec, parse_spec
from friday.support.json_repair import decode

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / "run.yml").read_text()


def test_run_yml_loads_and_validates():
    spec = load_spec(ROOT / "run.yml")
    assert spec.gate.alpha == 0.3 and spec.composition.cap == 6 and spec.drift.round == 4
    assert spec.models.roles["lens_minter"] == spec.models.roles["nominator"]
    assert len(spec.spec_hash) == 16


def test_unknown_key_is_refused():
    with pytest.raises(ConfigError, match="unknown keys"):
        parse_spec(TEXT.replace("  alpha: 0.3", "  alfa: 0.4\n  alpha: 0.3"))


def test_missing_key_is_refused():
    with pytest.raises(ConfigError, match="missing"):
        parse_spec(TEXT.replace("  n_max: 12\n", ""))


def test_wrong_type_is_refused():
    with pytest.raises(ConfigError, match="expected int"):
        parse_spec(TEXT.replace("  n_min: 4", "  n_min: four"))


def test_weights_must_sum_to_one():
    with pytest.raises(ConfigError, match="w_a"):
        parse_spec(TEXT.replace("w_r: 0.25", "w_r: 0.3"))


def test_every_knob_reaches_its_algorithm():
    spec = parse_spec(TEXT.replace("alpha: 0.3", "alpha: 0.1").replace("ablate_p: 0.75", "ablate_p: 0.5")
                      .replace("fold_rho: 0.85", "fold_rho: 0.8").replace("lambda_max: 0.9", "lambda_max: 0.8"))
    m = build_mechanics(spec)
    assert m.gate.alpha == 0.1 and m.detector.alpha == 0.1 and m.gate.threshold == pytest.approx(10)
    assert m.gate.betting.lambda_max == 0.8 and m.curation.fold_rho == 0.8
    assert flip_constants(spec)["threshold"] == pytest.approx(10)


def test_unknown_algorithm_name_is_refused():
    from friday.registry import UnknownImplementation

    with pytest.raises(UnknownImplementation):
        build_mechanics(parse_spec(TEXT.replace("name: agrapa", "name: kelly")))


@pytest.mark.parametrize(("text", "want", "repairs"), [
    ('{"a": 1}', {"a": 1}, ()),
    ('```json\n{"a": 1}\n```', {"a": 1}, ("strip_fences",)),
    ('Sure! {"a": [1, 2,],} thanks', {"a": [1, 2]}, ("outermost", "trailing_commas")),
    ('<think>{"no": 1}</think>{"a": "}"}', {"a": "}"}, ("strip_think",)),
])
def test_decode_repairs_syntax(text, want, repairs):
    d = decode(text)
    assert d.value == want and d.repairs == repairs


def test_decode_reports_undecodable():
    d = decode("no json here")
    assert not d.ok and d.error
