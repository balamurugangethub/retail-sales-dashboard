"use strict";
// Data page: import with a cleaning preview, browse/edit rows, bulk fixes.

const D = { file: null, preview: null, page: 1, size: 50, sort: "-date", values: null, readOnly: false };
const COLS = [["date", "Date"], ["store", "Store"], ["category", "Category"], ["product", "Product"],
              ["orders", "Orders"], ["units", "Units"], ["revenue", "Sales"], ["cost", "Cost"]];

// ---- import ------------------------------------------------------------------------
async function runPreview(mapping) {
  if (!D.file) return;
  const fd = new FormData();
  fd.append("file", D.file);
  fd.append("drop_duplicates", $("dedupe").checked ? "1" : "0");
  if (mapping) fd.append("mapping", JSON.stringify(mapping));
  try {
    D.preview = await api("/api/import/preview", { method: "POST", form: fd });
    renderPreview();
  } catch (e) { notify("Could not read the file: " + e.message, true); }
}

function renderPreview() {
  const p = D.preview;
  $("preview").hidden = false;
  const s = $("summary");
  if (p.error) {
    s.className = "summary bad";
    s.textContent = p.error;
  } else {
    s.className = "summary " + (p.problem_count ? "warn" : "ok");
    s.textContent = `${p.total_rows.toLocaleString()} rows read · ${p.clean_rows.toLocaleString()} ready to import` +
      (p.problem_count ? ` · ${p.problem_count.toLocaleString()} will be skipped` : "") +
      (p.date_range ? ` · ${p.date_range[0]} to ${p.date_range[1]}` : "") + ` · dates read ${p.date_order}`;
  }
  // mapping
  const opts = (sel) => [el("option", { value: "", text: "(none)" }),
    ...p.columns.map((c) => el("option", { value: c, text: c, selected: c === sel }))];
  $("mapping").replaceChildren(el("tbody", {}, p.fields.map(([f, label]) => {
    const col = p.mapping[f];
    const i = col ? p.columns.indexOf(col) : -1;
    const sample = i >= 0 ? p.sample_raw.map((r) => r[i]).filter(Boolean).slice(0, 3).join(", ") : "";
    return el("tr", {}, [el("th", { text: label }), el("td", {}, [el("select", { "data-field": f }, opts(col))]),
                         el("td", { class: "muted small", text: sample })]);
  })));
  const li = (cls, text) => el("li", { class: cls, text });
  $("fixes").replaceChildren(...Object.entries(p.fixes || {}).map(([k, n]) => li("ok", `${k}: ${n.toLocaleString()}`)));
  if (!p.error && !Object.keys(p.fixes || {}).length) $("fixes").append(li("ok", "Nothing needed fixing."));
  $("warnings").replaceChildren(...(p.warnings || []).map((w) => li("warn", w)));
  $("problems-title").hidden = !p.problem_count;
  $("problems").replaceChildren(...(p.problems || []).slice(0, 25).map((x) => li("bad", `Line ${x.line}: ${x.reason}`)),
    ...(p.problem_count > 25 ? [li("bad", `…and ${p.problem_count - 25} more`)] : []));
  const head = el("thead", {}, [el("tr", {}, COLS.map(([, l]) => el("th", { text: l })))]);
  $("sample").replaceChildren(head, el("tbody", {}, (p.sample_clean || []).map((r) =>
    el("tr", {}, r.map((v, i) => el("td", { text: i >= 6 ? fmtValue("money", v) : String(v) }))))));
  $("import-btn").disabled = !p.import_id;
  $("import-btn").title = p.read_only ? "Read-only demo" : "";
}

function currentMapping() {
  const m = {};
  document.querySelectorAll("#mapping select").forEach((s) => { m[s.dataset.field] = s.value || null; });
  return m;
}

async function doImport() {
  const mode = document.querySelector("input[name=mode]:checked").value;
  if (mode === "replace" && !confirm("Replace ALL current sales data with this file?")) return;
  try {
    const r = await api("/api/import/commit", { method: "POST", body: { import_id: D.preview.import_id, mode } });
    notify(`Imported ${r.imported.toLocaleString()} rows. Open the Dashboard builder to explore them.`);
    D.preview = null; D.file = null; $("preview").hidden = true; $("file-input").value = "";
    await refreshAll();
  } catch (e) { notify("Import failed: " + e.message, true); }
}

function setupImport() {
  const zone = $("drop-zone");
  const take = (f) => { if (!f) return; D.file = f; zone.querySelector("strong").textContent = f.name; runPreview(); };
  $("file-input").addEventListener("change", (e) => take(e.target.files[0]));
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("drop"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("drop"));
  zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("drop"); take(e.dataTransfer.files[0]); });
  $("recheck-btn").addEventListener("click", () => runPreview(currentMapping()));
  $("dedupe").addEventListener("change", () => runPreview(D.preview ? currentMapping() : null));
  $("import-btn").addEventListener("click", doImport);
}

