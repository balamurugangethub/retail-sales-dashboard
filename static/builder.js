"use strict";
// Cognos-style dashboard builder: drag fields onto the canvas or double-click them,
// switch chart types, save named dashboards, and talk to the AI assistant.

const B = {
  fields: null, meta: null, widgets: [], selected: null, dashId: null, dirty: false,
  filters: { preset: "all", start: "", end: "", store: "", category: "" },
};
const charts = new Map();
let uid = 0;
const LOCAL_KEY = "rsd-dashboards";      // read-only demo saves dashboards in the browser
const CHART_LABELS = { bar: "Bar", hbar: "Horizontal bar", stacked: "Stacked bar", line: "Line", area: "Area",
                       pie: "Pie", kpi: "KPI", table: "Table" };
const SIZE_LABELS = { 1: "Small", 2: "Medium", 3: "Wide", 4: "Full width" };

const dimInfo = (id) => B.fields.dimensions.find((d) => d.id === id);
const measureInfo = (id) => B.fields.measures.find((m) => m.id === id);

function autoChart(w) {
  if (!w.dims.length) return "kpi";
  if (w.dims.length === 2) return "stacked";
  const d = dimInfo(w.dims[0]);
  if (d.time) return "line";
  if (w.dims[0] === "product") return "hbar";
  if (w.dims[0] === "category" && w.measures.length === 1) return "pie";
  return "bar";
}

function autoTitle(w) {
  let t = w.measures.map((m) => measureInfo(m).label).join(" & ") || "New chart";
  if (w.dims.length) t += " by " + w.dims.map((d) => dimInfo(d).label.toLowerCase()).join(" and ");
  if (w.limit) t = (w.sort && w.sort.startsWith("+") ? "Bottom " : "Top ") + w.limit + ": " + t;
  const f = Object.values(w.filters || {});
  if (f.length) t += " (" + f.join(", ") + ")";
  return t;
}

function newWidget(spec = {}) {
  const w = { id: ++uid, chart: spec.chart || null, dims: [...(spec.dims || [])], measures: [...(spec.measures || [])],
              span: spec.span || (spec.chart === "kpi" || (!spec.dims || !spec.dims.length) ? 1 : 2),
              limit: spec.limit || null, sort: spec.sort || null, filters: { ...(spec.filters || {}) },
              title: spec.title || "", customTitle: false, autoChart: !spec.chart };
  if (spec.title && spec.title !== autoTitle(w)) w.customTitle = true;
  if (!w.chart) w.chart = autoChart(w);
  if (!spec.span && w.chart === "kpi") w.span = 1;
  if (!spec.span && (w.chart === "table" || w.chart === "line" || w.chart === "area") && w.dims.length) w.span = 2;
  return w;
}

function markDirty() { B.dirty = true; }

// ---- fields panel -------------------------------------------------------------
function renderFields() {
  const make = (list, kind, items) => {
    $(list).replaceChildren(...items.map((f) => {
      const li = el("li", { class: "field " + kind, draggable: "true", tabindex: "0", text: f.label,
                            title: "Drag onto the canvas or double-click", "data-kind": kind, "data-id": f.id });
      li.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("application/x-field", JSON.stringify({ kind, id: f.id }));
        e.dataTransfer.effectAllowed = "copy";
        document.body.classList.add("dragging-field");
      });
      li.addEventListener("dragend", () => document.body.classList.remove("dragging-field"));
      li.addEventListener("dblclick", () => addFieldToSelection({ kind, id: f.id }));
      // Touch screens have no drag and drop or reliable double-tap: a single tap adds the field
      li.addEventListener("click", () => { if (matchMedia("(pointer: coarse)").matches) addFieldToSelection({ kind, id: f.id }); });
      li.addEventListener("keydown", (e) => { if (e.key === "Enter") addFieldToSelection({ kind, id: f.id }); });
      return li;
    }));
  };
  make("measure-list", "measure", B.fields.measures);
  make("dim-list", "dimension", B.fields.dimensions);
}

