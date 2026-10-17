// Stage: one built app at a time, full screen, for presenting. The playlist (the "deck") and every figure on
// the stage are read from the export; the app runs in the same sandboxed iframe as the gallery (scripts
// only, an opaque origin, the server's CSP).
//
// Keys: ← → previous / next, 1-9 jump, Enter hands the keyboard to the app, Esc takes it back (the server's
// relay posts Escape out of the app), P splits the stage into the pass and its partner(s), F hides the
// caption bar, L opens the playlist.

import { term } from "./glossary.js";
import { armChip, emptyState, fmt, h, lossColor } from "./util.js";

const MAX_FRAMES = 3;          // live iframes on the stage at once
const TOP_PAIRS = 3;           // pairs with the largest exposure delta appended to the default deck
const ARMS = ["empty", "notes", "journal"];
const ESCAPE_MESSAGE = "friday:stage-escape";

const isScored = (p) => Boolean(p.artifact_path) && !p.transport_failed && p.loss !== null && p.loss !== undefined;
const roundLabel = (p) => (p.readout ? "readout" : `round ${p.round}`);

// ---- the deck ---------------------------------------------------------------------------------------

// For each cell its lowest-loss scored pass (training and readout), ordered by cell; then the exposed pass
// of each pair with the largest exposure delta d. Entries: {id, why}.
export function defaultDeck(doc, cells) {
  const best = new Map();
  for (const p of doc.passes) {
    if (!isScored(p)) continue;
    const cell = cells.get(p.id) ?? "cell unread";
    const cur = best.get(cell);
    if (!cur || p.loss < cur.loss || (p.loss === cur.loss && p.id < cur.id)) best.set(cell, p);
  }
  const deck = [...best.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([cell, p]) => ({ id: p.id, why: `lowest loss in ${cell}` }));
  const seen = new Set(deck.map((e) => e.id));
  const deltas = [];
  for (const d of doc.decisions) {
    for (const delta of d.ledger.exposure.deltas) deltas.push({ decision: d.id, ...delta });
  }
  deltas.sort((a, b) => b.d - a.d);
  let added = 0;
  for (const delta of deltas) {
    if (added >= TOP_PAIRS) break;
    const exposed = doc.passes.find((p) => p.round === delta.round && p.category === delta.category
      && p.exposed === delta.decision && isScored(p));
    if (!exposed || seen.has(exposed.id)) continue;
    deck.push({ id: exposed.id, why: `pair with d ${fmt.loss(delta.d)} for ${delta.decision}` });
    seen.add(exposed.id);
    added += 1;
  }
  return deck;
}

// The pass ids of `?deck=` that the export holds as built apps, in the given order, and the ids skipped.
export function deckFromIds(doc, ids) {
  const byId = new Map(doc.passes.map((p) => [p.id, p]));
  const deck = [];
  const skipped = [];
  for (const id of ids) {
    const p = byId.get(id);
    if (p && p.artifact_path) deck.push({ id, why: "from the deck link" });
    else skipped.push(id);
  }
  return { deck, skipped };
}

// ---- partners ---------------------------------------------------------------------------------------

// The pair partner of journal-arm training pass `p`, the decision the two differ by, and d from that
// decision's Ledger account. Returns {partner, decision, kind, delta} or {refused: reason}.
export function pairOf(doc, p) {
  if (p.readout || p.arm !== "journal" || p.pair_slot === null || p.pair_slot === undefined) {
    return { refused: `${p.id} is not one side of a journal-arm pair` };
  }
  const others = doc.passes.filter((q) => q.round === p.round && q.category === p.category && q.arm === "journal"
    && !q.readout && q.pair_slot !== null && q.pair_slot !== p.pair_slot && !q.transport_failed && q.artifact_path);
  if (!others.length) return { refused: `no built partner recorded for ${p.id}` };
  const partner = others[others.length - 1];         // a re-run replaces the failed slot it re-ran
  const decisionId = p.exposed ?? partner.exposed ?? p.ablated ?? partner.ablated ?? null;
  const a = new Set(p.decisions_composed);
  const b = new Set(partner.decisions_composed);
  const differ = [...a].filter((x) => !b.has(x)).concat([...b].filter((x) => !a.has(x)));
  if (differ.some((x) => x !== decisionId) || p.notes !== partner.notes) {
    return { refused: `${p.id} and ${partner.id} differ by more than the decision under test` };
  }
  if (!decisionId) return { partner, decision: null, kind: null, delta: null };
  const decision = doc.decisions.find((d) => d.id === decisionId) ?? null;
  const kind = (p.exposed ?? partner.exposed) ? "exposure" : "ablation";
  const delta = decision?.ledger[kind].deltas.find((x) => x.round === p.round && x.category === p.category) ?? null;
  return { partner, decision, decisionId, kind, delta };
}

