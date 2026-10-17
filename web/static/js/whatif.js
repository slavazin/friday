// What if: the run's own nomination rules with no in-flight cap would have tested more pools. This page shows
// their decisions, drafted by the run's nominator, and held-out requests built bare and with every one of them
// composed. Illustrative and unscored: none of these decisions passed the gate.
import { frozenApp } from "./gallery.js";
import { emptyState, getJSON, h } from "./util.js";

export class WhatIfPage {
  constructor(root) {
    this.root = root;
    this.load();
  }

  async load() {
    let doc;
    try {
      doc = await getJSON("/api/whatif");
    } catch {
      this.root.replaceChildren(emptyState("No what-if showcase built yet.", "Run scripts/whatif.py."));
      return;
    }
    const decisions = doc.decisions.filter((d) => d.payload);
    const head = h("div", { class: "page-head" }, h("div", {},
      h("h1", {}, "What if every recurring pool had been tested?"),
      h("p", { class: "lede" }, "The run tests two candidate decisions at a time (gate.in_flight), and both held their ",
        "slots all run. With no cap, its own nomination rules would have drafted ", String(decisions.length),
        " decisions. Each request below is built twice by the same worker: bare, and with all of them composed."),
      h("p", { class: "whatif-note" }, doc.note)));
    const list = h("div", { class: "whatif-decisions" }, ...decisions.map((d) => h("div", { class: "whatif-dec" },
      h("div", { class: "whatif-dec-head" }, h("b", {}, d.id), h("span", { class: `chip ${d.actual ? "exp" : "abl"}` },
        d.actual ? "tested in the run" : "held back by the cap"), h("span", { class: "muted small" },
        `nominated at round ${d.nominated_at_round} · ${d.pool}`)),
      h("p", { class: "payload" }, d.payload),
      d.fires_when.length ? h("div", { class: "muted small" }, "fires on ", d.fires_when.join(", ")) : null)));
    const built = new Map(doc.builds.map((b) => [b.id, b]));
    const rows = doc.requests.map((r) => {
      const side = (arm, label) => {
        const b = built.get(`${r.id}-${arm}`);
        return h("div", { class: "whatif-side" }, h("div", { class: "pair-label" }, label),
          b?.built ? h("div", { class: "frame-wrap whatif-frame" }, frozenApp(b.id, `${r.id} ${label}`, "app-frame",
            "/whatif/apps/", "/whatif/posters/")) : emptyState("This build produced no app.", ""));
      };
      return h("div", { class: "whatif-row" },
        h("div", { class: "whatif-req" }, h("b", {}, r.id), r.cell ? h("span", { class: "muted" }, ` · ${r.cell}`) : null,
          h("p", { class: "caption" }, r.text)),
        h("div", { class: "pair-row" }, side("bare", "bare"), side("with", `with all ${decisions.length} decisions`)));
    });
    this.root.replaceChildren(head, h("h2", {}, "The would-be decisions"), list,
      h("h2", {}, "The same requests, bare and with them"), ...rows);
    for (const img of this.root.querySelectorAll("img[data-src]")) img.src = img.dataset.src;   // ten stills: load them all
  }
}
