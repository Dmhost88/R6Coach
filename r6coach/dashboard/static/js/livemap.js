/* R6 Coach dashboard: the live map and the setup briefs. Loaded before app.js; uses its helpers
   ($, api, esc, iconCache) only at run time. */
"use strict";

// ------------------------------------------------------------------ live map state
// floors: the layout's images that exist on disk, each with its index fi in the layout.
// view: zoom z and pan tx/ty in stage pixels. focus: rooms the last coach line named.
const mapView = {
  map: null, name: "", floors: [], fi: null, you: null, sides: {}, features: [], sites: [],
  focus: [], focusUntil: 0, pinned: false,
  show: { labels: true, sites: true, features: true, utility: true },
  view: { z: 1, tx: 0, ty: 0 },
  brief: null, hot: null,
};
const FOCUS_MS = 25000;
const Z_MAX = 6;

// Utility kinds: a short code on the icon, the words for it, and whose it is.
const KIND = {
  reinforce: ["R", "Reinforce"], hatch: ["H", "Reinforce hatch"], open: ["O", "Open (hole or rotate)"],
  rotate: ["Rt", "Rotate"], mira: ["M", "Mira window"], bandit: ["Bd", "Bandit battery"],
  kaid: ["Kd", "Kaid claw"], castle: ["Ca", "Castle barricade"], deployable: ["Sh", "Deployable shield"],
  trap: ["Tr", "Trap"], camera: ["Cm", "Camera"], anchor: ["A", "Stand here"],
  thermite: ["Th", "Thermite charge"], hibana: ["Hb", "Hibana pellets"], maverick: ["Mv", "Maverick torch"],
  ace: ["Ac", "Ace charge"], breach: ["Br", "Breach"], entry: ["En", "Entry"], drone: ["Dr", "Drone spot"],
  plant: ["P", "Plant spot"],
};
const FEATURE_SVG = {
  stairs: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 14h4v-4h4V6h4V2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>',
  hatch: '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="2.5" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M2.5 2.5l11 11M13.5 2.5l-11 11" stroke="currentColor" stroke-width="1.2"/></svg>',
  window: '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="3" width="12" height="10" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M8 3v10M2 8h12" stroke="currentColor" stroke-width="1.2"/></svg>',
  door: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 14V2h8v12M3 14h11" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="9" cy="8.5" r="1" fill="currentColor"/></svg>',
};
const FEATURE_WORD = { stairs: "Stairs", hatch: "Hatch", window: "Window", door: "Door" };

const stage = () => $("map-stage");
const world = () => $("map-world");
const curFloor = () => mapView.floors.find((f) => f.fi === mapView.fi);
const focusLive = () => Date.now() < mapView.focusUntil;

// ------------------------------------------------------------------ loading
async function loadLayout() {
  let lay;
  try { lay = await api("/api/layout"); } catch (e) { return; }
  const changed = lay.map !== mapView.map;
  mapView.map = lay.map;
  mapView.name = lay.name || lay.map || "Map";
  mapView.floors = (lay.floors || []).map((f, fi) => ({ ...f, fi })).filter((f) => f.url);
  mapView.you = lay.you;
  mapView.sides = lay.sides || {};
  mapView.features = lay.features || [];
  mapView.sites = lay.sites || [];
  $("livemap").hidden = !mapView.floors.length;
  if (!mapView.floors.length) return;
  if (changed || !curFloor()) {
    const you = mapView.you && mapView.floors.find((f) => f.fi === mapView.you.fi);
    mapView.fi = (you || mapView.floors.find((f) => f.floor === "1F") || mapView.floors[0]).fi;
    resetView(false);
  }
  $("map-name").textContent = mapView.name;
  const count = {};
  mapView.floors.forEach((f) => { count[f.floor] = (count[f.floor] || 0) + 1; });
  const seen = {};
  $("map-floors").innerHTML = mapView.floors.map((f) => {
    seen[f.floor] = (seen[f.floor] || 0) + 1;
    const label = count[f.floor] > 1 ? `${f.floor} (${seen[f.floor]})` : f.floor;
    return `<button type="button" class="rc-btn rc-btn--quiet livemap__floor" data-fi="${f.fi}">${esc(label)}</button>`;
  }).join("");
  ["top", "bottom", "left", "right"].forEach((e) => {
    const el = $("map-edge-" + e);
    const name = mapView.sides[e];
    el.hidden = !name;
    // Short on the map ("Front Yard and Campfire"); the full text is the tooltip.
    const short = (name || "").replace(/\s*\([^)]*\)/g, "").replace(/\s*\/\s*/g, " / ");
    el.textContent = name ? `${{ top: "↑", bottom: "↓", left: "←", right: "→" }[e]} ${short}` : "";
    el.title = name ? `Outside, beyond this edge: ${name}` : "";
  });
  drawMap();
}

function mapReset() {
  mapView.floors = []; mapView.focus = []; mapView.map = null; mapView.brief = null;
  setups.data = null;
}

function showFloor(fi, pinned = false) {
  if (!mapView.floors.some((f) => f.fi === fi)) return;
  if (mapView.fi !== fi) { mapView.fi = fi; }
  if (pinned) mapView.pinned = true;
  drawMap();
}

// ------------------------------------------------------------------ zoom and pan
function stageSize() { const r = stage().getBoundingClientRect(); return [r.width, r.height]; }

// The view is kept in percent of the stage (tx, ty) so it survives resizes; px helpers convert.
function applyView(animate) {
  const w = world();
  const { z, tx, ty } = mapView.view;
  w.classList.toggle("is-animating", !!animate && !REDUCED.matches);
  w.style.transform = `translate(${tx}%, ${ty}%) scale(${z})`;
  w.style.setProperty("--z", z);
  w.classList.toggle("is-zoomed", z >= 1.8);
  $("map-zlevel").textContent = z > 1.01 ? `${z.toFixed(1)}×` : "";
  clearLabelsSoon(animate && !REDUCED.matches ? 440 : 0);
}

