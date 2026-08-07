"""Tiny local read-only web dashboard for property-hunter data.

Serves a single self-contained HTML page plus a JSON endpoint backed by the
SQLite database (opened read-only). Uses only the standard library, so there
are no new dependencies; bind to 127.0.0.1 by default.
"""

from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from property_hunter.config import Settings


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql)]


def read_data(db_path: Path) -> dict:
    """Read all dashboard data from the database (read-only)."""
    if not Path(db_path).exists():
        return {"error": f"database not found: {db_path}"}

    conn = _connect_ro(db_path)
    try:
        runs = _rows(conn, """
            SELECT id, trigger, status, started_at, finished_at FROM runs
            ORDER BY id DESC
        """)
        listings = _rows(conn, """
            SELECT l.id, l.source_listing_id, l.source_url, l.operation, l.property_type,
                   l.street_address, l.barrio, l.region, l.lat, l.lng, l.beds, l.baths,
                   l.covered_area_m2, l.total_area_m2, l.agency_name, l.date_posted,
                   l.first_seen_at, l.last_seen_at, l.is_active,
                   o.price_cents AS ask_cents, o.currency,
                   p.predicted_price_cents AS predicted_cents, p.is_fallback,
                   (SELECT COUNT(*) FROM detections d WHERE d.listing_id = l.id AND d.status = 'active')
                       AS active_detections,
                   (SELECT COUNT(*) FROM detections d WHERE d.listing_id = l.id) AS total_detections
            FROM listings l
            LEFT JOIN observations o ON o.id = (
                SELECT o2.id FROM observations o2 WHERE o2.listing_id = l.id
                ORDER BY o2.observed_at DESC, o2.id DESC LIMIT 1)
            LEFT JOIN predictions p ON p.id = (
                SELECT p2.id FROM predictions p2 WHERE p2.listing_id = l.id
                ORDER BY p2.predicted_at DESC, p2.id DESC LIMIT 1)
            ORDER BY l.last_seen_at DESC, l.id DESC
        """)
        detections = _rows(conn, """
            SELECT d.id, d.run_id, d.signals, d.score, d.status, d.first_seen_at, d.created_at,
                   l.street_address, l.barrio, l.region, l.operation, l.source_url,
                   l.beds, l.baths, l.covered_area_m2, l.total_area_m2,
                   o.price_cents AS ask_cents, o.currency,
                   n.status AS notif_status, n.attempt_count, n.last_error
            FROM detections d
            JOIN listings l ON l.id = d.listing_id
            LEFT JOIN observations o ON o.listing_id = d.listing_id AND o.run_id = d.run_id
            LEFT JOIN notifications n ON n.detection_id = d.id
            ORDER BY d.score DESC, d.id DESC
        """)
        for d in detections:
            try:
                d["signals_list"] = json.loads(d["signals"])
            except (TypeError, ValueError):
                d["signals_list"] = []
        baselines = _rows(conn, """
            SELECT b.id, z.region, z.barrio, b.operation, b.property_type,
                   b.observation_count, b.is_sufficient,
                   b.median_price_cents, b.median_rent_cents, b.median_price_per_m2_cents,
                   b.window_start, b.window_end, b.computed_at
            FROM baselines b JOIN zones z ON z.id = b.zone_id
            ORDER BY b.computed_at DESC, z.region, z.barrio
        """)
        models = _rows(conn, """
            SELECT id, run_id, trained_at, training_count, r2_score, mae_cents,
                   is_current, notes
            FROM model_versions ORDER BY id DESC
        """)
        predictions = _rows(conn, """
            SELECT id, listing_id, run_id, model_version_id, predicted_price_cents,
                   is_fallback, predicted_at
            FROM predictions ORDER BY id DESC
        """)
        notifications = _rows(conn, """
            SELECT id, detection_id, run_id, channel, recipient, status,
                   attempt_count, last_error
            FROM notifications ORDER BY id DESC
        """)
    finally:
        conn.close()

    return {
        "db_path": str(db_path),
        "runs": runs,
        "listings": listings,
        "detections": detections,
        "baselines": baselines,
        "models": models,
        "predictions": predictions,
        "notifications": notifications,
    }


