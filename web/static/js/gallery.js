// Gallery: every built app live in a sandboxed iframe (no allow-same-origin: an app cannot reach this page
// or its API), captioned by its own how-to and tagged with the decisions composed into it.

import { markFirst, term } from "./glossary.js";
import { armChip, emptyState, fmt, h, lossColor } from "./util.js";

function appFrame(passId, title, cls = "app-frame") {
  // src is set only when the frame nears the viewport (lazy).
  return h("iframe", { class: cls, "data-src": `/apps/${encodeURIComponent(passId)}`, sandbox: "allow-scripts",
    loading: "lazy", referrerpolicy: "no-referrer", title });
}

function lazyLoad(root, scroller) {
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const f = e.target;
      if (!f.src) f.src = f.dataset.src;
      io.unobserve(f);
    }
  }, { root: scroller, rootMargin: "300px" });
  for (const f of root.querySelectorAll("iframe[data-src]")) io.observe(f);
  return io;
}

export class GalleryPage {
  constructor(root, cfg, doc, cells) {
    this.cfg = cfg;
    this.doc = doc;
    this.cells = cells;                       // pass id -> cell, from the `pass` spans' attributes
    this.root = root;
    this.byId = new Map(doc.passes.map((p) => [p.id, p]));
    this.filters = { cell: "", arm: "", round: "" };
    this.io = null;
    this._build();
  }

  _roundLabel(p) {
    return p.readout ? "readout" : `round ${p.round}`;
  }

