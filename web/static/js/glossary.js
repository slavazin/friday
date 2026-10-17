// The vocabulary terms the page uses (SPEC § 2), each with a one-line gloss in plain words.
// A term shows its gloss on hover or focus; the first appearance on each page is underlined.

import { h } from "./util.js";

export const GLOSS = {
  "request": "a one-line build demand in a user's words, with the element ids it must have",
  "category": "one request's identity; a held-out category is never built during training",
  "cell": "one kind of app: a goal family (what it is) crossed with an interaction mode (how it is used)",
  "pass": "one worker building one app once, then the eval scores it and the judge reads it",
  "round": "every request of the round built by its pair and by the notes arm, then the round close",
  "round close": "the single step after a round's passes: lenses, pools, nomination, gate, detector, commits",
  "worker": "the small model that turns a composed prompt into one HTML app",
  "self-repair turn": "one extra worker turn given its own app's console errors and missing ids",
  "composition": "the worker's prompt: fixed instructions plus the admitted decisions whose hooks match",
  "eval": "the fixed scorer: it runs the app in a headless browser and never learns",
  "probe suite": "the eval's request-independent part: the page loads, logs no error, and its controls respond",
  "how-to": "the section every app ships, declaring what was built and what each control does",
  "acceptance check": "one scripted browser step list with an expected result",
  "loss": "the eval's score of one app: 0 is perfect, 1 is broken; lower is better",
  "arm": "one policy of memory use: journal (Friday), notes (shared notes) or empty (no memory)",
  "journal": "the journal arm: workers receive the admitted decisions whose hooks match the request",
  "notes": "the notes arm: every worker reads one shared notes file, the baseline Friday is measured against",
  "empty": "the empty arm: no memory at all, built only at the readout",
  "judge": "the strong model that reads each finished pass and files observations",
  "lens": "a typed question the judge answers on every pass, with a probability and a quote",
  "lens value": "how well a lens's answers predict the pass's loss over a window (AUC; 0.5 is chance)",
  "observation": "one thing the judge noticed in a pass, with what would have lowered the loss",
  "pool": "a group of observations sharing one shape; a recurring pool can be nominated",
  "decision": "one Journal entry D-NNNN: a hook and a payload, admitted only by its Ledger account",
  "Journal": "the team's decisions, each with its status, its evidence and its git history",
  "hook": "the tags a decision fires on, and the tags that block it",
  "payload": "the directive a decision gives the worker, in at most 60 words",
  "candidate": "a drafted decision under test, given to only one worker of each pair",
  "pair": "the two journal-arm passes of one request in one round, identical except for one exposure",
  "exposure": "the coin per pair that decides which of the two workers gets the decision under test",
  "paired delta": "d = loss without − loss with, for one pair; positive means the decision helped",
  "Ledger account": "a decision's running record of paired deltas and its e-process wealth",
  "e-process": "betting wealth over the paired deltas; valid whenever it is read",
  "gate": "the pre-registered bar: a candidate is admitted when its wealth reaches 1/α",
  "admitted": "passed the gate; composed into every matching request",
  "ablation": "after admission, one worker of a pair omits the decision, to check it still helps",
  "detector": "the readings over ablation deltas (and the judge's dispositions) that move a decision to suspect",
  "suspect": "an admitted decision whose evidence turned; it stays composed until confirmed or cleared",
  "retire": "the status flip ending a decision, naming its evidence and any successor",
  "disposition": "the judge's verdict on a composed decision for one pass: applied, not-applicable, contradicted",
  "regime": "the world's conditions: R1 before the drift round, R2 from it on",
  "drift round": "the round the world changes: a new goal mix and a phone-screen constraint, unannounced",
  "readout": "the single close scoring of the empty, notes and journal arms on the held-out set",
  "held-out set": "requests from kinds of app never built during training, read once at the close",
  "spend cap": "the dollar limit at which the run halts",
  "tag": "a trait word the tagger attaches to a request; hooks match on tags",
  "fast-track": "a pool nominated early because many distinct requests agree on one direction",
  "nominator": "the model that drafts a candidate decision from one pool",
  "ping": "the coordinator's notice to a human; the loop never waits for an answer",
};

// A term node: the word (or `text`), carrying its gloss.
export function term(key, text) {
  const gloss = GLOSS[key];
  if (!gloss) throw new Error(`no gloss for term ${key}`);
  return h("span", { class: "term", tabindex: "0", "data-gloss": gloss, "data-term": key }, text ?? key);
}

// Marks the first appearance of each term inside `root` (underlined, gloss on hover).
export function markFirst(root) {
  const seen = new Set();
  for (const el of root.querySelectorAll(".term")) {
    const key = el.dataset.term;
    el.classList.toggle("first", !seen.has(key));
    seen.add(key);
  }
}

// One tooltip for the whole page, positioned under the hovered or focused term.
export function installTooltip() {
  const tip = document.getElementById("tip");
  const show = (el) => {
    tip.textContent = el.dataset.gloss;
    tip.hidden = false;
    const r = el.getBoundingClientRect();
    const w = Math.min(380, window.innerWidth - 24);
    tip.style.maxWidth = `${w}px`;
    const left = Math.max(12, Math.min(r.left, window.innerWidth - w - 12));
    const below = r.bottom + 8;
    tip.style.left = `${left}px`;
    tip.style.top = `${below + 60 > window.innerHeight ? r.top - 8 - tip.offsetHeight : below}px`;
  };
  const hide = () => { tip.hidden = true; };
  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest?.(".term");
    if (el) show(el);
  });
  document.addEventListener("mouseout", (e) => { if (e.target.closest?.(".term")) hide(); });
  document.addEventListener("focusin", (e) => { if (e.target.classList?.contains("term")) show(e.target); });
  document.addEventListener("focusout", hide);
}
