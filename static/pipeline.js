"use strict";

const when = (t) => new Date(t * 1000).toLocaleString();

function stepBox(s, n) {
  return el("li", { class: "pl-step" }, [
    el("span", { class: "pl-num", text: String(n) }),
    el("strong", { text: s.step }),
    el("span", { class: "pl-rows", text: `${s.rows.toLocaleString()} rows` }),
    el("span", { class: "pl-detail", text: s.detail }),
    el("span", { class: "pl-ms", text: s.ms < 1 ? "<1 ms" : `${s.ms.toLocaleString()} ms` }),
  ]);
}

function checkBox(q, n) {
  const detail = q.issues ? `${q.issues} problem${q.issues === 1 ? "" : "s"} found` : "no problems found";
  return el("li", { class: "pl-step" }, [
    el("span", { class: "pl-num", text: String(n) }),
    el("strong", { text: "Quality check" }),
    el("span", { class: "pl-rows", text: `${q.score} / 100, ${q.grade}` }),
    el("span", { class: "pl-detail", text: detail }),
    el("a", { class: "pl-ms", href: "/explore", text: "Open in Explore" }),
  ]);
}

function card(d) {
  const steps = d.steps ? d.steps.map((s, i) => stepBox(s, i + 1)) : [];
  const note = d.steps ? `Opened ${when(d.created)}${d.filename ? ` from ${d.filename}` : ""}`
    : `Opened ${when(d.created)}, before step history was kept: ${d.rows.toLocaleString()} rows, ${d.columns} columns`;
  return el("section", { class: "card" }, [
    el("div", { class: "card-head" }, [el("h2", { text: d.name })]),
    el("p", { class: "hint", text: note }),
    el("ol", { class: "pl-flow" }, [...steps, checkBox(d.quality, steps.length + 1)]),
  ]);
}

(async function init() {
  let r;
  try { r = await api("/api/pipeline"); } catch (e) { return notify(e.message, true); }
  $("empty").hidden = r.datasets.length > 0;
  $("runs").replaceChildren(...r.datasets.map(card));
})();