_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Property Hunter</title>
<style>
:root {
  --bg: #0f1420; --panel: #161d2c; --panel2: #1c2436; --border: #2a3450;
  --text: #dbe2f0; --muted: #8b94ab; --accent: #5b8cff; --good: #3fb96b;
  --bad: #e05858; --warn: #e0a13f; --fallback: #c9a227;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
       font: 14px/1.45 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
header { padding: 14px 20px; border-bottom: 1px solid var(--border);
         display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
         background: var(--panel); position: sticky; top: 0; z-index: 5; }
header h1 { margin: 0; font-size: 16px; letter-spacing: .4px; }
header .sub { color: var(--muted); font-size: 12px; }
header .spacer { flex: 1; }
button { background: var(--accent); color: #fff; border: 0; border-radius: 6px;
         padding: 6px 12px; cursor: pointer; font-size: 13px; }
button:disabled { opacity: .5; cursor: default; }
main { max-width: 1200px; margin: 0 auto; padding: 18px 20px 60px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
         gap: 10px; margin-bottom: 18px; }
.card { background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
        padding: 12px 14px; }
.card .n { font-size: 22px; font-weight: 600; }
.card .l { color: var(--muted); font-size: 12px; margin-top: 2px; }
.card.warn .n { color: var(--warn); } .card.good .n { color: var(--good); }
.card.bad .n { color: var(--bad); }
.tabs { display: flex; gap: 6px; border-bottom: 1px solid var(--border);
        margin-bottom: 14px; flex-wrap: wrap; }
.tabs button { background: transparent; color: var(--muted); border: 1px solid transparent;
               border-radius: 6px 6px 0 0; }
.tabs button.active { color: var(--text); background: var(--panel);
                      border-color: var(--border); border-bottom-color: var(--panel); }
.filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; align-items: center; }
.filters input, .filters select { background: var(--panel2); color: var(--text);
        border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; font-size: 13px; }
.filters label { color: var(--muted); font-size: 13px; display: flex; gap: 5px;
                 align-items: center; }
table { width: 100%; border-collapse: collapse; background: var(--panel);
        border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--border);
         font-size: 13px; white-space: nowrap; }
th { color: var(--muted); font-weight: 600; cursor: pointer; user-select: none; }
th.sorted { color: var(--text); }
tbody tr:hover { background: var(--panel2); }
.muted { color: var(--muted); }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11px;
         font-weight: 600; }
.badge.good { background: rgba(63,185,107,.15); color: var(--good); }
.badge.warn { background: rgba(224,161,63,.15); color: var(--warn); }
.badge.bad  { background: rgba(224,88,88,.15); color: var(--bad); }
.badge.plain { background: rgba(139,148,171,.15); color: var(--muted); }
.badge.acc   { background: rgba(91,140,255,.15); color: var(--accent); }
.sig { font-size: 12px; color: var(--muted); }
.err { background: rgba(224,88,88,.12); border: 1px solid rgba(224,88,88,.4);
       padding: 14px; border-radius: 8px; color: var(--bad); }
#toast { position: fixed; bottom: 18px; right: 18px; background: var(--panel2);
         border: 1px solid var(--border); padding: 10px 14px; border-radius: 8px;
         display: none; }
#overlay { position: fixed; inset: 0; background: rgba(6,10,18,.7); display: none;
           align-items: center; justify-content: center; z-index: 20; padding: 20px; }
#overlay.open { display: flex; }
.modal { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
         max-width: 760px; width: 100%; max-height: 86vh; overflow-y: auto;
         padding: 18px 22px; position: relative; }
.modal h2 { margin: 0 0 4px; font-size: 15px; }
.modal .close { position: absolute; top: 10px; right: 14px; background: none;
                color: var(--muted); font-size: 20px; cursor: pointer; border: 0; }
.modal h3 { font-size: 13px; margin: 18px 0 6px; color: var(--accent);
            text-transform: uppercase; letter-spacing: .5px; }
.modal h3:first-of-type { margin-top: 10px; }
.modal p, .modal li { color: var(--text); font-size: 13px; line-height: 1.55; }
.modal ul { margin: 4px 0 0; padding-left: 20px; }
.modal code { background: var(--panel2); border: 1px solid var(--border);
              border-radius: 4px; padding: 0 4px; font-size: 12px; }
