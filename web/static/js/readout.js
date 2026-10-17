// Readout: the SPEC § 12 readings as the export holds them. R1 and R2 show their mean, p and one-sided
// lower bound against the claim threshold on a number line (never a two-sided interval). Unread and failed
// readings are shown as such: "unread" is never drawn as 0, and an unearned verdict is never styled as earned.

import { markFirst, term } from "./glossary.js";
import { fmt, h, s } from "./util.js";

const EARNED = new Set(["improves", "leaner than shared notes", "not shown worse than shared notes", "forgets what went stale", "the judge learns what to look at"]);

function verdictEl(v) {
  if (v === null || v === undefined) return h("span", { class: "verdict v-unread", "data-verdict": "unread" }, "unread");
  const cls = EARNED.has(v) ? "v-earned" : v === "reported" ? "v-reported" : v === "unresolved" ? "v-unresolved" : "v-unearned";
  return h("span", { class: `verdict ${cls}`, "data-verdict": v }, v);
}

// A number line in loss units: the lower bound (or −∞), the claim threshold, the mean. The earned side of
// the threshold is shaded; the bound must lie strictly above the threshold to earn the claim.
function boundLine(c) {
  const W = 560, H = 96, L = 34, R = 34, y = 50;
  const vals = [c.claim_above, c.mean, c.lower_bound, 0].filter((v) => v !== null && v !== undefined);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max(0.05, (hi - lo) * 0.2);
  lo -= pad; hi += pad;
  const x = (v) => L + ((v - lo) / (hi - lo)) * (W - L - R);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "bound-line", role: "img",
    "aria-label": `${c.reading} lower bound against its claim threshold` });
  svg.append(s("rect", { x: x(c.claim_above), y: y - 14, width: W - R - x(c.claim_above), height: 28, class: "earned-zone" }));
  svg.append(s("line", { x1: L - 20, x2: W - R + 14, y1: y, y2: y, class: "axis-line" }));
  if (c.claim_above !== 0) {
    svg.append(s("line", { x1: x(0), x2: x(0), y1: y - 8, y2: y + 8, class: "zero-tick" }));
    svg.append(s("text", { x: x(0), y: y + 26, class: "tick", "text-anchor": "middle" }, "0"));
  }
  svg.append(s("line", { x1: x(c.claim_above), x2: x(c.claim_above), y1: y - 22, y2: y + 22, class: "threshold" }));
  svg.append(s("text", { x: x(c.claim_above), y: y - 28, class: "threshold-label", "text-anchor": "middle" },
    `claim above ${fmt.loss(c.claim_above)}`));
  const bx = c.lower_bound === null ? L - 20 : x(c.lower_bound);
  svg.append(s("line", { x1: bx, x2: W - R + 14, y1: y, y2: y, class: "bound-ray" }));
  svg.append(s("path", { d: `M${bx + 8},${y - 12} L${bx},${y - 12} L${bx},${y + 12} L${bx + 8},${y + 12}`, class: "bound-mark" }));
  svg.append(s("text", { x: Math.max(bx, 6), y: y + 28, class: "bound-label", "text-anchor": c.lower_bound === null ? "start" : "middle" },
    c.lower_bound === null ? "bound −∞" : `bound ${fmt.loss(c.lower_bound)}`));
  if (c.mean !== null) {
    svg.append(s("circle", { cx: x(c.mean), cy: y, r: 7, class: "mean-dot" }));
    svg.append(s("text", { x: x(c.mean), y: y - 16, class: "mean-label", "text-anchor": "middle" }, `mean ${fmt.loss(c.mean)}`));
  }
  return svg;
}

function pairedCard(c, title, claim, extra) {
  const head = h("div", { class: "rd-head" }, h("div", {}, h("div", { class: "rd-id" }, c.reading), h("div", { class: "rd-title" }, title)),
    verdictEl(c.verdict));
  if (c.population === null) {
    return h("div", { class: "rd-card wide is-unread", "data-reading": c.reading }, head,
      h("p", { class: "rd-unread" }, "unread: no held-out category was scored in both arms",
        c.unread_categories.length ? ` (${c.unread_categories.join(", ")})` : ""), extra);
  }
  const level = `${Math.round(c.level * 100)} %`;
  const note = c.lower_bound === null ? `no ${level} bound attainable at ${c.n_categories} categories` : `${level} one-sided lower bound`;
  return h("div", { class: "rd-card wide", "data-reading": c.reading }, head,
    h("div", { class: "rd-pop muted" }, `loss(${c.baseline}) − loss(journal) over ${c.population}`),
    h("div", { class: "rd-main" },
      h("div", { class: "rd-figs" },
        h("div", { class: "fig" }, h("div", { class: "fig-v", "data-fig": `${c.reading}-mean` }, fmt.loss(c.mean)), h("div", { class: "fig-k muted" }, "mean difference")),
        h("div", { class: "fig" }, h("div", { class: "fig-v", "data-fig": `${c.reading}-p` }, fmt.p(c.p)), h("div", { class: "fig-k muted" }, "one-sided p")),
        h("div", { class: "fig" }, h("div", { class: "fig-v", "data-fig": `${c.reading}-bound` }, c.lower_bound === null ? "−∞" : fmt.loss(c.lower_bound)),
          h("div", { class: "fig-k muted" }, note))),
      boundLine(c)),
    h("p", { class: "rd-claim muted" }, claim),
    c.unread_categories.length ? h("p", { class: "rd-warn" }, `unread categories: ${c.unread_categories.join(", ")}`) : null,
    extra);
}