function addFieldToSelection(field) {
  const w = B.widgets.find((x) => x.id === B.selected);
  if (w) addField(w, field);
  else createFromField(field);
}

function createFromField(field, beforeId = null) {
  const w = newWidget(field.kind === "measure" ? { measures: [field.id] } : { dims: [field.id], measures: ["sales"] });
  insertWidget(w, beforeId);
  select(w.id);
}

function insertWidget(w, beforeId = null) {
  const i = beforeId ? B.widgets.findIndex((x) => x.id === beforeId) : -1;
  if (i >= 0) B.widgets.splice(i, 0, w); else B.widgets.push(w);
  markDirty(); renderCanvas();
}

function addField(w, field) {
  if (field.kind === "measure") {
    if (w.measures.includes(field.id)) return notify(measureInfo(field.id).label + " is already in this chart.");
    if (w.measures.length >= 4) return notify("A chart can show at most 4 measures.", true);
    w.measures.push(field.id);
    if (w.chart === "pie") w.chart = "bar";
  } else {
    if (w.dims.includes(field.id)) return notify(dimInfo(field.id).label + " is already in this chart.");
    if (w.dims.length >= 2) w.dims[1] = field.id; else w.dims.push(field.id);
    if (w.chart === "kpi") { w.autoChart = true; if (w.span === 1) w.span = 2; }
    if (w.dims.length === 2 && w.measures.length > 1) w.measures = w.measures.slice(0, 1);
  }
  if (w.autoChart) w.chart = autoChart(w);
  markDirty(); renderWidget(w);
}

function removeField(w, kind, id) {
  if (kind === "measure") {
    if (w.measures.length === 1) return notify("A chart needs at least one measure. Drop another one first.", true);
    w.measures = w.measures.filter((m) => m !== id);
  } else {
    w.dims = w.dims.filter((d) => d !== id);
    if (w.sort && w.sort.replace(/^[+-]/, "") === id) w.sort = null;
    if (!w.dims.length) { w.limit = null; w.sort = null; }
  }
  if (w.autoChart || (w.chart !== "kpi" && !w.dims.length) || (w.chart === "kpi" && w.dims.length)) w.chart = autoChart(w);
  markDirty(); renderWidget(w);
}

// ---- canvas ----------------------------------------------------------------------
function select(id) {
  B.selected = id;
  document.querySelectorAll(".widget").forEach((n) => n.classList.toggle("selected", +n.dataset.id === id));
}

function renderCanvas() {
  const canvas = $("canvas");
  const keep = new Set(B.widgets.map((w) => w.id));
  for (const [id, ch] of charts) if (!keep.has(id)) { ch.destroy(); charts.delete(id); }
  canvas.querySelectorAll(".widget").forEach((n) => n.remove());
  $("empty").hidden = B.widgets.length > 0;
  B.widgets.forEach((w) => canvas.append(widgetShell(w)));
  B.widgets.forEach((w) => loadWidget(w));
  select(B.selected);
}

function renderWidget(w) {
  const old = document.querySelector(`.widget[data-id="${w.id}"]`);
  if (!old) return renderCanvas();
  if (charts.has(w.id)) { charts.get(w.id).destroy(); charts.delete(w.id); }
  old.replaceWith(widgetShell(w));
  loadWidget(w); select(B.selected);
}

