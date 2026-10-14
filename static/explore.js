"use strict";
const SAMPLES = [
  ["4_hospital_visits.csv", "Hospital visits", "One year of outpatient visits by department: patients, waiting time and satisfaction."],
  ["5_school_results.csv", "School results", "120 students across three terms: scores, attendance and pass or fail."],
  ["3_retail_chain_trends.csv", "Retail chain", "Four stores over 18 months with sales and cost."],
];
const CHARTS = [["bar", "Bars"], ["hbar", "Horizontal bars"], ["line", "Line"], ["area", "Area"], ["pie", "Pie"], ["table", "Table only"]];
const OPS = [["eq", "is"], ["ne", "is not"], ["gt", "is more than"], ["ge", "is at least"], ["lt", "is less than"], ["le", "is at most"], ["contains", "contains"], ["blank", "is empty"], ["filled", "is filled"]];
let ds = null, readOnly = false, chart = null, fcChart = null, last = null, lastFc = null, timer = null;
const fv = (id) => $(id).value;

const fmtNum = (v) => v === null || v === undefined ? "–" : (typeof v !== "number" ? String(v) : Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : (Math.round(v * 100) / 100).toLocaleString());
const fillSelect = (sel, items, keep) => { const old = keep ? sel.value : null; sel.replaceChildren(...items.map(([v, t]) => el("option", { value: v, text: t }))); if (old !== null && items.some(([v]) => String(v) === old)) sel.value = old; };
const cols = () => ds.columns.map((c, i) => [String(i), c.name, c.type]);
const colType = (i) => ds.columns[+i].type;

async function loadList(selectId) {
  const r = await api("/api/datasets");
  readOnly = r.read_only;
  $("upload-label").hidden = readOnly;
  fillSelect($("ds-select"), r.datasets.length ? r.datasets.map((d) => [d.id, `${d.name} (${d.rows.toLocaleString()} rows)`]) : [["", "No datasets yet"]]);
  $("start").hidden = r.datasets.length > 0;
  $("work").hidden = r.datasets.length === 0;
  $("ds-delete").hidden = readOnly || r.datasets.length === 0;
  if (!r.datasets.length) { ds = null; return; }
  if (selectId) $("ds-select").value = String(selectId);
  await openDataset($("ds-select").value);
}

async function openDataset(id) {
  ds = await api("/api/datasets/" + id);
  $("ds-note").textContent = `${ds.rows.toLocaleString()} rows, ${ds.columns.length} columns`;
  $("insights").replaceChildren(...ds.insights.map((t) => el("li", { text: t })));
  renderProfile();
  loadQuality(id);
  const numCols = cols().filter((c) => c[2] === "number"), dateCols = cols().filter((c) => c[2] === "date");
  fillSelect($("m-agg"), ds.aggs.map((a) => [a.id, a.label]));
  $("m-agg").value = numCols.length ? "sum" : "rows";
  fillSelect($("m-field"), cols().map((c) => [c[0], c[1]]));
  if (numCols.length) $("m-field").value = numCols[0][0];
  fillSelect($("m2-field"), [["", "None"], ...numCols.map((c) => [c[0], c[1]])]);
  const dimCols = cols();
  fillSelect($("d1-field"), dimCols.map((c) => [c[0], c[1]]));
  const firstText = cols().find((c) => c[2] === "text" && ds.profile[+c[0]].distinct <= 60) || dateCols[0] || cols()[0];
  $("d1-field").value = firstText[0];
  fillSelect($("d2-field"), [["", "None"], ...dimCols.map((c) => [c[0], c[1]])]);
  fillSelect($("d1-grain"), ds.grains.map((g) => [g, g[0].toUpperCase() + g.slice(1)]));
  $("d1-grain").value = "month";
  fillSelect($("chart-type"), CHARTS);
  $("filters").replaceChildren();
  fixForm();
  const fcOk = dateCols.length && numCols.length;
  $("fc-card").hidden = !fcOk;
  if (fcOk) {
    fillSelect($("fc-date"), dateCols.map((c) => [c[0], c[1]]));
    fillSelect($("fc-measure"), numCols.map((c) => [c[0], c[1]]));
    $("fc-agg").value = /avg|average|score|rate|%|wait|price|satisfaction|ratio/i.test(numCols[0][1]) ? "avg" : "sum";
  }
  await Promise.all([run(), fcOk ? runForecast() : null]);
}

