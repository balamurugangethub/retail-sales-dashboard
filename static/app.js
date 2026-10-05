"use strict";

const $ = (id) => document.getElementById(id);
const state = { preset: "6m", start: "", end: "", store: "", category: "", grain: "month",
                storeMetric: "sales", trendMetric: "sales" };
let meta = null;
const charts = {};
const METRICS = { sales: "Sales", profit: "Profit", orders: "Orders" };
const PALETTE = ["#1f6fe5", "#f1c21b", "#2e9e6b", "#d64545", "#8a5cf6", "#14a3b8"];

const fmtMoney = (v) => v >= 1000 ? "$" + (v / 1000).toFixed(v >= 100000 ? 0 : 1) + "K" : "$" + v.toFixed(0);
const fmtMetric = (m, v) => m === "orders" ? v.toLocaleString() : fmtMoney(v);
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

function el(tag, props = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "text") e.textContent = v; else if (k === "class") e.className = v; else e.setAttribute(k, v);
  }
  children.forEach((c) => e.append(c));
  return e;
}

function notify(msg, isError = false) {
  const n = $("notice");
  n.textContent = msg; n.className = "notice" + (isError ? " error" : ""); n.hidden = false;
  clearTimeout(notify.t); notify.t = setTimeout(() => (n.hidden = true), 6000);
}

async function api(path, params = {}) {
  const q = new URLSearchParams(params);
  const r = await fetch(path + (q.toString() ? "?" + q : ""));
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}

function queryParams(extra = {}) {
  const p = { start: state.start, end: state.end };
  if (state.store) p.store = state.store;
  if (state.category) p.category = state.category;
  return Object.assign(p, extra);
}

// ---- dates ---------------------------------------------------------------
const iso = (d) => d.toISOString().slice(0, 10);
const utc = (s) => new Date(s + "T00:00:00Z");

function applyPreset() {
  if (!meta.max_date) return;
  const max = utc(meta.max_date);
  const monthsBack = (n) => new Date(Date.UTC(max.getUTCFullYear(), max.getUTCMonth() - (n - 1), 1));
  if (state.preset === "6m") state.start = iso(monthsBack(6));
  else if (state.preset === "3m") state.start = iso(monthsBack(3));
  else if (state.preset === "30d") state.start = iso(new Date(max.getTime() - 29 * 86400000));
  else if (state.preset === "all") state.start = meta.min_date;
  if (state.preset !== "custom") state.end = meta.max_date;
  if (state.start < meta.min_date) state.start = meta.min_date;
  $("f-start").value = state.start; $("f-end").value = state.end;
}

// ---- rendering -------------------------------------------------------------
function renderKpis(k) {
  const defs = [
    ["sales", "Total sales", (v) => "$" + Math.round(v).toLocaleString()],
    ["profit", "Profit", (v) => "$" + Math.round(v).toLocaleString()],
    ["orders", "Orders", (v) => v.toLocaleString()],
    ["basket", "Avg. basket", (v) => "$" + v.toFixed(2)],
  ];
  const box = $("kpis"); box.replaceChildren();
  for (const [key, label, fmt] of defs) {
    const ch = k.change_pct[key];
    const cls = ch === null ? "flat" : ch > 0 ? "up" : ch < 0 ? "down" : "flat";
    const txt = ch === null ? "no previous data" : (ch > 0 ? "▲ +" : ch < 0 ? "▼ " : "") + ch.toFixed(1) + "% vs previous period";
    box.append(el("div", { class: "kpi" }, [
      el("div", { class: "label", text: label }),
      el("div", { class: "value", text: fmt(k.current[key]) }),
      el("div", { class: "delta " + cls, text: txt }),
    ]));
  }
}

function themeText() { return { color: cssVar("--muted"), grid: cssVar("--line") }; }

function upsertChart(id, config) {
  if (charts[id]) { charts[id].destroy(); }
  charts[id] = new Chart($(id), config);
}

function renderStoreChart(rows) {
  const m = state.storeMetric, t = themeText();
  const values = rows.map((r) => r[m]);
  const colors = rows.map((r) => state.store && r.store !== state.store ? "#9db0c9" : PALETTE[0]);
  upsertChart("chart-store", {
    type: "bar",
    data: { labels: rows.map((r) => r.store), datasets: [{ label: METRICS[m], data: values, backgroundColor: colors, borderRadius: 4 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => METRICS[m] + ": " + fmtMetric(m, c.parsed.y) } } },
      scales: { x: { ticks: { color: t.color }, grid: { display: false } },
                y: { beginAtZero: true, ticks: { color: t.color, callback: (v) => fmtMetric(m, v) }, grid: { color: t.grid } } },
      onClick: (_e, els) => {
        if (!els.length) return;
        const s = rows[els[0].index].store;
        setStore(state.store === s ? "" : s);
      },
      onHover: (e, els) => { e.native.target.style.cursor = els.length ? "pointer" : "default"; },
    },
  });
}