function widgetShell(w) {
  const title = el("h3", { class: "w-title", text: w.customTitle ? w.title : autoTitle(w), title: "Double-click to rename" });
  title.addEventListener("dblclick", () => {
    title.contentEditable = "true"; title.focus();
    document.getSelection().selectAllChildren(title);
  });
  const finishTitle = () => {
    title.contentEditable = "false";
    const t = title.textContent.trim().slice(0, 80);
    w.customTitle = !!t && t !== autoTitle(w); w.title = w.customTitle ? t : "";
    title.textContent = w.customTitle ? w.title : autoTitle(w); markDirty();
  };
  title.addEventListener("blur", finishTitle);
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); title.blur(); } });

  const chartSel = el("select", { "aria-label": "Chart type", class: "mini" },
    Object.entries(CHART_LABELS).map(([k, v]) => el("option", { value: k, text: v, selected: k === w.chart })));
  chartSel.addEventListener("change", () => {
    const c = chartSel.value;
    if (c !== "kpi" && c !== "table" && !w.dims.length) { chartSel.value = w.chart; return notify("Add a dimension (e.g. Store) first, or use KPI / Table.", true); }
    if (c === "pie" && w.measures.length > 1) w.measures = w.measures.slice(0, 1);
    if (c === "kpi") { w.dims = []; w.limit = null; w.sort = null; }
    w.chart = c; w.autoChart = false; markDirty(); renderWidget(w);
  });
  const sizeSel = el("select", { "aria-label": "Size", class: "mini" },
    Object.entries(SIZE_LABELS).map(([k, v]) => el("option", { value: k, text: v, selected: +k === w.span })));
  sizeSel.addEventListener("change", () => { w.span = +sizeSel.value; markDirty(); renderWidget(w); });
  const topSel = el("select", { "aria-label": "Show top", class: "mini", hidden: !w.dims.length },
    [["", "All"], ["5", "Top 5"], ["10", "Top 10"], ["20", "Top 20"], ["-5", "Bottom 5"]].map(([v, t]) =>
      el("option", { value: v, text: t, selected: v === (w.limit ? (w.sort && w.sort.startsWith("+") ? "-" : "") + w.limit : "") })));
  topSel.addEventListener("change", () => {
    const v = topSel.value;
    if (!v) { w.limit = null; w.sort = null; }
    else { w.limit = Math.abs(+v); w.sort = (v.startsWith("-") ? "+" : "-") + w.measures[0]; }
    markDirty(); renderWidget(w);
  });
  const remove = el("button", { class: "icon", title: "Remove chart", "aria-label": "Remove chart", text: "×" });
  remove.addEventListener("click", (e) => {
    e.stopPropagation();
    B.widgets = B.widgets.filter((x) => x.id !== w.id);
    if (B.selected === w.id) B.selected = null;
    markDirty(); renderCanvas();
  });

  const chips = el("div", { class: "chips" }, [
    ...w.dims.map((d) => chip("dimension", d, dimInfo(d).label, w)),
    ...w.measures.map((m) => chip("measure", m, measureInfo(m).label, w)),
    ...Object.entries(w.filters || {}).map(([k, v]) => {
      const c = el("span", { class: "chip filter", text: v, title: k + " filter" });
      c.append(el("button", { class: "x", text: "×", "aria-label": "Remove filter",
        onclick: () => { delete w.filters[k]; markDirty(); renderWidget(w); } }));
      return c;
    }),
  ]);

  const head = el("div", { class: "w-head", draggable: "true", title: "Drag to move" }, [
    el("span", { class: "grip", text: "⠿", "aria-hidden": "true" }), title, remove]);
  const tools = el("div", { class: "w-tools" }, [chips, el("div", { class: "w-selects" }, [chartSel, topSel, sizeSel])]);
  head.addEventListener("dragstart", (e) => {
    if (e.target !== head) return;
    e.dataTransfer.setData("application/x-widget", String(w.id)); e.dataTransfer.effectAllowed = "move";
  });

  const body = el("div", { class: "w-body chart-" + w.chart }, [el("div", { class: "loading", text: "Loading…" })]);
  const box = el("article", { class: "widget span-" + w.span, "data-id": w.id, tabindex: "0" }, [head, tools, body]);
  box.addEventListener("click", () => select(w.id));
  box.addEventListener("dragover", (e) => {
    if ([...e.dataTransfer.types].some((t) => t.startsWith("application/x-"))) { e.preventDefault(); box.classList.add("drop"); }
  });
  box.addEventListener("dragleave", () => box.classList.remove("drop"));
  box.addEventListener("drop", (e) => {
    e.preventDefault(); e.stopPropagation(); box.classList.remove("drop");
    const f = e.dataTransfer.getData("application/x-field");
    const moved = e.dataTransfer.getData("application/x-widget");
    if (f) { select(w.id); addField(w, JSON.parse(f)); }
    else if (moved && +moved !== w.id) {
      const src = B.widgets.find((x) => x.id === +moved);
      B.widgets = B.widgets.filter((x) => x.id !== +moved);
      B.widgets.splice(B.widgets.findIndex((x) => x.id === w.id), 0, src);
      markDirty(); renderCanvas();
    }
  });
  return box;
}

