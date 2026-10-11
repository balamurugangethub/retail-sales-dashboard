"use strict";
// Welcome (privacy/cookie choice), sign-in/register and account pages.
(function () {
  const $ = (id) => document.getElementById(id);
  const next = () => {
    const n = new URLSearchParams(location.search).get("next") || "/";
    return n.startsWith("/") && !n.startsWith("//") ? n : "/";
  };
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify(body || {}) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  }
  const page = location.pathname;

  if (page === "/welcome") {
    $("accept").onclick = async () => {
      await post("/api/consent", { accept: true });
      const me = await (await fetch("/api/auth/me")).json();
      location.href = me.auth_enabled && !me.user ? "/login?next=" + encodeURIComponent(next()) : next();
    };
    $("decline").onclick = async () => {
      await post("/api/consent", { accept: false });
      $("declined").hidden = false;
    };
  }

  if (page === "/login" || page === "/register") {
    const register = page === "/register";
    const form = $("auth-form");
    const q = location.search;
    document.querySelectorAll("[data-register]").forEach((e) => (e.hidden = !register));
    $("title").textContent = register ? "Create your account" : "Sign in";
    $("subtitle").textContent = register ? "Each account gets its own private copy of the data, starting with sample sales you can replace."
                                         : "Welcome back. Your dashboards and data are waiting.";
    $("submit").textContent = register ? "Create account" : "Sign in";
    form.password.autocomplete = register ? "new-password" : "current-password";
    $("switch").innerHTML = register ? 'Already have an account? <a href="/login' + q + '">Sign in</a>'
                                     : 'New here? <a href="/register' + q + '">Create an account</a>';
    fetch("/api/auth/me").then((r) => r.json()).then((me) => {
      if (!me.auth_enabled) {
        $("subtitle").textContent = "Accounts are switched off on this public demo. Run the app on your own computer to create one.";
        form.hidden = true; $("switch").innerHTML = '<a href="/">Explore the demo</a>';
      } else if (me.user) location.href = next();
    });
    form.onsubmit = async (ev) => {
      ev.preventDefault();
      $("form-error").textContent = "";
      const body = { email: form.email.value, password: form.password.value };
      if (register) Object.assign(body, { name: form.name.value, shop: form.shop.value,
                                          accept_policy: form.accept_policy.checked });
      $("submit").disabled = true;
      try {
        await post(register ? "/api/auth/register" : "/api/auth/login", body);
        location.href = next();
      } catch (e) {
        $("form-error").textContent = e.message;
      } finally { $("submit").disabled = false; }
    };
  }

  if (page === "/account") {
    fetch("/api/auth/me").then((r) => r.json()).then((me) => {
      if (!me.user) { location.href = "/login?next=/account"; return; }
      const u = me.user;
      const rows = [["Name", u.name], ["Shop", u.shop || "–"], ["Email", u.email], ["Member since", u.created.slice(0, 10)],
                    ["Privacy policy", "Accepted version " + u.policy_version + " on " + u.policy_accepted_at.slice(0, 10)]];
      rows.forEach(([k, v]) => {
        const dt = document.createElement("dt"); dt.textContent = k;
        const dd = document.createElement("dd"); dd.textContent = v;
        $("details").append(dt, dd);
      });
    });
    $("logout").onclick = async () => { await post("/api/auth/logout"); location.href = "/login"; };
    $("delete-form").onsubmit = async (ev) => {
      ev.preventDefault();
      if (!confirm("Delete your account and all of its data for good?")) return;
      try {
        await post("/api/account/delete", { password: ev.target.password.value });
        location.href = "/register";
      } catch (e) { $("form-error").textContent = e.message; }
    };
  }
})();