function renderTrendChart(rows) {
  const m = state.trendMetric, t = themeText();
  upsertChart("chart-trend", {
    type: "line",
    data: { labels: rows.map((r) => r.period), datasets: [{ label: METRICS[m], data: rows.map((r) => r[m]),
      borderColor: PALETTE[0], backgroundColor: PALETTE[0] + "33", fill: true, tension: .25, pointRadius: rows.length > 40 ? 0 : 4 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => METRICS[m] + ": " + fmtMetric(m, c.parsed.y) } } },
      scales: { x: { ticks: { color: t.color, maxTicksLimit: 12 }, grid: { display: false } },
                y: { beginAtZero: true, ticks: { color: t.color, callback: (v) => fmtMetric(m, v) }, grid: { color: t.grid } } },
    },
  });
}

function renderCategories(rows) {
  upsertChart("chart-cat", {
    type: "doughnut",
    data: { labels: rows.map((r) => r.category), datasets: [{ data: rows.map((r) => r.sales), backgroundColor: PALETTE, borderWidth: 0 }] },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: "58%",
      plugins: { legend: { position: "right", labels: { color: cssVar("--ink") } },
                 tooltip: { callbacks: { label: (c) => c.label + ": " + fmtMoney(c.parsed) } } },
      onClick: (_e, els) => {
        if (!els.length) return;
        const c = rows[els[0].index].category;
        setCategory(state.category === c ? "" : c);
      },
    },
  });
}

function renderInsights(items) {
  const ul = $("insights"); ul.replaceChildren();
  if (!items.length) ul.append(el("li", { class: "info", text: "No data for this selection." }));
  items.forEach((i) => ul.append(el("li", { class: i.level, text: i.text })));
}

function renderHeatmap(rows) {
  const t = $("heatmap"); t.replaceChildren();
  if (!rows.length) return;
  const periods = [...new Set(rows.map((r) => r.period))].sort();
  const stores = [...new Set(rows.map((r) => r.store))].sort();
  const lookup = new Map(rows.map((r) => [r.store + "|" + r.period, r.sales]));
  const max = Math.max(...rows.map((r) => r.sales)), min = Math.min(...rows.map((r) => r.sales));
  t.append(el("thead", {}, [el("tr", {}, [el("th", { text: "Store" }), ...periods.map((p) => el("th", { text: p.slice(2) }))])]));
  const body = el("tbody");
  stores.forEach((s) => {
    const tr = el("tr", {}, [el("td", { class: "store", text: s, title: "Filter to " + s })]);
    tr.firstChild.addEventListener("click", () => setStore(state.store === s ? "" : s));
    periods.forEach((p) => {
      const v = lookup.get(s + "|" + p) || 0;
      const a = max === min ? 0.4 : 0.12 + 0.78 * (v - min) / (max - min);
      const td = el("td", { class: "cell", text: fmtMoney(v) });
      td.style.background = `rgba(31,111,229,${a.toFixed(2)})`;
      td.style.color = a > 0.55 ? "#fff" : cssVar("--ink");
      tr.append(td);
    });
    body.append(tr);
  });
  t.append(body);
}

function renderProducts(rows) {
  const t = $("products"); t.replaceChildren();
  t.append(el("thead", {}, [el("tr", {}, ["Product", "Category", "Sales", "Units"].map((h, i) => el("th", { text: h, style: i === 1 ? "text-align:left" : "" })))]));
  const body = el("tbody");
  rows.forEach((r) => body.append(el("tr", {}, [
    el("td", { text: r.product }), el("td", { text: r.category, style: "text-align:left" }),
    el("td", { text: fmtMoney(r.sales) }), el("td", { text: r.units.toLocaleString() })])));
  t.append(body);
}