function chip(kind, id, label, w) {
  return el("span", { class: "chip " + kind, text: label }, [
    el("button", { class: "x", text: "×", "aria-label": "Remove " + label, onclick: (e) => { e.stopPropagation(); removeField(w, kind, id); } })]);
}

function queryParams(w) {
  const f = B.filters;
  const p = { dims: w.dims.join(","), measures: w.measures.join(","), start: f.start, end: f.end,
              store: f.store, category: f.category };
  Object.assign(p, w.filters || {});
  if (w.limit) { p.limit = w.limit; p.sort = w.sort || "-" + w.measures[0]; }
  return p;
}

async function loadWidget(w) {
  const body = document.querySelector(`.widget[data-id="${w.id}"] .w-body`);
  try {
    const res = await api("/api/query", { params: queryParams(w) });
    if (!body.isConnected) return;
    drawWidget(w, body, res);
  } catch (e) {
    if (body.isConnected) body.replaceChildren(el("div", { class: "w-error", text: e.message }));
  }
}

function drawWidget(w, body, res) {
  const nd = w.dims.length, cols = res.columns;
  if (!res.rows.length) return body.replaceChildren(el("div", { class: "w-error", text: "No data for these filters." }));
  if (w.chart === "kpi") {
    const row = res.rows[0];
    return body.replaceChildren(...cols.slice(nd).map((c, i) => el("div", { class: "kpi-cell" }, [
      el("div", { class: "value", text: fmtValue(c.format, row[nd + i]) }), el("div", { class: "label", text: c.label })])));
  }
  if (w.chart === "table") return body.replaceChildren(el("div", { class: "table-wrap" }, [table(cols, res.rows)]));

  const canvas = el("canvas");
  body.replaceChildren(el("div", { class: "chart-box" }, [canvas]));
  const t = { color: cssVar("--muted"), grid: cssVar("--line"), ink: cssVar("--ink") };
  const measuresCols = cols.slice(nd);
  let labels, datasets;
  if (nd === 2) {
    // pivot: first dimension on the axis, one series per value of the second
    labels = [...new Set(res.rows.map((r) => r[0]))];
    const series = [...new Set(res.rows.map((r) => r[1]))];
    const look = new Map(res.rows.map((r) => [r[0] + "\u0000" + r[1], r[2]]));
    datasets = series.slice(0, 12).map((s, i) => ({ label: String(s), data: labels.map((l) => look.get(l + "\u0000" + s) || 0),
      backgroundColor: PALETTE[i % PALETTE.length], borderColor: PALETTE[i % PALETTE.length], fmt: measuresCols[0].format }));
  } else {
    labels = res.rows.map((r) => r[0]);
    const firstFmt = measuresCols[0].format;
    datasets = measuresCols.map((c, i) => ({ label: c.label, data: res.rows.map((r) => r[1 + i]),
      backgroundColor: w.chart === "pie" ? PALETTE : (w.chart === "area" ? PALETTE[i] + "40" : PALETTE[i]),
      borderColor: PALETTE[i], fmt: c.format, yAxisID: c.format === firstFmt ? "y" : "y1" }));
  }
  const isLine = w.chart === "line" || w.chart === "area";
  datasets.forEach((d) => {
    if (isLine) Object.assign(d, { fill: w.chart === "area", tension: .25, pointRadius: labels.length > 40 ? 0 : 3 });
    else if (w.chart !== "pie") d.borderRadius = 3;
  });
  const fmtOf = (ds) => ds.fmt || "number";
  const tooltip = { callbacks: { label: (c) => (c.dataset.label ? c.dataset.label + ": " : "") +
    fmtValue(fmtOf(c.dataset), w.chart === "pie" ? c.parsed : (w.chart === "hbar" ? c.parsed.x : c.parsed.y)) } };
  let config;
  if (w.chart === "pie") {
    config = { type: "doughnut", data: { labels, datasets: [{ ...datasets[0], borderWidth: 0 }] },
      options: { responsive: true, maintainAspectRatio: false, cutout: "55%",
        plugins: { legend: { position: "right", labels: { color: t.ink, boxWidth: 12 } }, tooltip } } };
  } else {
    const horizontal = w.chart === "hbar";
    const stacked = w.chart === "stacked";
    const valueAxis = (fmt, pos) => ({ beginAtZero: true, stacked, position: pos,
      ticks: { color: t.color, callback: (v) => fmtShort(fmt, v) }, grid: { color: t.grid, drawOnChartArea: pos !== "right" } });
    const catAxis = { stacked, ticks: { color: t.color, maxTicksLimit: horizontal ? 30 : 14, autoSkip: true }, grid: { display: false } };
    const scales = horizontal ? { y: catAxis, x: valueAxis(datasets[0].fmt, "bottom") } : { x: catAxis, y: valueAxis(datasets[0].fmt, "left") };
    if (!horizontal && datasets.some((d) => d.yAxisID === "y1")) scales.y1 = valueAxis(datasets.find((d) => d.yAxisID === "y1").fmt, "right");
    if (horizontal) datasets.forEach((d) => { d.xAxisID = "x"; delete d.yAxisID; });
    config = { type: isLine ? "line" : "bar", data: { labels, datasets },
      options: { responsive: true, maintainAspectRatio: false, indexAxis: horizontal ? "y" : "x",
        plugins: { legend: { display: datasets.length > 1, labels: { color: t.ink, boxWidth: 12 } }, tooltip }, scales } };
  }
  charts.set(w.id, new Chart(canvas, config));
}

