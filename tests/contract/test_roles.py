import pytest

from friday.domain.types import Disposition
from friday.roles.ask import ask_json
from friday.roles.judge import Rejected, validate_lens_answers, validate_report
from tests.fakes import ScriptedClient
from tests.unit.builders import lens

SRC = "<!doctype html><script>canvas.addEventListener('keydown', go)</script>"


def test_lens_answers_require_verbatim_quotes():
    raw = {"lenses": [{"id": "L-03", "p": 0.8, "quote": "canvas.addEventListener('keydown', go)"}]}
    got = validate_lens_answers(raw, "x1", [lens("L-03")], SRC, 0.5, "judge/m")
    assert got[0].p == 0.8 and got[0].judge_model == "judge/m"
    with pytest.raises(Rejected, match="verbatim"):
        validate_lens_answers({"lenses": [{"id": "L-03", "p": 0.8, "quote": "window.onkey"}]},
                              "x1", [lens("L-03")], SRC, 0.5, "m")
    with pytest.raises(Rejected, match="no answer"):
        validate_lens_answers({"lenses": []}, "x1", [lens("L-03")], SRC, 0.5, "m")


def test_low_p_drops_the_quote():
    got = validate_lens_answers({"lenses": [{"id": "L-03", "p": 0.2, "quote": "canvas"}]},
                                "x1", [lens("L-03")], SRC, 0.5, "m")
    assert got[0].quote == ""


def test_report_validates_lens_ids_quotes_and_dispositions():
    raw = {"observations": [{"lens_id": "L-03", "shape": "handler on unfocused element",
                             "direction": "bind handlers on window", "quote": "addEventListener('keydown'"}],
           "dispositions": {"D-0001": "applied", "D-0002": {"disposition": "contradicted", "quote": "canvas"}}}
    obs, disp = validate_report(raw, ["L-03"], ["D-0001", "D-0002"], SRC, 3)
    assert obs[0].lens_id == "L-03" and disp["D-0002"][0] is Disposition.CONTRADICTED
    with pytest.raises(Rejected, match="no disposition"):
        validate_report({**raw, "dispositions": {}}, ["L-03"], ["D-0001"], SRC, 3)
    with pytest.raises(Rejected, match="unknown lens_id"):
        validate_report({"observations": [{**raw["observations"][0], "lens_id": "L-77"}]}, ["L-03"], [], SRC, 3)
    with pytest.raises(Rejected, match="verbatim"):
        bad = {"dispositions": {"D-0001": {"disposition": "contradicted", "quote": "nope"}}}
        validate_report(bad, ["L-03"], ["D-0001"], SRC, 3)


def test_ask_json_reasks_once_then_reports_failure():
    def need_a(v):
        if "a" not in v:
            raise Rejected("missing a")
        return v["a"]

    ok = ask_json(ScriptedClient(["{}", '```json\n{"a": 2}\n```']), "judge", [], need_a, reasks=1, tag="t")
    assert ok.value == 2 and ok.attempts == 2 and ok.failures == ("missing a",)
    client = ScriptedClient(["nope", "{}"])
    bad = ask_json(client, "judge", [], need_a, reasks=1, tag="t")
    assert not bad.ok and len(bad.failures) == 2
    assert "missing a" not in client.calls[0][1] and "not usable" in client.calls[1][1][-1].content
