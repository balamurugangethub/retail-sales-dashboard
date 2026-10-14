"use strict";
// SQL workbench: schema panel, editor, examples, recent queries, result table and CSV download.

const S = { tables: [], last: null };
const RECENT_KEY = "rsd_sql_recent";

function recent() {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch (e) { return []; }
}
function remember(sql) {
  try {
    const list = [sql, ...recent().filter((q) => q !== sql)].slice(0, 10);
    localStorage.setItem(RECENT_KEY, JSON.stringify(list));
  } catch (e) { /* private window: history just is not kept */ }
  fillRecent();
}
function fillRecent() {
  const list = recent(), sel = $("recent");
  sel.replaceChildren(el("option", { value: "", text: list.length ? "Choose one" : "None yet" }),
    ...list.map((q, i) => el("option", { value: String(i), text: q.replace(/\s+/g, " ").slice(0, 70) })));
}

function insertAtCursor(text) {
  const box = $("sql"), start = box.selectionStart, end = box.selectionEnd;
  box.setRangeText(text, start, end, "end");
  box.focus();
}

function renderSchema() {
  $("schema").replaceChildren(...S.tables.map((t) => el("details", { open: S.tables.length < 4 }, [
    el("summary", {}, [
      el("a", { href: "#", class: "sql-name", text: t.name, onclick: (e) => { e.preventDefault(); insertAtCursor(t.name); } }),
      el("span", { class: "hint", text: ` ${t.rows.toLocaleString()} rows` })]),
    el("ul", { class: "sql-cols" }, t.columns.map((c) => el("li", {}, [
      el("a", { href: "#", class: "sql-name", text: c.name, onclick: (e) => { e.preventDefault(); insertAtCursor(c.name); } }),
      el("span", { class: "hint", text: " " + c.type })])))])));
}

function fillExamples() {
  const t = S.tables[0], text = t.columns.find((c) => c.type === "text"), num = t.columns.find((c) => c.type === "number");
  const date = t.columns.find((c) => c.type === "date");
  const ex = [["Preview rows", `SELECT *\nFROM ${t.name}\nLIMIT 20;`],
              ["Count rows", `SELECT COUNT(*) AS rows\nFROM ${t.name};`]];
  if (text) ex.push([`Rows per ${text.name}`, `SELECT ${text.name}, COUNT(*) AS rows\nFROM ${t.name}\nGROUP BY ${text.name}\nORDER BY rows DESC\nLIMIT 20;`]);
  if (text && num) ex.push([`Average ${num.name} per ${text.name}`, `SELECT ${text.name}, ROUND(AVG(${num.name}), 2) AS avg_${num.name}\nFROM ${t.name}\nGROUP BY ${text.name}\nORDER BY avg_${num.name} DESC;`]);
  if (date && num) ex.push([`${num.name} by month`, `SELECT strftime('%Y-%m', ${date.name}) AS month, SUM(${num.name}) AS total\nFROM ${t.name}\nGROUP BY month\nORDER BY month;`]);
  if (text) ex.push([`Missing ${text.name}`, `SELECT COUNT(*) AS missing\nFROM ${t.name}\nWHERE ${text.name} IS NULL OR ${text.name} = '';`]);
  $("examples").replaceChildren(el("option", { value: "", text: "Choose one" }),
    ...ex.map(([label, sql], i) => el("option", { value: sql, text: label })));
}

function renderResult(out) {
  S.last = out;
  const table = $("result");
  table.replaceChildren(
    el("thead", {}, el("tr", {}, out.columns.map((c) => el("th", { text: c })))),
    el("tbody", {}, out.rows.map((r) => el("tr", {}, r.map((v) =>
      el("td", { text: v === null ? "–" : typeof v === "number" ? Number(v.toFixed(4)).toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(v) }))))));
  $("results-card").hidden = false;
  $("result-note").textContent = `${out.rows.length.toLocaleString()} row${out.rows.length === 1 ? "" : "s"} in ${out.ms} ms` +
    (out.truncated ? `. Only the first ${out.rows.length.toLocaleString()} are shown, add a LIMIT or a filter to narrow it.` : ".");
}

async function runQuery() {
  const sql = $("sql").value;
  $("status").textContent = "Running…";
  try {
    const out = await api("/api/sql/run", { method: "POST", body: { sql } });
    renderResult(out); remember(sql.trim());
    $("status").textContent = "";
  } catch (e) {
    $("status").textContent = "";
    $("results-card").hidden = true;
    notify(e.message, true);
  }
}

function csvCell(v) {
  const s = v === null ? "" : String(v);
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
function downloadCsv() {
  if (!S.last) return;
  const lines = [S.last.columns, ...S.last.rows].map((r) => r.map(csvCell).join(","));
  const url = URL.createObjectURL(new Blob([lines.join("\r\n") + "\r\n"], { type: "text/csv" }));
  el("a", { href: url, download: "query-result.csv" }).click();
  URL.revokeObjectURL(url);
}

async function init() {
  const { tables } = await api("/api/sql/schema");
  S.tables = tables;
  if (!tables.length) { $("empty").hidden = false; return; }
  $("work").hidden = false;
  renderSchema(); fillExamples(); fillRecent();
  $("sql").value = recent()[0] || `SELECT *\nFROM ${tables[0].name}\nLIMIT 20;`;
  $("run").onclick = runQuery;
  $("export").onclick = downloadCsv;
  $("sql").addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); runQuery(); } });
  $("examples").onchange = (e) => { if (e.target.value) { $("sql").value = e.target.value; e.target.value = ""; } };
  $("recent").onchange = (e) => { if (e.target.value !== "") { $("sql").value = recent()[Number(e.target.value)]; e.target.value = ""; } };
}

init().catch((e) => notify(e.message, true));