async function loadQuality(id) {
  $("q-score").textContent = "Checking";
  $("q-summary").textContent = "";
  $("q-issues").replaceChildren();
  let q;
  try { q = await api(`/api/datasets/${id}/quality`); } catch (e) { $("q-score").textContent = "Unavailable"; return; }
  if (!ds || String(ds.id) !== String(id)) return;
  $("q-score").textContent = `${q.score} / 100, ${q.grade}`;
  $("q-summary").textContent = `${q.completeness_pct}% of cells are filled and ${q.duplicate_rows.toLocaleString()} rows are repeats. The checks only read your data; nothing is changed.`;
  const rows = q.issues.length ? q.issues.map((i) => el("li", { class: i.severity === "high" ? "warn" : "info", text: i.message }))
    : [el("li", { class: "good", text: "No problems found: no gaps, repeats, odd spellings or extreme numbers." })];
  $("q-issues").replaceChildren(...rows);
}

function renderProfile() {
  const head = el("tr", {}, ["Column", "Type", "Filled", "Different values", "Smallest", "Largest", "Typical / top values"].map((x) => el("th", { text: x })));
  const rows = ds.profile.map((p) => {
    const typ = p.type, range = (k) => typ === "text" ? "" : fmtNum(p[k]);
    const extra = typ === "number" ? "avg " + fmtNum(p.mean) : typ === "text" ? (p.top || []).map((t) => `${t[0]} (${t[1]})`).join(", ") : "";
    return el("tr", {}, [el("td", { text: p.name }), el("td", { text: typ }), el("td", { text: `${p.filled.toLocaleString()}${p.missing ? ` (${p.missing.toLocaleString()} empty)` : ""}` }),
      el("td", { text: p.distinct.toLocaleString() }), el("td", { text: range("min") }), el("td", { text: range("max") }), el("td", { text: extra, class: "wrap" })]);
  });
  $("profile").replaceChildren(el("thead", {}, head), el("tbody", {}, rows));
  const sh = el("tr", {}, ds.columns.map((c) => el("th", { text: c.name })));
  $("sample").replaceChildren(el("thead", {}, sh), el("tbody", {}, ds.sample.map((r) => el("tr", {}, r.map((v) => el("td", { text: v === null ? "" : fmtNum(v) }))))));
}

// Only offer calculations that make sense for the chosen column, and a date grain for date columns
function fixForm() {
  const t = colType(fv("m-field")), agg = $("m-agg");
  [...agg.options].forEach((o) => { const a = ds.aggs.find((x) => x.id === o.value); o.hidden = o.disabled = !!(a.types && !a.types.includes(t)); });
  if (agg.selectedOptions[0] && agg.selectedOptions[0].disabled) agg.value = "count";
  $("m-field").disabled = agg.value === "rows";
  $("d1-grain-wrap").hidden = colType(fv("d1-field")) !== "date";
}

function addFilter() {
  const row = el("div", { class: "ex-filter" });
  const f = el("select", { "aria-label": "Filter field" }), op = el("select", { "aria-label": "Filter type" }), val = el("input", { type: "text", "aria-label": "Filter value", placeholder: "value" });
  fillSelect(f, cols().map((c) => [c[0], c[1]])); fillSelect(op, OPS);
  const sync = () => { val.hidden = ["blank", "filled"].includes(op.value); };
  [f, op].forEach((x) => x.addEventListener("change", () => { sync(); schedule(); }));
  val.addEventListener("input", schedule); sync();
  row.append(f, op, val, el("button", { class: "btn small ghost", type: "button", text: "Remove", onclick: () => { row.remove(); schedule(); } }));
  $("filters").append(row);
}
const filters = () => [...$("filters").children].map((r) => { const [f, op, val] = r.querySelectorAll("select, input"); return { field: f.value, op: op.value, value: val.value }; })
  .filter((f) => ["blank", "filled"].includes(f.op) || f.value !== "");