// For readout pass `p`: the same held-out request in each arm, the same rep (by id order) where it exists.
export function armsOf(doc, p) {
  const rep = (arm) => doc.passes.filter((q) => q.readout && q.category === p.category && q.arm === arm && isScored(q))
    .sort((x, y) => (x.id < y.id ? -1 : 1));
  const mine = rep(p.arm);
  const k = Math.max(0, mine.findIndex((q) => q.id === p.id));
  return ARMS.map((arm) => {
    const list = rep(arm);
    return { arm, pass: arm === p.arm ? p : (list[k] ?? list[0] ?? null) };
  });
}

// ---- the page ---------------------------------------------------------------------------------------

export class StagePage {
  constructor(root, cfg, doc, cells) {
    this.root = root;
    this.cfg = cfg;
    this.doc = doc;
    this.cells = cells;
    this.byId = new Map(doc.passes.map((p) => [p.id, p]));
    this.frames = new Map();                 // pass id -> iframe (at most MAX_FRAMES)
    this.split = false;
    this.bare = false;
    this.i = 0;
    const q = new URLSearchParams(location.search).get("deck");
    if (q !== null) {
      const { deck, skipped } = deckFromIds(doc, q.split(",").map((x) => x.trim()).filter(Boolean));
      this.deck = deck;
      this.skipped = skipped;
    } else {
      this.deck = defaultDeck(doc, cells);
      this.skipped = [];
    }
    this.onKey = (e) => this._key(e);
    this.onMessage = (e) => this._message(e);
    this._build();
  }

  start() {
    document.addEventListener("keydown", this.onKey);
    window.addEventListener("message", this.onMessage);
    this.root.focus({ preventScroll: true });
    this._render();
  }

  stop() {
    document.removeEventListener("keydown", this.onKey);
    window.removeEventListener("message", this.onMessage);
    for (const f of this.frames.values()) f.remove();     // unload every app when the stage closes
    this.frames.clear();
  }

  _build() {
    this.pos = h("span", { class: "stage-pos" });
    this.cap = h("div", { class: "stage-cap" });
    this.cols = h("div", { class: "stage-cols", hidden: true });
    this.stageArea = h("div", { class: "stage-frames" });
    this.mid = h("div", { class: "stage-mid", hidden: true });
    this.stageArea.append(this.mid);
    this.note = h("div", { class: "stage-note", role: "status" });
    this.toast = h("div", { class: "stage-toast", role: "status", hidden: true });
    this.link = h("input", { class: "stage-link", readonly: true, "aria-label": "deck link", hidden: true });
    const btn = (label, key, fn, cls = "") => h("button", { class: `btn stage-btn ${cls}`, type: "button", "data-key": key,
      onclick: (e) => { e.currentTarget.blur(); fn(); } }, label);
    this.pairBtn = btn("Pair  P", "p", () => this.toggleSplit());
    this.bar = h("div", { class: "stage-bar" },
      h("div", { class: "stage-bar-main" }, this.pos, this.cap),
      h("div", { class: "stage-bar-side" },
        this.pairBtn, btn("Playlist  L", "l", () => this.togglePicker()),
        btn("Copy deck link", "c", () => this.copyLink(), "copy"),
        h("a", { class: "btn stage-btn", href: "#gallery" }, "Exit")),
      h("div", { class: "stage-hint muted" }, "← → move · 1-9 jump · Enter play the app · Esc back to the stage · ",
        "P pair · F hide this bar · L playlist"));
    this.picker = h("div", { class: "stage-picker", hidden: true });
    this.root.append(this.bar, this.cols, this.stageArea, this.note, this.toast, this.link, this.picker);
    if (this.skipped.length) {
      this.note.textContent = `Skipped ${this.skipped.length} id${this.skipped.length > 1 ? "s" : ""} the export `
        + `holds no built app for: ${this.skipped.join(", ")}`;
    }
    if (!this.deck.length) {
      this.stageArea.replaceChildren(emptyState("The deck is empty.", "No built app in the export matches it."));
    }
  }

  get current() {
    return this.deck.length ? this.byId.get(this.deck[this.i].id) : null;
  }

  go(i) {
    if (!this.deck.length) return;
    this.i = Math.max(0, Math.min(this.deck.length - 1, i));
    this.split = false;
    this._render();
  }

  toggleSplit() {
    const p = this.current;
    if (!p) return;
    if (!this.split && !p.readout) {
      const pair = pairOf(this.doc, p);
      if (pair.refused) {
        this._flash(pair.refused);
        return;
      }
    }
    this.split = !this.split;
    this._render();
  }

  toggleBare() {
    this.bare = !this.bare;
    this.root.classList.toggle("bare", this.bare);
  }