function table(cols, rows) {
  return el("table", {}, [
    el("thead", {}, [el("tr", {}, cols.map((c) => el("th", { text: c.label })))]),
    el("tbody", {}, rows.map((r) => el("tr", {}, r.map((v, i) =>
      el("td", { text: cols[i].kind === "measure" ? fmtValue(cols[i].format, v) : String(v ?? "") }))))),
  ]);
}

function setupCanvasDrop() {
  const canvas = $("canvas");
  canvas.addEventListener("dragover", (e) => {
    if ([...e.dataTransfer.types].includes("application/x-field")) { e.preventDefault(); canvas.classList.add("drop"); }
    else if ([...e.dataTransfer.types].includes("application/x-widget")) e.preventDefault();
  });
  canvas.addEventListener("dragleave", (e) => { if (e.target === canvas) canvas.classList.remove("drop"); });
  canvas.addEventListener("drop", (e) => {
    e.preventDefault(); canvas.classList.remove("drop");
    const f = e.dataTransfer.getData("application/x-field");
    const moved = e.dataTransfer.getData("application/x-widget");
    if (f) createFromField(JSON.parse(f));
    else if (moved) {          // dropped on empty space: move to the end
      const src = B.widgets.find((x) => x.id === +moved);
      B.widgets = B.widgets.filter((x) => x.id !== +moved).concat(src);
      markDirty(); renderCanvas();
    }
  });
  canvas.addEventListener("click", (e) => { if (e.target === canvas) select(null); });
  const clearDrop = () => document.querySelectorAll(".drop").forEach((n) => n.classList.remove("drop"));
  document.addEventListener("drop", clearDrop, true);
  document.addEventListener("dragend", clearDrop, true);
}