function spec() {
  const dims = [{ field: fv("d1-field"), grain: fv("d1-grain") }];
  if (fv("d2-field")) dims.push({ field: fv("d2-field"), grain: fv("d1-grain") });
  const measures = [{ field: fv("m-field"), agg: fv("m-agg") }];
  if (fv("m2-field")) measures.push({ field: fv("m2-field"), agg: fv("m-agg") === "avg" ? "avg" : "sum" });
  return { dims, measures, filters: filters(), limit: +fv("limit") };
}

async function run() {
  try { last = await api(`/api/datasets/${ds.id}/query`, { method: "POST", body: spec() }); }
  catch (e) { return notify(e.message, true); }
  drawChart(); drawTable();
}

function drawTable() {
  const head = el("tr", {}, last.columns.map((c) => el("th", { text: c.label })));
  $("result").replaceChildren(el("thead", {}, head), el("tbody", {}, last.rows.map((r) => el("tr", {}, r.map((v) => el("td", { text: fmtNum(v) }))))));
}

function drawChart() {
  const type = fv("chart-type"), box = $("chart-box");
  box.hidden = type === "table";
  if (chart) { chart.destroy(); chart = null; }
  if (type === "table" || !last.rows.length) return;
  const nd = last.dims, rows = last.rows, mCols = last.columns.slice(nd);
  let labels, sets;
  if (nd === 2) {                                            // pivot: one series per value of the second grouping
    const first = [...new Set(rows.map((r) => r[0]))], tot = {};
    rows.forEach((r) => { tot[r[1]] = (tot[r[1]] || 0) + (+r[2] || 0); });
    const second = Object.keys(tot).sort((a, b) => tot[b] - tot[a]).slice(0, 6);
    labels = first;
    sets = second.map((s) => ({ label: s, data: first.map((f) => { const r = rows.find((x) => x[0] === f && String(x[1]) === s); return r ? r[2] : null; }) }));
  } else {
    labels = rows.map((r) => r[0]);
    sets = mCols.map((c, k) => ({ label: c.label, data: rows.map((r) => r[1 + k]) }));
  }
  const pie = type === "pie", line = type === "line" || type === "area";
  sets.forEach((s, i) => {
    if (pie) { s.backgroundColor = labels.map((_l, k) => Mono.fill(k)); return; }
    s.borderColor = Mono.chart(); s.borderWidth = 1;
    if (line) { s.fill = type === "area" && i === 0; s.backgroundColor = Mono.fill(6); s.borderDash = Mono.dash(i); s.pointStyle = Mono.point(i); s.pointRadius = labels.length > 40 ? 0 : 3.5; s.tension = 0; }
    else s.backgroundColor = Mono.fill(i);
  });
  const g = Mono.valueGrid(), b = Mono.axisBorder();
  const horizontal = type === "hbar";
  const tick = { color: Mono.muted(), callback: function (v) { const l = this.getLabelForValue ? this.getLabelForValue(v) : v; return String(l).length > 18 ? String(l).slice(0, 17) + "…" : l; } };
  chart = new Chart($("chart"), { type: pie ? "pie" : line ? "line" : "bar", data: { labels, datasets: sets }, options: {
    maintainAspectRatio: false, indexAxis: horizontal ? "y" : "x",
    plugins: { legend: { display: sets.length > 1 || pie, position: pie ? "right" : "top", labels: { color: Mono.text() } } },
    scales: pie ? {} : { [horizontal ? "y" : "x"]: { ticks: tick, grid: { display: false }, border: b },
                         [horizontal ? "x" : "y"]: { beginAtZero: true, ticks: { color: Mono.muted(), callback: (v) => fmtShort("number", v) }, grid: g, border: b } } } });
}

async function runForecast() {
  const body = { date: fv("fc-date"), measure: fv("fc-measure"), agg: fv("fc-agg"), filters: filters() };
  try { lastFc = await api(`/api/datasets/${ds.id}/forecast`, { method: "POST", body }); }
  catch (e) { return notify(e.message, true); }
  drawForecast();
}

