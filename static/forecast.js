"use strict";
let data = null, chart = null;
const KEY = "rsd-stock";
const readStock = () => { try { return JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (_e) { return {}; } };
const stock = readStock();
const saveStock = () => { try { localStorage.setItem(KEY, JSON.stringify(stock)); } catch (_e) { /* kept for this visit only */ } };
const money = (v) => fmtValue("money", v);
const toOrder = (p) => {
  const have = stock[p.product];
  return have === undefined || have === "" ? null : Math.max(0, Math.ceil(p.forecast_units + p.safety_stock - Number(have)));
};
const arrow = { rising: "▲ Rising", falling: "▼ Falling", steady: "● Steady", new: "New" };

function renderKpis() {
  const t = data.totals, c = data.counts;
  const delta = t.change_pct === null ? "" : `${t.change_pct > 0 ? "+" : ""}${t.change_pct}% vs last 4 weeks`;
  const k = (label, value, note) => el("div", { class: "kpi" }, [el("div", { class: "label", text: label }), el("div", { class: "value", text: value }), el("div", { class: "delta flat", text: note || "" })]);
  $("kpis").replaceChildren(
    k("Expected sales, next 4 weeks", money(t.forecast_sales), delta),
    k("Expected units", t.forecast_units.toLocaleString(), `${data.forecast_from} to ${data.forecast_to}`),
    k("Rising products", String(c.rising), "10% or more up"),
    k("Falling products", String(c.falling), "10% or more down"));
}

function renderChart() {
  const h = data.chart.history, f = data.chart.forecast;
  const labels = [...h.map((x) => x.week), ...f.map((x) => x.week)];
  const pad = (arr) => [...Array(h.length - 1).fill(null), ...arr];
  const last = h[h.length - 1].sales;
  const ds = [
    { label: "Actual weekly sales", data: [...h.map((x) => x.sales), ...f.map(() => null)], borderColor: Mono.chart(), backgroundColor: Mono.fill(6), fill: true, tension: 0, pointRadius: 2 },
    { label: "Forecast", data: pad([last, ...f.map((x) => x.sales)]), borderColor: Mono.chart(), borderDash: [6, 4], fill: false, tension: 0, pointStyle: "rectRot", pointRadius: 4 },
    { label: "Likely high", data: pad([last, ...f.map((x) => x.high)]), borderColor: Mono.muted(), borderDash: [2, 3], borderWidth: 1, fill: false, pointRadius: 0 },
    { label: "Likely low", data: pad([last, ...f.map((x) => x.low)]), borderColor: Mono.muted(), borderDash: [2, 3], borderWidth: 1, fill: false, pointRadius: 0 },
  ];
  const t = { grid: Mono.valueGrid(), border: Mono.axisBorder() };
  if (chart) chart.destroy();
  chart = new Chart($("chart-forecast"), { type: "line", data: { labels, datasets: ds }, options: {
    maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
    plugins: { legend: { labels: { color: Mono.text() } }, tooltip: { callbacks: { label: (c) => c.raw === null ? "" : `${c.dataset.label}: ${money(c.raw)}` } } },
    scales: { x: { ticks: { color: Mono.muted(), maxTicksLimit: 10 }, grid: { display: false }, border: t.border },
              y: { beginAtZero: true, ticks: { color: Mono.muted(), callback: (v) => fmtShort("money", v) }, grid: t.grid, border: t.border } } } });
  $("chart-note").textContent = `Based on ${data.weeks_of_history} complete weeks up to ${data.as_of}. The dotted lines show the range sales will probably stay inside.`;
}

function renderTable() {
  const head = el("tr", {}, ["Product", "Category", "Last 4 wks", "Next 4 wks", "Trend", "Safety stock", "On hand", "To order"].map((x) => el("th", { text: x })));
  const rows = data.products.map((p) => {
    const input = el("input", { type: "number", min: "0", step: "1", "aria-label": "On hand " + p.product, class: "stock", value: stock[p.product] ?? "" });
    const out = el("td", { class: "to-order" });
    const show = () => { const n = toOrder(p); out.textContent = n === null ? "–" : n.toLocaleString(); out.classList.toggle("need", n > 0); };
    input.addEventListener("input", () => { if (input.value === "") delete stock[p.product]; else stock[p.product] = input.value; saveStock(); show(); });
    show();
    const trend = el("td", { text: arrow[p.trend] + (p.change_pct === null ? "" : ` ${p.change_pct > 0 ? "+" : ""}${p.change_pct}%`) });
    return el("tr", {}, [el("td", { text: p.product }), el("td", { text: p.category }), el("td", { text: p.recent_units.toLocaleString() }),
      el("td", { text: p.forecast_units.toLocaleString() }), trend, el("td", { text: p.safety_stock.toLocaleString() }), el("td", {}, input), out]);
  });
  $("plan").replaceChildren(el("thead", {}, head), el("tbody", {}, rows));
  $("csv-btn").disabled = false;
}

function downloadCsv() {
  const q = (v) => `"${String(v).replace(/"/g, '""').replace(/^([=+\-@])/, "'$1")}"`;
  const lines = [["Product", "Category", "Last 4 weeks units", "Next 4 weeks units", "Trend", "Safety stock", "On hand", "To order"].map(q).join(",")];
  data.products.forEach((p) => lines.push([p.product, p.category, p.recent_units, p.forecast_units, p.trend, p.safety_stock, stock[p.product] ?? "", toOrder(p) ?? ""].map(q).join(",")));
  const a = el("a", { href: URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" })), download: "order-plan.csv" });
  document.body.append(a); a.click(); a.remove();
}

async function load() {
  try {
    data = await api("/api/forecast", { params: { store: $("f-store").value, category: $("f-category").value } });
  } catch (e) { return notify(e.message, true); }
  $("empty").hidden = data.ok; $("body").hidden = !data.ok; $("csv-btn").disabled = !data.ok;
  if (!data.ok) { $("empty").textContent = data.message; return; }
  renderKpis(); renderChart(); renderTable();
}

(async function init() {
  const meta = await api("/api/meta");
  meta.stores.forEach((s) => $("f-store").append(el("option", { value: s, text: s })));
  meta.categories.forEach((s) => $("f-category").append(el("option", { value: s, text: s })));
  $("f-store").onchange = $("f-category").onchange = load;
  $("csv-btn").onclick = downloadCsv;
  window.addEventListener("themechange", () => data && data.ok && renderChart());
  load();
})();
