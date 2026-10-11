"use strict";
// Colour themes. Loaded in <head> on every page so the saved theme applies before
// anything paints. A theme is three colours: paper (background), ink (text, lines,
// buttons) and chart (bars, lines and slices). The choice is kept in this browser only.
(function () {
  const KEY = "rsd-theme";
  const COLOURS = [
    // name, shade used on white, shade used on black
    ["Purple", "#6d28d9", "#b794ff"],
    ["Pink", "#c2185b", "#ff7eb6"],
    ["Dark blue", "#0b2a6f", "#5b8cff"],
    ["Red", "#c62828", "#ff5a5a"],
    ["Yellow", "#8a6d00", "#ffd400"],
    ["Green", "#1b7a3a", "#3ddc84"],
    ["Light blue", "#0277bd", "#6cc6ff"],
    ["Orange", "#c75000", "#ff9a3c"],
  ];
  const THEMES = [
    { id: "black-white", name: "Black & white", paper: "#000000", ink: "#ffffff" },
    { id: "white-black", name: "White & black", paper: "#ffffff", ink: "#000000" },
    ...COLOURS.map(([n, onWhite]) => ({ id: n.toLowerCase().replace(" ", "-") + "-white", name: n + " & white", paper: "#ffffff", ink: onWhite })),
    ...COLOURS.map(([n, , onBlack]) => ({ id: n.toLowerCase().replace(" ", "-") + "-black", name: n + " & black", paper: "#000000", ink: onBlack })),
  ];
  const DEFAULT = THEMES[0];

  const FONTS = [
    ["Inter (default)", '"Inter", "Helvetica Neue", "Segoe UI", Helvetica, Arial, sans-serif'],
    ["System", 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'],
    ["Verdana", 'Verdana, Geneva, sans-serif'],
    ["Trebuchet", '"Trebuchet MS", Tahoma, sans-serif'],
    ["Georgia (serif)", 'Georgia, "Times New Roman", serif'],
    ["Palatino (serif)", '"Palatino Linotype", Palatino, "Book Antiqua", serif'],
    ["Courier (mono)", '"Courier New", Courier, monospace'],
    ["Consolas (mono)", 'Consolas, "Lucida Console", Menlo, monospace'],
  ];
  // Suggested colours for the quick-pick rows
  const SUGGESTED = ["#ffffff", "#000000", "#b794ff", "#6d28d9", "#ff7eb6", "#c2185b", "#5b8cff", "#0b2a6f", "#ff5a5a", "#c62828",
                     "#ffd400", "#8a6d00", "#3ddc84", "#1b7a3a", "#6cc6ff", "#0277bd", "#ff9a3c", "#c75000"];
  const FIELDS = [
    ["paper", "Background"], ["text", "Text"], ["chart", "Charts"], ["grid", "Graph lines"],
  ];

  const hex = (s) => /^#[0-9a-f]{6}$/i.test(s || "") ? s.toLowerCase() : null;
  function mix(a, b, t) {   // t of a, the rest b
    const p = (h, i) => parseInt(h.slice(1 + 2 * i, 3 + 2 * i), 16);
    return "#" + [0, 1, 2].map((i) => Math.round(p(a, i) * t + p(b, i) * (1 - t)).toString(16).padStart(2, "0")).join("");
  }
  const isDark = (h) => { const n = parseInt(h.slice(1), 16); return ((n >> 16) * 299 + ((n >> 8) & 255) * 587 + (n & 255) * 114) / 1000 < 128; };

  // text, chart and grid are null while they should follow the theme's ink
  const fromTheme = (t) => ({ id: t.id, paper: t.paper, ink: t.ink, text: null, chart: null, grid: null, font: null });

  function load() {
    try {
      const t = JSON.parse(localStorage.getItem(KEY) || "null");
      if (t && hex(t.paper) && hex(t.ink)) {
        const font = FONTS.some((f) => f[1] === t.font) ? t.font : null;
        return { id: t.id || "custom", paper: hex(t.paper), ink: hex(t.ink), text: hex(t.text), chart: hex(t.chart), grid: hex(t.grid), font };
      }
    } catch (_e) { /* storage blocked or bad value: use the default */ }
    return fromTheme(DEFAULT);
  }

  // what each setting resolves to, whether the visitor overrode it or not
  const resolved = (t) => ({
    paper: t.paper, ink: t.ink, text: t.text || t.ink, chart: t.chart || t.ink,
    grid: t.grid || mix(t.ink, t.paper, .25), font: t.font || FONTS[0][1],
  });

  function apply(t) {
    const r = resolved(t), s = document.documentElement.style;
    s.setProperty("--paper", r.paper); s.setProperty("--ink", r.ink); s.setProperty("--text", r.text); s.setProperty("--chart", r.chart);
    s.setProperty("--grid", r.grid); s.setProperty("--font", r.font);
    s.setProperty("--muted", mix(r.text, r.paper, .65));
    s.setProperty("--line", mix(r.ink, r.paper, .25));
    s.setProperty("--wash", mix(r.ink, r.paper, .08));
    s.setProperty("color-scheme", isDark(r.paper) ? "dark" : "light");
  }

  function save(t) {
    try { localStorage.setItem(KEY, JSON.stringify(t)); } catch (_e) { /* still applies for this page */ }
    apply(t);
    window.dispatchEvent(new CustomEvent("themechange", { detail: t }));
  }

  let current = load();
  apply(current);

  // ---- picker ---------------------------------------------------------------
  function picker() {
    const box = document.createElement("div");
    box.className = "theme-picker";
    const h = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; };
    const change = (patch) => { current = { ...current, ...patch }; save(current); mark(); };
    box.append(h("h3", null, "Colour theme"));
    const grid = h("div", "swatches");
    const mark = () => {
      grid.querySelectorAll(".swatch").forEach((b) => b.classList.toggle("on", b.dataset.id === current.id));
      const r = resolved(current);
      FIELDS.forEach(([k]) => { inputs[k].value = r[k]; });
      box.querySelectorAll(".quick button").forEach((b) => b.classList.toggle("on", b.dataset.hex === r[b.dataset.key]));
      fontSel.value = r.font;
    };
    THEMES.forEach((t) => {
      const b = h("button", "swatch"); b.type = "button"; b.dataset.id = t.id; b.title = t.name;
      b.style.background = t.paper; b.style.color = t.ink; b.style.borderColor = t.ink;
      b.append(h("span", "aa", "Aa"), h("span", "nm", t.name));
      b.onclick = () => { current = fromTheme(t); save(current); mark(); };
      grid.append(b);
    });
    box.append(grid, h("h3", null, "Customise (works on any theme)"));
    const inputs = {};
    FIELDS.forEach(([k, label]) => {
      const row = h("div", "cust-row"), l = h("label", null, label), i = document.createElement("input");
      i.type = "color"; i.title = label + ": open the colour wheel"; inputs[k] = i;
      i.oninput = () => change({ id: "custom", [k]: i.value });
      l.append(i);
      const quick = h("div", "quick");
      SUGGESTED.forEach((c) => {
        const b = h("button"); b.type = "button"; b.title = c; b.style.background = c; b.dataset.hex = c; b.dataset.key = k;
        b.setAttribute("aria-label", label + " " + c);
        b.onclick = () => change({ id: "custom", [k]: c });
        quick.append(b);
      });
      row.append(l, quick); box.append(row);
    });
    const frow = h("div", "cust-row"), fl = h("label", null, "Font"), fontSel = document.createElement("select");
    FONTS.forEach(([n, v]) => { const o = h("option", null, n); o.value = v; fontSel.append(o); });
    fontSel.onchange = () => change({ id: "custom", font: fontSel.value === FONTS[0][1] ? null : fontSel.value });
    fl.append(fontSel); frow.append(fl); box.append(frow);
    const reset = h("button", "btn small ghost", "Reset to black & white"); reset.type = "button";
    reset.onclick = () => { current = fromTheme(DEFAULT); save(current); mark(); };
    box.append(reset);
    mark();
    return box;
  }

  // A "Theme" button that opens the picker in a popover under it
  function button() {
    const wrap = document.createElement("span"); wrap.className = "theme-wrap";
    const b = document.createElement("button"); b.type = "button"; b.className = "theme-btn"; b.textContent = "Theme";
    b.setAttribute("aria-expanded", "false");
    let pop = null;
    const close = () => { if (pop) { pop.remove(); pop = null; b.setAttribute("aria-expanded", "false"); } };
    b.onclick = (e) => {
      e.stopPropagation();
      if (pop) return close();
      pop = picker(); pop.classList.add("pop"); pop.onclick = (ev) => ev.stopPropagation();
      wrap.append(pop); b.setAttribute("aria-expanded", "true");
    };
    document.addEventListener("click", close);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    wrap.append(b);
    return wrap;
  }

  window.Theme = { picker, button, current: () => current };
})();
