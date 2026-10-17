// The page shell: loads the run constants and the export once, then builds each page on first visit.

import { GalleryPage } from "./gallery.js";
import { installTooltip, term } from "./glossary.js";
import { LivePage } from "./live.js";
import { ReadoutPage } from "./readout.js";
import { ReplayPage } from "./replay.js";
import { TimelinePage } from "./timeline.js";
import { StagePage } from "./stage.js";
import { cellText, emptyState, getJSON, h } from "./util.js";

const TABS = ["stage", "gallery", "live", "replay", "timeline", "readout"];
const pages = {};
let cfg = null;
let doc = null;
let exportError = null;
let cells = new Map();

async function loadExport() {
  try {
    doc = await getJSON("/api/export");
  } catch (e) {
    exportError = e.status === 404 ? "The export is not written yet: the run has not reached its first export." : e.message;
  }
  try {
    const sp = await getJSON("/api/spans?run=main&name_prefix=pass");
    const passSpans = sp.spans.filter((x) => x.name === "pass");
    cells = new Map(passSpans.filter((x) => cellText(x.attrs.cell)).map((x) => [x.pass_id, cellText(x.attrs.cell)]));
    // A pass whose span records no cell (readout spans before the runner wrote it) takes the cell another
    // span of the same request recorded, matched through the export's category (the request id).
    if (doc) {
      const byRequest = new Map();
      for (const x of passSpans) {
        const c = cellText(x.attrs.cell);
        if (c && x.attrs.request_id && !byRequest.has(x.attrs.request_id)) byRequest.set(x.attrs.request_id, c);
      }
      for (const p of doc.passes) {
        if (!cells.has(p.id) && byRequest.has(p.category)) cells.set(p.id, byRequest.get(p.category));
      }
    }
  } catch {
    cells = new Map();   // the gallery then reads "cell unread"
  }
}

function needsExport(el) {
  if (doc) return true;
  el.replaceChildren(emptyState("Nothing to show yet.", exportError ?? "loading"));
  return false;
}

function build(tab) {
  const el = document.getElementById(`page-${tab}`);
  if (pages[tab]) return pages[tab];
  if (tab === "live") pages.live = new LivePage(el, cfg);
  else if (tab === "replay") pages.replay = new ReplayPage(el, cfg);
  else if (!needsExport(el)) return null;
  else if (tab === "stage") pages.stage = new StagePage(el, cfg, doc, cells);
  else if (tab === "gallery") pages.gallery = new GalleryPage(el, cfg, doc, cells);
  else if (tab === "timeline") pages.timeline = new TimelinePage(el, cfg, doc);
  else if (tab === "readout") pages.readout = new ReadoutPage(el, cfg, doc);
  return pages[tab];
}

function show() {
  const fromHash = location.hash.slice(1);
  const tab = TABS.includes(fromHash) ? fromHash : (location.pathname === "/stage" ? "stage" : "gallery");
  for (const t of TABS) {
    const on = t === tab;
    document.getElementById(`page-${t}`).classList.toggle("on", on);
    document.querySelector(`.tabs [data-tab="${t}"]`).classList.toggle("on", on);
  }
  for (const t of ["live", "replay", "stage"]) if (t !== tab) pages[t]?.stop();
  const page = build(tab);
  if (tab === "live") page.start();
  if (tab === "replay") page.load();
  if (tab === "stage") page?.start();
  document.body.dataset.tab = tab;
}

async function boot() {
  installTooltip();
  cfg = await getJSON("/api/config");
  await loadExport();
  const meta = document.getElementById("runmeta");
  meta.replaceChildren(
    h("span", {}, `${cfg.rounds} rounds`),
    h("span", {}, term("drift round"), ` ${cfg.drift_round}`),
    doc ? h("span", { class: "muted" }, `run spec ${doc.run.spec_hash.slice(0, 8)}`) : null);
  window.addEventListener("hashchange", show);
  show();
  document.body.dataset.ready = "1";
}

boot();
