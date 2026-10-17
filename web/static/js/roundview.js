// The round view: a lane per pass, the round close's phases, the round counter and spend against the cap.
// It updates the nodes that changed and never rebuilds the page, so polling causes no layout jumps.

import { term } from "./glossary.js";
import { PHASES, PHASE_LABEL, STEPS } from "./runstate.js";
import { armChip, fmt, h, lossColor, lossLegend, setClass, setText } from "./util.js";

const STEP_INDEX = Object.fromEntries(STEPS.map(([k], i) => [k, i]));
const STEP_LABEL = Object.fromEntries(STEPS.map(([k, label]) => [k, label]));

export class RoundView {
  constructor(root, cfg, { title, onPick } = {}) {
    this.cfg = cfg;
    this.onPick = onPick;
    this.cards = new Map();
    this.shownRound = null;
    this.phaseEls = new Map();
    this.root = root;
    root.classList.add("roundview");

    this.counter = h("div", { class: "counter" });
    this.roundTitle = h("div", { class: "round-title" });
    this.roundSub = h("div", { class: "round-sub muted" });
    this.spendText = h("div", { class: "spend-text" });
    this.spendFill = h("div", { class: "spend-fill" });
    const spend = h("div", { class: "spend" },
      h("div", { class: "spend-head" }, h("span", { class: "muted" }, "spend against the ", term("spend cap")),
        this.spendText),
      h("div", { class: "spend-bar" }, this.spendFill));
    root.append(h("div", { class: "rv-head" },
      h("div", { class: "rv-title" }, title ? h("div", { class: "eyebrow" }, title) : null, this.roundTitle, this.roundSub),
      this.counter, spend));

    this.lanes = h("div", { class: "lanes" });
    root.append(this.lanes);

    const phases = h("div", { class: "phases" });
    for (const p of PHASES) {
      const count = h("div", { class: "phase-count" });
      const el = h("div", { class: "phase" }, h("div", { class: "phase-name" }, PHASE_LABEL[p]), count);
      this.phaseEls.set(p, { el, count });
      phases.append(el);
    }
    this.closeLabel = h("div", { class: "close-label" }, term("round close"), h("span", { class: "close-state muted" }));
    root.append(h("div", { class: "close-strip" }, this.closeLabel, phases));
    root.append(h("div", { class: "rv-foot" }, lossLegend(),
      h("div", { class: "foot-terms muted" }, "each card is a ", term("pass"), " · ", term("arm"), ": ",
        term("journal"), ", ", term("notes"), ", ", term("empty"), " · a card colours by its ", term("loss"))));
  }

  // Draws `state` showing round `pick` (or the latest round that has begun).
  render(state, pick = null) {
    const r = (pick !== null && state.rounds.get(pick)) || state.latest();
    this._counter(state, r);
    this._spend(state);
    if (!r) {
      setText(this.roundTitle, "Waiting for the run to start");
      setText(this.roundSub, "no round has begun in the telemetry log yet");
      this._lanes(null);
      this._phases(null);
      return;
    }
    const total = this.cfg.rounds;
    setText(this.roundTitle, r.readout ? "Readout" : `Round ${r.round} of ${total}`);
    const regime = r.regime ? `regime ${r.regime}` : "";
    const drift = !r.readout && r.round === this.cfg.drift_round ? " · the drift round" : "";
    const status = r.state === "done" ? "finished" : r.state === "running" ? "in progress" : "";
    setText(this.roundSub, [status, regime].filter(Boolean).join(" · ") + drift);
    this._lanes(r);
    this._phases(r.readout ? null : r);
  }

  _counter(state, current) {
    const want = [];
    for (let i = 1; i <= this.cfg.rounds; i++) want.push(i);
    for (const r of state.rounds.values()) if (r.readout && !want.includes(r.round)) want.push(r.round);
    const key = want.join(",");
    if (this.counter.dataset.key !== key) {
      this.counter.dataset.key = key;
      this.counter.replaceChildren(...want.map((n) => {
        const isReadout = n > this.cfg.rounds;
        const b = h("button", { class: "dot", "data-round": String(n), type: "button",
          title: isReadout ? "the readout" : `round ${n}`, onclick: () => this.onPick?.(n) },
        isReadout ? "readout" : String(n));
        if (n === this.cfg.drift_round) b.append(h("span", { class: "drift-tag" }, "drift"));
        return b;
      }));
    }
    for (const b of this.counter.children) {
      const n = Number(b.dataset.round);
      const r = state.rounds.get(n);
      setClass(b, "is-done", r?.state === "done");
      setClass(b, "is-running", r?.state === "running");
      setClass(b, "is-shown", current?.round === n);
      setClass(b, "is-drift", n === this.cfg.drift_round);
    }
  }

  _spend(state) {
    const cap = this.cfg.spend_cap_usd;
    setText(this.spendText, state.spend === null ? "unread until the first round close" : `${fmt.usd(state.spend)} of ${fmt.usd(cap)}`);
    const share = state.spend === null ? 0 : Math.min(1, state.spend / cap);
    this.spendFill.style.setProperty("--share", String(share));
  }