// Setup pins keep their size on screen while the map zooms, so at some zooms one sits on a room
// name ("St En indow"). After each draw or zoom, move any name that a pin covers to just below it.
let clearLabelsTimer = 0;
function clearLabelsSoon(ms) {
  clearTimeout(clearLabelsTimer);
  clearLabelsTimer = setTimeout(() => requestAnimationFrame(clearLabels), ms);
}
function clearLabels() {
  const w = world();
  const el = $("map-pins");
  if (!w || !el) return;
  const labels = [...el.querySelectorAll(".livemap__pin--room, .livemap__pin--area, .livemap__pin--feature")];
  labels.forEach((l) => { l.style.marginTop = ""; l.classList.remove("is-crowded"); });
  const pins = [...el.querySelectorAll(".livemap__pin--util .ubtn")].map((b) => b.getBoundingClientRect());
  if (!pins.length) return;
  const over = (r, s) => r.left < s.right && r.right > s.left && r.top < s.bottom && r.bottom > s.top;
  const z = w.getBoundingClientRect().width / (w.offsetWidth || 1) || 1;
  labels.forEach((l) => {
    const r = l.getBoundingClientRect();
    const hit = pins.filter((p) => over(r, p));
    if (!hit.length) return;
    const dy = Math.max(...hit.map((p) => p.bottom)) - r.top + 2;
    l.style.marginTop = `${dy / z}px`;
  });
  // A moved name can land on another. Coach-named rooms and stairs/hatch icons always stay; a plain
  // room or area name that still overlaps a pin or a name already shown is left out while the setup shows.
  const shown = [...el.querySelectorAll(".livemap__pin--room.is-focus, .livemap__pin--feature")].map((l) => l.getBoundingClientRect());
  labels.filter((l) => !l.classList.contains("is-focus") && !l.classList.contains("livemap__pin--feature")).forEach((l) => {
    const r = l.getBoundingClientRect();
    if (pins.some((p) => over(r, p)) || shown.some((s) => over(r, s))) l.classList.add("is-crowded");
    else shown.push(r);
  });
}

function clampView() {
  const v = mapView.view;
  v.z = Math.min(Z_MAX, Math.max(1, v.z));
  v.tx = Math.min(0, Math.max(100 - 100 * v.z, v.tx));
  v.ty = Math.min(0, Math.max(100 - 100 * v.z, v.ty));
}

// Zoom by factor keeping the stage point (px, py), in pixels, still.
function zoomAt(factor, px, py, animate = false) {
  const [W, H] = stageSize();
  if (!W || !H) return;
  const v = mapView.view;
  const z = Math.min(Z_MAX, Math.max(1, v.z * factor));
  const qx = px / W * 100, qy = py / H * 100;
  v.tx = qx - (qx - v.tx) * (z / v.z);
  v.ty = qy - (qy - v.ty) * (z / v.z);
  v.z = z;
  clampView();
  applyView(animate);
}

function panBy(dxPx, dyPx, animate = false) {
  const [W, H] = stageSize();
  if (!W || !H) return;
  mapView.view.tx += dxPx / W * 100;
  mapView.view.ty += dyPx / H * 100;
  clampView();
  applyView(animate);
}

function resetView(animate = true) {
  mapView.view = { z: 1, tx: 0, ty: 0 };
  applyView(animate);
}

// Zoom so the box [x0, y0, x1, y1] (percent of the image) fills the stage with some room around it.
function fitBox(b, maxZ = 3.5) {
  const bw = Math.max(8, b[2] - b[0]) * 1.6;
  const bh = Math.max(8, b[3] - b[1]) * 1.6;
  const z = Math.min(maxZ, Math.max(1, Math.min(100 / bw, 100 / bh)));
  const cx = (b[0] + b[2]) / 2, cy = (b[1] + b[3]) / 2;
  mapView.view = { z, tx: 50 - cx * z, ty: 50 - cy * z };
  clampView();
  applyView(true);
}