// ---- data flow ---------------------------------------------------------------
async function refresh() {
  try {
    const [kpis, byStore, trend, cats, ins, heat, prods] = await Promise.all([
      api("/api/kpis", queryParams()),
      api("/api/by-store", { start: state.start, end: state.end, ...(state.category && { category: state.category }) }),
      api("/api/trend", queryParams({ grain: state.grain })),
      api("/api/categories", { start: state.start, end: state.end, ...(state.store && { store: state.store }) }),
      api("/api/insights", queryParams()),
      api("/api/heatmap", queryParams()),
      api("/api/top-products", queryParams({ limit: 8 })),
    ]);
    renderKpis(kpis); renderStoreChart(byStore); renderTrendChart(trend); renderCategories(cats);
    renderInsights(ins); renderHeatmap(heat); renderProducts(prods);
    $("export-btn").href = "/api/export.csv?" + new URLSearchParams(queryParams());
  } catch (e) {
    notify("Could not load data: " + e.message, true);
  }
}

function setStore(v) { state.store = v; $("f-store").value = v; refresh(); }
function setCategory(v) { state.category = v; $("f-category").value = v; refresh(); }

function buildSeg(target, key) {
  const box = document.querySelector(`.seg[data-target="${target}"]`);
  Object.entries(METRICS).forEach(([m, label]) => {
    const b = el("button", { text: label, type: "button" });
    b.addEventListener("click", () => { state[key] = m; syncSeg(); refresh(); });
    b.dataset.m = m; box.append(b);
  });
}
function syncSeg() {
  document.querySelectorAll(".seg").forEach((box) => {
    const key = box.dataset.target === "store" ? "storeMetric" : "trendMetric";
    box.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.m === state[key]));
  });
}

async function loadMeta() {
  meta = await api("/api/meta");
  const fill = (sel, items) => { sel.length = 1; items.forEach((i) => sel.append(new Option(i, i))); };
  fill($("f-store"), meta.stores); fill($("f-category"), meta.categories);
  $("f-store").value = state.store; $("f-category").value = state.category;
  $("f-start").min = $("f-end").min = meta.min_date; $("f-start").max = $("f-end").max = meta.max_date;
  applyPreset();
  // Public demo: hide the buttons that would change data
  if (meta.read_only) document.querySelectorAll("label.btn, #reset-btn").forEach((b) => (b.hidden = true));
}

async function init() {
  buildSeg("store", "storeMetric"); buildSeg("trend", "trendMetric"); syncSeg();
  await loadMeta();
  $("f-preset").addEventListener("change", (e) => { state.preset = e.target.value; applyPreset(); refresh(); });
  const dateChange = () => { state.start = $("f-start").value; state.end = $("f-end").value;
    state.preset = "custom"; $("f-preset").value = "custom"; if (state.start && state.end) refresh(); };
  $("f-start").addEventListener("change", dateChange); $("f-end").addEventListener("change", dateChange);
  $("f-store").addEventListener("change", (e) => setStore(e.target.value));
  $("f-category").addEventListener("change", (e) => setCategory(e.target.value));
  $("grain").addEventListener("change", (e) => { state.grain = e.target.value; refresh(); });
  $("clear-btn").addEventListener("click", () => {
    state.store = state.category = ""; state.preset = "6m"; $("f-preset").value = "6m";
    $("f-store").value = $("f-category").value = ""; applyPreset(); refresh(); });

  $("upload-input").addEventListener("change", async (e) => {
    const file = e.target.files[0]; if (!file) return;
    const replace = confirm("Replace all current data with this file?\n\nOK = replace, Cancel = add to existing data.");
    const fd = new FormData(); fd.append("file", file); fd.append("mode", replace ? "replace" : "append");
    try {
      const r = await fetch("/api/upload", { method: "POST", body: fd });
      const body = await r.json();
      if (!r.ok) throw new Error(body.error);
      $("data-badge").textContent = "Your data";
      notify(`Imported ${body.imported.toLocaleString()} rows.`);
      state.store = state.category = ""; state.preset = "all"; $("f-preset").value = "all";
      await loadMeta(); await refresh();
    } catch (err) { notify("Upload failed: " + err.message, true); }
    e.target.value = "";
  });

  $("reset-btn").addEventListener("click", async () => {
    if (!confirm("Restore the original sample data? Any uploaded data will be removed.")) return;
    await fetch("/api/reset", { method: "POST" });
    $("data-badge").textContent = "Sample data";
    state.store = state.category = ""; state.preset = "6m"; $("f-preset").value = "6m";
    await loadMeta(); await refresh(); notify("Sample data restored.");
  });
  await refresh();
}

init().catch((e) => notify("Failed to start: " + e.message, true));