// ---- saving -----------------------------------------------------------------------
function currentSpec() {
  return {
    widgets: B.widgets.map((w) => {
      const o = { chart: w.chart, dims: w.dims, measures: w.measures, span: w.span, title: w.customTitle ? w.title : "" };
      if (w.limit) { o.limit = w.limit; o.sort = w.sort; }
      if (Object.keys(w.filters || {}).length) o.filters = w.filters;
      return o;
    }),
    filters: Object.fromEntries(Object.entries(B.filters).filter(([, v]) => v)),
  };
}

const localDashboards = () => { try { return JSON.parse(localStorage.getItem(LOCAL_KEY)) || []; } catch { return []; } };
const saveLocal = (list) => { try { localStorage.setItem(LOCAL_KEY, JSON.stringify(list)); } catch { /* storage blocked */ } };

async function listDashboards() {
  const list = B.meta.read_only ? localDashboards().map(({ id, name }) => ({ id, name })) : await api("/api/dashboards");
  const sel = $("dash-select");
  sel.replaceChildren(el("option", { value: "", text: list.length ? "Saved dashboards…" : "No saved dashboards" }),
    ...list.map((d) => el("option", { value: d.id, text: d.name, selected: d.id === B.dashId })));
  $("delete-btn").hidden = !B.dashId;
}

async function save() {
  const name = $("dash-name").value.trim() || "Untitled dashboard";
  const spec = currentSpec();
  try {
    if (B.meta.read_only) {
      const list = localDashboards();
      const id = B.dashId || Date.now();
      const i = list.findIndex((d) => d.id === id);
      const rec = { id, name, spec };
      if (i >= 0) list[i] = rec; else list.push(rec);
      saveLocal(list); B.dashId = id;
      notify("Saved in this browser (the public demo cannot store dashboards on the server).");
    } else {
      const d = B.dashId ? await api("/api/dashboards/" + B.dashId, { method: "PUT", body: { name, spec } })
                         : await api("/api/dashboards", { method: "POST", body: { name, spec } });
      B.dashId = d.id; notify(`Saved "${d.name}".`);
    }
    B.dirty = false; await listDashboards();
  } catch (e) { notify("Could not save: " + e.message, true); }
}

async function openDashboard(id) {
  if (!id) return;
  if (B.dirty && B.widgets.length && !confirm("Discard unsaved changes to this dashboard?")) { $("dash-select").value = B.dashId || ""; return; }
  try {
    const d = B.meta.read_only ? localDashboards().find((x) => String(x.id) === String(id))
                               : await api("/api/dashboards/" + id);
    if (!d) throw new Error("not found");
    B.dashId = d.id; $("dash-name").value = d.name;
    B.widgets = (d.spec.widgets || []).map((s) => newWidget(s));
    const f = d.spec.filters || {};
    Object.assign(B.filters, { preset: f.preset || "all", start: f.start || "", end: f.end || "",
                               store: f.store || "", category: f.category || "" });
    syncFilterInputs(); B.selected = null; B.dirty = false; renderCanvas(); listDashboards();
  } catch (e) { notify("Could not open dashboard: " + e.message, true); }
}

function newDashboard() {
  if (B.dirty && B.widgets.length && !confirm("Start a new dashboard? Unsaved changes will be lost.")) return;
  B.dashId = null; B.widgets = []; B.selected = null; B.dirty = false;
  $("dash-name").value = "Untitled dashboard"; renderCanvas(); listDashboards();
}

