// Live round: polls the telemetry log every telemetry.live_poll_ms, reading only rows after the last seq
// seen, so a span that opened and closed between two polls is still applied.

import { RoundView } from "./roundview.js";
import { RunState } from "./runstate.js";
import { getJSON, h } from "./util.js";

export class LivePage {
  constructor(root, cfg) {
    this.cfg = cfg;
    this.state = new RunState();
    this.pick = null;
    this.timer = null;
    this.running = false;
    this.inFlight = false;
    this.status = h("div", { class: "poll-status muted" });
    this.view = new RoundView(h("div"), cfg, {
      title: "Live round",
      onPick: (n) => { this.pick = n; this.view.render(this.state, this.pick); },
    });
    root.append(this.view.root, this.status);
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.view.render(this.state, this.pick);
    this._tick();
  }

  stop() {
    this.running = false;
    clearTimeout(this.timer);
    this.timer = null;
  }

  async _tick() {
    if (!this.running || this.inFlight) return;
    this.inFlight = true;
    try {
      let more = true;
      while (more) {
        const q = new URLSearchParams({ after_seq: String(this.state.lastSeq), run: this.cfg.telemetry_run ?? "main" });
        const page = await getJSON(`/api/telemetry?${q}`);
        for (const row of page.rows) this.state.apply(row);
        more = page.more;
        if (!page.available) this.status.textContent = "Waiting for the telemetry log to be written.";
      }
      if (this.state.changed) {
        this.view.render(this.state, this.pick);
        this.state.changed = false;
      }
      this.status.textContent = `Polling the telemetry log every ${this.cfg.live_poll_ms} ms · rows read through seq ${this.state.lastSeq}`;
      this.status.dataset.seq = String(this.state.lastSeq);
    } catch (e) {
      this.status.textContent = `Telemetry unavailable (${e.message}); retrying.`;
    } finally {
      this.inFlight = false;
      if (this.running) {
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this._tick(), this.cfg.live_poll_ms);
      }
    }
  }
}