function boundsOf(points) {
  if (!points.length) return null;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

function initPanZoom() {
  const st = stage();
  const pts = new Map();
  let last = null, moved = 0, pinch = null;
  st.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = st.getBoundingClientRect();
    zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  st.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 && e.pointerType === "mouse") return;
    pts.set(e.pointerId, [e.clientX, e.clientY]);
    if (pts.size === 1) { last = [e.clientX, e.clientY]; moved = 0; }
    if (pts.size === 2) {
      const [a, b] = [...pts.values()];
      pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), z: mapView.view.z };
    }
    world().classList.remove("is-animating");
  });
  st.addEventListener("pointermove", (e) => {
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, [e.clientX, e.clientY]);
    const r = st.getBoundingClientRect();
    if (pts.size === 2 && pinch) {
      const [a, b] = [...pts.values()];
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      const mid = [(a[0] + b[0]) / 2 - r.left, (a[1] + b[1]) / 2 - r.top];
      zoomAt((pinch.z * d / pinch.d) / mapView.view.z, mid[0], mid[1]);
      moved = 99;
      return;
    }
    if (pts.size !== 1 || !last) return;
    const dx = e.clientX - last[0], dy = e.clientY - last[1];
    last = [e.clientX, e.clientY];
    moved += Math.abs(dx) + Math.abs(dy);
    if (moved > 4) {
      if (!st.hasPointerCapture(e.pointerId)) st.setPointerCapture(e.pointerId);
      st.classList.add("is-dragging");
      panBy(dx, dy);
    }
  });
  const up = (e) => {
    pts.delete(e.pointerId);
    if (pts.size < 2) pinch = null;
    if (!pts.size) { last = null; st.classList.remove("is-dragging"); }
  };
  st.addEventListener("pointerup", up);
  st.addEventListener("pointercancel", up);
  // A drag that started on a pin must not also click it.
  st.addEventListener("click", (e) => { if (moved > 4) { e.stopPropagation(); e.preventDefault(); moved = 0; } }, true);
  st.addEventListener("dblclick", (e) => {
    const r = st.getBoundingClientRect();
    zoomAt(2, e.clientX - r.left, e.clientY - r.top, true);
  });
  st.addEventListener("keydown", (e) => {
    const [W, H] = stageSize();
    const step = 60;
    const keys = {
      "+": () => zoomAt(1.4, W / 2, H / 2, true), "=": () => zoomAt(1.4, W / 2, H / 2, true),
      "-": () => zoomAt(1 / 1.4, W / 2, H / 2, true), "0": () => resetView(),
      ArrowLeft: () => panBy(step, 0, true), ArrowRight: () => panBy(-step, 0, true),
      ArrowUp: () => panBy(0, step, true), ArrowDown: () => panBy(0, -step, true),
    };
    if (!keys[e.key] || e.target !== st) return;
    e.preventDefault();
    keys[e.key]();
  });
  $("map-zoom-in").onclick = () => { const [W, H] = stageSize(); zoomAt(1.5, W / 2, H / 2, true); };
  $("map-zoom-out").onclick = () => { const [W, H] = stageSize(); zoomAt(1 / 1.5, W / 2, H / 2, true); };
  $("map-reset").onclick = () => { mapView.pinned = false; mapView.focusUntil = 0; mapView.hot = null; resetView(); followYou(); drawMap(); };
  new ResizeObserver(() => { clampView(); applyView(false); }).observe(st);
}

// ------------------------------------------------------------------ drawing
const pct = (n) => `${Number(n).toFixed(2)}%`;
function pin(x, y, cls, html, attrs = "") {
  return `<div class="livemap__pin ${cls}" style="left:${pct(x)};top:${pct(y)}" ${attrs}>${html}</div>`;
}
function centre(b) { return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]; }

// Each step has one colour, used on its map icon, its row and its picture frame, so they read as one.
const stepVar = (n) => `--step: var(--step-${((n - 1) % 6) + 1})`;
function utilityIcon(p, extra = "") {
  const [code, word] = KIND[p.kind] || ["?", p.kind];
  return `<span class="uicon uicon--${p.team === "attack" ? "att" : p.team === "defense" ? "def" : "any"} ${extra}" style="${stepVar(p.n || 1)}" title="${esc(word)}">${esc(code)}</span>`;
}

// ------------------------------------------------------------------ glance view
// In a match the page turns into a glance view: the coach's line big, the current step and its
// picture, the map without its chrome. "auto" follows the screen; the button cycles auto, on, off.
const IN_MATCH = new Set(["map_ban", "operator_ban", "operator_select", "loadout", "prep_phase", "action_phase", "spectating", "round_end"]);
let glancePref = "auto";
try { glancePref = localStorage.getItem("r6coach-glance") || "auto"; } catch (e) { /* private window */ }
function applyGlance() {
  const st = (typeof currentState !== "undefined" && currentState) || {};
  const on = glancePref === "on" || (glancePref === "auto" && IN_MATCH.has(st.screen));
  const was = document.body.classList.contains("is-glance");
  document.body.classList.toggle("is-glance", on);
  const b = $("btn-glance");
  b.textContent = `Glance view: ${glancePref}`;
  b.setAttribute("aria-pressed", String(on));
  if (was !== on) drawMap();
}
function cycleGlance() {
  glancePref = { auto: "on", on: "off", off: "auto" }[glancePref] || "auto";
  try { localStorage.setItem("r6coach-glance", glancePref); } catch (e) { /* private window */ }
  applyGlance();
}

// The compact match line: score, side, site and operator in one row.
function renderCompact(s) {
  const bits = [sideName(s.side), s.site, s.my_operator, s.my_location].filter(Boolean);
  $("match-compact").textContent = bits.join(" · ") || "Waiting for the game";
}

// ------------------------------------------------------------------ the coach's current line
function nowSaid(ev, live) {
  const d = ev.data || {};
  if (!d.text || d.phase === "test") return;
  const box = $("now-line");
  box.hidden = false;
  box.classList.remove("is-old");
  $("now-text").textContent = d.text;
  const ph = (PHASES[d.phase] || EXTRA_PHASES[d.phase] || {}).label || "Coach";
  $("now-phase").textContent = ph;
  $("now-when").textContent = clock(ev.ts);
  $("now-rooms").innerHTML = "";
  clearTimeout(box._t);
  if (live) box._t = setTimeout(() => box.classList.add("is-old"), FOCUS_MS);
  else box.classList.add("is-old");
}
function nowRooms(rooms) {
  $("now-rooms").innerHTML = rooms.map((r, i) =>
    `<button type="button" class="nowroom" data-goto-fi="${r.fi ?? fiOf(r.floor)}"><b>${i + 1}</b>${esc(r.floor)} ${esc(r.room)}</button>`).join("");
}

