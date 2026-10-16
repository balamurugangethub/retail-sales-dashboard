"use strict";
// Warehouse page: everything here is read from the dbt marts (see marts.py).

const state = { store: "", start: "", end: "" };
const charts = {};
const money = (v) => "$" + Math.round(v).toLocaleString();
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

function renderSource(s) {
  $("source-badge").textContent = s.kind === "warehouse" ? "Live warehouse" : "Warehouse snapshot";
  const when = s.exported_at ? ` Exported ${new Date(s.exported_at).toLocaleString()}.` : "";
  $("source-note").textContent = `${s.label}, ${s.first_day} to ${s.last_day}.${when}${s.note ? " " + s.note : ""}`;
  const rows = s.rows || {};
  const dbt = s.dbt ? `${s.dbt.passed} of ${s.dbt.tests} dbt tests passed` : "dbt tests not run yet";
  const steps = [
    ["Landing", `${(s.files_loaded || 0).toLocaleString()} daily files`, "One CSV per day from the tills of all 5 stores"],
    ["Validate", "quality.py checks", "Columns, dates, stores, numbers, and the app's quality score"],
    ["raw", `${num(rows.raw || 0)} rows`, "raw.sales_lines, values as they arrived"],
    ["staging", `${num(rows.staging || 0)} rows`, "stg_sales_lines: typed, repeated lines removed"],
    ["marts", `${num(rows.fact || 0)} rows`, `fct_sales with dim_store, dim_product, dim_date. ${dbt}`],
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

function render(d) {
  renderSource(d.source);
  renderKpis(d.kpis);
  renderCharts(d);
  table("products", ["Product", "Category", "Sales", "Units"],
    d.top_products.map((p) => [p.product, p.category, money(p.revenue), num(p.units)]));
  table("audit", ["When", "File", "Result", "Rows", "Quality", "Details"],
    d.audit.map((a) => [a.run_at, a.file, a.status === "loaded" ? "Loaded" : "Rejected", num(a.rows),
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