.modal .kbd { color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1>Property Hunter</h1>
  <span class="sub" id="dbmeta"></span>
  <span class="spacer"></span>
  <button id="help">Help</button>
  <button id="refresh">Refresh</button>
</header>
<main>
  <div id="err" class="err" style="display:none"></div>
  <div class="cards" id="cards"></div>
  <div class="tabs" id="tabs">
    <button data-tab="listings" class="active">Listings</button>
    <button data-tab="detections">Detections</button>
    <button data-tab="baselines">Baselines</button>
    <button data-tab="runs">Runs</button>
    <button data-tab="model">Model</button>
    <button data-tab="notifications">Notifications</button>
  </div>
  <div id="filters" class="filters"></div>
  <div id="view"></div>
</main>
<div id="toast"></div>
<div id="overlay">
  <div class="modal">
    <button class="close" id="closeHelp" title="Close">×</button>
    <h2>How to read this dashboard</h2>
    <p class="kbd">Every tab re-reads the SQLite database. Currency is <code>USD</code>
      unless the source quoted the listing in pesos (<code>ARS</code>). The counts and
      prices come from the most recent pipeline run unless a newer <code>run-all</code> is running.</p>

    <h3>Summary cards</h3>
    <ul>
      <li><strong>listings / active</strong> — total stored vs. still visible on the source (a delisted property is <code>is_active=0</code> but its history is kept).</li>
      <li><strong>zones</strong> — distinct neighborhoods with at least one listing.</li>
      <li><strong>sufficient baselines</strong> — zones with ≥ <code>MIN_OBSERVATIONS_PER_ZONE</code> observations; only these produce reliable medians (all others are flagged insufficient).</li>
      <li><strong>active detections</strong> — currently-flagged opportunities, not yet superseded by a later run.</li>
      <li><strong>failed notifications</strong> — deliveries that exhausted their retries (e.g. SMTP not configured).</li>
      <li><strong>last run</strong> — status of the most recent pass: <span class="badge good">ok</span>, <span class="badge warn">running</span>, or <span class="badge bad">failed</span>.</li>
      <li><strong>model versions</strong> — how many trained valuation models exist (0 until <code>ML_MIN_TRAIN_SAMPLES</code> is reached).</li>
    </ul>

    <h3>Listings</h3>
    <ul>
      <li><strong>Ask</strong> — current asking price; <strong>Est. value</strong> — the model's market estimate (or the zone price-per-m² fallback).</li>
      <li><span class="badge good">model</span> means the estimate came from a trained model; <span class="badge warn">fb</span> means fallback (model not trained yet, or the listing has no zone).</li>
      <li>The <strong>Det</strong> badge shows how many <em>active</em> detections a listing currently has (red = flagged).</li>
      <li><strong>off</strong> badge next to an id means the property has been delisted. Click the id to open the source page.</li>
      <li>Use the search box and dropdowns to filter; click any column header to sort (asc/desc).</li>
    </ul>

    <h3>Detections</h3>
    <ul>
      <li>Each row is one property flagged by an opportunity rule; the <strong>Signals</strong> column shows what fired and the numbers behind it, e.g. <code>undervaluation: ARS 5.200.000 → ARS 17.797.831</code> = asking price vs. expected value.</li>
      <li><strong>Score</strong> is the fraction of enabled rules that fired (e.g. 33% = 1 of 3). The exact signals are more informative than the score.</li>
      <li><strong>Status</strong> <span class="badge warn">active</span> means current; superseded detections for the same property from older runs are kept as history.</li>
      <li><strong>Notify</strong> shows whether the digest was delivered, is pending, or failed (<span class="badge bad">failed</span> → check SMTP config / the error in the Notifications tab).</li>
    </ul>

    <h3>Baselines</h3>
    <ul>
      <li>Per neighborhood × operation medians over the configured window (<code>BASELINE_WINDOW_DAYS</code>).</li>
      <li><strong>Median price / rent</strong> — typical asking price/rent; <strong>Median $/m²</strong> — value per square meter, the basis of the fallback estimate.</li>
      <li><span class="badge good">sufficient</span> = enough observations (≥ <code>MIN_OBSERVATIONS_PER_ZONE</code>) to trust; <span class="badge plain">insufficient</span> = single-digit listings, unreliable.</li>
    </ul>

    <h3>Runs</h3>
    <ul>
      <li>One row per pipeline pass (manual <code>collect</code>/<code>run-all</code>, or the daily scheduler trigger).</li>
      <li><span class="badge good">ok</span> = all stages succeeded; <span class="badge warn">running</span> = in progress; <span class="badge bad">failed</span> = a stage aborted.</li>
    </ul>

    <h3>Model</h3>
    <ul>
      <li><strong>Predictions</strong> — one value estimate per active sale listing.</li>
      <li><strong>Fallback %</strong> — share of estimates using the zone median instead of a trained model. This stays high until you collect ≥ <code>ML_MIN_TRAIN_SAMPLES</code> sales.</li>
      <li>Model rows show training set size and measured quality: <strong>R²</strong> (fit, closer to 1 is better) and <strong>MAE</strong> (average error in money).</li>
    </ul>

    <h3>Notifications</h3>
    <ul>
      <li>One row per detection per notify pass. <span class="badge good">delivered</span> = mail sent; <span class="badge warn">pending</span> = queued; <span class="badge bad">failed</span> = retries exhausted.</li>
      <li><strong>Attempts</strong> and <strong>Error</strong> explain failures (commonly “SMTP not configured” — set <code>SMTP_HOST</code>/<code>SMTP_USER</code>/<code>ALERT_EMAIL</code>).</li>
    </ul>
  </div>
</div>
<script>
const state = { data: null, tab: "listings", q: "", op: "", barrio: "",
                onlyActive: false, sort: null, dir: 1 };
const $ = id => document.getElementById(id);

function money(cents, currency) {
  if (cents == null || cents === "") return "—";
  const code = currency === "ARS" ? "ARS" : "USD";
  try { return (cents / 100).toLocaleString("es-AR",
    { style: "currency", currency: code, maximumFractionDigits: 0 }); }
  catch { return (cents / 100) + " " + code; }
}
function area(v) { return v == null ? "—" : v + " m²"; }
function esc(s) { if (s == null) return ""; return String(s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;"); }
function badge(text, cls) { return `<span class="badge ${cls}">${esc(text)}</span>`; }
function link(url, label) { return `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>`; }

function fmtTs(ts) { return ts ? ts.replace("T", " ").slice(0, 19) + " UTC" : "—"; }

function refresh() {
  fetch("/api/data").then(r => r.json()).then(data => {
    state.data = data;
    if (data.error) { $("err").style.display = ""; $("err").textContent = data.error; return; }
    $("err").style.display = "none";
    $("dbmeta").textContent = data.db_path;
    renderCards(); renderFilters(); render();
  }).catch(e => {
    $("err").style.display = ""; $("err").textContent = "Failed to load data: " + e;
  });
}

function renderCards() {
  const d = state.data;
  const active = d.listings.filter(l => l.is_active);
  const dets = d.detections.filter(x => x.status === "active");
  const sufs = d.baselines.filter(b => b.is_sufficient).length;
  const last = d.runs[0];
  const failedN = d.notifications.filter(n => n.status === "failed").length;
  const html = `
   <div class="card"><div class="n">${d.listings.length}</div><div class="l">listings</div></div>
   <div class="card good"><div class="n">${active.length}</div><div class="l">active</div></div>
   <div class="card"><div class="n">${new Set(d.baselines.map(b=>b.barrio)).size}</div><div class="l">zones</div></div>
   <div class="card"><div class="n">${sufs}</div><div class="l">sufficient baselines</div></div>
   <div class="card ${dets.length ? "warn" : "good"}"><div class="n">${dets.length}</div><div class="l">active detections</div></div>
   <div class="card ${failedN ? "bad" : "good"}"><div class="n">${failedN}</div><div class="l">failed notifications</div></div>
   <div class="card"><div class="n">${last ? badge(last.status, last.status === "ok" ? "good" : last.status === "running" ? "warn" : "bad") : "—"}</div><div class="l">last run (${last ? esc(last.trigger) + " #" + last.id : "none"})</div></div>
   <div class="card"><div class="n">${d.models.length}</div><div class="l">model versions</div></div>`;
  $("cards").innerHTML = html;
}

function renderFilters() {
  const d = state.data;
  const barrios = [...new Set(d.listings.map(l => l.barrio).filter(Boolean))].sort();
  const ops = [...new Set(d.listings.map(l => l.operation).filter(Boolean))].sort();
  $("filters").innerHTML = `
    <input id="q" placeholder="Search address, barrio, agency…" value="${esc(state.q)}">
    <select id="op"><option value="">all ops</option>
      ${ops.map(o => `<option value="${esc(o)}" ${o === state.op ? "selected" : ""}>${esc(o)}</option>`).join("")}
    </select>
    <select id="barrio"><option value="">all barrios</option>
      ${barrios.map(b => `<option value="${esc(b)}" ${b === state.barrio ? "selected" : ""}>${esc(b)}</option>`).join("")}
    </select>
    <label><input type="checkbox" id="onlyActive" ${state.onlyActive ? "checked" : ""}> active only</label>`;
  $("q").addEventListener("input", e => { state.q = e.target.value; render(); });
  $("op").addEventListener("change", e => { state.op = e.target.value; render(); });
  $("barrio").addEventListener("change", e => { state.barrio = e.target.value; render(); });
  $("onlyActive").addEventListener("change", e => { state.onlyActive = e.target.checked; render(); });
}

function sortRows(rows, sort, dir) {
  if (!sort) return rows;
  return [...rows].sort((a, b) => {
    let x = a[sort], y = b[sort];
    if (typeof x === "string") { x = (x || "").toLowerCase(); y = (y || "").toLowerCase(); }
    if (x == null) x = ""; if (y == null) y = "";
    if (x < y) return -1 * dir; if (x > y) return 1 * dir; return 0;
  });
}
function header(label, key, col) {
  const dir = state.sort === key ? state.dir : 0;
  const cls = dir ? "sorted" : "";
  const arrow = dir === 1 ? " ▲" : dir === -1 ? " ▼" : "";
  return `<th data-sort="${key}" data-col="${col}" class="${cls}">${label}${arrow}</th>`;
}
function bindSort() {
  document.querySelectorAll("th[data-sort]").forEach(th => th.addEventListener("click", () => {
    const key = th.dataset.sort;
    if (state.sort === key) state.dir *= -1; else { state.sort = key; state.dir = 1; }
    render();
  }));
}

function filteredListings() {
  const q = state.q.toLowerCase();
  return state.data.listings.filter(l => {
    if (state.op && l.operation !== state.op) return false;
    if (state.barrio && l.barrio !== state.barrio) return false;
    if (state.onlyActive && !l.is_active) return false;
    if (q) {
      const hay = [l.street_address, l.barrio, l.region, l.agency_name, String(l.source_listing_id)].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function render() {
  const d = state.data;
  if (state.tab === "listings") renderListings();
  else if (state.tab === "detections") renderDetections();
  else if (state.tab === "baselines") renderBaselines();
  else if (state.tab === "runs") renderRuns();
  else if (state.tab === "model") renderModel();
  else if (state.tab === "notifications") renderNotifications();
  $("filters").style.display = state.tab === "listings" ? "" : "none";
  document.querySelectorAll("#tabs button").forEach(b =>
    b.classList.toggle("active", b.dataset.tab === state.tab));
  bindSort();
}

function renderListings() {
  const rows = sortRows(filteredListings(), state.sort, state.dir);
  $("view").innerHTML = `<table>
    <thead><tr>
      ${header("ID", "source_listing_id", "id")}
      ${header("Address", "street_address", "addr")}
      ${header("Barrio", "barrio", "barrio")}
      ${header("Region", "region", "region")}
      ${header("Op", "operation", "op")}
      ${header("Type", "property_type", "type")}
      ${header("Beds", "beds", "beds")}
      ${header("Area", "covered_area_m2", "area")}
      ${header("Ask", "ask_cents", "ask")}
      ${header("Est. value", "predicted_cents", "pred")}
      ${header("Agency", "agency_name", "agency")}
      <th>Det</th>
      ${header("Seen", "last_seen_at", "seen")}
    </tr></thead>
    <tbody>${
      rows.map(l => `<tr>
        <td>${l.is_active ? "" : badge("off", "plain")} ${link(l.source_url, l.source_listing_id)}</td>
        <td>${esc(l.street_address) || "—"}</td>
        <td>${esc(l.barrio)}</td>
        <td class="muted">${esc(l.region)}</td>
        <td>${esc(l.operation)}</td>
        <td class="muted">${esc(l.property_type)}</td>
        <td class="num">${l.beds ?? "—"}</td>
        <td class="num">${area(l.covered_area_m2)}</td>
        <td class="num">${money(l.ask_cents, l.currency)}</td>
        <td class="num">${l.predicted_cents == null ? "—" :
            money(l.predicted_cents, l.currency) + (l.is_fallback ? badge("fb", "warn") : badge("model", "good"))}</td>
        <td class="muted">${esc(l.agency_name) || "—"}</td>
        <td>${l.active_detections ? badge(l.active_detections, "bad") : l.total_detections ? badge(l.total_detections, "plain") : "—"}</td>
        <td class="muted">${fmtTs(l.last_seen_at)}</td>
      </tr>`).join("")
    }
    </tbody></table>
    <p class="muted">${rows.length} of ${state.data.listings.length} listings</p>`;
}

function renderDetections() {
  const rows = sortRows(state.data.detections, state.sort, state.dir);
  $("view").innerHTML = `<table>
    <thead><tr>
      ${header("ID", "id", "id")}
      ${header("Barrio", "barrio", "barrio")}
      ${header("Address", "street_address", "addr")}
      ${header("Ask", "ask_cents", "ask")}
      <th>Signals</th>
      <th>Score</th>
      <th>Status</th>
      <th>Notify</th>
    </tr></thead>
    <tbody>${
      rows.map(d => {
        const sigs = (d.signals_list || []).map(s =>
          `${esc(s.type)}: ${money(s.observed, d.currency)} → ${money(s.expected, d.currency)}`).join("<br>");
        return `<tr>
        <td>${link(d.source_url, d.id)}</td>
        <td>${esc(d.barrio)}</td>
        <td>${esc(d.street_address) || "—"}</td>
        <td class="num">${money(d.ask_cents, d.currency)}</td>
        <td class="sig">${sigs}</td>
        <td class="num">${(d.score * 100).toFixed(0)}%</td>
        <td>${badge(d.status, d.status === "active" ? "warn" : "plain")}</td>
        <td>${d.notif_status ? badge(d.notif_status, d.notif_status === "delivered" ? "good" : d.notif_status === "failed" ? "bad" : "plain") : "—"}</td>
      </tr>`; }).join("")
    }
    </tbody></table>
    <p class="muted">${rows.length} detections</p>`;
}

function renderBaselines() {
  const rows = sortRows(state.data.baselines, state.sort, state.dir);
  $("view").innerHTML = `<table>
    <thead><tr>
      ${header("Barrio", "barrio", "barrio")}
      ${header("Region", "region", "region")}
      ${header("Op", "operation", "op")}
      ${header("Type", "property_type", "type")}
      ${header("Obs", "observation_count", "obs")}
      <th>Status</th>
      ${header("Median price", "median_price_cents", "price")}
      ${header("Median rent", "median_rent_cents", "rent")}
      ${header("Median $/m²", "median_price_per_m2_cents", "ppm2")}
    </tr></thead>
    <tbody>${
      rows.map(b => `<tr>
        <td>${esc(b.barrio)}</td>
        <td class="muted">${esc(b.region)}</td>
        <td>${esc(b.operation)}</td>
        <td class="muted">${esc(b.property_type) || "all"}</td>
        <td class="num">${b.observation_count}</td>
        <td>${b.is_sufficient ? badge("sufficient", "good") : badge("insufficient", "plain")}</td>
        <td class="num">${money(b.median_price_cents, "USD")}</td>
        <td class="num">${money(b.median_rent_cents, "USD")}</td>
        <td class="num">${money(b.median_price_per_m2_cents, "USD")}</td>
      </tr>`).join("")
    }
    </tbody></table>
    <p class="muted">${rows.length} baselines (window shown: most recent computed_at)</p>`;
}

function renderRuns() {
  const rows = state.data.runs;
  $("view").innerHTML = `<table>
    <thead><tr><th>ID</th><th>Trigger</th><th>Status</th><th>Started</th><th>Finished</th></tr></thead>
    <tbody>${rows.map(r => `<tr>
      <td>${r.id}</td><td>${esc(r.trigger)}</td>
      <td>${badge(r.status, r.status === "ok" ? "good" : r.status === "running" ? "warn" : "bad")}</td>
      <td class="muted">${fmtTs(r.started_at)}</td>
      <td class="muted">${fmtTs(r.finished_at)}</td>
    </tr>`).join("")}</tbody></table>`;
}

function renderModel() {
  const d = state.data;
  const models = d.models.length ? d.models.map(m => `<tr>
      <td>${m.id}</td><td>${m.run_id}</td>
      <td class="num">${m.training_count}</td>
      <td class="num">${m.r2_score == null ? "—" : Number(m.r2_score).toFixed(3)}</td>
      <td class="num">${money(m.mae_cents, "USD")}</td>
      <td>${m.is_current ? badge("current", "good") : badge("superseded", "plain")}</td>
      <td class="muted">${fmtTs(m.trained_at)}</td></tr>`).join("")
    : `<tr><td colspan="7" class="muted">No model trained yet — predictions use the zone fallback while below ML_MIN_TRAIN_SAMPLES.</td></tr>`;
  const fb = d.predictions.filter(p => p.is_fallback).length;
  $("view").innerHTML = `
    <div class="cards">
      <div class="card"><div class="n">${d.predictions.length}</div><div class="l">predictions</div></div>
      <div class="card warn"><div class="n">${fb}</div><div class="l">fallback (${fb ? (100 * fb / d.predictions.length).toFixed(0) : 0}%)</div></div>
    </div>
    <table><thead><tr><th>ID</th><th>Run</th><th>Count</th><th>R²</th><th>MAE</th><th>Status</th><th>Trained</th></tr></thead>
    <tbody>${models}</tbody></table>`;
}

function renderNotifications() {
  const rows = sortRows(state.data.notifications, state.sort, state.dir);
  $("view").innerHTML = `<table>
    <thead><tr>
      ${header("ID", "id", "id")}
      ${header("Detection", "detection_id", "det")}
      ${header("Run", "run_id", "run")}
      <th>Channel</th><th>Recipient</th><th>Status</th><th>Attempts</th><th>Error</th>
    </tr></thead>
    <tbody>${rows.map(n => `<tr>
      <td>${n.id}</td><td class="num">${n.detection_id}</td><td class="num">${n.run_id}</td>
      <td>${esc(n.channel)}</td><td class="muted">${esc(n.recipient)}</td>
      <td>${badge(n.status, n.status === "delivered" ? "good" : n.status === "failed" ? "bad" : n.status === "pending" ? "warn" : "plain")}</td>
      <td class="num">${n.attempt_count}</td>
      <td class="muted">${esc(n.last_error) || "—"}</td>
    </tr>`).join("")}</tbody></table>`;
}

document.querySelectorAll("#tabs button").forEach(b =>
  b.addEventListener("click", () => { state.tab = b.dataset.tab; state.sort = null; render(); }));
$("refresh").addEventListener("click", refresh);

const overlay = $("overlay");
$("help").addEventListener("click", () => overlay.classList.add("open"));
$("closeHelp").addEventListener("click", () => overlay.classList.remove("open"));
overlay.addEventListener("click", e => { if (e.target === overlay) overlay.classList.remove("open"); });
document.addEventListener("keydown", e => { if (e.key === "Escape") overlay.classList.remove("open"); });

refresh();
</script>
</body>
</html>"""


class _Handler(BaseHTTPRequestHandler):
    server: "UIHTTPServer"  # type: ignore[assignment]

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._send(200, "text/html; charset=utf-8", _HTML.encode("utf-8"))
        elif path == "/api/data":
            payload = json.dumps(read_data(self.server.db_path), default=str).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", payload)
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # quiet default access logs
        pass


class UIHTTPServer(ThreadingHTTPServer):
    def __init__(self, db_path: Path, addr: tuple[str, int]):
        self.db_path = db_path
        super().__init__(addr, _Handler)
        self.daemon_threads = True


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the dashboard until interrupted. Prints the URL."""
    server = UIHTTPServer(settings.db_path, (host, port))
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"Property Hunter dashboard: {url}  (db: {settings.db_path})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
