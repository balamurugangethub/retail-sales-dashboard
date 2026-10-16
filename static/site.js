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
    if (me.user && me.user.demo) {
      const bar = document.createElement("div");
      bar.className = "demo-bar"; bar.setAttribute("role", "status");
      bar.append("You are using a demo shop with sample data. It is deleted after 24 hours. ");
      const a = document.createElement("a");
      a.href = "/register"; a.textContent = "Create your own account";
      a.onclick = async (ev) => {                      // sign out of the demo first, or the register page sends you back
        ev.preventDefault();
        await realFetch("/api/auth/logout", { method: "POST" });
        location.href = "/register";
      };
      bar.append(a);
      document.body.prepend(bar);
    }
    link("/privacy", "Privacy");
    if (me.user) link("/account", me.user.shop || me.user.name, "account nav-right");
    if (window.ChartStyle && ["/", "/builder"].includes(location.pathname)) nav.append(ChartStyle.button());
    const find = document.createElement("button");
    find.type = "button"; find.className = "theme-btn"; find.append("Search ");
    const kbd = document.createElement("kbd"); kbd.textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘K" : "Ctrl K"; find.append(kbd);
    find.onclick = () => window.Palette && Palette.open(); nav.append(find);
    if (window.Theme) nav.append(Theme.button());
    const here = nav.querySelector("a.on");                // on a phone the row scrolls: show the current page
    if (here && nav.scrollWidth > nav.clientWidth) nav.scrollLeft = Math.max(0, here.offsetLeft - 16);
  }).catch(() => {});
})();

// Monochrome chart styling shared by the overview and the builder. Every series is drawn
// in ink on paper; series are told apart by fill pattern, line dash and point shape.
window.Mono = (function () {
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const ink = () => css("--ink") || "#000";
  const paper = () => css("--surface") || css("--paper") || "#fff";   // charts sit on cards
  const chart = () => css("--chart-color") || ink();
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
    const k = i % 8, fg = chart(), bg = paper();
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
    const d = Chart.defaults, fg = chart(), bg = paper();
    d.font.family = css("--chart-font") || getComputedStyle(document.body).fontFamily || d.font.family;
    d.font.size = 12;
    d.color = muted(); d.borderColor = css("--chart-grid") || fg;
    d.elements.bar.borderRadius = 5;
    d.elements.bar.borderSkipped = false;
    Object.assign(d.animation, { duration: 650, easing: "easeOutQuart" });
    d.transitions.active.animation.duration = 200;
    d.elements.bar.borderColor = fg;
    d.elements.bar.borderWidth = 1;
    d.elements.line.borderWidth = 2;
    d.elements.point.backgroundColor = bg;
    d.elements.point.borderColor = fg;
    d.elements.point.borderWidth = 1.5;
    d.elements.arc.borderColor = fg;
    d.elements.arc.borderWidth = 1;
    Object.assign(d.plugins.tooltip, { backgroundColor: bg, titleColor: css("--text") || ink(), bodyColor: css("--text") || ink(),
      borderColor: css("--line") || fg, borderWidth: 1, cornerRadius: 10, padding: 10, displayColors: true, boxPadding: 4, caretSize: 6 });
    Object.assign(d.plugins.legend.labels, { boxWidth: 12, boxHeight: 12, color: text() });
  }

  // Axis styling: dashed hairline grid
  const valueGrid = () => ({ color: css("--chart-grid") || css("--line") || ink(), lineWidth: 1 });
  const axisBorder = () => ({ color: css("--chart-grid") || css("--line") || ink(), dash: [2, 4] });

  const muted = () => css("--chart-text") || css("--muted") || ink();
  const text = () => css("--chart-text") || css("--text") || ink();
  return { ink, paper, muted, text, chart, fill, dash: (i) => DASHES[i % DASHES.length], point: (i) => POINTS[i % POINTS.length],
           applyDefaults, valueGrid, axisBorder };
})();
window.Mono.applyDefaults();
window.addEventListener("themechange", () => window.Mono.applyDefaults());