function drawMap() {
  const fl = curFloor();
  if (!fl) return;
  const img = $("map-img");
  if (img.getAttribute("src") !== fl.url) { img.src = fl.url; }
  img.alt = `${mapView.name} ${fl.floor} blueprint`;
  if (fl.size) stage().style.aspectRatio = `${fl.size[0]} / ${fl.size[1]}`;
  $("map-floors").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.fi) === fl.fi)));
  document.querySelectorAll(".livemap__toggle[data-show]").forEach((b) => b.setAttribute("aria-pressed", String(!!mapView.show[b.dataset.show])));

  const live = focusLive();
  const focus = live ? mapView.focus : [];
  const onThis = (r) => (r.fi != null ? r.fi === fl.fi : r.floor === fl.floor) && fl.rooms[r.room];
  const svg = [], pins = [], route = [];
  const focusRooms = focus.filter(onThis);

  // Sites: a dashed outline around each site's rooms on this floor, with a numbered badge.
  if (mapView.show.sites) {
    mapView.sites.forEach((s) => s.areas.filter((a) => a.fi === fl.fi).forEach((a) => {
      const [x0, y0, x1, y1] = a.box;
      svg.push(`<rect class="livemap__site${s.current ? " is-current" : ""}" x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}"/>`);
      pins.push(pin(x0, y0, "livemap__pin--site" + (s.current ? " is-current" : ""),
        `<span class="sitebadge">${icon("objective")}Site ${s.n}${s.current ? " · now" : ""}</span>`,
        `title="${esc(s.name)}"`));
    }));
  }
  // Coach focus: dim the rest of the floor, light the named rooms, join them in order.
  if (focusRooms.length) {
    const holes = focusRooms.map((r) => { const [x0, y0, x1, y1] = fl.rooms[r.room].box; return `M${x0} ${y0}H${x1}V${y1}H${x0}Z`; }).join("");
    svg.unshift(`<path class="livemap__dim" fill-rule="evenodd" d="M0 0H100V100H0Z${holes}"/>`);
    focusRooms.forEach((r) => {
      const b = fl.rooms[r.room].box;
      svg.push(`<rect class="livemap__room" x="${b[0]}" y="${b[1]}" width="${b[2] - b[0]}" height="${b[3] - b[1]}"/>`);
      route.push(centre(b));
    });
    if (route.length > 1) svg.push(`<polyline class="livemap__route" points="${route.map((p) => p.join(",")).join(" ")}"/>`);
  }
  // Room names, in the game's callouts. Focused rooms carry their order number.
  Object.entries(fl.rooms).forEach(([name, r]) => {
    const n = focus.findIndex((f) => onThis(f) && f.room === name);
    if (!mapView.show.labels && n < 0) return;
    pins.push(pin(r.box[0], r.box[1], "livemap__pin--room" + (n >= 0 ? " is-focus" : ""),
      `<span class="roomlabel">${n >= 0 ? `<b>${n + 1}</b>` : ""}${esc(name)}</span>`));
  });
  // Stairs, hatches, windows, doors and outside areas.
  if (mapView.show.features) {
    mapView.features.filter((f) => f.fi === fl.fi).forEach((f) => {
      if (f.kind === "label") { pins.push(pin(f.x, f.y, "livemap__pin--area", `<span class="arealabel">${esc(f.name)}</span>`)); return; }
      pins.push(pin(f.x, f.y, "livemap__pin--feature", `<span class="ficon ficon--${f.kind}" title="${esc(f.name || FEATURE_WORD[f.kind])}">${FEATURE_SVG[f.kind] || ""}</span>`
        + (f.name ? `<span class="ficon__name">${esc(f.name)}</span>` : "")));
    });
  }
  // The selected setup's utility, numbered in the order to place it.
  const brief = mapView.brief;
  const elsewhere = [];
  if (brief && mapView.show.utility) {
    brief.placements.forEach((p) => {
      if (!p.pos) return;
      if (p.pos.fi !== fl.fi) { elsewhere.push(p); return; }
      const hot = mapView.hot === p.n ? " is-hot" : "";
      pins.push(pin(p.pos.x, p.pos.y, "livemap__pin--util" + hot,
        `<button type="button" class="ubtn" data-n="${p.n}" aria-label="Step ${p.n}: ${esc(p.what)}${p.note ? ". " + esc(p.note) : ""}">${utilityIcon(p)}<span class="ubtn__n" style="${stepVar(p.n)}">${p.n}</span></button>`));
    });
  }
  // The player's room from the HUD.
  const you = mapView.you;
  if (you && you.fi === fl.fi && fl.rooms[you.room]) {
    const [cx, cy] = centre(fl.rooms[you.room].box);
    pins.push(pin(cx, cy, "livemap__pin--you", '<span class="youring"></span><span class="youlabel">You</span>'));
  }
  $("map-svg").innerHTML = svg.join("");
  const pinEl = $("map-pins");
  pinEl.innerHTML = pins.join("");
  // While rooms are named, everything else fades back so the numbered rooms win.
  pinEl.classList.toggle("has-focus", focusRooms.length > 0);
  pinEl.classList.toggle("has-hot", !!(brief && mapView.hot));
  clearLabelsSoon(0);

  // What's on other floors, as buttons that go there.
  const parts = [];
  const other = focus.filter((r) => !onThis(r));
  if (other.length) parts.push(`Also named: ${other.map((r) => floorLink(r.fi ?? fiOf(r.floor), `${r.floor} ${r.room}`)).join(", ")}`);
  if (elsewhere.length) {
    const byFloor = {};
    elsewhere.forEach((p) => { (byFloor[p.pos.fi] = byFloor[p.pos.fi] || []).push(p.n); });
    parts.push("Setup steps on other floors: " + Object.entries(byFloor).map(([fi, ns]) =>
      floorLink(Number(fi), `${floorName(Number(fi))} (step ${ns.join(", ")})`)).join(", "));
  }
  $("map-said").innerHTML = parts.join(" · ");
  drawLegend(brief);
}