async function deleteDashboard() {
  if (!B.dashId || !confirm(`Delete the dashboard "${$("dash-name").value}"?`)) return;
  try {
    if (B.meta.read_only) saveLocal(localDashboards().filter((d) => d.id !== B.dashId));
    else await api("/api/dashboards/" + B.dashId, { method: "DELETE" });
    B.dirty = false; newDashboard(); notify("Dashboard deleted.");
  } catch (e) { notify("Could not delete: " + e.message, true); }
}

// ---- filters ------------------------------------------------------------------------
function syncFilterInputs() {
  const f = B.filters;
  if (f.preset !== "custom") Object.assign(f, presetRange(f.preset, B.meta.min_date, B.meta.max_date));
  $("f-preset").value = f.preset; $("f-start").value = f.start; $("f-end").value = f.end;
  $("f-store").value = f.store; $("f-category").value = f.category;
}

function fillFilterOptions() {
  const fill = (sel, items, all) => sel.replaceChildren(el("option", { value: "", text: all }), ...items.map((i) => el("option", { value: i, text: i })));
  fill($("f-store"), B.meta.stores, "All stores"); fill($("f-category"), B.meta.categories, "All categories");
  ["f-start", "f-end"].forEach((id) => { $(id).min = B.meta.min_date || ""; $(id).max = B.meta.max_date || ""; });
}

function setupFilters() {
  const changed = () => { markDirty(); B.widgets.forEach(loadWidgetFresh); };
  $("f-preset").addEventListener("change", (e) => { B.filters.preset = e.target.value; syncFilterInputs(); changed(); });
  const dateChange = () => { B.filters.start = $("f-start").value; B.filters.end = $("f-end").value;
    B.filters.preset = "custom"; $("f-preset").value = "custom"; if (B.filters.start && B.filters.end) changed(); };
  $("f-start").addEventListener("change", dateChange); $("f-end").addEventListener("change", dateChange);
  $("f-store").addEventListener("change", (e) => { B.filters.store = e.target.value; changed(); });
  $("f-category").addEventListener("change", (e) => { B.filters.category = e.target.value; changed(); });
}

function loadWidgetFresh(w) { renderWidget(w); }

// ---- assistant -----------------------------------------------------------------------
const SUGGESTIONS = ["Build a dashboard", "Top 5 products by profit", "Sales by month as a line",
                     "Which store is falling behind?", "Clean my data"];

function chatMsg(who, text, extra = []) {
  const m = el("div", { class: "msg " + who }, [el("div", { class: "bubble", text }), ...extra]);
  $("chat").append(m); $("chat").scrollTop = $("chat").scrollHeight;
  return m;
}

async function ask(message) {
  chatMsg("me", message);
  const thinking = chatMsg("bot", "Thinking…");
  try {
    const res = await api("/api/assistant", { method: "POST", body: {
      message, widgets: B.widgets.map((w) => ({ title: w.customTitle ? w.title : autoTitle(w), chart: w.chart, dims: w.dims, measures: w.measures })),
      filters: { start: B.filters.start, end: B.filters.end, store: B.filters.store, category: B.filters.category } } });
    thinking.remove();
    const extra = [];
    let changed = false;
    for (const a of res.actions) {
      if (a.type === "add_widget" && a.optional) {
        extra.push(el("button", { class: "btn small", text: "Add this chart", onclick: (e) => { e.target.disabled = true; addFromSpec(a.widget); } }));
      } else if (a.type === "add_widget") { addFromSpec(a.widget, false); changed = true; }
      else if (a.type === "clear_dashboard") { B.widgets = []; B.selected = null; markDirty(); changed = true; }
      else extra.push(confirmCard(a, res.read_only));
    }
    if (res.note) extra.push(el("div", { class: "note", text: res.note }));
    chatMsg("bot", res.reply, extra);
    if (changed) renderCanvas();
  } catch (e) {
    thinking.remove(); chatMsg("bot", "Sorry, something went wrong: " + e.message);
  }
}

function addFromSpec(spec, render = true) {
  const w = newWidget(spec);
  B.widgets.push(w); markDirty();
  if (render) renderCanvas();
}

