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
