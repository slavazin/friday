// Timeline: per decision, its status flips and its wealth path against the 1/α line; per lens, its value
// history; the drift round marked; the git log beside it. Every figure is the export's, drawn as-is.

import { markFirst, term } from "./glossary.js";
import { emptyState, fmt, h, s } from "./util.js";

const W = 860;
const LEFT = 96;
const RIGHT = 120;
const STATUS_COLOR = { candidate: "var(--st-candidate)", admitted: "var(--st-admitted)", suspect: "var(--st-suspect)",
  retired: "var(--st-retired)", expired: "var(--st-retired)", refused: "var(--st-retired)" };
const DECISION_COLORS = ["#c4b5fd", "#67e8f9", "#fda4af", "#bef264", "#fcd34d", "#93c5fd"];

export class TimelinePage {
  constructor(root, cfg, doc) {
    this.cfg = cfg;
    this.doc = doc;
    this.root = root;
    this.rounds = doc.run.rounds;
    this.drift = doc.run.drift_round;
    this.bw = (W - LEFT - RIGHT) / this.rounds;
    this.color = new Map(doc.decisions.map((d, i) => [d.id, DECISION_COLORS[i % DECISION_COLORS.length]]));
    this.selected = null;
    this.lensFocus = null;
    this._build();
  }

  x(endOfRound) { return LEFT + endOfRound * this.bw; }

  _axis(svg, top, bottom, { labels = true } = {}) {
    const g = s("g", { class: "axis" });
    const dx = this.x(this.drift - 1);
    g.append(s("rect", { x: dx, y: top, width: this.x(this.rounds) - dx, height: bottom - top, class: "drift-band" }));
    for (let r = 0; r <= this.rounds; r++) {
      g.append(s("line", { x1: this.x(r), x2: this.x(r), y1: top, y2: bottom, class: "grid" }));
    }
    if (labels) {
      for (let r = 1; r <= this.rounds; r++) {
        g.append(s("text", { x: this.x(r) - this.bw / 2, y: bottom + 18, class: "tick", "text-anchor": "middle" },
          `round ${r}`));
      }
    }
    svg.append(g);
  }