function drawForecast() {
  if (fcChart) { fcChart.destroy(); fcChart = null; }
  const r = lastFc;
  if (!r.ok) { $("fc-note").textContent = r.message; return; }
  const h = r.history, f = r.forecast, lastV = h[h.length - 1].value;
  const pad = (a) => [...Array(h.length - 1).fill(null), ...a];
  const ch = r.change_pct === null ? "" : ` That is ${r.change_pct > 0 ? "+" : ""}${r.change_pct}% against the last ${r.horizon} ${r.grain}s.`;
  $("fc-note").textContent = `Expected over the next ${r.horizon} ${r.grain}s: ${fmtNum(r.next_total)}.${ch} Dotted lines show the range it will probably stay inside.`;
  const sets = [
    { label: "Actual", data: [...h.map((x) => x.value), ...f.map(() => null)], borderColor: Mono.chart(), backgroundColor: Mono.fill(6), fill: true, tension: 0, pointRadius: 2 },
    { label: "Forecast", data: pad([lastV, ...f.map((x) => x.value)]), borderColor: Mono.chart(), borderDash: [6, 4], fill: false, tension: 0, pointStyle: "rectRot", pointRadius: 4 },
    { label: "Likely high", data: pad([lastV, ...f.map((x) => x.high)]), borderColor: Mono.muted(), borderDash: [2, 3], borderWidth: 1, fill: false, pointRadius: 0 },
    { label: "Likely low", data: pad([lastV, ...f.map((x) => x.low)]), borderColor: Mono.muted(), borderDash: [2, 3], borderWidth: 1, fill: false, pointRadius: 0 },
  ];
  fcChart = new Chart($("fc-chart"), { type: "line", data: { labels: [...h.map((x) => x.period), ...f.map((x) => x.period)], datasets: sets }, options: {
    maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, plugins: { legend: { labels: { color: Mono.text() } } },
    scales: { x: { ticks: { color: Mono.muted(), maxTicksLimit: 10 }, grid: { display: false }, border: Mono.axisBorder() },
              y: { beginAtZero: true, ticks: { color: Mono.muted(), callback: (v) => fmtShort("number", v) }, grid: Mono.valueGrid(), border: Mono.axisBorder() } } } });
}

function schedule() { clearTimeout(timer); timer = setTimeout(() => { run(); if (!$("fc-card").hidden) runForecast(); }, 250); }

async function upload(file, name) {
  if (readOnly) return notify("This hosted demo is read-only. Run the app on your computer to open your own data.", true);
  const form = new FormData(); form.append("file", file, file.name); if (name) form.append("name", name);
  try { const r = await api("/api/datasets", { method: "POST", form }); await loadList(r.id); notify("Opened " + (name || file.name)); }
  catch (e) { notify(e.message, true); }
}

(async function init() {
  SAMPLES.forEach(([file, title, text]) => $("samples").append(el("button", { class: "sample", type: "button", onclick: async () => {
    const r = await fetch("/static/samples/" + file); upload(new File([await r.blob()], file), title);
  } }, [el("strong", { text: title }), el("span", { text })])));
  $("file-input").onchange = (e) => { if (e.target.files[0]) upload(e.target.files[0]); e.target.value = ""; };
  $("ds-select").onchange = () => $("ds-select").value && openDataset($("ds-select").value);
  $("ds-delete").onclick = async () => {
    if (!ds || !confirm(`Remove “${ds.name}” from the app? Your original file is not touched.`)) return;
    await api("/api/datasets/" + ds.id, { method: "DELETE" }); await loadList();
  };
  $("add-filter").onclick = () => { addFilter(); };
  ["m-agg", "m-field", "m2-field", "d1-field", "d1-grain", "d2-field", "chart-type", "limit"].forEach((id) => $(id).addEventListener("change", () => { fixForm(); id === "chart-type" ? drawChart() : schedule(); }));
  ["fc-date", "fc-measure", "fc-agg"].forEach((id) => $(id).addEventListener("change", runForecast));
  window.addEventListener("themechange", () => { if (last) drawChart(); if (lastFc) drawForecast(); });
  await loadList();
})();