// ---- motion and quick navigation ---------------------------------------------------
(function () {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Numbers in KPI tiles count up from zero when they first appear or change
  const seen = new Map();
  function countUp(node) {
    const text = node.textContent, m = text.match(/-?[\d,]+(\.\d+)?/);
    const label = node.parentElement.querySelector(".label"), key = label ? label.textContent : text;
    if (reduce || !m || seen.get(key) === text) return;       // same figure as before (a theme change redraws the tiles): leave it
    seen.set(key, text);
    const target = parseFloat(m[0].replace(/,/g, "")), decimals = m[1] ? m[1].length - 1 : 0, useCommas = m[0].includes(",") || Math.abs(target) >= 1000;
    const pre = text.slice(0, m.index), post = text.slice(m.index + m[0].length), t0 = performance.now(), ms = 800;
    const show = (v) => (node.textContent = pre + v.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals, useGrouping: useCommas }) + post);
    const tick = (now) => {
      const p = Math.min(1, (now - t0) / ms), eased = 1 - Math.pow(1 - p, 4);
      if (p < 1) { show(target * eased); requestAnimationFrame(tick); } else node.textContent = text;
    };
    show(0); requestAnimationFrame(tick);
  }
  new MutationObserver((list) => {
    for (const rec of list) rec.addedNodes.forEach((n) => {
      if (n.nodeType !== 1) return;
      if (n.matches(".kpi .value, .kpi-cell .value")) countUp(n);
      else n.querySelectorAll && n.querySelectorAll(".kpi .value, .kpi-cell .value").forEach(countUp);
    });
  }).observe(document.documentElement, { childList: true, subtree: true });

  // Buttons ripple from the pointer
  document.addEventListener("pointerdown", (e) => {
    const b = e.target.closest && e.target.closest(".btn");
    if (!b || reduce || b.disabled) return;
    const r = b.getBoundingClientRect(), size = Math.max(r.width, r.height), s = document.createElement("span");
    s.className = "ripple"; s.style.width = s.style.height = size + "px";
    s.style.left = e.clientX - r.left - size / 2 + "px"; s.style.top = e.clientY - r.top - size / 2 + "px";
    b.append(s); setTimeout(() => s.remove(), 650);
  });

  // Command palette: Ctrl/Cmd + K, or the Search button in the header
  let open = null;
  function items() {
    const pages = [...document.querySelectorAll(".nav a")].map((a) => ({ kind: "Go to", label: a.textContent.trim(), run: () => (location.href = a.href) }));
    const themes = window.Theme ? Theme.list().map((t) => ({ kind: "Theme", label: `${t.name} ${t.mode.toLowerCase()}`, dot: t.accent, run: () => Theme.apply(t.id) })) : [];
    return [...pages, ...themes];
  }
  function close() { if (open) { open.remove(); open = null; } }
  function openPalette() {
    if (open) return close();
    const all = items(); let shown = all, at = 0;
    const back = document.createElement("div"); back.className = "cmdk-back";
    const box = document.createElement("div"); box.className = "cmdk"; box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "Search pages and themes");
    const input = document.createElement("input"); input.placeholder = "Jump to a page or switch theme…"; input.setAttribute("aria-label", "Search");
    const list = document.createElement("ul"), foot = document.createElement("div");
    foot.className = "foot"; foot.textContent = "↑ ↓ to move   ↵ to open   esc to close";
    const draw = () => {
      list.replaceChildren(...shown.map((it, i) => {
        const li = document.createElement("li"); li.className = i === at ? "on" : "";
        const name = document.createElement("span"); name.textContent = it.label;
        const kind = document.createElement("span"); kind.className = "kind";
        if (it.dot) { const d = document.createElement("i"); d.style.background = it.dot; kind.append(d); }
        kind.append(it.kind); li.append(name, kind);
        li.onmousemove = () => { if (at !== i) { at = i; draw(); } };
        li.onclick = () => { close(); it.run(); };
        return li;
      }));
      if (!shown.length) { const n = document.createElement("li"); n.className = "none"; n.textContent = "Nothing matches."; list.replaceChildren(n); }
      const cur = list.querySelector(".on"); if (cur) cur.scrollIntoView({ block: "nearest" });
    };
    input.oninput = () => { const q = input.value.trim().toLowerCase(); shown = all.filter((it) => (it.kind + " " + it.label).toLowerCase().includes(q)); at = 0; draw(); };
    input.onkeydown = (e) => {
      if (e.key === "ArrowDown") { at = Math.min(shown.length - 1, at + 1); draw(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { at = Math.max(0, at - 1); draw(); e.preventDefault(); }
      else if (e.key === "Enter" && shown[at]) { const it = shown[at]; close(); it.run(); }
      else if (e.key === "Escape") close();
    };
    back.onclick = (e) => { if (e.target === back) close(); };
    box.append(input, list, foot); back.append(box); document.body.append(back); open = back; draw(); input.focus();
  }
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
    else if (e.key === "Escape") close();
  });
  window.Palette = { open: openPalette };
})();
