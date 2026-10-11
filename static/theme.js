"use strict";
// Two separate settings, both kept in this browser only and loaded in <head> so they
// apply before anything paints:
//   Theme       the whole app's two colours (background and ink), picked from a fixed list.
//   ChartStyle  how charts and graphs look (chart colour, chart text, graph lines, chart
//               background, chart font), like the chart properties in Cognos. It never
//               touches the rest of the interface.
(function () {
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
    ["Same as the app", null],
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

  const hex = (s) => /^#[0-9a-f]{6}$/i.test(s || "") ? s.toLowerCase() : null;
  function mix(a, b, t) {   // t of a, the rest b
    const p = (h, i) => parseInt(h.slice(1 + 2 * i, 3 + 2 * i), 16);
    return "#" + [0, 1, 2].map((i) => Math.round(p(a, i) * t + p(b, i) * (1 - t)).toString(16).padStart(2, "0")).join("");
  }
  const isDark = (h) => { const n = parseInt(h.slice(1), 16); return ((n >> 16) * 299 + ((n >> 8) & 255) * 587 + (n & 255) * 114) / 1000 < 128; };
  const read = (key) => { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (_e) { return null; } };
  const write = (key, v) => { try { localStorage.setItem(key, JSON.stringify(v)); } catch (_e) { /* still applies for this page */ } };
  const h = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; };
  const root = document.documentElement.style;

  // ---- theme: the whole app -------------------------------------------------
  let theme = THEMES.find((t) => t.id === (read("rsd-theme") || {}).id) || DEFAULT;
  function applyTheme(t) {
    root.setProperty("--paper", t.paper); root.setProperty("--ink", t.ink);
    root.setProperty("--muted", mix(t.ink, t.paper, .65));
    root.setProperty("--line", mix(t.ink, t.paper, .25));
    root.setProperty("--wash", mix(t.ink, t.paper, .08));
    root.setProperty("color-scheme", isDark(t.paper) ? "dark" : "light");
  }
  applyTheme(theme);
  const fire = () => window.dispatchEvent(new CustomEvent("themechange"));

  function themePicker() {
    const box = h("div", "theme-picker");
    box.append(h("h3", null, "Colour theme"), h("p", "tp-note", "Changes the whole app."));
    const grid = h("div", "swatches");
    THEMES.forEach((t) => {
      const b = h("button", "swatch"); b.type = "button"; b.title = t.name;
      b.style.background = t.paper; b.style.color = t.ink; b.style.borderColor = t.ink;
      b.classList.toggle("on", t.id === theme.id);
      b.append(h("span", "aa", "Aa"), h("span", "nm", t.name));
      b.onclick = () => {
        theme = t; write("rsd-theme", { id: t.id }); applyTheme(t); fire();
        grid.querySelectorAll(".swatch").forEach((s) => s.classList.toggle("on", s === b));
      };
      grid.append(b);
    });
    box.append(grid);
    return box;
  }

  // ---- chart style: charts and graphs only ----------------------------------
  const FIELDS = [["chart", "Chart colour"], ["text", "Chart text"], ["grid", "Graph lines"], ["bg", "Chart background"]];
  const VARS = { chart: "--chart-color", text: "--chart-text", grid: "--chart-grid", bg: "--chart-bg" };
  let style = (() => {
    const s = read("rsd-chart-style") || {};
    return { chart: hex(s.chart), text: hex(s.text), grid: hex(s.grid), bg: hex(s.bg),
             font: FONTS.some((f) => f[1] === s.font) ? s.font : null };
  })();
  function applyStyle() {
    Object.entries(VARS).forEach(([k, v]) => (style[k] ? root.setProperty(v, style[k]) : root.removeProperty(v)));
    if (style.font) root.setProperty("--chart-font", style.font); else root.removeProperty("--chart-font");
  }
  applyStyle();
  const hasStyle = () => !!(style.chart || style.text || style.grid || style.bg || style.font);

  function stylePicker() {
    const box = h("div", "theme-picker");
    box.append(h("h3", null, "Chart style"), h("p", "tp-note", "Changes charts and graphs only, not the rest of the app."));
    const inputs = {};
    const set = (patch) => { style = { ...style, ...patch }; write("rsd-chart-style", style); applyStyle(); fire(); mark(); };
    const shown = (k) => style[k] || getComputedStyle(document.documentElement).getPropertyValue(VARS[k]).trim() || "#808080";
    FIELDS.forEach(([k, label]) => {
      const row = h("div", "cust-row"), l = h("label", null, label), i = document.createElement("input");
      i.type = "color"; i.title = label + ": open the colour wheel"; inputs[k] = i;
      i.oninput = () => set({ [k]: i.value });
      l.append(i);
      const quick = h("div", "quick");
      SUGGESTED.forEach((c) => {
        const b = h("button"); b.type = "button"; b.title = c; b.style.background = c; b.dataset.hex = c; b.dataset.key = k;
        b.setAttribute("aria-label", label + " " + c);
        b.onclick = () => set({ [k]: c });
        quick.append(b);
      });
      row.append(l, quick); box.append(row);
    });
    const frow = h("div", "cust-row"), fl = h("label", null, "Chart font"), sel = document.createElement("select");
    FONTS.forEach(([n, v], idx) => { const o = h("option", null, n); o.value = String(idx); sel.append(o); });
    sel.onchange = () => set({ font: FONTS[+sel.value][1] });
    fl.append(sel); frow.append(fl); box.append(frow);
    const reset = h("button", "btn small ghost", "Reset chart style"); reset.type = "button";
    reset.onclick = () => { style = { chart: null, text: null, grid: null, bg: null, font: null }; write("rsd-chart-style", style); applyStyle(); fire(); mark(); };
    box.append(reset);
    function mark() {
      FIELDS.forEach(([k]) => { inputs[k].value = hex(shown(k)) || "#808080"; });
      box.querySelectorAll(".quick button").forEach((b) => b.classList.toggle("on", b.dataset.hex === style[b.dataset.key]));
      sel.value = String(Math.max(0, FONTS.findIndex((f) => f[1] === style.font)));
    }
    mark();
    return box;
  }

  // A header button that opens a picker in a popover under it
  function button(label, make) {
    const wrap = h("span", "theme-wrap"), b = h("button", "theme-btn", label);
    b.type = "button"; b.setAttribute("aria-expanded", "false");
    let pop = null;
    const close = () => { if (pop) { pop.remove(); pop = null; b.setAttribute("aria-expanded", "false"); } };
    b.onclick = (e) => {
      e.stopPropagation();
      if (pop) return close();
      document.dispatchEvent(new Event("click"));   // close any other open popover first
      pop = make(); pop.classList.add("pop"); pop.onclick = (ev) => ev.stopPropagation();
      wrap.append(pop); b.setAttribute("aria-expanded", "true");
    };
    document.addEventListener("click", close);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    wrap.append(b);
    return wrap;
  }

  window.Theme = { picker: themePicker, button: () => button("Theme", themePicker) };
  window.ChartStyle = { picker: stylePicker, button: () => button("Chart style", stylePicker), active: hasStyle };
})();