// ---- rows table ------------------------------------------------------------------------
async function loadRows() {
  try {
    const r = await api("/api/rows", { params: { page: D.page, size: D.size, sort: D.sort, q: $("q").value.trim(),
      store: $("r-store").value, category: $("r-category").value } });
    const pages = Math.max(1, Math.ceil(r.total / D.size));
    $("row-count").textContent = r.total.toLocaleString() + " rows";
    $("page-info").textContent = `Page ${r.page} of ${pages}`;
    $("prev-btn").disabled = r.page <= 1; $("next-btn").disabled = r.page >= pages;
    const head = el("thead", {}, [el("tr", {}, [...COLS.map(([k, l]) => {
      const arrow = D.sort.replace("-", "") === k ? (D.sort.startsWith("-") ? " ▼" : " ▲") : "";
      return el("th", { class: "sortable", text: l + arrow, onclick: () => { D.sort = D.sort === "-" + k ? k : "-" + k; D.page = 1; loadRows(); } });
    }), el("th", { text: "" })])]);
    const body = el("tbody", {}, r.rows.map((row) => el("tr", {}, [
      ...COLS.map(([k]) => {
        const td = el("td", { text: k === "revenue" || k === "cost" ? fmtValue("money", row[k]) : String(row[k]), "data-k": k });
        if (!D.readOnly) td.addEventListener("dblclick", () => editCell(td, row, k));
        return td;
      }),
      el("td", {}, [D.readOnly ? null : el("button", { class: "icon", text: "🗑", title: "Delete row", "aria-label": "Delete row",
        onclick: async () => { if (!confirm("Delete this row?")) return;
          try { await api("/api/rows/" + row.id, { method: "DELETE" }); loadRows(); } catch (e) { notify(e.message, true); } } })]),
    ])));
    $("rows").replaceChildren(head, body);
  } catch (e) { notify("Could not load rows: " + e.message, true); }
}

function editCell(td, row, k) {
  if (td.querySelector("input")) return;
  const input = el("input", { value: String(row[k]), class: "cell-edit", type: k === "date" ? "date" : "text" });
  td.replaceChildren(input); input.focus(); input.select && input.select();
  let done = false;
  const finish = async (saveIt) => {
    if (done) return; done = true;
    if (saveIt && input.value !== String(row[k])) {
      try { await api("/api/rows/" + row.id, { method: "PATCH", body: { [k]: input.value } }); notify("Saved."); }
      catch (e) { notify("Not saved: " + e.message, true); }
      loadRows(); if (["store", "category", "product"].includes(k)) loadValues();
    } else loadRows();
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
  input.addEventListener("blur", () => finish(true));
}

// ---- bulk tools ------------------------------------------------------------------------
async function loadValues() {
  const f = await api("/api/fields");
  D.values = f.values;
  const fill = (sel, items, all) => { const cur = sel.value;
    sel.replaceChildren(...(all ? [el("option", { value: "", text: all })] : []), ...items.map((v) => el("option", { value: v, text: v })));
    if (items.includes(cur)) sel.value = cur; };
  fill($("r-store"), [...D.values.store].sort(), "All stores");
  fill($("r-category"), [...D.values.category].sort(), "All categories");
  fill($("ren-from"), [...D.values[$("ren-field").value]].sort());
  fill($("del-value"), [...D.values[$("del-field").value]].sort());
}

function setupBulk() {
  $("ren-field").addEventListener("change", loadValues);
  $("del-field").addEventListener("change", loadValues);
  $("tidy-btn").addEventListener("click", async () => {
    try {
      const r = await api("/api/data/tidy", { method: "POST" });
      const fixed = Object.values(r.names_fixed).reduce((s, n) => s + n, 0);
      notify(`Tidied ${fixed.toLocaleString()} rows with inconsistent names and removed ${r.duplicates_removed.toLocaleString()} duplicates.`);
      refreshAll();
    } catch (e) { notify(e.message, true); }
  });
  $("ren-btn").addEventListener("click", async () => {
    const field = $("ren-field").value, from = $("ren-from").value, to = $("ren-to").value.trim();
    if (!from || !to) return notify("Pick a name and type the new one.", true);
    try {
      const r = await api("/api/data/rename", { method: "POST", body: { field, from, to } });
      notify(`Renamed "${from}" to "${to}" on ${r.updated.toLocaleString()} rows.`); $("ren-to").value = ""; refreshAll();
    } catch (e) { notify(e.message, true); }
  });
  $("del-btn").addEventListener("click", async () => {
    const field = $("del-field").value, value = $("del-value").value;
    if (!value || !confirm(`Permanently delete every row for ${field} "${value}"?`)) return;
    try {
      const r = await api("/api/data/delete", { method: "POST", body: { field, value } });
      notify(`Deleted ${r.deleted.toLocaleString()} rows.`); refreshAll();
    } catch (e) { notify(e.message, true); }
  });
  $("reset-btn").addEventListener("click", async () => {
    if (!confirm("Replace everything with the original sample data?")) return;
    try { await api("/api/reset", { method: "POST" }); notify("Sample data restored."); refreshAll(); }
    catch (e) { notify(e.message, true); }
  });
}

async function refreshAll() { D.page = 1; await Promise.all([loadValues(), loadRows()]); }

async function init() {
  const meta = await api("/api/meta");
  D.readOnly = meta.read_only;
  if (D.readOnly) {
    $("ro-banner").hidden = false;
    document.querySelectorAll("#reset-btn, #tidy-btn, #ren-btn, #del-btn").forEach((b) => (b.disabled = true));
  }
  setupImport(); setupBulk();
  let t;
  $("q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { D.page = 1; loadRows(); }, 300); });
  $("r-store").addEventListener("change", () => { D.page = 1; loadRows(); });
  $("r-category").addEventListener("change", () => { D.page = 1; loadRows(); });
  $("prev-btn").addEventListener("click", () => { D.page--; loadRows(); });
  $("next-btn").addEventListener("click", () => { D.page++; loadRows(); });
  await refreshAll();
}

init().catch((e) => notify("Failed to start: " + e.message, true));
