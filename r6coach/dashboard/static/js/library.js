/* R6 Coach dashboard: the setup library (the Setups tab). Browse every standard setup in the
   repository's r6coach/knowledge/data/setups/*.yaml by map, bomb site, side and operator, with the
   steps on the blueprint and the pictures with their marks. Read-only: pictures are added and
   marked on the Live tab's Setup card. Loaded after livemap.js and before app.js; uses their
   helpers ($, api, esc, sideName, KIND, utilityIcon, stepWords, marksHtml, stepVar, pct) at run time. */
"use strict";

const lib = {
  index: null, map: "", site: "", op: "", side: "", q: "",
  brief: null, step: 1, fi: null, wired: false,
};
const LIB_TEAM = "__team";

// ------------------------------------------------------------------ loading
// Called each time the tab opens, so a git pull or an edit to the YAML shows without a restart.
async function libLoad(route) {
  libWire();
  try { lib.index = await api("/api/library"); } catch (e) {
    $("lib-count").textContent = "Couldn't load the setups.";
    return;
  }
  const [map, id] = route || [];
  if (map && lib.index.maps.some((m) => m.id === map)) lib.map = map;
  libFilters();
  libList();
  const repo = lib.index.repo || {};
  $("lib-repo").innerHTML = repo.dir
    ? `<details class="lib-dev"><summary>Where these come from</summary>The repository's <a href="${esc(repo.dir)}" target="_blank" rel="noopener">setup files</a>; edits there show here when you reopen this tab.</details>`
    : "";
  const errs = Object.entries(lib.index.errors || {});
  if (errs.length) $("lib-repo").innerHTML += `<br><span class="lib-err">Not loaded: ${errs.map(([m, e]) => esc(`${m}: ${e}`)).join("; ")}</span>`;
  if (map && id) libOpen(map, id, false);
  else if (lib.brief && (!map || lib.brief.map === map)) libOpen(lib.brief.map, lib.brief.id, false);
}

function libWire() {
  if (lib.wired) return;
  lib.wired = true;
  $("lib-map").onchange = (e) => { lib.map = e.target.value; lib.site = ""; libFilters(); libList(); };
  $("lib-site").onchange = (e) => { lib.site = e.target.value; libFilters(); libList(); };
  $("lib-op").onchange = (e) => { lib.op = e.target.value; libList(); if (lib.brief) libRender(); };
  $("lib-q").oninput = (e) => { lib.q = e.target.value.trim().toLowerCase(); libList(); };
  document.querySelectorAll("[data-libside]").forEach((b) => {
    b.onclick = () => {
      lib.side = b.dataset.libside;
      document.querySelectorAll("[data-libside]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      libFilters(); libList();
    };
  });
  $("lib-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-libbrief]");
    if (b) libOpen(b.dataset.map, b.dataset.libbrief);
  });
  $("lib-brief").addEventListener("click", libBriefClick);
  let t = 0;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => lib.brief && libFixRooms(), 150); });
}

// ------------------------------------------------------------------ filters and list
function libMatches(b, skip = "") {
  if (lib.map && b.map !== lib.map) return false;
  if (skip !== "site" && lib.site && b.site !== lib.site) return false;
  if (lib.side && b.side !== lib.side) return false;
  if (skip !== "op" && lib.op) {
    if (lib.op === LIB_TEAM ? b.operators.length : !b.operators.includes(lib.op)) return false;
  }
  if (lib.q) {
    const hay = [b.title, b.site_name, b.operators.join(" "), b.kinds.map((k) => (KIND[k] || [k, k])[1]).join(" ")].join(" ").toLowerCase();
    if (!lib.q.split(/\s+/).every((w) => hay.includes(w))) return false;
  }
  return true;
}

function libFilters() {
  const idx = lib.index;
  const count = (m) => idx.briefs.filter((b) => b.map === m.id).length;
  $("lib-map").innerHTML = `<option value="">All maps</option>` +
    idx.maps.map((m) => `<option value="${esc(m.id)}">${esc(m.name)} (${count(m)})</option>`).join("");
  $("lib-map").value = lib.map;
  const m = idx.maps.find((x) => x.id === lib.map);
  $("lib-site").disabled = !m;
  $("lib-site").innerHTML = `<option value="">${m ? "Every site" : "Pick a map first"}</option>` +
    (m ? m.sites.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("") : "");
  if (!m || !m.sites.some((s) => s.id === lib.site)) lib.site = "";
  $("lib-site").value = lib.site;
  // Operators that have a setup under the other filters, with how many.
  const ops = {};
  let team = 0;
  idx.briefs.filter((b) => libMatches(b, "op")).forEach((b) => {
    if (!b.operators.length) team++;
    b.operators.forEach((o) => { ops[o] = (ops[o] || 0) + 1; });
  });
  const names = Object.keys(ops).sort((a, b) => a.localeCompare(b));
  if (lib.op && lib.op !== LIB_TEAM && !ops[lib.op]) lib.op = "";
  $("lib-op").innerHTML = `<option value="">Any operator</option>` +
    (team ? `<option value="${LIB_TEAM}">Whole team (${team})</option>` : "") +
    names.map((o) => `<option value="${esc(o)}">${esc(o)} (${ops[o]})</option>`).join("");
  $("lib-op").value = lib.op;
}

