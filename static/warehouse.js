"use strict";
// Warehouse page: everything here is read from the dbt marts (see marts.py).

const state = { store: "", start: "", end: "" };
const charts = {};
const money = (v) => "$" + Math.round(v).toLocaleString();
const pounds = (v) => "£" + Math.round(v).toLocaleString();
const ago = (iso) => {
  if (!iso) return "never";
  const h = (Date.now() - new Date(iso.replace(" ", "T") + "Z").getTime()) / 3600000;
  return h < 1 ? `${Math.max(1, Math.round(h * 60))} min ago` : h < 48 ? `${Math.round(h)} h ago` : `${Math.round(h / 24)} days ago`;
};
const dur = (s) => (s == null ? "–" : s < 90 ? `${s.toFixed(1)} s` : `${(s / 60).toFixed(1)} min`);
const FRESH = { pass: "Fresh", warn: "Getting stale", error: "Stale", "not checked": "Not checked" };
const num = (v) => Math.round(v).toLocaleString();

function axes() { return { color: Mono.muted(), grid: Mono.valueGrid(), border: Mono.axisBorder() }; }

function draw(id, config) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart($(id), config);
}

function table(id, head, rows) {
  $(id).replaceChildren(
    el("thead", {}, el("tr", {}, head.map((h) => el("th", { text: h })))),
    el("tbody", {}, rows.map((r) => el("tr", {}, r.map((c) => el("td", { text: c }))))));
}

function renderSource(s, catalogUrl) {
  $("source-badge").textContent = { warehouse: "Live warehouse", published: "Published daily" }[s.kind] || "Warehouse snapshot";
  $("catalog-link").href = catalogUrl;
  const when = s.exported_at ? ` Exported ${new Date(s.exported_at).toLocaleString()}${s.target ? ` from a ${s.target}` : ""}.` : "";
  $("source-note").textContent = `${s.label}, ${s.first_day} to ${s.last_day}.${when}${s.note ? " " + s.note : ""}`;
  const rows = s.rows || {};
  const dbt = s.dbt ? `${s.dbt.passed} of ${s.dbt.tests} dbt tests passed` : "dbt tests not run yet";
  const online = s.online_retail || {};
  const steps = [
    ["Landing", `${((s.files_loaded || 0) + (online.files_loaded || 0)).toLocaleString()} daily files`,
      `${(s.files_loaded || 0).toLocaleString()} from the 5 stores' tills, ${(online.files_loaded || 0).toLocaleString()} from the online shop`],
    ["Validate", "quality.py checks", "Columns, dates, stores, numbers, and the app's quality score"],
    ["raw", `${num((rows.raw || 0) + ((online.rows || {}).raw || 0))} rows`, "raw.sales_lines and raw.online_retail_lines, values as they arrived"],
    ["staging", `${num((rows.staging || 0) + ((online.rows || {}).staging || 0))} rows`, "Typed, repeated lines removed"],
    ["marts", `${num((rows.fact || 0) + ((online.rows || {}).fact || 0))} rows`, `fct_sales and fct_online_sales with their dimensions. ${dbt}`],
  ];
  $("layers").replaceChildren(...steps.map(([name, count, detail], i) => el("li", { class: "pl-step" }, [
    el("span", { class: "pl-num", text: String(i + 1) }), el("strong", { text: name }),
    el("span", { class: "pl-rows", text: count }), el("span", { class: "pl-detail", text: detail })])));
}

function renderKpis(k) {
  const defs = [["Sales", money(k.revenue), `${num(k.units)} units`],
    ["Profit", money(k.profit), `${k.margin_pct.toFixed(1)}% margin`],
    ["Orders", num(k.orders), `${money(k.discounts)} given in discounts`],
    ["Avg. basket", "$" + k.basket.toFixed(2), "sales per order"]];
  $("kpis").replaceChildren(...defs.map(([label, value, note]) => el("div", { class: "kpi" }, [
    el("div", { class: "label", text: label }), el("div", { class: "value", text: value }),
    el("div", { class: "delta flat", text: note })])));
}

