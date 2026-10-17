// Replay: the live view driven by a recorded log instead of the clock. Rows are ordered by when they
// happened (a span's close row at its end time) and applied as a virtual clock passes them.

import { RoundView } from "./roundview.js";
import { RunState } from "./runstate.js";
import { allRows, fmt, h, setClass, setText } from "./util.js";

const SPEEDS = [1, 10, 60];

const eventTime = (r) => (r.kind === "span" && r.ended_at !== null ? r.ended_at : r.at);

export class ReplayPage {
  constructor(root, cfg) {
    this.cfg = cfg;
    this.root = root;
    this.events = [];
    this.i = 0;
    this.t = 0;
    this.speed = 60;
    this.playing = false;
    this.raf = null;
    this.pick = null;
    this.loaded = null;

    this.playBtn = h("button", { class: "btn primary", type: "button", onclick: () => this.toggle() }, "Play");
    this.speedBtns = SPEEDS.map((sp) => h("button", { class: "btn seg", type: "button", "data-speed": String(sp),
      onclick: () => this.setSpeed(sp) }, `${sp}×`));
    this.roundBtns = h("div", { class: "seg-group" });
    this.stopAt = null;           // when set, play freezes here (the end of the round being replayed)
    this.current = null;          // the round (or "readout") last jumped to
    this.restartBtn = h("button", { class: "btn seg", type: "button", title: "replay this round from its start",
      onclick: () => (this.current !== null ? this.jumpTo(this.current) : this.seek(this.t0)) }, "↺ Restart round");
    this.slider = h("input", { type: "range", min: "0", max: "1000", value: "0", class: "scrub",
      "aria-label": "replay position", oninput: (e) => { this.stopAt = null; this.seek(this.t0 + (this.t1 - this.t0) * (e.target.value / 1000)); } });
    this.clock = h("div", { class: "replay-clock" });
    this.stateLabel = h("div", { class: "replay-state muted" });
    const bar = h("div", { class: "replay-bar" }, this.playBtn,
      h("div", { class: "seg-group" }, h("span", { class: "muted seg-label" }, "speed"), ...this.speedBtns),
      h("div", { class: "seg-group" }, h("span", { class: "muted seg-label" }, "round"), this.roundBtns, this.restartBtn),
      this.slider, this.clock, this.stateLabel);
    this.view = new RoundView(h("div"), cfg, { title: "Replay of the recorded run",
      onPick: (n) => this.jumpTo(n) });
    root.append(bar, this.view.root);
    this.setSpeed(60);
  }

  load() {
    this.loaded ??= this._load();
    return this.loaded;
  }

  async _load() {
    setText(this.stateLabel, "loading the recorded log…");
    const { rows } = await allRows({ run: this.cfg.telemetry_run ?? "main" });
    this.events = rows.map((r, k) => ({ t: eventTime(r), k, r })).sort((a, b) => a.t - b.t || a.k - b.k);
    if (!this.events.length) {
      setText(this.stateLabel, "the telemetry log holds no rows yet");
      this.root.dataset.state = "empty";
      return;
    }
    this.t0 = this.events[0].t;
    this.t1 = this.events[this.events.length - 1].t;
    // Round starts keyed by round number; the readout's start is keyed "readout", whatever round it is scoped to.
    this.starts = new Map();
    const roundSpans = new Set();
    for (const { t, r } of this.events) {
      if (r.kind !== "span" || r.ended_at !== null) continue;
      if (r.name === "round") roundSpans.add(r.round);
      const key = r.name === "readout" ? "readout" : r.name === "round" ? r.round : undefined;
      if (key !== undefined && !this.starts.has(key)) this.starts.set(key, { t, readout: key === "readout" });
    }
    // A jump lands just before the round's first pass closes: its first wave of lanes is on screen.
    const ro = this.starts.get("readout");
    for (const { t, r } of this.events) {
      if (r.name !== "pass" || r.ended_at === null) continue;
      const st = ro && t >= ro.t && !roundSpans.has(r.round) ? ro : this.starts.get(r.round);
      if (st && st.jump === undefined) st.jump = Math.max(st.t, t - 1e-6);
    }
    // Round ends: the last close of the round finishing (a resumed run may open a round twice); the readout's
    // end is its last span closing. Replaying a round freezes there, on the round's final state.
    this.ends = new Map();
    for (const { t, r } of this.events) {
      if (r.kind !== "span" || r.ended_at === null) continue;
      if (r.name === "close" && r.round !== null) this.ends.set(r.round, t);
      if (r.name === "readout") this.ends.set("readout", t);
    }
    // a round past run.rounds was started and stopped without a close (the run was cut): not offered
    for (const n of [...this.starts.keys()]) if (n !== "readout" && n > this.cfg.rounds && !this.ends.has(n)) this.starts.delete(n);
    this.roundBtns.replaceChildren(...[...this.starts].map(([n, { readout }]) =>
      h("button", { class: "btn seg", type: "button", "data-jump": String(n), onclick: () => this.jumpTo(n) },
        readout ? "readout" : `round ${n}`)));
    this.seek(this.t0);
    this.root.dataset.state = "ready";
  }