  _build() {
    const head = h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "The Journal over the run"),
        h("p", { class: "lede" }, "Each ", term("decision"), " is drafted from a ", term("pool"), ", tested on ",
          term("pair", "pairs"), " by its ", term("Ledger account"), ", and ", term("admitted", "admitted"),
          " when its ", term("e-process"), " wealth reaches 1/α. From the ", term("drift round"), " on, the ",
          term("regime"), " changes and the evidence can turn.")));
    this.detail = h("div", { class: "tl-detail" });
    const log = h("div", { class: "gitlog" }, h("h3", {}, "git log"),
      ...(this.doc.git_log.length ? this.doc.git_log.map((c) => h("div", { class: "commit" },
        h("span", { class: "sha" }, c.sha.slice(0, 7)), h("span", {}, c.subject)))
        : [h("div", { class: "muted" }, "no commit yet")]));
    const charts = h("div", { class: "tl-charts" }, this._statusChart(), this._wealthChart(), this._lensChart());
    this.root.append(head, h("div", { class: "tl-body" }, charts, h("div", { class: "tl-side" }, this.detail, log)));
    markFirst(this.root);
    this.select(this.doc.decisions.find((d) => d.status_history.length)?.id ?? this.doc.decisions[0]?.id ?? null);
  }

  _statusChart() {
    const ds = this.doc.decisions;
    const rowH = 34;
    const top = 28;
    const height = top + Math.max(1, ds.length) * rowH + 30;
    const svg = s("svg", { viewBox: `0 0 ${W} ${height}`, class: "chart", role: "img",
      "aria-label": "decision status over rounds" });
    this._axis(svg, top - 8, height - 30);
    svg.append(s("text", { x: this.x(this.drift - 1) + 6, y: top - 12, class: "drift-label" },
      `drift round ${this.drift}: regime R1 → R2`));
    ds.forEach((d, i) => {
      const y = top + i * rowH + rowH / 2;
      const g = s("g", { class: "lane", "data-decision": d.id, onclick: () => this.select(d.id) });
      g.append(s("text", { x: 8, y: y + 5, class: "lane-label" }, d.id));
      // Status between flips, from drafting (at the close of drafted_round) to the end of the run.
      let status = "candidate";
      let from = d.drafted_round;
      const segs = [];
      for (const f of d.status_history) {
        segs.push([from, f.round, status]);
        status = f.new;
        from = f.round;
      }
      segs.push([from, this.rounds, status]);
      for (const [a, b, st] of segs) {
        if (b > a) g.append(s("rect", { x: this.x(a), y: y - 9, width: this.x(b) - this.x(a), height: 18, rx: 4,
          fill: STATUS_COLOR[st] ?? "var(--muted)", class: "seg" }, s("title", {}, `${d.id} ${st}`)));
      }
      g.append(s("circle", { cx: this.x(d.drafted_round), cy: y, r: 5, class: "drafted" },
        s("title", {}, `${d.id} drafted at the round ${d.drafted_round} close`)));
      for (const f of d.status_history) {
        g.append(s("line", { x1: this.x(f.round), x2: this.x(f.round), y1: y - 13, y2: y + 13, class: "flip" }));
        g.append(s("text", { x: this.x(f.round) + 5, y: y - 12, class: "flip-label" }, `→ ${f.new}`));
      }
      svg.append(g);
    });
    const legend = h("div", { class: "legend" }, ...Object.entries({ candidate: "candidate", admitted: "admitted",
      suspect: "suspect", retired: "retired" }).map(([k, t]) => {
      const sw = h("span", { class: "swatch" });
      sw.style.setProperty("--c", STATUS_COLOR[k]);
      return h("span", { class: "legend-item" }, sw, k === "suspect" ? term("suspect") : k === "candidate"
        ? term("candidate") : k === "retired" ? term("retire", "retired") : t);
    }));
    return h("div", { class: "panel" }, h("h2", {}, "Status flips"), legend, svg);
  }

  _wealthChart() {
    const height = 250;
    const top = 16;
    const bottom = height - 32;
    const bar = this.cfg.admit_wealth;
    const tested = this.doc.decisions.filter((d) => d.ledger.exposure.wealth_path.length);
    const maxW = Math.max(bar * 1.35, ...tested.flatMap((d) => d.ledger.exposure.wealth_path));
    const minW = Math.min(0.6, ...tested.flatMap((d) => d.ledger.exposure.wealth_path));
    const y = (w) => bottom - ((Math.log(w) - Math.log(minW)) / (Math.log(maxW) - Math.log(minW))) * (bottom - top);
    const svg = s("svg", { viewBox: `0 0 ${W} ${height}`, class: "chart", role: "img", "aria-label": "wealth paths" });
    this._axis(svg, top, bottom);
    for (const w of [1, 2, bar]) {
      if (w < minW || w > maxW) continue;
      svg.append(s("text", { x: LEFT - 8, y: y(w) + 4, class: "tick", "text-anchor": "end" }, fmt.wealth(w)));
    }
    svg.append(s("line", { x1: LEFT, x2: this.x(this.rounds), y1: y(1), y2: y(1), class: "base" }));
    svg.append(s("line", { x1: LEFT, x2: this.x(this.rounds), y1: y(bar), y2: y(bar), class: "bar-line" }));
    svg.append(s("text", { x: LEFT + 6, y: y(bar) - 8, class: "bar-label", "text-anchor": "start" },
      `1/α = ${fmt.wealth(bar)}: the gate admits`));
    for (const d of tested) {
      const deltas = d.ledger.exposure.deltas;
      const path = d.ledger.exposure.wealth_path;
      const perRound = new Map();
      for (const x of deltas) perRound.set(x.round, (perRound.get(x.round) ?? 0) + 1);
      const seen = new Map();
      const pts = [[this.x(deltas[0].round - 1), y(1)]];   // W_0 = 1 before the first pair
      deltas.forEach((x, j) => {
        const k = (seen.get(x.round) ?? 0) + 1;
        seen.set(x.round, k);
        pts.push([this.x(x.round - 1) + (k / perRound.get(x.round)) * this.bw * 0.92, y(path[j])]);
      });
      const g = s("g", { class: "wline", "data-decision": d.id, onclick: () => this.select(d.id) });
      g.style.setProperty("--c", this.color.get(d.id));
      g.append(s("polyline", { points: pts.map((p) => p.join(",")).join(" "), class: "wpath" }));
      pts.slice(1).forEach(([px, py], j) => g.append(s("circle", { cx: px, cy: py, r: 4.5, class: "wpt" },
        s("title", {}, `${d.id} W_${j + 1} = ${fmt.wealth(path[j])} (round ${deltas[j].round}, ${deltas[j].category}, d ${fmt.loss(deltas[j].d)})`))));
      const [lx, ly] = pts[pts.length - 1];
      g.append(s("text", { x: lx + 8, y: ly + 4, class: "wlabel" }, `${d.id} ${fmt.wealth(d.ledger.exposure.wealth)}`));
      svg.append(g);
    }
    return h("div", { class: "panel" }, h("h2", {}, "Wealth against 1/α"),
      h("p", { class: "muted small" }, "one point per ", term("paired delta"), "; log scale; W starts at 1"),
      tested.length ? svg : emptyState("No candidate has taken exposure yet.", ""));
  }

  _lensChart() {
    const height = 220;
    const top = 14;
    const bottom = height - 32;
    const y = (v) => bottom - v * (bottom - top);
    const svg = s("svg", { viewBox: `0 0 ${W} ${height}`, class: "chart", role: "img", "aria-label": "lens values" });
    this._axis(svg, top, bottom);
    for (const v of [0, 0.5, 1]) svg.append(s("text", { x: LEFT - 8, y: y(v) + 4, class: "tick", "text-anchor": "end" }, v.toFixed(1)));
    svg.append(s("line", { x1: LEFT, x2: this.x(this.rounds), y1: y(0.5), y2: y(0.5), class: "base" }));
    const lines = new Map();
    for (const lens of this.doc.lenses) {
      const minted = lens.minted_round !== null;
      const g = s("g", { class: `lline${minted ? " minted" : ""}${lens.status === "superseded" || lens.status === "retired" ? " gone" : ""}`,
        "data-lens": lens.id });
      let run = [];
      const flush = () => { if (run.length > 1) g.append(s("polyline", { points: run.join(" "), class: "lpath" })); run = []; };
      for (const v of lens.value_history) {
        const cx = this.x(v.round) - this.bw / 2;
        if (v.value === null) {
          flush();
          g.append(s("circle", { cx, cy: bottom - 4, r: 4, class: "unread-pt" },
            s("title", {}, `${lens.id} round ${v.round}: unread (${v.n} answers)`)));
          continue;
        }
        run.push(`${cx},${y(v.value)}`);
        g.append(s("circle", { cx, cy: y(v.value), r: 3.5, class: "lpt" },
          s("title", {}, `${lens.id} round ${v.round}: ${fmt.value(v.value)} over ${v.n} answers`)));
      }
      flush();
      lines.set(lens.id, g);
      svg.append(g);
    }
    const legend = h("div", { class: "legend lens-legend" }, ...this.doc.lenses.map((lens) => {
      const b = h("button", { class: `chip lens${lens.minted_round !== null ? " minted" : ""}`, type: "button",
        title: lens.angle, onclick: () => this._focusLens(lens.id, lines) }, lens.id,
      lens.minted_round !== null ? ` minted r${lens.minted_round}` : "", lens.status !== "active" ? ` · ${lens.status}` : "");
      return b;
    }));
    return h("div", { class: "panel" }, h("h2", {}, term("lens value"), " over rounds"),
      h("p", { class: "muted small" }, "hollow points on the axis are unread: too few answered passes in the window; ",
        "minted lenses in colour"), legend, svg);
  }

  _focusLens(id, lines) {
    this.lensFocus = this.lensFocus === id ? null : id;
    for (const [k, g] of lines) {
      g.classList.toggle("focus", k === this.lensFocus);
      g.classList.toggle("dim", this.lensFocus !== null && k !== this.lensFocus);
    }
  }

  select(id) {
    this.selected = id;
    for (const el of this.root.querySelectorAll("[data-decision]")) el.classList.toggle("sel", el.dataset.decision === id);
    const d = this.doc.decisions.find((x) => x.id === id);
    if (!d) {
      this.detail.replaceChildren(emptyState("No decision drafted yet.", ""));
      return;
    }
    const e = d.ledger.exposure;
    const a = d.ledger.ablation;
    const kv = (k, v) => h("div", { class: "kv" }, h("span", { class: "muted" }, k), h("b", {}, v));
    const deltaRows = (rows) => rows.length
      ? h("table", { class: "deltas" }, h("tr", {}, h("th", {}, "round"), h("th", {}, "category"), h("th", {}, "d"), h("th", {}, "dQ")),
        ...rows.map((r) => h("tr", {}, h("td", {}, r.round), h("td", {}, r.category), h("td", {}, fmt.loss(r.d)), h("td", {}, fmt.int(r.dQ)))))
      : h("div", { class: "muted small" }, "no deltas");
    this.detail.replaceChildren(
      h("div", { class: "eyebrow" }, "click a lane or a wealth line"),
      h("h2", { "data-detail": d.id }, d.id, " ", h("span", { class: `status st-${d.status}` }, d.status)),
      h("p", { class: "payload" }, d.payload),
      h("div", { class: "kvs" }, kv("hook fires on", d.fires_when.join(", ") || "—"), kv("not on", d.not_this.join(", ") || "—"),
        kv("drafted", `round ${d.drafted_round} close, from ${d.pool_id ?? "—"}`)),
      h("h3", {}, term("Ledger account"), ": ", term("exposure")),
      h("div", { class: "kvs" }, kv("pairs", fmt.int(e.n)), kv("mean d", fmt.loss(e.mean_d)), kv("wealth", fmt.wealth(e.wealth)),
        kv("mirror wealth", fmt.wealth(e.mirror_wealth))),
      deltaRows(e.deltas),
      h("h3", {}, term("ablation")),
      h("div", { class: "kvs" }, kv("pairs", fmt.int(a.n)), kv("mean d", fmt.loss(a.mean_d)),
        kv("harm wealth since admitted", fmt.wealth(a.harm_wealth_since_admitted)), kv("pairs since admitted", fmt.int(a.n_since_admitted))),
      deltaRows(a.deltas),
      h("h3", {}, "status history"),
      d.status_history.length ? h("ul", { class: "flips" }, ...d.status_history.map((f) => h("li", {},
        `round ${f.round}: ${f.old} → ${f.new} (${f.cause})`))) : h("div", { class: "muted small" }, "no flip yet"),
      h("h3", {}, term("disposition", "dispositions")),
      h("div", { class: "kvs" }, ...(Object.keys(d.fires).length ? Object.entries(d.fires).map(([k, v]) => kv(k, v))
        : [h("span", { class: "muted small" }, "no fire")])));
    markFirst(this.detail);
  }
}
