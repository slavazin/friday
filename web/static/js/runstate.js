// The run as the telemetry log tells it: one reducer over rows, shared by the live view and the replay.
// Rows are applied in order; nothing here computes a figure, it only keeps what the rows say.

// Pass steps in the order a pass runs them (docs/telemetry.md), with the span names that mark each.
export const STEPS = [
  ["compose", "compose", ["pass.compose"]],
  ["build", "build", ["pass.worker"]],
  ["repair", "repair", ["pass.repair"]],
  ["load", "eval · load", ["eval.load"]],
  ["probe", "eval · probe", ["eval.probe"]],
  ["how-to", "eval · how-to", ["eval.how_to"]],
  ["checks", "eval · checks", ["eval.checks"]],
  ["rubric", "eval · rubric", ["eval.rubric"]],
  ["judge", "judge", ["judge.lens_call", "judge.report_call"]],
  ["scored", "scored", []],
];
const STEP_OF = Object.fromEntries(STEPS.flatMap(([key, , names]) => names.map((n) => [n, key])));

// Round-close phases in SPEC § 9 order (docs/telemetry.md `close.<phase>`).
export const PHASES = ["steers", "lens_curation", "observations", "pooling", "nomination", "gate", "detector", "pings"];
export const PHASE_LABEL = {
  steers: "steers", lens_curation: "lens curation", observations: "observations", pooling: "pooling",
  nomination: "nomination", gate: "gate", detector: "detector", pings: "pings",
};

function newRound(n) {
  return {
    round: n, regime: null, state: "pending", readout: false, hasSpan: false, passes: new Map(), concurrency: null,
    close: { state: "pending", phases: new Map(PHASES.map((p) => [p, { state: "pending", counts: {} }])) },
  };
}

export class RunState {
  constructor() {
    this.rounds = new Map();
    this.spend = null;          // the last run.spend reading (usd, cumulative after a round close)
    this.halt = null;
    // The readout span, whatever round it is scoped to: null until it opens, then {state, round}.
    this.readoutSpan = null;
    this.lastSeq = 0;
    this.lastAt = null;
    this.changed = true;
  }

  round(n) {
    if (!this.rounds.has(n)) this.rounds.set(n, newRound(n));
    return this.rounds.get(n);
  }

  // The round a non-span-boundary row lands in. Once the readout span has opened, a row whose round has no
  // `round` span (or carries no round at all) belongs to a readout round, which takes the readout span's state.
  _roundFor(n) {
    const ro = this.readoutSpan;
    if (ro === null) return this.round(n);
    if (n === null || n === undefined) n = ro.round ?? this._nextRound();
    const r = this.round(n);
    if (!r.hasSpan) { r.readout = true; r.state = ro.state; }
    return r;
  }

  // One past the highest round that a `round` span opened.
  _nextRound() {
    let max = 0;
    for (const r of this.rounds.values()) if (r.hasSpan && !r.readout && r.round > max) max = r.round;
    return max + 1;
  }

  // The round shown by default: the latest one that has begun.
  latest() {
    let best = null;
    for (const r of this.rounds.values()) if (r.round !== null && r.round !== undefined && r.state !== "pending" && (best === null || r.round > best.round)) best = r;
    return best;
  }

  apply(row) {
    this.lastSeq = Math.max(this.lastSeq, row.seq);
    this.lastAt = row.ended_at ?? row.at;
    this.changed = true;
    const closing = row.kind === "span" && row.ended_at !== null;
    const n = row.round;
    if (row.kind === "span") {
      if (row.name === "round") {
        const r = this.round(n);
        r.hasSpan = true;
        r.readout = false;
        if (row.attrs.regime) r.regime = row.attrs.regime;
        r.state = closing ? "done" : "running";
        return;
      }
      if (row.name === "readout") {
        const state = closing ? "done" : "running";
        this.readoutSpan = { state, round: n ?? this.readoutSpan?.round ?? null };
        if (n !== null && n !== undefined) { const r = this.round(n); r.hasSpan = false; r.readout = true; }
        for (const r of this.rounds.values()) if (r.readout) r.state = state;
        return;
      }
      if (row.name === "pass") {
        const r = this._roundFor(n);
        if (r.state === "pending") r.state = "running";
        const card = this._card(r, row.pass_id, row.attrs);
        if (closing) card.closed = true;
        return;
      }
      if (row.name === "close") {
        this.round(n).close.state = closing ? (row.ok === false ? "failed" : "done") : "running";
        return;
      }
      if (row.name.startsWith("close.")) {
        const ph = this.round(n).close.phases.get(row.name.slice(6));
        if (!ph) return;
        ph.state = closing ? (row.ok === false ? "failed" : "done") : "running";
        if (closing) ph.counts = row.attrs;
        return;
      }
      const step = STEP_OF[row.name];
      if (step && row.pass_id) {
        const card = this._card(this._roundFor(n), row.pass_id);
        if (closing) card.done.add(step);
        else card.step = step;
        if (step === "repair") card.repaired = true;
      }
      return;
    }
    if (row.kind === "metric") {
      if (row.name === "pass.loss" && row.pass_id) this._card(this._roundFor(n), row.pass_id).loss = row.value;
      else if (row.name === "run.spend") this.spend = row.value;
      else if (row.name === "round.concurrency") this.round(n).concurrency = row.value;
      return;
    }
    if (row.kind === "event") {
      if (row.name === "pass.outcome" && row.pass_id) {
        const card = this._card(this._roundFor(n), row.pass_id);
        card.outcome = row.attrs.outcome;
        if (card.outcome === "scored") { card.step = "scored"; card.done.add("scored"); }
      } else if (row.name === "run.halt") {
        this.halt = row.attrs;
      }
    }
  }

  _card(r, id, attrs) {
    let c = r.passes.get(id);
    if (!c) {
      c = { id, attrs: {}, step: null, done: new Set(), repaired: false, outcome: null, loss: null, closed: false };
      r.passes.set(id, c);
    }
    if (attrs) c.attrs = attrs;
    return c;
  }
}