  togglePicker() {
    if (!this.picker.hidden) {
      this.picker.hidden = true;
      return;
    }
    this.picker.replaceChildren(h("div", { class: "picker-head" }, h("h2", {}, "Playlist"),
      h("span", { class: "muted small" }, "Enter or click to open")),
      h("ol", { class: "picker-list" }, ...this.deck.map((e, i) => {
        const p = this.byId.get(e.id);
        const loss = h("span", { class: "loss-chip" }, `loss ${fmt.loss(p.loss)}`);
        loss.style.setProperty("--loss-color", lossColor(p.loss));
        return h("li", {}, h("button", { type: "button", class: `picker-item${i === this.i ? " on" : ""}`, "data-i": String(i),
          onclick: () => { this.picker.hidden = true; this.go(i); } },
        h("span", { class: "picker-n" }, String(i + 1)), h("span", { class: "picker-id" }, p.category),
        armChip(p.arm), h("span", { class: "muted" }, roundLabel(p)), loss, h("span", { class: "muted small" }, e.why)));
      })));
    this.picker.hidden = false;
    this.picker.querySelector(".picker-item.on")?.focus();
  }

  deckLink() {
    return `${location.origin}/stage?deck=${this.deck.map((e) => encodeURIComponent(e.id)).join(",")}`;
  }

  async copyLink() {
    const url = this.deckLink();
    this.link.value = url;
    try {
      await navigator.clipboard.writeText(url);
      this._flash("Deck link copied");
    } catch {
      this.link.hidden = false;                     // no clipboard: show the link to copy by hand
      this.link.select();
      this._flash("Copy the deck link from the box");
    }
  }