function fiOf(floor) { const f = mapView.floors.find((x) => x.floor === floor); return f ? f.fi : null; }
function floorName(fi) { const f = mapView.floors.find((x) => x.fi === fi); return f ? f.floor : "?"; }
function floorLink(fi, text) {
  return fi == null ? esc(text) : `<button type="button" class="linkbtn" data-goto-fi="${fi}">${esc(text)}</button>`;
}

function drawLegend(brief) {
  const used = brief && mapView.show.utility ? [...new Set(brief.placements.map((p) => p.kind))] : [];
  const feats = mapView.show.features ? [...new Set(mapView.features.filter((f) => f.fi === mapView.fi && f.kind !== "label").map((f) => f.kind))] : [];
  $("map-legend").innerHTML = used.map((k) => {
    const p = brief.placements.find((x) => x.kind === k);
    return `<span class="legend__item">${utilityIcon(p)}${esc(KIND[k] ? KIND[k][1] : k)}</span>`;
  }).join("") + feats.map((k) => `<span class="legend__item"><span class="ficon ficon--${k}">${FEATURE_SVG[k] || ""}</span>${FEATURE_WORD[k] || k}</span>`).join("");
}

// ------------------------------------------------------------------ following the game
function followYou() {
  const you = mapView.you;
  if (you && you.fi != null && !mapView.pinned && !focusLive()) mapView.fi = you.fi;
}

function mapFocus(d, live) {
  if (!live || !d.rooms || !d.rooms.length) return;
  const go = async () => {
    if (d.map && d.map !== mapView.map) await loadLayout();
    mapView.focus = d.rooms;
    mapView.focusUntil = Date.now() + FOCUS_MS;
    nowRooms(d.rooms);
    mapView.pinned = false;
    const first = d.rooms[0];
    const fi = first.fi ?? fiOf(first.floor);
    if (fi == null) return;
    mapView.fi = fi;
    const fl = curFloor();
    const boxes = d.rooms.filter((r) => (r.fi ?? fiOf(r.floor)) === fi && fl.rooms[r.room]).map((r) => fl.rooms[r.room].box);
    drawMap();
    const b = boundsOf(boxes.flatMap((x) => [[x[0], x[1]], [x[2], x[3]]]));
    if (b) fitBox(b);
    clearTimeout(mapView._focusT);
    mapView._focusT = setTimeout(() => { followYou(); drawMap(); }, FOCUS_MS + 50);
  };
  go();
}

function mapState(s) {
  if (!s) return;
  renderCompact(s);
  applyGlance();
  if ((s.map || null) && !mapView.floors.length && !mapView._loading) {
    mapView._loading = true;
    loadLayout().finally(() => { mapView._loading = false; });
  } else {
    const where = `${s.my_location || ""}|${s.site || ""}`;
    if (where !== mapView._lastWhere) {
      mapView._lastWhere = where;
      api("/api/layout").then((lay) => {
        if (lay.map !== mapView.map) { loadLayout(); return; }
        mapView.you = lay.you;
        mapView.sites = lay.sites || [];
        // Follow the player's floor unless a coach line, a click or a setup is showing another one.
        followYou();
        drawMap();
      }).catch(() => {});
    }
  }
  setupsState(s);
}

// ------------------------------------------------------------------ setup briefs
const setups = { data: null, key: null, chosen: null, side: null, site: null };
const SETUP_SCREENS = new Set(["map_ban", "operator_ban", "operator_select", "loadout", "prep_phase"]);

function setupsState(s) {
  const key = [s.map, s.site, (s.site_options || []).join(","), s.side, s.my_operator].join("|");
  if (key === setups.key) return;
  setups.key = key;
  // A new situation from the game replaces any side or site picked by hand.
  setups.side = null; setups.site = null;
  loadSetups();
}

async function loadSetups() {
  const q = new URLSearchParams();
  const st = currentState || {};
  if (setups.side || setups.site) {
    q.set("map", mapView.map || st.map || "");
    q.set("side", setups.side || st.side || "");
    if (setups.site) q.set("site", setups.site);
    if (st.my_operator) q.set("operator", st.my_operator);
  }
  let d;
  try { d = await api("/api/setups" + (q.toString() ? "?" + q : "")); } catch (e) { return; }
  setups.data = d;
  renderSetups();
}

