"use strict";
// Two separate settings, both kept in this browser only and loaded in <head> so they
// apply before anything paints:
//   Theme       the whole app's two colours (background and ink), picked from a fixed list.
//   ChartStyle  how charts and graphs look (chart colour, chart text, graph lines, chart
//               background, chart font), like the chart properties in Cognos. It never
//               touches the rest of the interface.
(function () {
  // Curated palettes. Each family has a dark and a light version built from one hue: a tinted
  // background, a slightly lifted surface for cards, soft text and one accent. The accent drives
  // buttons, the active page, hover edges and charts.   name, hue, saturation, dark accent, light accent
  const FAMILIES = [
    ["Obsidian", 40, 6, "#ebe7de", "#1d1b18"],
    ["Sapphire", 222, 42, "#6e98f0", "#1d3f94"],
    ["Amethyst", 268, 32, "#b89cf2", "#5f3fb0"],
    ["Garnet", 350, 34, "#e57a86", "#962235"],
    ["Rose Quartz", 340, 30, "#eb9bb9", "#a62d5c"],
    ["Champagne", 42, 30, "#e6c878", "#80601a"],
    ["Emerald", 160, 38, "#52c8a0", "#15694c"],
    ["Glacier", 198, 40, "#7cc9ea", "#17708f"],
    ["Copper", 24, 36, "#e69d62", "#a24c14"],
  ];
  function hsl(h, s, l) {      // hue 0-360, saturation and lightness in percent -> #rrggbb
    s /= 100; l /= 100;
    const k = (n) => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
    const f = (n) => Math.round(255 * (l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1))))).toString(16).padStart(2, "0");
    return "#" + f(0) + f(8) + f(4);
  }
  const THEMES = FAMILIES.flatMap(([name, h, s, dark, light]) => [
    { id: name.toLowerCase().replace(" ", "-") + "-dark", name, mode: "Dark", accent: dark,
      paper: hsl(h, s * 0.85, 6), surface: hsl(h, s * 0.75, 9.5), text: hsl(h, Math.min(s, 16), 94) },
    { id: name.toLowerCase().replace(" ", "-") + "-light", name, mode: "Light", accent: light,
      paper: hsl(h, s * 0.45, 95.5), surface: "#ffffff", text: hsl(h, s * 0.8, 10) },
  ]);
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
  const SUGGESTED = ["#ffffff", "#000000", ...FAMILIES.flatMap((f) => [f[3], f[4]])];

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
  const rgba = (h, a) => { const n = parseInt(h.slice(1), 16); return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${a})`; };
  function applyTheme(t) {
    const dark = isDark(t.paper);
    const set = (k, v) => root.setProperty(k, v);
    set("--paper", t.paper); set("--surface", t.surface); set("--text", t.text); set("--ink", t.accent);
    set("--on-accent", isDark(t.accent) ? "#ffffff" : t.paper === t.surface ? "#000000" : mix("#000000", t.paper, .85));
    set("--muted", mix(t.text, t.paper, dark ? .62 : .66));
    set("--line", mix(t.text, t.paper, dark ? .16 : .15));
    set("--wash", mix(t.text, t.paper, dark ? .06 : .05));
    set("--glow", rgba(t.accent, dark ? .16 : .12));
    set("--shadow", dark ? "0 10px 30px rgba(0,0,0,.45)" : "0 10px 30px " + rgba(t.text, .10));
    set("color-scheme", dark ? "dark" : "light");
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = t.paper;
  }
  applyTheme(theme);
  const fire = () => window.dispatchEvent(new CustomEvent("themechange"));

  function pickTheme(t, grid) {
    theme = t; write("rsd-theme", { id: t.id });
    document.documentElement.classList.add("theme-fade");
    applyTheme(t); fire();
    setTimeout(() => document.documentElement.classList.remove("theme-fade"), 450);
    if (grid) grid.querySelectorAll(".swatch").forEach((x) => x.classList.toggle("on", x.dataset.id === t.id));
  }

  function themePicker() {
    const box = h("div", "theme-picker");
    box.append(h("h3", null, "Colour theme"), h("p", "tp-note", "Changes the whole app. Each palette has a dark and a light version."));
    ["Dark", "Light"].forEach((mode) => {
      box.append(h("h3", "tp-mode", mode));
      const grid = h("div", "swatches");
      THEMES.filter((t) => t.mode === mode).forEach((t) => {
        const b = h("button", "swatch"); b.type = "button"; b.title = t.name + " " + mode.toLowerCase(); b.dataset.id = t.id;
        b.style.background = `linear-gradient(135deg, ${t.surface}, ${t.paper})`; b.style.color = t.text; b.style.borderColor = mix(t.text, t.paper, .2);
        b.classList.toggle("on", t.id === theme.id);
        const dot = h("span", "dot"); dot.style.background = t.accent;
        const top = h("span", "sw-top"); top.append(h("span", "aa", "Aa"), dot);
        b.append(top, h("span", "nm", t.name));
        b.onclick = () => { pickTheme(t, box); };
        grid.append(b);
      });
      box.append(grid);
    });
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

  window.Theme = { picker: themePicker, button: () => button("Theme", themePicker), list: () => THEMES.map((t) => ({ id: t.id, name: t.name, mode: t.mode, accent: t.accent })),
                 apply: (id) => { const t = THEMES.find((x) => x.id === id); if (t) pickTheme(t); }, current: () => theme.id };
  window.ChartStyle = { picker: stylePicker, button: () => button("Chart style", stylePicker), active: hasStyle };
})();
