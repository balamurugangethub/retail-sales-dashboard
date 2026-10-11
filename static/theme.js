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

  const hex = (s) => /^#[0-9a-f]{6}$/i.test(s || "") ? s.toLowerCase() : null;
  function mix(a, b, t) {   // t of a, the rest b
    const p = (h, i) => parseInt(h.slice(1 + 2 * i, 3 + 2 * i), 16);
    return "#" + [0, 1, 2].map((i) => Math.round(p(a, i) * t + p(b, i) * (1 - t)).toString(16).padStart(2, "0")).join("");
  }
  const isDark = (h) => { const n = parseInt(h.slice(1), 16); return ((n >> 16) * 299 + ((n >> 8) & 255) * 587 + (n & 255) * 114) / 1000 < 128; };

  function load() {
    try {
      const t = JSON.parse(localStorage.getItem(KEY) || "null");
      if (t && hex(t.paper) && hex(t.ink)) return { id: t.id || "custom", paper: hex(t.paper), ink: hex(t.ink), chart: hex(t.chart) || hex(t.ink) };
    } catch (_e) { /* storage blocked or bad value: use the default */ }
    return { ...DEFAULT, chart: DEFAULT.ink };
  }

  function apply(t) {
    const s = document.documentElement.style;
    s.setProperty("--paper", t.paper); s.setProperty("--ink", t.ink); s.setProperty("--chart", t.chart || t.ink);
    s.setProperty("--muted", mix(t.ink, t.paper, .65));
    s.setProperty("--line", mix(t.ink, t.paper, .25));
    s.setProperty("--wash", mix(t.ink, t.paper, .08));
    s.setProperty("color-scheme", isDark(t.paper) ? "dark" : "light");
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
    box.append(h("h3", null, "Colour theme"));
    const grid = h("div", "swatches");
    const mark = () => grid.querySelectorAll(".swatch").forEach((b) => b.classList.toggle("on", b.dataset.id === current.id));
    THEMES.forEach((t) => {
      const b = h("button", "swatch"); b.type = "button"; b.dataset.id = t.id; b.title = t.name;
      b.style.background = t.paper; b.style.color = t.ink; b.style.borderColor = t.ink;
      b.append(h("span", "aa", "Aa"), h("span", "nm", t.name));
      b.onclick = () => { current = { id: t.id, paper: t.paper, ink: t.ink, chart: t.ink }; save(current); sync(); mark(); };
      grid.append(b);
    });
    box.append(grid, h("h3", null, "Your own colours"));
    const row = h("div", "custom");
    const inputs = {};
    [["paper", "Background"], ["ink", "Text"], ["chart", "Charts"]].forEach(([k, label]) => {
      const l = h("label", null, label), i = document.createElement("input");
      i.type = "color"; inputs[k] = i;
      i.oninput = () => { current = { ...current, id: "custom", [k]: i.value }; save(current); mark(); };
      l.append(i); row.append(l);
    });
    const reset = h("button", "btn small ghost", "Reset"); reset.type = "button";
    reset.onclick = () => { current = { ...DEFAULT, chart: DEFAULT.ink }; save(current); sync(); mark(); };
    row.append(reset);
    box.append(row);
    const sync = () => { inputs.paper.value = current.paper; inputs.ink.value = current.ink; inputs.chart.value = current.chart; };
    sync(); mark();
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