function renderSetups() {
  const d = setups.data;
  const card = $("setups-card");
  const hasSites = d && d.map && Object.keys(d.site_names || {}).length;
  card.hidden = !hasSites;
  if (!hasSites) { mapView.brief = null; drawMap(); return; }
  const st = currentState || {};
  card.classList.toggle("is-now", SETUP_SCREENS.has(st.screen));
  const siteName = d.site ? d.site_names[d.site] : d.sites.length > 1 ? "possible sites" : "every site";
  $("setups-for").textContent = [mapView.name || d.map, d.side ? sideName(d.side) : "both sides", siteName,
    d.operator].filter(Boolean).join(" · ");
  $("setups-site").innerHTML = `<option value="">${d.sites.length > 1 ? "Possible sites" : "All sites"}</option>` +
    Object.entries(d.site_names).map(([id, n]) => `<option value="${esc(id)}"${id === d.site ? " selected" : ""}>${esc(n)}</option>`).join("");
  document.querySelectorAll(".setups__sides [data-side]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.side === d.side)));
  const briefs = d.briefs || [];
  if (!briefs.some((b) => b.id === setups.chosen)) setups.chosen = briefs.length ? briefs[0].id : null;
  const showSite = !d.site;
  $("setups-list").innerHTML = briefs.map((b) => `<li><button type="button" class="setups__item" data-brief="${esc(b.id)}" aria-pressed="${b.id === setups.chosen}">
      <span class="setups__title">${esc(b.title)}</span>
      <span class="setups__meta">${esc(b.operators.length ? b.operators.join(", ") : "Team")} · ${esc(sideName(b.side))}${showSite ? " · " + esc(d.site_names[b.site] || b.site) : ""}</span>
    </button></li>`).join("") || '<li class="hint-sm">No standard setups for this yet.</li>';
  const brief = briefs.find((b) => b.id === setups.chosen) || null;
  const changed = (mapView.brief && mapView.brief.id) !== (brief && brief.id);
  mapView.brief = brief;
  renderBrief(brief);
  // A newly suggested setup zooms the map to it, unless a coach line is showing rooms right now.
  if (changed && brief && !focusLive() && SETUP_SCREENS.has(st.screen)) focusBrief(brief, false); else drawMap();
}

// The brief: the current step big (icon, words, first-person picture), the others one line each.
function renderBrief(b) {
  const el = $("setups-brief");
  if (!b) { el.innerHTML = ""; return; }
  const heroN = mapView.hot || 1;
  const hero = b.placements.find((p) => p.n === heroN) || b.placements[0];
  const total = b.placements.length;
  const me = (currentState || {}).my_operator;
  const mine = me ? b.placements.filter((p) => p.by && p.by.toLowerCase() === me.toLowerCase()).map((p) => p.n) : [];
  el.innerHTML = `${b.call ? `<p class="lib-call">${esc(b.call)}</p>` : ""}
    ${mine.length ? `<p class="lib-job">As ${esc(me)}: step ${mine.join(", ")}.</p>` : ""}
    <p class="setups__text">${esc(b.text)}</p>
    ${b.counters ? `<p class="lib-counters"><b>If they stop it:</b> ${esc(b.counters)}</p>` : ""}
    <div class="hero" style="${stepVar(hero.n)}" data-n="${hero.n}">
      <div class="hero__head">
        <button type="button" class="step__go hero__go" data-n="${hero.n}" aria-label="Show step ${hero.n} on the map">${utilityIcon(hero, "uicon--big")}</button>
        <div class="hero__words">
          <p class="hero__count">Step ${hero.n} of ${total}${hero.pos ? "" : " · not on the map yet"}</p>
          <p class="hero__what">${esc(stepWords(hero))}</p>
          ${hero.note ? `<p class="hero__note">${esc(hero.note)}</p>` : ""}
        </div>
      </div>
      ${shotSlot(hero)}
      ${total > 1 ? `<div class="hero__nav">
        <button type="button" class="rc-btn rc-btn--quiet" data-n="${hero.n > 1 ? hero.n - 1 : total}">Previous step</button>
        <button type="button" class="rc-btn rc-btn--quiet" data-n="${hero.n < total ? hero.n + 1 : 1}">Next step</button></div>` : ""}
    </div>
    <ol class="steps">${b.placements.map((p) => `<li class="step${p.n === hero.n ? " is-hot" : ""}" style="${stepVar(p.n)}">
      <button type="button" class="step__row" data-n="${p.n}">
        ${utilityIcon(p)}<span class="step__n">${p.n}</span>
        <span class="step__what">${esc(stepWords(p))}</span>
        ${p.shot ? '<span class="step__pic" title="Has a picture">Picture</span>' : ""}
      </button></li>`).join("")}</ol>`;
}

// "Thermite · Breach: Office | Library wall (2F)" reads better than the data's "2F thermite: ...".
// The server sends the place on its own, so a note with brackets in it never leaks into the title.
function stepWords(p) {
  const word = KIND[p.kind] ? KIND[p.kind][1] : p.kind;
  const by = p.by && !word.toLowerCase().includes(p.by.toLowerCase()) ? p.by + " · " : "";
  return `${by}${word}: ${p.where || p.what} (${p.floor})`;
}

// Markers on a first-person picture, positioned in % of it: circles, boxes, and arrows (line + head dot).
function marksHtml(marks, n) {
  const out = [], lines = [];
  (marks || []).forEach((m) => {
    if (m.shape === "box") {
      const x = Math.min(m.x, m.x2), y = Math.min(m.y, m.y2);
      out.push(`<span class="mark mark--box" style="left:${x}%;top:${y}%;width:${Math.abs(m.x2 - m.x)}%;height:${Math.abs(m.y2 - m.y)}%"></span>`);
    } else if (m.shape === "arrow") {
      lines.push(`<line x1="${m.x}" y1="${m.y}" x2="${m.x2}" y2="${m.y2}"/>`);
      out.push(`<span class="mark mark--head" style="left:${m.x2}%;top:${m.y2}%"></span>`);
    } else {
      out.push(`<span class="mark mark--circle" style="left:${m.x}%;top:${m.y}%"></span>`);
    }
  });
  const first = (marks || [])[0];
  if (first && n) {
    const lx = first.shape === "arrow" ? first.x2 : first.x, ly = first.shape === "arrow" ? first.y2 : first.y;
    out.push(`<span class="mark__n" style="left:${lx}%;top:${ly}%">${n}</span>`);
  }
  const svg = lines.length ? `<svg class="marks__svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">${lines.join("")}</svg>` : "";
  return `<span class="marks" aria-hidden="true">${svg}${out.join("")}</span>`;
}

function shotSlot(p) {
  if (p.shot) {
    return `<div class="shot" data-slot="${esc(p.slot)}" data-n="${p.n}">
      <button type="button" class="shotframe shot__thumb" data-edit="${p.n}" aria-label="Open the picture for step ${p.n} to mark where it goes">
        <img src="${esc(p.shot)}" alt="" loading="lazy">${marksHtml(p.marks, p.n)}</button>
      ${p.marks && p.marks.length ? "" : `<button type="button" class="linkbtn shot__act" data-edit="${p.n}">Mark the spot</button>`}
    </div>`;
  }
  return `<div class="shot shot--empty" data-slot="${esc(p.slot)}" data-n="${p.n}" tabindex="0" aria-label="Picture for step ${p.n}: drop or paste an image">
    <span class="shot__drop">Drop or paste a picture</span>
    <span class="shot__acts"><button type="button" class="linkbtn" data-grab="${esc(p.slot)}">Grab game screen</button>
    <button type="button" class="linkbtn" data-frames="${esc(p.slot)}">My frames</button>
    <button type="button" class="linkbtn" data-upload="${esc(p.slot)}">Choose file</button></span>
  </div>`;
}

// ------------------------------------------------------------------ picture editor
// One dialog: the picture big, with its markers. Click to drop a circle; drag for an arrow or box.
// Every change saves at once.
const editor = { p: null, tool: "circle", marks: [], start: null };

function openEditor(n) {
  const p = mapView.brief && mapView.brief.placements.find((x) => x.n === n);
  if (!p) return;
  editor.p = p;
  editor.marks = (p.marks || []).slice();
  shotTarget = p.slot;
  $("shot-view-title").textContent = `Step ${p.n}: ${stepWords(p)}`;
  $("shot-view-note").textContent = p.note || "";
  $("shot-view-img").src = p.shot;
  drawEditor();
  if (!$("shot-view").open) $("shot-view").showModal();
}

function drawEditor(preview) {
  const marks = preview ? editor.marks.concat([preview]) : editor.marks;
  $("shot-edit-marks").innerHTML = marksHtml(marks, editor.p && editor.p.n);
  document.querySelectorAll("#shot-tools [data-tool]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.tool === editor.tool)));
  $("shot-undo").disabled = !editor.marks.length;
}

async function saveMarks() {
  const p = editor.p;
  const r = await fetch(`/api/setups/${encodeURIComponent(setups.data.map)}/shot/${encodeURIComponent(p.slot)}/marks`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ marks: editor.marks }) });
  if (r.ok) { p.marks = (await r.json()).marks; renderBrief(mapView.brief); }
}