function renderCharts(d) {
  const t = axes();
  const moneyAxis = { beginAtZero: true, ticks: { color: t.color, callback: (v) => fmtShort("money", v) }, grid: t.grid, border: t.border };
  const tip = { callbacks: { label: (c) => `${c.dataset.label}: ${money(c.parsed.y ?? c.parsed.x)}` } };
  draw("chart-trend", {
    type: "line",
    data: { labels: d.monthly.map((r) => r.month), datasets: [
      { label: "Sales", data: d.monthly.map((r) => r.revenue), borderColor: Mono.chart(), backgroundColor: Mono.chart(), borderWidth: 2, tension: 0, pointRadius: 2.5, pointStyle: "rect" },
      { label: "Profit", data: d.monthly.map((r) => r.profit), borderColor: Mono.chart(), backgroundColor: Mono.paper(), borderDash: [6, 4], borderWidth: 2, pointRadius: 2.5, pointStyle: "circle", tension: 0 }] },
    options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: t.color, usePointStyle: true } }, tooltip: tip },
      scales: { x: { ticks: { color: t.color, maxTicksLimit: 12 }, grid: { display: false } }, y: moneyAxis } },
  });
  draw("chart-store", {
    type: "bar",
    data: { labels: d.stores.map((r) => r.store), datasets: [{ label: "Sales", data: d.stores.map((r) => r.revenue),
      backgroundColor: d.stores.map((r) => state.store && r.store !== state.store ? Mono.paper() : Mono.chart()),
      borderColor: Mono.chart(), borderWidth: 1, maxBarThickness: 64 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: tip },
      scales: { x: { ticks: { color: t.color }, grid: { display: false } }, y: moneyAxis },
      onClick: (_e, els) => { if (els.length) setStore(state.store === d.stores[els[0].index].store ? "" : d.stores[els[0].index].store); },
      onHover: (e, els) => { e.native.target.style.cursor = els.length ? "pointer" : "default"; } },
  });
  draw("chart-cat", {
    type: "bar",
    data: { labels: d.categories.map((r) => r.category), datasets: [
      { label: "Sales", data: d.categories.map((r) => r.revenue), backgroundColor: Mono.chart(), maxBarThickness: 28 },
      { label: "Profit", data: d.categories.map((r) => r.profit), backgroundColor: Mono.fill(2), borderColor: Mono.chart(), borderWidth: 1, maxBarThickness: 28 }] },
    options: { indexAxis: "y", responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: t.color } }, tooltip: tip },
      scales: { x: Object.assign({}, moneyAxis), y: { ticks: { color: t.color }, grid: { display: false } } } },
  });
}

function renderHealth(h) {
  const runs = h.runs || [];
  $("health-card").hidden = !runs.length;
  if (!runs.length) return;
  const last = runs[0];
  const fresh = (h.freshness || []).find((f) => f.status !== "pass") || (h.freshness || [])[0];
  const tiles = [
    ["Last run", last.status === "success" ? "Succeeded" : last.status === "failed" ? "Failed" : "Running",
      `${ago(last.finished_at || last.started_at)}, ${last.trigger}`],
    ["Duration", dur(last.seconds), `${num(last.files_loaded || 0)} files, ${num(last.rows_loaded || 0)} rows loaded`],
    ["Rejected files", num(last.files_rejected || 0), last.files_rejected ? "moved to quarantine, alert sent" : "none in the last run"],
    ["dbt tests", last.dbt_tests ? `${last.dbt_tests - (last.dbt_tests_failed || 0)} / ${last.dbt_tests}` : "–",
      last.dbt_models ? `${last.dbt_models} models built` : "not run"],
    ["Freshness", FRESH[last.freshness] || "–", fresh ? `newest load ${ago(fresh.max_loaded_at)}` : "dbt source freshness"],
    ["Success rate", h.success_rate == null ? "–" : `${h.success_rate}%`, `of the last ${runs.length} run${runs.length === 1 ? "" : "s"}`],
  ];
  $("health").replaceChildren(...tiles.map(([name, value, detail], i) => el("li", { class: "pl-step" }, [
    el("span", { class: "pl-num", text: name.toUpperCase() }), el("strong", { text: value }),
    el("span", { class: "pl-detail", text: detail })])));
  if (last.error) $("health-note").textContent = `The last run failed: ${last.error}`;
  const t = axes(), order = runs.slice().reverse();
  draw("chart-runs", {
    type: "bar",
    data: { labels: order.map((r) => r.started_at.slice(5, 16)), datasets: [{ label: "Minutes",
      data: order.map((r) => +((r.seconds || 0) / 60).toFixed(2)),
      backgroundColor: order.map((r) => (r.status === "success" ? Mono.chart() : Mono.paper())),
      borderColor: Mono.chart(), borderWidth: 1, maxBarThickness: 22 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false },
      tooltip: { callbacks: { label: (c) => `${order[c.dataIndex].status}: ${dur(order[c.dataIndex].seconds)}, ${num(order[c.dataIndex].rows_loaded || 0)} rows` } } },
      scales: { x: { ticks: { color: t.color, maxTicksLimit: 8 }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { color: t.color }, grid: t.grid, border: t.border } } },
  });
  table("steps", ["Step", "Time", "Result"], (h.steps || []).map((s) => [s.step.replace(/_/g, " "), dur(s.seconds),
    s.status === "success" ? s.detail || "OK" : `Failed: ${s.detail || ""}`]));
  table("freshness", ["Table", "Newest load", "Status"], (h.freshness || []).map((f) => [f.source_table,
    ago(f.max_loaded_at), FRESH[f.status] || f.status]));
  table("runs", ["Started", "Trigger", "Result", "Time", "Files", "Rows", "Rejected", "Tests", "Freshness"],
    runs.map((r) => [r.started_at, r.trigger, r.status, dur(r.seconds), num(r.files_loaded || 0), num(r.rows_loaded || 0),
      num(r.files_rejected || 0), r.dbt_tests ? `${r.dbt_tests - (r.dbt_tests_failed || 0)}/${r.dbt_tests}` : "–",
      FRESH[r.freshness] || "–"]));
  $("runs").querySelectorAll("tbody tr").forEach((tr, i) => { if (runs[i].status === "failed") tr.classList.add("bad"); });
}