function simpleCard(id, title, verdict, rows, note) {
  return h("div", { class: `rd-card${verdict === null ? " is-unread" : ""}`, "data-reading": id },
    h("div", { class: "rd-head" }, h("div", {}, h("div", { class: "rd-id" }, id), h("div", { class: "rd-title" }, title)),
      verdictEl(verdict)),
    note ? h("div", { class: "rd-pop muted" }, note) : null,
    h("div", { class: "rd-rows" }, ...rows.map(([k, v]) => h("div", { class: "kv" }, h("span", { class: "muted" }, k), h("b", {}, v)))));
}

const list = (xs) => (xs.length ? xs.join(", ") : "none");

export class ReadoutPage {
  constructor(root, cfg, doc) {
    const r = doc.readout;
    const arms = ["empty", "notes", "journal"];
    const tok = r.tokens_composed;
    // The one figure derived here: the ratio of the two export means, as the readout lines print it.
    const ratio = tok.journal.mean === null || tok.notes.mean === null || tok.notes.mean === 0 ? null : tok.journal.mean / tok.notes.mean;
    const tokensBeside = h("div", { class: "rd-beside" },
      h("div", { class: "muted small" }, "tokens composed per pass, beside R2 (\"leaner\" needs the journal arm's mean below the notes arm's)"),
      h("div", { class: "kvs" },
        ...arms.map((a) => h("div", { class: "kv" }, h("span", { class: "muted" }, `${a} · ${tok[a].n} passes`), h("b", {}, fmt.tokens(tok[a].mean)))),
        h("div", { class: "kv" }, h("span", { class: "muted" }, "journal ÷ notes"), h("b", { "data-fig": "token-ratio" }, fmt.value(ratio)))));
    const r3 = r.r3, r4 = r.r4, ht = r.hook_transfer;
    const head = h("div", { class: "page-head" }, h("div", {},
      h("h1", {}, "The readout"),
      h("p", { class: "lede" }, "The ", term("readout"), " builds each request of the ", term("held-out set"), " in every ",
        term("arm"), ": ", term("empty"), ", ", term("notes"), " and ", term("journal"), ". Every pre-registered reading is shown, failed and unread ones included. ",
        h("span", { class: "muted" }, `Planned: ${r.population}.`))));
    const grid = h("div", { class: "rd-grid" },
      pairedCard(r.r1, "Version N+1: Friday against no memory",
        `"improves" is earned when the lower bound lies above ${fmt.loss(r.r1.claim_above)} (the same event as p ≤ the pre-registered threshold)`),
      pairedCard(r.r2, "Lean and useful: Friday against shared notes",
        `"leaner than shared notes" is earned when the lower bound lies above ${fmt.loss(r.r2.claim_above)}: no worse than the notes arm by more than the margin`,
        tokensBeside),
      simpleCard("R3", "Earned forgetting", r3.verdict, r3.verdict === null ? [["reading", "unread: no decision drafted"]] : [
        ["admitted before the drift, retired after", `${r3.admitted_before_retired_after.length} (${list(r3.admitted_before_retired_after)})`],
        ["admitted after the drift", `${r3.admitted_after.length} (${list(r3.admitted_after)})`],
        ["moved to suspect after the drift", `${r3.suspected_after.length} (${list(r3.suspected_after)})`],
        ["retired by steer (not counted)", String(r3.steered_retired_after.length)],
        ["candidates tested c · α · c", `${fmt.int(r3.c)} · ${fmt.mean2(r3.alpha_c)}`]], r3.population),
      simpleCard("R4", "The judge's eyes grow", r4.verdict, r4.verdict === null ? [["reading", "unread: no lens set"]] : [
        ["minted lenses kept active", `${r4.kept_minted.length} (${list(r4.kept_minted)})`],
        ["minted lenses still hidden", `${r4.hidden_minted.length} (${list(r4.hidden_minted)})`]], r4.population));
    const desc = h("div", { class: "rd-desc" },
      simpleCard("Cost", "Spend", "reported", [["total", fmt.usd(r.cost.total_usd)], ["decisions admitted", String(r.cost.admitted)],
        ["per admitted decision", fmt.usd(r.cost.usd_per_admitted)]]),
      simpleCard("Quality", "Q per arm, no claim", "reported", arms.map((a) => [`${a} · ${r.quality[a].n} passes`, fmt.mean2(r.quality[a].mean)])),
      simpleCard("How-to", "How-to honesty, no claim", "reported", arms.map((a) => {
        const x = r.how_to[a];
        return [`${a} · ${x.n_passes} passes`, `passed ${fmt.share(x.declared_passed_share.mean)} · ids declared ${fmt.share(x.contract_declared_share.mean)} · silent ${fmt.mean2(x.silent_per_pass.mean)}`];
      })),
      simpleCard("Hook transfer", "Held-out requests a decision fires on, no claim", ht.population === null ? null : "reported",
        ht.population === null ? [["reading", ht.untagged.length ? `unread: ${ht.untagged.length} held-out requests untagged` : "unread: no held-out set"]]
          : [["fires on", `${ht.requests_fired.length} (${list(ht.requests_fired)})`], ["decisions in S*", list(ht.s_star)]], ht.population),
      simpleCard("Unscored", "Readout passes failed for transport or without an eval", "reported",
        arms.map((a) => [a, String(r.unscored[a])])));
    root.append(head, grid, h("h2", { class: "section-title" }, "Descriptive readings"), desc);
    markFirst(root);
  }
}