  setSpeed(sp) {
    this.speed = sp;
    for (const b of this.speedBtns) setClass(b, "on", Number(b.dataset.speed) === sp);
  }

  toggle() {
    if (this.playing) this.pause();
    else this.play();
  }

  play() {
    if (!this.events.length) return;
    if (this.i >= this.events.length) this.seek(this.t0);
    this.playing = true;
    this.root.dataset.state = "playing";
    setText(this.playBtn, "Pause");
    let last = performance.now();
    const frame = (now) => {
      if (!this.playing) return;
      const dt = Math.min(0.25, (now - last) / 1000);   // a background tab resumes without a jump
      last = now;
      const next = this.t + dt * this.speed;
      if (this.stopAt !== null && next >= this.stopAt) {      // freeze on the round's last moment
        this._advance(this.stopAt + 1e-6);
        this.pause();
        this.root.dataset.state = "frozen";
        setText(this.stateLabel, `${this.current === "readout" ? "readout" : `round ${this.current}`} finished · frozen on its last moment`);
        this.stopAt = null;
        return;
      }
      this._advance(next);
      if (this.i >= this.events.length) {
        this.pause();
        this.root.dataset.state = "ended";
        setText(this.stateLabel, "end of the recorded run");
        return;
      }
      this.raf = requestAnimationFrame(frame);
    };
    this.raf = requestAnimationFrame(frame);
  }

  pause() {
    this.playing = false;
    cancelAnimationFrame(this.raf);
    this.raf = null;
    setText(this.playBtn, "Play");
    if (this.root.dataset.state === "playing") this.root.dataset.state = "paused";
  }

  stop() { this.pause(); }

  // Restarts round `n` (or "readout") from its start and plays it, freezing when the round ends.
  jumpTo(n) {
    const key = n !== "readout" && this.state?.rounds.get(n)?.readout ? "readout" : n;
    const st = this.starts?.get(key);
    if (!st) return;
    this.pick = null;
    this.pause();
    this.current = key;
    this.seek(st.t);
    this.stopAt = this.ends?.get(key) ?? null;
    for (const b of this.roundBtns.children) setClass(b, "on", b.dataset.jump === String(key));
    this.play();
  }

  // Rebuilds the state from the first row through time `t`.
  seek(t) {
    this.state = new RunState();
    this.i = 0;
    this.t = this.t0;
    this._advance(t, true);
  }

  _advance(t, force = false) {
    this.t = Math.min(t, this.t1);
    let applied = false;
    while (this.i < this.events.length && this.events[this.i].t <= this.t) {
      this.state.apply(this.events[this.i].r);
      this.i++;
      applied = true;
    }
    if (applied || force) this.view.render(this.state, this.pick);
    this.slider.value = String(Math.round(((this.t - this.t0) / Math.max(1e-9, this.t1 - this.t0)) * 1000));
    setText(this.clock, `T+${fmt.secs(this.t - this.t0)} of ${fmt.secs(this.t1 - this.t0)}`);
    if (this.playing) setText(this.stateLabel, `${this.speed}× · ${this.i} of ${this.events.length} rows`);
    else if (this.root.dataset.state !== "ended") setText(this.stateLabel, `${this.i} of ${this.events.length} rows`);
  }
}
