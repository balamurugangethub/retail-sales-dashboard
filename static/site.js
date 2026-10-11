"use strict";
// Loaded on every app page: adds the account / privacy links to the header and
// sends the visitor back to sign-in if their session ends while the page is open.
(function () {
  const realFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const r = await realFetch(...args);
    const url = String(args[0] && args[0].url ? args[0].url : args[0]);
    if (r.status === 401 && url.startsWith("/api/") && !url.startsWith("/api/auth/")) {
      location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
    }
    return r;
  };
  realFetch("/api/auth/me").then((r) => r.json()).then((me) => {
    const nav = document.querySelector(".nav");
    if (!nav) return;
    const link = (href, text, cls) => {
      const a = document.createElement("a");
      a.href = href; a.textContent = text; if (cls) a.className = cls;
      if (location.pathname === href) a.classList.add("on");
      nav.append(a);
    };
    link("/privacy", "Privacy");
    if (me.user) link("/account", me.user.shop || me.user.name, "account nav-right");
  }).catch(() => {});
})();

// Monochrome chart styling shared by the overview and the builder. Every series is drawn
// in ink on paper; series are told apart by fill pattern, line dash and point shape.
window.Mono = (function () {
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const ink = () => css("--ink") || "#000";
  const paper = () => css("--paper") || "#fff";
  const DASHES = [[], [6, 4], [2, 3], [10, 4, 2, 4], [1, 5], [14, 4]];
  const POINTS = ["circle", "rect", "triangle", "rectRot", "crossRot", "star"];
  const cache = new Map();

  // 0 solid, 1 outline only, then hatches and dots
  function draw(i, c, s, fg) {
    c.strokeStyle = fg; c.fillStyle = fg; c.lineWidth = 1.5;
    const line = (x1, y1, x2, y2) => { c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2, y2); c.stroke(); };
    if (i === 2) { line(0, s, s, 0); line(-1, 1, 1, -1); line(s - 1, s + 1, s + 1, s - 1); }
    else if (i === 3) { c.fillRect(1, 1, 2.5, 2.5); c.fillRect(s / 2 + 1, s / 2 + 1, 2.5, 2.5); }
    else if (i === 4) { line(0, s / 2, s, s / 2); line(s / 2, 0, s / 2, s); }
    else if (i === 5) { line(0, s / 2, s, s / 2); }
    else if (i === 6) { line(0, 0, s, s); line(-1, s - 1, 1, s + 1); line(s - 1, -1, s + 1, 1); }
    else if (i === 7) { line(s / 2, 0, s / 2, s); }
  }

  function fill(i) {
    const k = i % 8, fg = ink(), bg = paper();
    if (k === 0) return fg;
    if (k === 1) return bg;
    const key = k + fg + bg;
    if (!cache.has(key)) {
      const s = 8, cv = document.createElement("canvas");
      cv.width = cv.height = s;
      const c = cv.getContext("2d");
      c.fillStyle = bg; c.fillRect(0, 0, s, s);
      draw(k, c, s, fg);
      cache.set(key, c.createPattern(cv, "repeat"));
    }
    return cache.get(key);
  }

  function applyDefaults() {
    if (!window.Chart) return;
    const d = Chart.defaults, fg = ink(), bg = paper();
    d.font.family = getComputedStyle(document.body).fontFamily || d.font.family;
    d.font.size = 12;
    d.color = fg; d.borderColor = fg;
    d.elements.bar.borderRadius = 0;
    d.elements.bar.borderColor = fg;
    d.elements.bar.borderWidth = 1;
    d.elements.line.borderWidth = 2;
    d.elements.point.backgroundColor = bg;
    d.elements.point.borderColor = fg;
    d.elements.point.borderWidth = 1.5;
    d.elements.arc.borderColor = fg;
    d.elements.arc.borderWidth = 1;
    Object.assign(d.plugins.tooltip, { backgroundColor: fg, titleColor: bg, bodyColor: bg, borderColor: bg, borderWidth: 1,
      cornerRadius: 0, displayColors: true, boxPadding: 4 });
    Object.assign(d.plugins.legend.labels, { boxWidth: 12, boxHeight: 12, color: fg });
  }

  // Axis styling: ink ticks, fine dashed grid
  const valueGrid = () => ({ color: ink(), lineWidth: 0.5 });
  const axisBorder = () => ({ color: ink(), dash: [2, 4] });

  return { ink, paper, fill, dash: (i) => DASHES[i % DASHES.length], point: (i) => POINTS[i % POINTS.length],
           applyDefaults, valueGrid, axisBorder };
})();
window.Mono.applyDefaults();