function libList() {
  const idx = lib.index;
  const rows = idx.briefs.filter((b) => libMatches(b));
  const shots = idx.briefs.reduce((n, b) => n + b.shots, 0);
  $("lib-count").textContent = `${rows.length} of ${idx.briefs.length} setups on ${idx.maps.length} maps · ${shots} pictures`;
  const mapName = Object.fromEntries(idx.maps.map((m) => [m.id, m.name]));
  const groups = [];
  rows.forEach((b) => {
    const key = `${b.map}|${b.site}`;
    let g = groups.find((x) => x.key === key);
    if (!g) groups.push(g = { key, map: b.map, site: b.site_name, rows: [] });
    g.rows.push(b);
  });
  const cur = lib.brief ? `${lib.brief.map}|${lib.brief.id}` : "";
  $("lib-list").innerHTML = groups.map((g) => `<div class="lib-group">
      <h3 class="lib-group__name">${lib.map ? "" : esc(mapName[g.map] || g.map) + " · "}${esc(g.site)}</h3>
      <ul class="setups__list">${g.rows.map((b) => `<li><button type="button" class="setups__item" data-map="${esc(b.map)}" data-libbrief="${esc(b.id)}" aria-pressed="${cur === `${b.map}|${b.id}`}">
        <span class="setups__title">${esc(b.title)}</span>
        <span class="setups__meta">${esc(sideName(b.side))} · ${esc(b.operators.length ? b.operators.join(", ") : "Whole team")} · ${b.steps} steps${b.shots ? ` · ${b.shots} pictures` : ""}</span>
      </button></li>`).join("")}</ul></div>`).join("")
    || '<p class="hint-sm">No setups match. Try another operator or clear the search.</p>';
}

// ------------------------------------------------------------------ one brief
async function libOpen(map, id, scroll = true) {
  let b;
  try { b = await api(`/api/library/${encodeURIComponent(map)}/${encodeURIComponent(id)}`); } catch (e) {
    $("lib-brief").innerHTML = `<h2 class="rc-card__title" id="lib-brief-title">Setup not found</h2><p class="hint-sm">It may have been renamed in the repository.</p>`;
    return;
  }
  lib.brief = b;
  lib.step = 1;
  lib.fi = libFloorFor(b.placements[0]);
  const hash = `#setups/${map}/${id}`;
  if (location.hash !== hash) history.replaceState(null, "", hash);
  libRender();
  libList();
  if (scroll && matchMedia("(max-width: 800px)").matches) $("lib-brief").scrollIntoView({ block: "start" });
}

function libFloorFor(p) {
  const b = lib.brief;
  if (p && p.pos && b.floors.some((f) => f.fi === p.pos.fi)) return p.pos.fi;
  const any = b.placements.find((x) => x.pos && b.floors.some((f) => f.fi === x.pos.fi));
  return any ? any.pos.fi : b.floors.length ? b.floors[0].fi : null;
}

function libRender() {
  const b = lib.brief;
  const src = b.source;
  const ops = b.operators.length ? b.operators.join(", ") : "Whole team";
  const shots = b.placements.filter((p) => p.shot).length;
  $("lib-brief").innerHTML = `
    <h2 class="rc-card__title lib-brief__title" id="lib-brief-title">${esc(b.title)}</h2>
    <p class="lib-chips">
      <span class="lib-chip">${esc(b.map_name)}</span>
      <span class="lib-chip">${esc(b.site_name)}</span>
      <span class="lib-chip lib-chip--${b.side === "attack" ? "att" : "def"}">${esc(sideName(b.side))}</span>
      <span class="lib-chip">${esc(ops)}</span>
    </p>
    ${b.call ? `<p class="lib-call">${esc(b.call)}</p>` : ""}
    ${libJobHtml(b)}
    <p class="setups__text lib-text">${esc(b.text)}</p>
    ${b.counters ? `<p class="lib-counters"><b>If they stop it:</b> ${esc(b.counters)}</p>` : ""}
    <div class="lib-map" id="lib-map-box">${libMapHtml()}</div>
    <h3 class="lib-h3">Steps <span class="hint-sm">${b.placements.length} steps · ${shots} with a picture</span></h3>
    <ol class="lib-steps">${b.placements.map(libStepHtml).join("")}</ol>
    ${src ? `<details class="lib-dev lib-src"><summary>Edit this setup</summary><a href="${esc(src.url)}" target="_blank" rel="noopener"><code>${esc(src.file)}</code> line ${src.line}</a> in the repository. Pictures are added and marked on the Live tab's Setup card.</details>` : ""}`;
  libDrawMap();
}