function renderOnline(o) {
  $("online-card").hidden = !o;
  if (!o) return;
  const k = o.kpis;
  $("online-note").textContent = `UCI Online Retail II: real invoice lines of a UK online gift shop, ${k.first_day} to ${k.last_day}, ` +
    "loaded through the same checks and dbt tests as the tills. Amounts in pounds sterling.";
  const defs = [["Net sales", pounds(k.net_sales), `${pounds(k.returns)} returned (${k.return_pct}%)`],
    ["Orders", num(k.orders), `${num(k.units)} units`],
    ["Avg. order", "£" + k.order_value.toFixed(2), "net sales per order"],
    ["Customers", k.customers == null ? "–" : num(k.customers), `in ${k.countries} countries`]];
  $("online-kpis").replaceChildren(...defs.map(([label, value, note]) => el("div", { class: "kpi" }, [
    el("div", { class: "label", text: label }), el("div", { class: "value", text: value }),
    el("div", { class: "delta flat", text: note })])));
  const t = axes();
  const axis = { beginAtZero: true, ticks: { color: t.color, callback: (v) => "£" + fmtShort("number", v) }, grid: t.grid, border: t.border };
  const tip = { callbacks: { label: (c) => `${c.dataset.label}: ${pounds(c.parsed.y ?? c.parsed.x)}` } };
  draw("chart-online", {
    type: "line",
    data: { labels: o.monthly.map((r) => r.month), datasets: [
      { label: "Net sales", data: o.monthly.map((r) => r.net_sales), borderColor: Mono.chart(), backgroundColor: Mono.chart(), borderWidth: 2, tension: 0, pointRadius: 2.5, pointStyle: "rect" },
      { label: "Returns", data: o.monthly.map((r) => r.returns), borderColor: Mono.chart(), backgroundColor: Mono.paper(), borderDash: [6, 4], borderWidth: 2, pointRadius: 2.5, tension: 0 }] },
    options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: t.color, usePointStyle: true } }, tooltip: tip },
      scales: { x: { ticks: { color: t.color, maxTicksLimit: 12 }, grid: { display: false } }, y: axis } },
  });
  draw("chart-country", {
    type: "bar",
    data: { labels: o.countries.map((r) => r.country), datasets: [{ label: "Net sales", data: o.countries.map((r) => r.net_sales),
      backgroundColor: Mono.chart(), maxBarThickness: 22 }] },
    options: { indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: tip },
      scales: { x: Object.assign({}, axis), y: { ticks: { color: t.color }, grid: { display: false } } } },
  });
  table("online-products", ["Product", "Code", "Units", "Net sales"],
    o.top_products.map((p) => [p.product, p.code, num(p.units), pounds(p.net_sales)]));
}

function render(d) {
  renderSource(d.source, d.catalog_url);
  renderHealth(d.health || {});
  renderOnline(d.online);
  renderKpis(d.kpis);
  renderCharts(d);
  table("products", ["Product", "Category", "Sales", "Units"],
    d.top_products.map((p) => [p.product, p.category, money(p.revenue), num(p.units)]));
  table("audit", ["When", "Feed", "File", "Result", "Rows", "Quality", "Details"],
    d.audit.map((a) => [a.run_at, a.feed === "online_retail" ? "Online shop" : "Tills", a.file, a.status === "loaded" ? "Loaded" : "Rejected", num(a.rows),
      a.score === null ? "–" : `${a.score} / 100`, a.message || ""]));
  $("audit").querySelectorAll("tbody tr").forEach((tr, i) => { if (d.audit[i].status !== "loaded") tr.classList.add("bad"); });
}

function fillFilters(d) {
  if ($("f-store").options.length === 1) {
    d.store_names.forEach((s) => $("f-store").append(el("option", { value: s, text: s })));
    for (const id of ["f-start", "f-end"]) d.months.forEach((m) => $(id).append(el("option", { value: m, text: m })));
    $("f-start").value = d.months[0]; $("f-end").value = d.months[d.months.length - 1];
  }
  $("f-store").value = state.store;
}

async function load() {
  try {
    const d = await api("/api/warehouse", { params: state });
    fillFilters(d);
    render(d);
  } catch (e) { notify(e.message, true); }
}

function setStore(s) { state.store = s; load(); }

$("f-store").onchange = (e) => setStore(e.target.value);
$("f-start").onchange = (e) => { state.start = e.target.value; load(); };
$("f-end").onchange = (e) => { state.end = e.target.value; load(); };
$("clear-btn").onclick = () => {
  state.store = state.start = state.end = "";
  $("f-start").selectedIndex = 0; $("f-end").selectedIndex = $("f-end").options.length - 1;
  load();
};
load();