  _flash(text) {
    this.toast.textContent = text;
    this.toast.hidden = false;
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => { this.toast.hidden = true; }, 2600);
  }

  // Escape inside an app arrives as the relay's message; only a message from one of the stage's frames counts.
  _message(e) {
    if (e.data !== ESCAPE_MESSAGE) return;
    if (![...this.frames.values()].some((f) => f.contentWindow === e.source)) return;
    this.releaseApp();
  }

  releaseApp() {
    document.activeElement?.blur?.();
    this.root.focus({ preventScroll: true });
  }

  playApp() {
    const f = this.stageArea.querySelector("iframe.slot-main, iframe.slot-c0");
    if (f) f.focus();
  }

  _key(e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
    const k = e.key;
    let used = true;
    if (k === "ArrowRight") this.go(this.i + 1);
    else if (k === "ArrowLeft") this.go(this.i - 1);
    else if (/^[1-9]$/.test(k)) this.go(Number(k) - 1);
    else if (k === "Enter" && !this.picker.hidden) {
      const b = document.activeElement?.closest?.(".picker-item");
      if (b) b.click(); else used = false;
    } else if (k === "Enter") this.playApp();
    else if (k === "Escape") {
      if (!this.picker.hidden) this.picker.hidden = true;
      this.releaseApp();
    } else if (k === "f" || k === "F") this.toggleBare();
    else if (k === "p" || k === "P") this.toggleSplit();
    else if (k === "l" || k === "L") this.togglePicker();
    else used = false;
    if (used) e.preventDefault();
  }

  // ---- frames -----------------------------------------------------------------------------------------

  // Places `layout` ([{id, slot}]) on the stage: frames not in it are unloaded first, so at most
  // MAX_FRAMES live at once; a frame already loaded keeps its document (it is moved by class only).
  _place(layout) {
    const want = new Set(layout.slice(0, MAX_FRAMES).map((x) => x.id));
    for (const [id, f] of this.frames) {
      if (!want.has(id)) {
        f.remove();
        this.frames.delete(id);
      }
    }
    for (const { id, slot } of layout.slice(0, MAX_FRAMES)) {
      let f = this.frames.get(id);
      if (!f) {
        f = h("iframe", { class: "stage-frame", sandbox: "allow-scripts", referrerpolicy: "no-referrer",
          title: `app built by pass ${id}`, "data-pass": id, src: `/apps/${encodeURIComponent(id)}` });
        f.addEventListener("load", () => { f.dataset.loaded = "1"; });
        this.frames.set(id, f);
        this.stageArea.append(f);
      }
      f.className = `stage-frame ${slot}`;
      f.tabIndex = slot === "slot-off" ? -1 : 0;
      f.setAttribute("aria-hidden", slot === "slot-off" ? "true" : "false");
    }
  }

  _render() {
    const p = this.current;
    if (!p) return;
    this.pos.textContent = `${this.i + 1} / ${this.deck.length}`;
    this.root.dataset.pass = p.id;
    if (this.split && p.readout) this._renderArms(p);
    else if (this.split) this._renderPair(p);
    else this._renderSingle(p);
    this.root.dataset.mode = this.split ? (p.readout ? "arms" : "pair") : "single";
    this.pairBtn.classList.toggle("on", this.split);
  }

  _renderSingle(p) {
    const layout = [{ id: p.id, slot: "slot-main" }];
    const next = this.deck[this.i + 1];
    const prev = this.deck[this.i - 1];
    if (next) layout.push({ id: next.id, slot: "slot-off" });
    if (prev) layout.push({ id: prev.id, slot: "slot-off" });
    this._place(layout);
    this.cols.hidden = true;
    this.mid.hidden = true;
    this.cap.replaceChildren(...this._captionOf(p, this.deck[this.i].why));
  }

  _renderPair(p) {
    const pair = pairOf(this.doc, p);
    if (pair.refused) {
      this.split = false;
      this._renderSingle(p);
      this._flash(pair.refused);
      return;
    }
    const [left, right] = p.pair_slot <= pair.partner.pair_slot ? [p, pair.partner] : [pair.partner, p];
    this._place([{ id: left.id, slot: "slot-l" }, { id: right.id, slot: "slot-r" }]);
    const side = (q) => {
      const tag = !pair.decisionId ? "no decision under test"
        : q.decisions_composed.includes(pair.decisionId) ? `with ${pair.decisionId}` : `without ${pair.decisionId}`;
      return this._colHead(q, tag);
    };
    this.cols.className = "stage-cols two";
    this.cols.replaceChildren(side(left), h("div", { class: "col-mid" }), side(right));
    this.cols.hidden = false;
    const dText = pair.delta ? fmt.loss(pair.delta.d) : "unread";
    this.mid.replaceChildren(
      pair.decision
        ? h("div", {}, h("div", { class: "eyebrow" }, term("decision"), " ", pair.decision.id, " · ", pair.kind),
          h("p", { class: "mid-payload" }, pair.decision.payload))
        : h("p", { class: "muted" }, "The two passes compose the same decisions: no decision is under test."),
      h("div", { class: "mid-d" }, term("paired delta", "d"), " ", h("b", { "data-d": dText }, dText)));
    this.mid.hidden = false;
    this.cap.replaceChildren(h("span", { class: "cap-req" }, term("pair"), ` · ${p.category}`),
      h("span", { class: "muted" }, this.cells.get(p.id) ?? "cell unread"),
      h("span", { class: "muted" }, roundLabel(p)));
  }

  _renderArms(p) {
    const arms = armsOf(this.doc, p);
    this._place(arms.filter((a) => a.pass).map((a) => ({ id: a.pass.id, slot: `slot-c${ARMS.indexOf(a.arm)}` })));
    this.cols.className = "stage-cols three";
    this.cols.replaceChildren(...arms.map((a) => (a.pass ? this._colHead(a.pass, null)
      : h("div", { class: "col-head muted", "data-arm": a.arm }, `${a.arm} arm: no scored pass`))));
    this.cols.hidden = false;
    this.mid.hidden = true;
    this.cap.replaceChildren(h("span", { class: "cap-req" }, term("readout"), ` · ${p.category}`),
      h("span", { class: "muted" }, this.cells.get(p.id) ?? "cell unread"),
      h("span", { class: "muted" }, "one held-out request in each arm"));
  }

  _colHead(q, tag) {
    const loss = h("span", { class: "loss-chip", "data-loss": fmt.loss(q.loss) }, `loss ${fmt.loss(q.loss)}`);
    loss.style.setProperty("--loss-color", lossColor(q.loss));
    return h("div", { class: "col-head", "data-pass": q.id, "data-arm": q.arm },
      tag ? h("span", { class: "col-tag" }, tag) : null, armChip(q.arm), loss,
      h("span", { class: "muted small col-id" }, q.id));
  }

  _captionOf(p, why) {
    const loss = h("span", { class: "loss-chip", "data-loss": fmt.loss(p.loss) }, `loss ${fmt.loss(p.loss)}`);
    loss.style.setProperty("--loss-color", lossColor(p.loss));
    const chips = p.decisions_composed.map((d) => h("span", { class: `chip dec${p.exposed === d ? " exp" : ""}` },
      p.exposed === d ? `${d} under test` : d));
    if (p.ablated) chips.push(h("span", { class: "chip dec abl" }, `${p.ablated} omitted`));
    if (p.notes) chips.push(h("span", { class: "chip notes" }, "notes file"));
    return [
      h("span", { class: "cap-id" }, p.category),
      h("span", { class: "cap-summary", title: p.how_to_summary ?? "" }, p.how_to_summary ?? "no how-to read"),
      h("span", { class: "cap-cell muted" }, this.cells.get(p.id) ?? "cell unread"),
      armChip(p.arm), h("span", { class: "muted" }, roundLabel(p)), loss,
      chips.length ? h("span", { class: "cap-chips" }, ...chips) : h("span", { class: "muted small" }, "no decision composed"),
      h("span", { class: "muted small cap-why" }, why),
    ];
  }
}