function confirmCard(a, readOnly) {
  const box = el("div", { class: "confirm" });
  if (readOnly) { box.append(el("div", { class: "note", text: "This public demo is read-only, so data can't be changed here. Run the app locally to do this." })); return box; }
  const go = el("button", { class: "btn small", text: a.type === "delete_rows" ? "Delete" : "Confirm" });
  const cancel = el("button", { class: "btn small ghost", text: "Cancel" });
  cancel.addEventListener("click", () => { box.replaceChildren(el("div", { class: "note", text: "Cancelled." })); });
  go.addEventListener("click", async () => {
    go.disabled = cancel.disabled = true;
    try {
      let msg;
      if (a.type === "rename") { const r = await api("/api/data/rename", { method: "POST", body: { field: a.field, from: a.from, to: a.to } }); msg = `Renamed ${r.updated.toLocaleString()} rows.`; }
      else if (a.type === "delete_rows") { const r = await api("/api/data/delete", { method: "POST", body: { field: a.field, value: a.value } }); msg = `Deleted ${r.deleted.toLocaleString()} rows.`; }
      else if (a.type === "tidy") { const r = await api("/api/data/tidy", { method: "POST" });
        const fixed = Object.values(r.names_fixed).reduce((s, n) => s + n, 0);
        msg = `Tidied ${fixed.toLocaleString()} rows with inconsistent names and removed ${r.duplicates_removed.toLocaleString()} duplicate rows.`; }
      box.replaceChildren(el("div", { class: "note ok", text: msg }));
      await refreshMeta(); renderCanvas();
    } catch (e) { box.replaceChildren(el("div", { class: "note err", text: "Failed: " + e.message })); }
  });
  box.append(go, cancel);
  return box;
}

function setupAssistant() {
  const badge = $("ai-badge");
  if (B.fields.assistant === "watsonx") { badge.textContent = "IBM watsonx.ai"; badge.classList.add("ibm"); }
  else { badge.textContent = "Built-in rules"; badge.title = "Set WATSONX_API_KEY and WATSONX_PROJECT_ID to use IBM watsonx.ai"; }
  $("suggestions").replaceChildren(...SUGGESTIONS.map((s) => el("button", { class: "sugg", text: s, onclick: () => ask(s) })));
  chatMsg("bot", "Hi! Ask me to build charts (\"profit by store as a pie\"), answer questions (\"what were total sales in March?\") or tidy your data.");
  $("chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("chat-input").value.trim(); if (!v) return;
    $("chat-input").value = ""; ask(v);
  });
  $("ai-toggle").addEventListener("click", () => {
    const hidden = $("assistant").classList.toggle("closed");
    $("ai-toggle").setAttribute("aria-expanded", String(!hidden));
    document.querySelector(".workspace").classList.toggle("no-ai", hidden);
    setTimeout(() => charts.forEach((c) => c.resize()), 50);
  });
}

async function refreshMeta() {
  [B.meta, B.fields] = await Promise.all([api("/api/meta"), api("/api/fields")]);
  fillFilterOptions(); syncFilterInputs();
}

async function init() {
  await refreshMeta();
  renderFields(); setupCanvasDrop(); setupFilters(); setupAssistant();
  $("save-btn").addEventListener("click", save);
  $("new-btn").addEventListener("click", newDashboard);
  $("delete-btn").addEventListener("click", deleteDashboard);
  $("dash-select").addEventListener("change", (e) => openDashboard(e.target.value));
  $("dash-name").addEventListener("input", markDirty);
  $("starter-btn").addEventListener("click", () => ask("Build a dashboard"));
  window.addEventListener("beforeunload", (e) => { if (B.dirty && B.widgets.length) e.preventDefault(); });
  await listDashboards();
  renderCanvas();
}

init().catch((e) => notify("Failed to start: " + e.message, true));
