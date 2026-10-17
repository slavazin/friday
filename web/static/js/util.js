// Shared helpers: fetching, formatting (one formatter per quantity), the loss colour scale, DOM and SVG.

export async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) {
    const err = new Error(`${url}: ${r.status}`);
    err.status = r.status;
    throw err;
  }
  return r.json();
}

// Every telemetry row matching `params`, paged by after_seq.
export async function allRows(params = {}) {
  const out = [];
  let after = 0;
  for (;;) {
    const q = new URLSearchParams({ ...params, after_seq: String(after) });
    const page = await getJSON(`/api/telemetry?${q}`);
    out.push(...page.rows);
    after = page.last_seq;
    if (!page.more) return { rows: out, available: page.available, lastSeq: after };
  }
}

// ---- formatting: absent data reads "unread", never 0 -------------------------------------------------

const UNREAD = "unread";
const fixed = (d) => (x) => (x === null || x === undefined ? UNREAD : Number(x).toFixed(d));

export const fmt = {
  loss: fixed(3),        // loss, paired delta d, readout differences and intervals (as the readout lines)
  p: fixed(4),           // p-values
  wealth: fixed(2),      // e-process wealth
  value: fixed(3),       // lens value (AUC)
  share: (x) => (x === null || x === undefined ? UNREAD : `${(x * 100).toFixed(0)} %`),
  mean2: fixed(2),       // Q and alpha*c, as the readout lines
  tokens: fixed(1),
  usd: (x) => (x === null || x === undefined ? UNREAD : `$${Number(x).toFixed(2)}`),
  int: (x) => (x === null || x === undefined ? UNREAD : String(x)),
  interval: (iv) => (iv ? `[${fixed(3)(iv[0])}, ${fixed(3)(iv[1])}]` : UNREAD),
  secs: (s) => {
    if (s === null || s === undefined) return "";
    if (s < 60) return `${s.toFixed(1)} s`;
    const m = Math.floor(s / 60);
    return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  },
};

// ---- the loss scale: blue (low loss) to orange (high loss), safe for red-green colour blindness -------

const STOPS = [[0, [77, 163, 255]], [0.5, [214, 208, 198]], [1, [255, 138, 61]]];

export function lossColor(x) {
  if (x === null || x === undefined) return "var(--muted)";
  const v = Math.max(0, Math.min(1, x));
  for (let i = 1; i < STOPS.length; i++) {
    const [b, cb] = STOPS[i];
    const [a, ca] = STOPS[i - 1];
    if (v <= b) {
      const t = (v - a) / (b - a);
      const c = ca.map((c0, k) => Math.round(c0 + (cb[k] - c0) * t));
      return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
    }
  }
  return "rgb(255, 138, 61)";
}

export function lossLegend() {
  const el = h("div", { class: "loss-legend" });
  el.append(h("span", { class: "legend-label" }, "loss 0"));
  const bar = h("span", { class: "legend-bar" });
  el.append(bar, h("span", { class: "legend-label" }, "1"));
  return el;
}

// ---- DOM ----------------------------------------------------------------------------------------------

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function setText(el, text) {
  const t = String(text);
  if (el.textContent !== t) el.textContent = t;
}

export function setClass(el, cls, on) {
  if (el.classList.contains(cls) !== Boolean(on)) el.classList.toggle(cls, Boolean(on));
}

const SVGNS = "http://www.w3.org/2000/svg";

export function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function emptyState(title, detail) {
  return h("div", { class: "empty" }, h("div", { class: "empty-title" }, title), h("div", { class: "muted" }, detail));
}

export function armChip(arm) {
  return h("span", { class: `chip arm arm-${arm}` }, arm);
}