function initEditor() {
  const frame = $("shot-edit");
  const at = (e) => {
    const r = frame.getBoundingClientRect();
    return [Math.min(100, Math.max(0, (e.clientX - r.left) / r.width * 100)), Math.min(100, Math.max(0, (e.clientY - r.top) / r.height * 100))];
  };
  const shape = (a, b) => {
    const far = Math.hypot(b[0] - a[0], b[1] - a[1]) > 2;
    if (editor.tool === "circle" || !far) return { shape: "circle", x: a[0], y: a[1] };
    return { shape: editor.tool, x: a[0], y: a[1], x2: b[0], y2: b[1] };
  };
  frame.addEventListener("pointerdown", (e) => { editor.start = at(e); frame.setPointerCapture(e.pointerId); });
  frame.addEventListener("pointermove", (e) => { if (editor.start) drawEditor(shape(editor.start, at(e))); });
  frame.addEventListener("pointerup", (e) => {
    if (!editor.start || editor.marks.length >= 12) { editor.start = null; return; }
    editor.marks.push(shape(editor.start, at(e)));
    editor.start = null;
    drawEditor();
    saveMarks();
  });
  $("shot-tools").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.tool) { editor.tool = b.dataset.tool; drawEditor(); }
    if (b.id === "shot-undo") { editor.marks.pop(); drawEditor(); saveMarks(); }
    if (b.id === "shot-replace-grab") grabShot(editor.p.slot, true);
    if (b.id === "shot-remove") {
      fetch(`/api/setups/${encodeURIComponent(setups.data.map)}/shot/${encodeURIComponent(editor.p.slot)}`, { method: "DELETE" })
        .then(() => { $("shot-view").close(); loadSetups(); });
    }
  });
}

async function grabShot(slot, reopen = false) {
  const r = await fetch(`/api/setups/${encodeURIComponent(setups.data.map)}/shot/${encodeURIComponent(slot)}/grab`, { method: "POST" });
  if (!r.ok) { alert((await r.json().catch(() => ({}))).detail || "Couldn't grab the game screen."); return; }
  await loadSetups();
  const p = mapView.brief && mapView.brief.placements.find((x) => x.slot === slot);
  if (p) openEditor(p.n);   // straight to marking the spot
  else if (reopen) $("shot-view").close();
}

// Ctrl+V anywhere on the page puts a copied screenshot (from a video, or the Snipping Tool) on the
// step you last touched, or the one open in the editor.
async function pasteShot(e) {
  const item = [...(e.clipboardData || {}).items || []].find((i) => i.type.startsWith("image/"));
  if (!item || !shotTarget || !setups.data) return;
  e.preventDefault();
  await putShot(shotTarget, item.getAsFile());
}

// Show a brief's utility: go to the floor with most of its steps and zoom to them.
// user: picked by hand, so the map stays on that floor instead of following the player.
function focusBrief(b, user = true) {
  const placed = b.placements.filter((p) => p.pos);
  if (!placed.length) { drawMap(); return; }
  const counts = {};
  placed.forEach((p) => { counts[p.pos.fi] = (counts[p.pos.fi] || 0) + 1; });
  const fi = Number(Object.entries(counts).sort((a, c) => c[1] - a[1])[0][0]);
  if (!mapView.floors.some((f) => f.fi === fi)) { drawMap(); return; }
  mapView.fi = fi;
  if (user) mapView.pinned = true;
  drawMap();
  const b2 = boundsOf(placed.filter((p) => p.pos.fi === fi).map((p) => [p.pos.x, p.pos.y]));
  if (b2) fitBox([b2[0] - 4, b2[1] - 6, b2[2] + 4, b2[3] + 6], 3);
}