// With an operator picked, the steps that are theirs, first: "your job" in one line.
function libJobHtml(b) {
  if (!lib.op || lib.op === LIB_TEAM || !b.operators.includes(lib.op)) return "";
  const mine = b.placements.filter((p) => p.by === lib.op);
  if (!mine.length) return `<p class="lib-job">As ${esc(lib.op)}: no step is assigned to you here; play the plan below.</p>`;
  return `<p class="lib-job">As ${esc(lib.op)}: ${mine.map((p) => `<button type="button" class="linkbtn" data-libstep="${p.n}">step ${p.n}</button>`).join(", ")}.</p>`;
}

// The blueprint for one floor with every step on it as its numbered utility icon.
function libMapHtml() {
  const b = lib.brief;
  if (!b.floors.length) return '<p class="hint-sm">No blueprint for this map on this PC (tools/fetch_blueprints.py gets them).</p>';
  const f = b.floors.find((x) => x.fi === lib.fi) || b.floors[0];
  const onFloor = (fi) => b.placements.filter((p) => p.pos && p.pos.fi === fi).length;
  const count = {};
  b.floors.forEach((x) => { count[x.floor] = (count[x.floor] || 0) + 1; });
  const seen = {};
  const floorBtns = b.floors.map((x) => {
    seen[x.floor] = (seen[x.floor] || 0) + 1;
    const label = count[x.floor] > 1 ? `${x.floor} (${seen[x.floor]})` : x.floor;
    const n = onFloor(x.fi);
    return `<button type="button" class="rc-btn rc-btn--quiet livemap__floor" data-libfi="${x.fi}" aria-pressed="${x.fi === f.fi}">${esc(label)}${n ? ` <span class="lib-floor-n">${n}</span>` : ""}</button>`;
  }).join("");
  const pins = b.placements.filter((p) => p.pos && p.pos.fi === f.fi).map((p) =>
    `<button type="button" class="lib-pin${p.n === lib.step ? " is-hot" : ""}" style="left:${pct(p.pos.x)};top:${pct(p.pos.y)}" data-libstep="${p.n}" aria-label="Step ${p.n}: ${esc(stepWords(p))}">${utilityIcon(p)}<span class="lib-pin__n">${p.n}</span></button>`).join("");
  // Room names first, under the pins; the rooms a step names stand out.
  const named = new Set(b.placements.map((p) => p.where));
  const rooms = Object.entries(f.rooms || {}).map(([name, [x, y]]) =>
    `<span class="lib-room${named.has(name) || [...named].some((w) => w.includes(name)) ? " is-named" : ""}" style="left:${pct(x)};top:${pct(y)}">${esc(name)}</span>`).join("");
  const off = b.placements.filter((p) => !p.pos).map((p) => p.n);
  const edge = (e, arrow) => b.sides && b.sides[e] ? `<span class="lib-edge lib-edge--${e}">${arrow} ${esc(b.sides[e].replace(/\s*\([^)]*\)/g, ""))}</span>` : "";
  return `<div class="livemap__floors lib-floors" role="group" aria-label="Floor">${floorBtns}</div>
    <div class="lib-stage">
      <div class="lib-world"><img src="${esc(f.url)}" alt="${esc(b.map_name)} ${esc(f.floor)} blueprint" decoding="async" draggable="false">${rooms}${pins}</div>
      ${edge("top", "↑")}${edge("bottom", "↓")}${edge("left", "←")}${edge("right", "→")}
    </div>
    ${off.length ? `<p class="hint-sm">Not on the map yet: step ${off.join(", ")}.</p>` : ""}`;
}

function libStepHtml(p) {
  const word = KIND[p.kind] ? KIND[p.kind][1] : p.kind;
  const mine = lib.op && p.by === lib.op ? " is-mine" : "";
  return `<li class="lib-step${p.n === lib.step ? " is-hot" : ""}${mine}" id="lib-step-${p.n}" style="${stepVar(p.n)}">
    <button type="button" class="lib-step__head" data-libstep="${p.n}" aria-pressed="${p.n === lib.step}">
      ${utilityIcon(p, "uicon--big")}
      <span class="lib-step__words">
        <span class="lib-step__count">Step ${p.n}${p.by ? ` · ${esc(p.by)}` : ""} · ${esc(word)} · ${esc(p.floor)}${p.pos ? "" : " · not on the map"}</span>
        <span class="lib-step__what">${esc(stepWords(p))}</span>
        ${p.note ? `<span class="lib-step__note">${esc(p.note)}</span>` : ""}
      </span>
    </button>
    ${p.shot
      ? `<button type="button" class="shotframe lib-shot" data-libview="${p.n}" aria-label="Open the picture for step ${p.n}">
          <img src="${esc(p.shot)}" alt="" loading="lazy">${libMarks(p)}</button>`
      : ""}
  </li>`;
}

