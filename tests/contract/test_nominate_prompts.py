"""Tier-1 checks on the pooling and nominator templates (SPEC § 2 Blinding, Payload language, Checks)."""

from pathlib import Path

from friday.algorithms.language import blinding_hits, word_hits
from friday.config import load_spec
from friday.domain.types import Observation, Pool
from friday.ports.models import Citation
from friday.roles.nominator import render_nominator
from friday.roles.pooling import render_pooling
from friday.roles.prompts import PromptBook

ROOT = Path(__file__).resolve().parents[2]
BOOK = PromptBook(ROOT / "prompts")
FAMILIES = tuple(load_spec(ROOT / "run.yml").stream.families)

# The nominator carries SPEC § 2 Payload language with these declared edits, and no others:
# section references the nominator cannot follow, Appendix A named by what the prompt includes, the
# gate (a term outside the nominator's input) and "drift" (on the nominator's blinding row).
PAYLOAD_EDITS = [
    ("### Payload language", "## Payload language"),
    ("within the 60-word bound (§ 2, payload)", "within the {{max_words}}-word bound"),
    ("and the gate then measures a payload nobody applied", "and a payload nobody applies cannot help"),
    ("restate Appendix A: a payload with no headroom expires (§ 7)",
     "restate the baseline instructions below: a payload with no headroom expires"),
    ('timers drift."   ', 'timers run late."'),
    ("restates Appendix A                        ", "restates the baseline instructions"),
    ("fits in 60 words", "fits in {{max_words}} words"),
]


def _spec_payload_language() -> str:
    spec = (ROOT / "SPEC.md").read_text()
    return spec.split("### Payload language", 1)[1].split("### Lens language", 1)[0].rstrip()


def test_nominator_prompt_carries_payload_language_with_only_declared_edits():
    expected = "### Payload language" + _spec_payload_language()
    for old, new in PAYLOAD_EDITS:
        assert old in expected, old
        expected = expected.replace(old, new)
    assert expected in BOOK.text("nominator/system.md")


def _rendered_nominator() -> str:
    pool = Pool("P-0001", "L-03", "SHAPE", ("LABEL",))
    o = Observation("O-0001", "x", 1, "L-03", "CAT", "S", "D", "Q", pool_id="P-0001", direction_label="LABEL")
    msgs = render_nominator(BOOK, pool, "LABEL", [o], [Citation("u", "t", "s")], frozenset({"tag"}), 60)
    return "\n".join(m.content for m in msgs)


def _rendered_pooling() -> str:
    o = Observation("O-0001", "x", 1, "L-03", "CAT", "S", "D", "Q")
    return "\n".join(m.content for m in render_pooling(BOOK, [Pool("P-0001", "L-03", "SHAPE", ("LABEL",))], [o], 2))


def test_nominator_templates_name_no_blinded_word_or_goal_family():
    text = _rendered_nominator()
    assert blinding_hits(text, BOOK.blinding["nominator"]) == []
    assert blinding_hits(text, FAMILIES) == []
    assert "{{" not in text


def test_pooling_templates_name_no_blinded_word_or_goal_family():
    # blinding.yml has no pooling row; the pooling call reads what the nominator reads, so it takes that row
    text = _rendered_pooling()
    assert blinding_hits(text, BOOK.blinding["nominator"]) == []
    assert blinding_hits(text, FAMILIES) == []
    assert "{{" not in text


def test_templates_carry_no_hedge_outside_the_quoted_examples():
    own = [BOOK.text("pooling/system.md"), BOOK.text("pooling/user.md"), BOOK.text("nominator/user.md"),
           BOOK.text("nominator/system.md")]
    quoted_examples = '("consider", "try to", "where possible")'
    texts = [t.replace(quoted_examples, "").replace('"Consider making keyboard controls work better."', "")
             for t in own]
    assert [word_hits(t, BOOK.hedges) for t in texts] == [[], [], [], []]