function focusStep(n) {
  const b = mapView.brief;
  const p = b && b.placements.find((x) => x.n === n);
  mapView.hot = n;
  if (p) shotTarget = p.slot;
  renderBrief(b);
  if (!p || !p.pos) { drawMap(); return; }
  mapView.fi = p.pos.fi;
  mapView.pinned = true;
  drawMap();
  fitBox([p.pos.x - 6, p.pos.y - 9, p.pos.x + 6, p.pos.y + 9], 4);
}

// ------------------------------------------------------------------ pictures
let shotTarget = null;
async function putShot(slot, file) {
  if (!file || !/^image\/(jpeg|png|webp)$/.test(file.type)) { alert("Use a JPEG, PNG or WebP picture."); return; }
  const r = await fetch(`/api/setups/${encodeURIComponent(setups.data.map)}/shot/${encodeURIComponent(slot)}`,
    { method: "PUT", headers: { "Content-Type": file.type }, body: file });
  if (!r.ok) { alert("Couldn't save that picture."); return; }
  loadSetups();
}

async function openFramePicker(slot) {
  shotTarget = slot;
  const dlg = $("frame-picker");
  $("frame-picker-for").textContent = `Frames from ${mapView.name || setups.data.map}, newest first. They're your own captures.`;
  $("frame-list").innerHTML = '<li class="hint-sm">Loading…</li>';
  dlg.showModal();
  let frames = [];
  try { frames = await api(`/api/frames?map=${encodeURIComponent(setups.data.map)}`); } catch (e) { /* shown below */ }
  $("frame-list").innerHTML = frames.map((f) => `<li><button type="button" class="frames__pick" data-frame="${esc(f.frame)}">
      <img src="${esc(f.url)}" alt="" loading="lazy"><span>${esc(sentence(f.screen || ""))}${f.where ? " · " + esc(f.where) : ""}<br><span class="muted">${esc(f.frame.replace("/", " "))}</span></span>
    </button></li>`).join("") || '<li class="hint-sm">No saved frames for this map yet. The coach saves one per screen change while you play.</li>';
}

async function useFrame(frame) {
  const r = await post(`/api/setups/${encodeURIComponent(setups.data.map)}/shot/${encodeURIComponent(shotTarget)}/frame`, { frame }).catch(() => null);
  $("frame-picker").close();
  if (r) loadSetups();
}

// ------------------------------------------------------------------ wiring
function initLiveMap() {
  initPanZoom();
  $("map-floors").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-fi]");
    if (b) { showFloor(Number(b.dataset.fi), true); resetView(); }
  });
  document.querySelectorAll(".livemap__toggle[data-show]").forEach((b) => {
    b.onclick = () => { mapView.show[b.dataset.show] = !mapView.show[b.dataset.show]; drawMap(); };
  });
  $("map-pins").addEventListener("click", (e) => {
    const b = e.target.closest(".ubtn");
    if (b) focusStep(Number(b.dataset.n));
  });
  $("map-said").addEventListener("click", (e) => {
    const b = e.target.closest("[data-goto-fi]");
    if (b) { showFloor(Number(b.dataset.gotoFi), true); resetView(); }
  });
  $("setups-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-brief]");
    if (!b) return;
    setups.chosen = b.dataset.brief;
    mapView.hot = null;
    renderSetups();
    focusBrief(mapView.brief);
  });
  $("setups-site").onchange = (e) => { setups.site = e.target.value || null; setups.side = setups.side || (setups.data && setups.data.side); loadSetups(); };
  document.querySelectorAll(".setups__sides [data-side]").forEach((b) => {
    b.onclick = () => { setups.side = b.dataset.side; setups.site = setups.site || (setups.data && setups.data.site); loadSetups(); };
  });
  const brief = $("setups-brief");
  brief.addEventListener("click", (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    const slot = t.closest("[data-slot]");
    if (slot) shotTarget = slot.dataset.slot;
    if (t.dataset.edit) openEditor(Number(t.dataset.edit));
    else if (t.dataset.n) focusStep(Number(t.dataset.n));
    else if (t.dataset.upload) { shotTarget = t.dataset.upload; $("shot-file").click(); }
    else if (t.dataset.grab) grabShot(t.dataset.grab);
    else if (t.dataset.frames) openFramePicker(t.dataset.frames);
  });
  brief.addEventListener("focusin", (e) => { const s = e.target.closest("[data-slot]"); if (s) shotTarget = s.dataset.slot; });
  initEditor();
  document.addEventListener("paste", pasteShot);
  $("btn-glance").onclick = cycleGlance;
  $("now-rooms").addEventListener("click", (e) => {
    const b = e.target.closest("[data-goto-fi]");
    if (b) showFloor(Number(b.dataset.gotoFi), true);
  });
  applyGlance();
  brief.addEventListener("dragover", (e) => {
    const s = e.target.closest(".shot");
    if (!s) return;
    e.preventDefault();
    s.classList.add("is-over");
  });
  brief.addEventListener("dragleave", (e) => { const s = e.target.closest(".shot"); if (s) s.classList.remove("is-over"); });
  brief.addEventListener("drop", (e) => {
    const s = e.target.closest(".shot");
    if (!s) return;
    e.preventDefault();
    s.classList.remove("is-over");
    putShot(s.dataset.slot, e.dataTransfer.files[0]);
  });
  $("shot-file").onchange = (e) => { const f = e.target.files[0]; e.target.value = ""; if (f && shotTarget) putShot(shotTarget, f); };
  $("frame-list").addEventListener("click", (e) => { const b = e.target.closest("[data-frame]"); if (b) useFrame(b.dataset.frame); });
  document.querySelectorAll("dialog [data-close]").forEach((b) => { b.onclick = () => b.closest("dialog").close(); });
  document.querySelectorAll("dialog").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));
  loadLayout();
  loadSetups();
}