// The marks with their labels written next to them, so the picture reads on its own.
function libMarks(p) {
  const labels = (p.marks || []).filter((m) => m.label).map((m) => {
    let x, y, cls = "";
    if (m.shape === "box") { x = Math.min(m.x, m.x2); y = Math.min(m.y, m.y2); cls = y < 9 ? " lib-label--in" : " lib-label--above"; }
    else if (m.shape === "arrow") { x = m.x2; y = m.y2; cls = " lib-label--below"; }
    else { x = m.x; y = m.y; cls = " lib-label--below"; }
    if (x > 62) { x = m.shape === "box" ? Math.max(m.x, m.x2) : x; cls += " lib-label--right"; }
    if (y > 88) cls = cls.replace("lib-label--below", "lib-label--above");
    return `<span class="lib-label${cls}" style="left:${x}%;top:${y}%">${esc(m.label)}</span>`;
  }).join("");
  return marksHtml(p.marks, p.n) + `<span class="lib-labels" aria-hidden="true">${labels}</span>`;
}

// Room names must not hide under a pin or under each other: a name a pin covers moves just below
// it; then, named rooms first, any name that would overlap one already shown is left out.
function libFixRooms() {
  const box = $("lib-map-box");
  if (!box) return;
  // The icons, not the buttons: the selected icon is scaled up and spills past its button.
  const pins = [...box.querySelectorAll(".lib-pin .uicon, .lib-pin__n")].map((p) => p.getBoundingClientRect());
  const labels = [...box.querySelectorAll(".lib-room")];
  labels.forEach((l) => { l.style.marginTop = ""; l.classList.remove("is-hidden"); });
  labels.forEach((l) => {
    const r = l.getBoundingClientRect();
    const hit = pins.filter((p) => r.left < p.right && r.right > p.left && r.top < p.bottom && r.bottom > p.top);
    if (hit.length) l.style.marginTop = `${Math.max(...hit.map((p) => p.bottom)) - r.top + 2}px`;
  });
  const shown = [];
  labels.sort((a, b) => b.classList.contains("is-named") - a.classList.contains("is-named")).forEach((l) => {
    const r = l.getBoundingClientRect();
    const clash = shown.some((s) => r.left < s.right && r.right > s.left && r.top < s.bottom && r.bottom > s.top)
      || (!l.classList.contains("is-named") && pins.some((p) => r.left < p.right && r.right > p.left && r.top < p.bottom && r.bottom > p.top));
    if (clash) l.classList.add("is-hidden"); else shown.push(r);
  });
}

function libDrawMap() {
  $("lib-map-box").innerHTML = libMapHtml();
  const img = $("lib-map-box").querySelector("img");
  if (img && !img.complete) img.addEventListener("load", libFixRooms, { once: true });
  requestAnimationFrame(libFixRooms);
}

function libSelect(n, fromMap) {
  const p = lib.brief.placements.find((x) => x.n === n);
  if (!p) return;
  lib.step = n;
  if (p.pos) lib.fi = p.pos.fi;
  libDrawMap();
  document.querySelectorAll(".lib-step").forEach((li) => {
    const on = li.id === `lib-step-${n}`;
    li.classList.toggle("is-hot", on);
    li.querySelector(".lib-step__head").setAttribute("aria-pressed", String(on));
  });
  if (fromMap) $(`lib-step-${n}`).scrollIntoView({ block: "nearest", behavior: REDUCED.matches ? "auto" : "smooth" });
}

function libBriefClick(e) {
  const fl = e.target.closest("[data-libfi]");
  if (fl) { lib.fi = +fl.dataset.libfi; libDrawMap(); return; }
  const v = e.target.closest("[data-libview]");
  if (v) { libView(+v.dataset.libview); return; }
  const s = e.target.closest("[data-libstep]");
  if (s) libSelect(+s.dataset.libstep, s.classList.contains("lib-pin"));
}

function libView(n) {
  const p = lib.brief.placements.find((x) => x.n === n);
  if (!p || !p.shot) return;
  libSelect(n, false);
  $("lib-view-title").textContent = `Step ${n}: ${stepWords(p)}`;
  $("lib-view-note").textContent = p.note || "";
  const frame = $("lib-view-frame");
  frame.setAttribute("style", stepVar(n));
  frame.innerHTML = `<img src="${esc(p.shot)}" alt="">${libMarks(p)}`;
  const dlg = $("lib-view");
  dlg.querySelector("[data-close]").onclick = () => dlg.close();
  dlg.showModal();
}
