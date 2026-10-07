"use strict";
// Small helpers shared by the builder and data pages.

const $ = (id) => document.getElementById(id);
const PALETTE = ["#1f6fe5", "#f1c21b", "#2e9e6b", "#d64545", "#8a5cf6", "#14a3b8", "#e8782a", "#6b7d99",
                 "#c2459e", "#3fb34f"];
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

function el(tag, props = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "text") e.textContent = v;
    else if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  (Array.isArray(children) ? children : [children]).forEach((c) => c !== null && c !== undefined && e.append(c));
  return e;
}

function notify(msg, isError = false) {
  const n = $("notice");
  n.textContent = msg; n.className = "notice" + (isError ? " error" : ""); n.hidden = false;
  clearTimeout(notify.t); notify.t = setTimeout(() => (n.hidden = true), 7000);
}

async function api(path, { method = "GET", params, body, form } = {}) {
  const q = params ? new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null)) : null;
  const opts = { method };
  if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
  if (form) opts.body = form;
  const r = await fetch(path + (q && q.toString() ? "?" + q : ""), opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function fmtValue(format, v) {
  if (v === null || v === undefined) return "–";
  if (format === "money") return "$" + (Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : v.toFixed(2));
  if (format === "pct") return v.toFixed(1) + "%";
  return Math.round(v).toLocaleString();
}

function fmtShort(format, v) {
  if (format === "pct") return v.toFixed(0) + "%";
  const a = Math.abs(v), p = format === "money" ? "$" : "";
  if (a >= 1e6) return p + (v / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return p + (v / 1e3).toFixed(a >= 1e5 ? 0 : 1) + "K";
  return p + (format === "money" && a < 100 ? v.toFixed(2) : Math.round(v));
}

// Period presets relative to the last date in the data
function presetRange(preset, minDate, maxDate) {
  if (!maxDate) return { start: "", end: "" };
  const max = new Date(maxDate + "T00:00:00Z");
  const iso = (d) => d.toISOString().slice(0, 10);
  const monthsBack = (n) => iso(new Date(Date.UTC(max.getUTCFullYear(), max.getUTCMonth() - (n - 1), 1)));
  let start = minDate;
  if (preset === "12m") start = monthsBack(12);
  else if (preset === "6m") start = monthsBack(6);
  else if (preset === "3m") start = monthsBack(3);
  else if (preset === "30d") start = iso(new Date(max.getTime() - 29 * 86400000));
  return { start: start < minDate ? minDate : start, end: maxDate };
}