  _build() {
    const built = this.doc.passes.filter((p) => p.artifact_path);
    const sel = (key, label, values) => h("label", { class: "filter" }, h("span", { class: "muted" }, label),
      h("select", { "data-filter": key, onchange: (e) => { this.filters[key] = e.target.value; this._grid(); } },
        h("option", { value: "" }, "all"), ...values.map(([v, t]) => h("option", { value: v }, t))));
    const cells = [...new Set(built.map((p) => this.cells.get(p.id)).filter(Boolean))].sort();
    const arms = [...new Set(built.map((p) => p.arm))].sort();
    const rounds = [...new Set(built.map((p) => this._roundLabel(p)))];
    this.count = h("span", { class: "muted" });
    const head = h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Every app the team built"),
        h("p", { class: "lede" }, "Each frame is one ", term("pass"), ": a ", term("worker"), " built the app once, then the ",
          term("eval"), " scored it. The caption is the app's own ", term("how-to"), "; the chips are the ",
          term("decision", "decisions"), " composed into its prompt. Click a decision to see the passes it changed, ",
          "each beside its ", term("pair"), " partner.")),
      h("div", { class: "filters" }, sel("cell", "cell", cells.map((c) => [c, c])),
        sel("arm", "arm", arms.map((a) => [a, a])), sel("round", "round", rounds.map((r) => [r, r])), this.count));
    this.grid = h("div", { class: "gallery-grid" });
    this.drawer = h("aside", { class: "drawer", hidden: true });
    this.scroller = h("div", { class: "gallery-scroll" }, this.grid);
    this.root.append(head, h("div", { class: "gallery-body" }, this.scroller, this.drawer));
    markFirst(head);
    this._grid();
  }

  _grid() {
    const f = this.filters;
    const shown = this.doc.passes.filter((p) => p.artifact_path
      && (!f.cell || this.cells.get(p.id) === f.cell) && (!f.arm || p.arm === f.arm)
      && (!f.round || this._roundLabel(p) === f.round));
    this.count.textContent = `showing ${shown.length} apps`;
    this.io?.disconnect();
    if (!shown.length) {
      this.grid.replaceChildren(emptyState("No app matches these filters.", "Clear a filter to see more."));
      return;
    }
    this.grid.replaceChildren(...shown.map((p) => this._card(p)));
    this.io = lazyLoad(this.grid, this.scroller);
  }

  _card(p) {
    const caption = p.how_to_summary
      ? h("p", { class: "caption" }, p.how_to_summary)
      : h("p", { class: "caption unread" }, `no how-to read (repair rung ${fmt.int(p.how_to_rung)})`);
    const chips = p.decisions_composed.map((d) => h("button", { class: `chip dec${p.exposed === d ? " exp" : ""}`,
      type: "button", "data-decision": d, onclick: () => this.openDecision(d) }, p.exposed === d ? `${d} under test` : d));
    if (p.ablated) chips.push(h("button", { class: "chip dec abl", type: "button", "data-decision": p.ablated,
      onclick: () => this.openDecision(p.ablated) }, `${p.ablated} omitted`));
    if (p.notes) chips.push(h("span", { class: "chip notes" }, "notes file"));
    const loss = h("span", { class: "loss-chip" }, `loss ${fmt.loss(p.loss)}`);
    loss.style.setProperty("--loss-color", lossColor(p.loss));
    return h("figure", { class: "app-card", "data-pass": p.id },
      h("div", { class: "frame-wrap" }, appFrame(p.id, `app built by pass ${p.id}`)),
      h("figcaption", {},
        h("div", { class: "app-meta" }, h("span", { class: "app-id" }, p.category), armChip(p.arm),
          h("span", { class: "muted" }, this._roundLabel(p)), loss),
        h("div", { class: "muted small" }, this.cells.get(p.id) ?? "cell unread"),
        caption,
        chips.length ? h("div", { class: "chips" }, chips) : h("div", { class: "chips muted small" }, "no decision composed")));
  }

  // The passes decision `id` changed: each pair it was exposed or ablated in, with its partner and the
  // pair's paired delta from the decision's Ledger account.
  openDecision(id) {
    const d = this.doc.decisions.find((x) => x.id === id);
    if (!d) return;
    const pairs = [];
    for (const [kind, account] of [["exposure", d.ledger.exposure], ["ablation", d.ledger.ablation]]) {
      for (const delta of account.deltas) {
        const both = this.doc.passes.filter((p) => p.round === delta.round && p.category === delta.category
          && p.arm === "journal" && !p.transport_failed && p.pair_slot !== null);
        const withIt = both.find((p) => p.decisions_composed.includes(id));
        const without = both.find((p) => !p.decisions_composed.includes(id));
        pairs.push({ kind, delta, withIt, without });
      }
    }
    const side = (p, label) => p
      ? h("div", { class: "pair-side" }, h("div", { class: "pair-label" }, label),
        h("div", { class: "frame-wrap small" }, appFrame(p.id, `app built by pass ${p.id}`, "app-frame mini")),
        h("div", { class: "pair-loss" }, "loss ", h("b", {}, fmt.loss(p.loss))), h("div", { class: "muted small" }, p.id))
      : h("div", { class: "pair-side muted" }, label, ": not recorded");
    const body = pairs.length
      ? pairs.map(({ kind, delta, withIt, without }) => h("div", { class: "pair" },
        h("div", { class: "pair-head" }, h("span", {}, `round ${delta.round} · ${delta.category}`),
          h("span", { class: `chip ${kind === "exposure" ? "exp" : "abl"}` }, kind),
          h("span", { class: "pair-d" }, term("paired delta", "d"), ` ${fmt.loss(delta.d)}`)),
        h("div", { class: "pair-row" }, side(withIt, `with ${id}`), side(without, `without ${id}`))))
      : [emptyState("No pass changed yet.", `${id} has no paired delta in its Ledger account.`)];
    this.drawer.replaceChildren(
      h("div", { class: "drawer-head" }, h("div", {}, h("div", { class: "eyebrow" }, term("decision")),
        h("h2", {}, id, " ", h("span", { class: `status st-${d.status}` }, d.status))),
        h("button", { class: "btn", type: "button", onclick: () => this.closeDrawer() }, "Close")),
      h("p", { class: "payload" }, d.payload),
      h("div", { class: "muted small" }, term("hook"), ": fires on ", d.fires_when.join(", ") || "—",
        d.not_this.length ? ` · not on ${d.not_this.join(", ")}` : ""),
      h("h3", {}, "Passes it changed"), ...body);
    this.drawer.hidden = false;
    markFirst(this.drawer);
    lazyLoad(this.drawer, this.drawer);
  }

  closeDrawer() {
    this.drawer.hidden = true;
    this.drawer.replaceChildren();
  }
}