  _lanes(r) {
    if (!r) {
      this.cards.clear();
      this.lanes.replaceChildren(h("div", { class: "lanes-empty muted" }, "No pass in flight."));
      this.shownRound = null;
      return;
    }
    if (this.shownRound !== r.round) {
      this.cards.clear();
      this.lanes.replaceChildren();
      this.shownRound = r.round;
    }
    const n = r.passes.size;
    const cols = n <= 4 ? Math.max(n, 2) : n <= 8 ? 4 : n <= 15 ? Math.ceil(n / 2.5) : Math.ceil(n / 3.5);
    this.lanes.style.setProperty("--cols", String(Math.max(1, Math.min(cols, 10))));
    this.lanes.style.setProperty("--rows", String(Math.max(1, Math.ceil(n / Math.max(1, cols)))));
    setClass(this.lanes, "compact", n > 18);
    for (const card of r.passes.values()) {
      let el = this.cards.get(card.id);
      if (!el) {
        el = this._newCard(card);
        this.cards.set(card.id, el);
        this.lanes.append(el.root);
      }
      this._updateCard(el, card);
    }
  }

  _newCard(card) {
    const a = card.attrs;
    const pips = h("div", { class: "pips" });
    const pipEls = new Map();
    for (const [k, label] of STEPS) {
      const p = h("span", { class: `pip pip-${k}`, title: label });
      pipEls.set(k, p);
      pips.append(p);
    }
    const step = h("div", { class: "card-step" });
    const loss = h("div", { class: "card-loss" });
    const lossLabel = h("div", { class: "card-loss-label muted" });
    const tags = h("div", { class: "card-tags" });
    const slot = a.pair_slot === null || a.pair_slot === undefined ? "" : ` · slot ${a.pair_slot}`;
    const root = h("div", { class: "card", "data-pass": card.id },
      h("div", { class: "card-top" }, h("span", { class: "card-req" }, a.request_id ?? card.id),
        h("span", { class: "card-arm" }, armChip(a.arm ?? "?"), h("span", { class: "muted" }, slot))),
      h("div", { class: "card-cell muted" }, a.cell ?? ""),
      pips, step, h("div", { class: "card-result" }, loss, lossLabel), tags);
    if (a.exposed) tags.append(h("span", { class: "chip exp" }, `exposed ${a.exposed}`));
    if (a.ablated) tags.append(h("span", { class: "chip abl" }, `ablated ${a.ablated}`));
    if (a.rerun_of) tags.append(h("span", { class: "chip rerun" }, "re-run"));
    return { root, pipEls, step, loss, lossLabel };
  }

  _updateCard(el, card) {
    const failed = card.outcome && card.outcome !== "scored";
    const scored = card.outcome === "scored";
    const cur = card.step ? STEP_INDEX[card.step] : -1;
    for (const [k, p] of el.pipEls) {
      const i = STEP_INDEX[k];
      setClass(p, "done", card.done.has(k) || (scored && k !== "repair" && i < cur));
      setClass(p, "now", !scored && !failed && k === card.step);
      setClass(p, "skipped", k === "repair" && !card.repaired && (cur > STEP_INDEX.repair || scored));
    }
    setClass(el.root, "is-scored", scored);
    setClass(el.root, "is-failed", failed);
    setClass(el.root, "is-running", !scored && !failed);
    if (scored) {
      setText(el.step, card.repaired ? "scored after a self-repair turn" : "scored");
      setText(el.loss, fmt.loss(card.loss));
      setText(el.lossLabel, "loss");
      el.root.style.setProperty("--loss-color", lossColor(card.loss));
    } else if (failed) {
      setText(el.step, card.outcome === "transport_failed" ? "failed for transport" : card.outcome.replace("_", " "));
      setText(el.loss, "no loss");
      setText(el.lossLabel, "");
    } else {
      setText(el.step, card.step ? STEP_LABEL[card.step] : "starting");
      setText(el.loss, "");
      setText(el.lossLabel, "");
    }
  }

  _phases(r) {
    setText(this.closeLabel.querySelector(".close-state"),
      !r ? "" : r.close.state === "pending" ? " · waiting for the passes" : r.close.state === "running" ? " · running" : " · done");
    for (const p of PHASES) {
      const { el, count } = this.phaseEls.get(p);
      const ph = r?.close.phases.get(p);
      const state = ph?.state ?? "pending";
      for (const s of ["pending", "running", "done", "failed"]) setClass(el, `is-${s}`, state === s);
      const counts = ph ? Object.entries(ph.counts).map(([k, v]) => `${k.replaceAll("_", " ")} ${v}`).join(" · ") : "";
      setText(count, state === "done" || state === "failed" ? counts || "—" : state === "running" ? "running" : "");
    }
  }
}
