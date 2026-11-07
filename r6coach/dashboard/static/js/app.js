/* R6 Coach dashboard. Vanilla JS, no build step. Talks to server.py over /api and /ws. */
"use strict";

const $ = (id) => document.getElementById(id);
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");

// Coach phases in match order, with the house icon for each.
const PHASES = {
  map_ban: { label: "Map ban", icon: "phase-map-ban" },
  operator_ban: { label: "Operator ban", icon: "phase-operator-ban" },
  operator_select: { label: "Operator select", icon: "phase-operator-select" },
  prep_phase: { label: "Prep phase", icon: "phase-prep" },
  action_phase: { label: "Action phase", icon: "phase-action" },
  between_rounds: { label: "Between rounds", icon: "phase-recap" },
};
const EXTRA_PHASES = {
  death: { label: "After death", icon: "phase-recap" },
  question: { label: "Answer", icon: "coach" },
  test: { label: "Voice check", icon: "coach" },
};
const ICONS = ["coach", "push-to-talk", "screen-read", "drone-route", "objective", "round-win",
  ...new Set(Object.values(PHASES).map((p) => p.icon))];
const MENU_SCREENS = new Set(["main_menu", "matchmaking", "other", "loading"]);

// ------------------------------------------------------------------ helpers
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const pad = (n) => String(n).padStart(2, "0");
function clock(ts) { const d = new Date(ts * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; }
function mmss(sec) { return `${Math.floor(sec / 60)}:${pad(sec % 60)}`; }
function sentence(s) { s = String(s ?? "").replace(/_/g, " "); return s.charAt(0).toUpperCase() + s.slice(1); }
const SIDE = { attack: "Attack", defense: "Defence", defence: "Defence" };
const sideName = (s) => (s ? SIDE[s] || sentence(s) : null);

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });

function flash(el, text, ms = 2500) {
  el.textContent = text;
  el.classList.remove("is-dirty");
  clearTimeout(el._t);
  if (ms) el._t = setTimeout(() => { if (el.textContent === text) el.textContent = ""; }, ms);
}

// ------------------------------------------------------------------ icons
// The house SVGs are inlined so their grey line can follow currentColor and the accent the signal token.
const iconCache = {};
async function loadIcons() {
  await Promise.all(ICONS.map(async (name) => {
    try {
      const r = await fetch(`/static/ds/assets/Icons/${name}.svg`);
      if (r.ok) iconCache[name] = (await r.text()).replace("<svg ", '<svg aria-hidden="true" focusable="false" ');
    } catch (e) { /* icons are decoration; words carry the meaning */ }
  }));
  document.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = iconCache[el.dataset.icon] || ""; });
}
const icon = (name) => `<span class="ic" aria-hidden="true">${iconCache[name] || ""}</span>`;

// ------------------------------------------------------------------ theme
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  $("btn-theme").textContent = t === "dark" ? "Light theme" : "Dark theme";
  try { localStorage.setItem("r6coach-theme", t); } catch (e) { /* private window */ }
}
setTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark");
$("btn-theme").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");

// ------------------------------------------------------------------ tabs
const tabButtons = [...document.querySelectorAll('.tabs [role="tab"]')];
let frameTimer = null;
function selectTab(name, focus = false) {
  if (!tabButtons.some((b) => b.dataset.tab === name)) name = "live";
  tabButtons.forEach((b) => {
    const on = b.dataset.tab === name;
    b.setAttribute("aria-selected", String(on));
    b.tabIndex = on ? 0 : -1;
    if (on && focus) b.focus();
    $(b.getAttribute("aria-controls")).hidden = !on;
  });
  clearInterval(frameTimer);
  if (name === "sees") { refreshFrame(); frameTimer = setInterval(refreshFrame, 3000); }
  if (name === "matches") loadMatches();
  if (name === "settings") loadSetup();
  if (location.hash.slice(1) !== name) history.replaceState(null, "", name === "live" ? location.pathname : "#" + name);
}
tabButtons.forEach((b, i) => {
  b.addEventListener("click", () => selectTab(b.dataset.tab));
  b.addEventListener("keydown", (e) => {
    let j = null;
    if (e.key === "ArrowRight") j = (i + 1) % tabButtons.length;
    if (e.key === "ArrowLeft") j = (i - 1 + tabButtons.length) % tabButtons.length;
    if (e.key === "Home") j = 0;
    if (e.key === "End") j = tabButtons.length - 1;
    if (j !== null) { e.preventDefault(); selectTab(tabButtons[j].dataset.tab, true); }
  });
});
window.addEventListener("hashchange", () => selectTab(location.hash.slice(1)));

// ------------------------------------------------------------------ header status
let lastStatus = null;
let wsDown = false;
function pill(el, text, kind) {
  el.textContent = text;
  el.className = "rc-pill" + (kind ? " rc-pill--" + kind : "");
}
function renderStatus(st) {
  lastStatus = st;
  const cap = st.capture || "unknown";
  const capOk = cap.startsWith("capturing") || cap.startsWith("replay:") && !cap.includes("no images");
  if (wsDown) pill($("pill-capture"), "dashboard: reconnecting", "error");
  else pill($("pill-capture"), "capture: " + cap, st.paused ? "warn" : capOk ? "live" : "warn");
  if (st.paused) pill($("pill-screen"), "screen: paused", "warn");
  else if (st.state) renderScreenPill(st.state.screen);
  if (!listening) {
    const key = st.ptt_key || "off";
    if (key === "off") pill($("pill-ptt"), "push-to-talk: off", "");
    else pill($("pill-ptt"), `push-to-talk: ${key}` + (st.ptt_ready ? "" : " (loading)"), st.ptt_ready ? "live" : "");
  }
  renderVoicePill();
  renderCost(st.cost, st.calls);
  $("btn-mute").textContent = st.muted ? "Unmute" : "Mute";
  $("btn-pause").textContent = st.paused ? "Resume reading" : "Pause reading";
  if (st.state) renderState(st.state);
}
function renderScreenPill(screen) {
  if (lastStatus && lastStatus.paused) return;
  pill($("pill-screen"), "screen: " + String(screen || "none").replace(/_/g, " "), "");
}
function renderVoicePill() {
  const st = lastStatus || {};
  if (st.muted) pill($("pill-voice"), "voice: muted", "warn");
  else if (speakingLi) pill($("pill-voice"), "voice: speaking", "live");
  else pill($("pill-voice"), "voice: on", "live");
}
function renderCost(cost, calls) {
  const c = Number(cost || 0);
  $("pill-cost").textContent = `cost: $${c.toFixed(2)}` + (calls != null ? ` · ${calls} calls` : "");
}

// ------------------------------------------------------------------ match state
let currentState = {};
function dd(html, cls = "") { return { html, cls }; }
function empty() { return dd("–", "rc-empty"); }
function tags(list) {
  return list && list.length ? dd(`<span class="tags">${list.map((x) => `<span class="tag">${esc(x)}</span>`).join("")}</span>`) : empty();
}
function renderState(s) {
  currentState = s || {};
  s = currentState;
  renderCompact(s);
  applyGlance();
  $("score-us").textContent = s.score_us ?? 0;
  $("score-them").textContent = s.score_them ?? 0;
  const inMatch = !!s.match_id || !MENU_SCREENS.has(s.screen || "other");
  const meta = [];
  if (s.round_number != null) meta.push(`Round ${s.round_number}`);
  if (s.side) meta.push(sideName(s.side));
  $("score-meta").textContent = meta.length ? meta.join(" · ") : inMatch ? "Match in progress" : "Waiting for a match";

  let site = empty();
  if (s.site) site = dd(esc(s.site));
  else if (s.site_options && s.site_options.length)
    site = dd(`<span class="muted">Possible:</span> <span class="tags">${s.site_options.map((x) => `<span class="tag">${esc(x)}</span>`).join("")}</span>`);
  let health = empty();
  if (s.player_alive === false) health = dd("Out", "muted");
  else if (s.health != null) health = dd(esc(s.health), "rc-num");
  const players = s.alive_us != null || s.alive_them != null
    ? dd(`<span class="us">${esc(s.alive_us ?? "–")}</span> v <span class="them">${esc(s.alive_them ?? "–")}</span>`, "rc-num")
    : empty();
  const you = s.my_operator ? dd(esc(s.my_operator) + (s.player_alive === false ? '<span class="muted"> · out</span>' : "")) : empty();
  const rows = [
    ["Map", s.map ? dd(esc(s.map)) : empty()],
    ["Side", s.side ? dd(esc(sideName(s.side))) : empty()],
    ["Site", site],
    ["Room", s.my_location ? dd(esc(s.my_location)) : empty()],
    ["Health", health],
    ["Players", players],
    ["Timer", s.timer_seconds != null ? dd(mmss(s.timer_seconds), "rc-num") : empty()],
    ["Reinforcements", s.team_reinforcements != null ? dd(`${esc(s.team_reinforcements)} left`, "rc-num") : empty()],
    ["Defuser", s.defuser_planted ? dd("Planted") : empty()],
    ["You", you],
    ["Team", tags(s.teammate_operators)],
    ["Enemy ops seen", tags(s.enemy_operators)],
    ["Bans", tags(s.banned_operators)],
    ["Pinged", tags((s.pinged_enemies || []).map((p) => p.location + (p.operator ? ` (${p.operator})` : "")))],
  ];
  $("state-list").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd class="${v.cls}">${v.html}</dd>`).join("");
  updateEmpty();
  // The same live numbers, as the local HUD reader sees them, on the "What the coach sees" tab.
  const hud = [["Screen", s.screen ? dd(esc(sentence(s.screen))) : empty()], rows[5], rows[6], rows[3], rows[4],
    ["Score", dd(`<span class="us">${esc(s.score_us ?? 0)}</span> – <span class="them">${esc(s.score_them ?? 0)}</span>`, "rc-num")], rows[7]];
  $("hud-list").innerHTML = hud.map(([k, v]) => `<dt>${k}</dt><dd class="${v.cls}">${v.html}</dd>`).join("");
  const ms = lastStatus && lastStatus.hud_ms;
  $("hud-when").textContent = ms != null ? `Last read took ${ms} ms` : lastStatus ? "Off (hud.enabled in config.yaml)" : "Starting";
}

// ------------------------------------------------------------------ coach feed
const feed = $("feed");
let speakingLi = null;
let speakTimer = null;

function phaseTag(phase) {
  const p = PHASES[phase] || EXTRA_PHASES[phase];
  if (!p) return phase ? `<span class="rc-feed__phase">${esc(sentence(phase))}</span>` : "";
  return `<span class="rc-feed__phase">${icon(p.icon)}${esc(p.label)}</span>`;
}
function fadeOut(li) {
  // The newest line keeps a signal-soft tint that fades over 4 seconds.
  li.classList.remove("is-speaking", "is-fading");
  li.classList.add("is-fresh");
  if (REDUCED.matches) { setTimeout(() => li.classList.remove("is-fresh"), 4000); return; }
  requestAnimationFrame(() => requestAnimationFrame(() => { li.classList.add("is-fading"); li.classList.remove("is-fresh"); }));
}
function stopSpeaking() {
  clearTimeout(speakTimer);
  if (speakingLi) { fadeOut(speakingLi); speakingLi = null; }
  renderVoicePill();
}
function addLine(ts, bodyHtml, cls = "") {
  const li = document.createElement("li");
  if (cls) li.className = cls;
  li.innerHTML = `<span class="rc-feed__t">${clock(ts)}</span><span class="rc-feed__body">${bodyHtml}</span>`;
  feed.prepend(li);
  while (feed.children.length > 200) feed.lastChild.remove();
  return li;
}
// A repeated error (same text) replaces its earlier row with a count, so it can't bury the coach's lines.
function addError(ts, text) {
  let count = 1;
  for (const li of feed.querySelectorAll("li.is-error")) {
    if (li.dataset.text === text) { count = +li.dataset.count + 1; li.remove(); break; }
  }
  const li = addLine(ts, esc(text) + (count > 1 ? ` <span class="rc-feed__count">×${count}</span>` : ""), "is-error");
  li.dataset.text = text;
  li.dataset.count = count;
}
// "Waiting for a match" shows until a match starts or the coach has said something.
let coachSpoke = false;
function updateEmpty() {
  const s = currentState || {};
  const inMatch = !!s.match_id || !MENU_SCREENS.has(s.screen || "other");
  $("feed-empty").hidden = coachSpoke || inMatch;
}
function coachSaid(ev, live) {
  const d = ev.data || {};
  setThinking(false);
  const li = addLine(ev.ts, phaseTag(d.phase) + esc(d.text));
  if (d.phase && d.phase !== "test") { coachSpoke = true; updateEmpty(); }
  if (!live) return;
  stopSpeaking();
  if (d.audio) {
    // is-speaking until the server says the line has finished (coach.done); 25 s as a backstop.
    li.classList.add("is-speaking");
    speakingLi = li;
    speakTimer = setTimeout(stopSpeaking, 25000);
    renderVoicePill();
  } else {
    fadeOut(li);
  }
}
let thinkTimer = null;
function setThinking(on, what = "") {
  clearTimeout(thinkTimer);
  $("coach-status").textContent = on ? what : "";
  if (on) thinkTimer = setTimeout(() => setThinking(false), 30000);
}

// ------------------------------------------------------------------ push-to-talk
let listening = false;
let listenTimer = null;
function setListening(on) {
  listening = on;
  $("listening").hidden = !on;
  clearTimeout(listenTimer);
  if (on) {
    pill($("pill-ptt"), "push-to-talk: listening", "warn");
    listenTimer = setTimeout(() => setListening(false), 20000);
  } else if (lastStatus) renderStatus(lastStatus);
}

// ------------------------------------------------------------------ rounds and match memory
let memTimer = null;
function refreshMemory(delay = 400) {
  clearTimeout(memTimer);
  memTimer = setTimeout(async () => {
    try { renderMemory(await api("/api/memory")); } catch (e) { /* server busy; next event retries */ }
  }, delay);
}
const RESULT = { true: ["won", "Win"], false: ["lost", "Loss"] };
function renderMemory(m) {
  const rounds = m.rounds || [];
  const cur = m.current;
  const inRound = cur && (currentState.match_id || !MENU_SCREENS.has(currentState.screen || "other"));
  const bars = rounds.map((r) => {
    const [cls, word] = RESULT[r.won] || ["unknown", "No result"];
    return `<div class="timeline__round" title="Round ${r.n}: ${word}"><span class="timeline__bar ${cls}"></span><span class="timeline__n">${r.n}</span></div>`;
  });
  if (inRound) bars.push(`<div class="timeline__round" title="Round ${cur.n}: in progress"><span class="timeline__bar current"></span><span class="timeline__n">${cur.n}</span></div>`);
  $("timeline").innerHTML = bars.join("");
  const won = rounds.filter((r) => r.won === true).length;
  const lost = rounds.filter((r) => r.won === false).length;
  const summary = rounds.length
    ? `<span class="us">${won} won</span> · <span class="them">${lost} lost</span>` + (rounds.length - won - lost ? ` · ${rounds.length - won - lost} no result` : "")
    : "No rounds yet.";
  $("timeline-summary").innerHTML = summary;
  $("timeline").hidden = !bars.length;
  $("timeline-summary").hidden = !rounds.length;
  $("timeline").setAttribute("aria-label", rounds.length ? `${won} rounds won, ${lost} lost` : "No rounds yet");

  const line = (r, current) => {
    const what = [sideName(r.side), r.site, r.operator ? `as ${r.operator}` : null].filter(Boolean).join(" · ") || "Side and site unknown";
    const detail = [];
    if (r.died_to) detail.push(`Died to ${r.died_to}` + (r.died_in ? ` in ${r.died_in}` : ""));
    else if (!current && r.won != null) detail.push("Survived");
    if (r.my_kills) detail.push(`${r.my_kills} kill${r.my_kills === 1 ? "" : "s"}`);
    if (r.team_kills || r.enemy_kills) detail.push(`team kills ${r.team_kills}, theirs ${r.enemy_kills}`);
    if (r.planted) detail.push("defuser planted");
    if (r.enemy_ops && r.enemy_ops.length) detail.push("they ran " + r.enemy_ops.join(", "));
    const [cls, word] = current ? ["none", "Now"] : (RESULT[r.won] ? RESULT[r.won] : ["none", "–"]);
    return `<li class="${current ? "is-current" : ""}"><span class="memory__n">R${r.n}</span>
      <span class="memory__what">${esc(what)}${detail.length ? `<br><span class="memory__detail">${esc(detail.join(" · "))}</span>` : ""}</span>
      <span class="memory__res ${cls}">${word}</span></li>`;
  };
  const items = rounds.slice().reverse().map((r) => line(r, false));
  if (inRound && (cur.died_to || cur.my_kills || cur.team_kills || cur.enemy_kills)) {
    // The round in progress takes side, site and operator from the live state until it ends.
    const s = currentState;
    items.unshift(line({ ...cur, side: cur.side || s.side, site: cur.site || s.site, operator: cur.operator || s.my_operator }, true));
  }
  const killers = Object.entries(m.killers_of_me || {});
  if (killers.length && killers[0][1] > 1) items.push(`<li class="memory__empty">Got you most: ${esc(killers[0][0])} ×${killers[0][1]}</li>`);
  $("memory").innerHTML = items.join("") || '<li class="memory__empty">Rounds appear here as they finish.</li>';
}

// The live map and the setup briefs are in livemap.js (loadLayout, mapFocus, mapState, mapView).

// ------------------------------------------------------------------ events
let lastRead = null;
function handle(ev, live = true) {
  const d = ev.data || {};
  switch (ev.topic) {
    case "state.updated": renderState(d.state); renderScreenPill(d.state && d.state.screen); mapState(d.state); break;
    case "map.focus": mapFocus(d, live); break;
    case "screen.changed": renderScreenPill(d.screen); refreshMemory(); break;
    case "coach.said": coachSaid(ev, live); nowSaid(ev, live); break;
    case "coach.done": if (live) stopSpeaking(); break;
    case "coach.thinking": if (live) setThinking(true, d.question ? "Thinking about your question" : "Thinking"); break;
    case "error": addError(ev.ts, `${d.source || "error"}: ${d.message || ""}`); break;
    case "vision.read": lastRead = ev; renderRead(); break;
    case "llm.usage": renderCost(d.total_cost, lastStatus ? lastStatus.calls : null); break;
    case "ptt.ready": if (lastStatus) { lastStatus.ptt_ready = true; renderStatus(lastStatus); } break;
    case "ptt.listening": if (live) setListening(true); break;
    case "ptt.question": if (live) setListening(false); addLine(ev.ts, esc("You: " + (d.text || "")), "is-you"); break;
    case "status": if (live) renderStatus({ ...lastStatus, ...d }); break;   // old ones in history are stale
    case "match.started":
      if (live) { addLine(ev.ts, "New match started.", "is-note"); mapReset(); }
      refreshMemory(); break;
    case "match.ended":
      if (live) {
        const s = d.state || {};
        addLine(ev.ts, esc(`Match over${s.map ? " on " + s.map : ""}: ${s.score_us ?? 0} – ${s.score_them ?? 0}.`), "is-note");
      }
      refreshMemory(); break;
    case "round.ended": case "player.died": case "killfeed": refreshMemory(); break;
  }
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { wsDown = false; if (lastStatus) renderStatus(lastStatus); };
  ws.onmessage = (m) => { try { handle(JSON.parse(m.data)); } catch (e) { console.warn("bad event", e); } };
  ws.onclose = () => { wsDown = true; if (lastStatus) renderStatus(lastStatus); stopSpeaking(); setTimeout(connect, 2000); };
}

// ------------------------------------------------------------------ controls
async function control(action, body) {
  try { const st = await post("/api/control/" + action, body); renderStatus(st); return st; } catch (e) { console.warn(action, e); return null; }
}
$("btn-mute").onclick = () => control("mute");
$("btn-repeat").onclick = () => control("repeat");
$("btn-skip").onclick = () => control("skip");
$("btn-pause").onclick = () => control("pause");
$("btn-force").onclick = () => control("force_read");
$("ask-form").onsubmit = (e) => {
  e.preventDefault();
  const t = $("ask-input").value.trim();
  if (!t) return;
  $("ask-input").value = "";
  control("ask", { text: t });
};

// ------------------------------------------------------------------ what the coach sees
const READ_FIELDS = [
  ["screen", "Screen", (v) => sentence(v)], ["map", "Map"], ["side", "Side", sideName], ["site", "Site"],
  ["site_options", "Possible sites"], ["spawn", "Spawn"], ["round_number", "Round", null, true],
  ["timer_seconds", "Timer", mmss, true], ["my_location", "Room"], ["my_operator", "You"],
  ["teammate_operators", "Team"], ["enemy_operators", "Enemy ops seen"], ["banned_operators", "Bans"],
  ["map_ban_options", "Map options"], ["team_reinforcements", "Reinforcements", null, true],
];
function renderRead() {
  if (!lastRead) return;
  const r = (lastRead.data || {}).read || {};
  const conf = r.confidence != null ? ` · confidence ${Number(r.confidence).toFixed(2)}` : "";
  $("read-when").textContent = `Read at ${clock(lastRead.ts)}${conf}`;
  const rows = [];
  for (const [k, label, fmt, num] of READ_FIELDS) {
    const v = r[k];
    if (v == null || (Array.isArray(v) && !v.length)) continue;
    const html = Array.isArray(v) ? tags(v).html : esc(fmt ? fmt(v) : v);
    rows.push(`<dt>${label}</dt><dd class="${num ? "rc-num" : ""}">${html}</dd>`);
  }
  if (r.score_us != null || r.score_them != null)
    rows.push(`<dt>Score</dt><dd class="rc-num"><span class="us">${esc(r.score_us ?? "–")}</span> – <span class="them">${esc(r.score_them ?? "–")}</span></dd>`);
  if (r.alive_us != null || r.alive_them != null)
    rows.push(`<dt>Players</dt><dd class="rc-num"><span class="us">${esc(r.alive_us ?? "–")}</span> v <span class="them">${esc(r.alive_them ?? "–")}</span></dd>`);
  if (r.notes) rows.push(`<dt>Notes</dt><dd>${esc(r.notes)}</dd>`);
  $("read-list").innerHTML = rows.join("");
  $("read-raw").textContent = JSON.stringify(r, null, 1);
}
function refreshFrame() {
  const img = new Image();
  img.onload = () => { $("frame").src = img.src; $("frame").hidden = false; $("frame-empty").hidden = true; };
  img.onerror = () => { $("frame").hidden = true; $("frame-empty").hidden = false; };
  img.src = "/api/frame.jpg?t=" + Date.now();
}

// ------------------------------------------------------------------ settings
const settings = { pool: { attack: [], defense: [], not_owned: [] }, operators: [], bySide: {}, loaded: false };
let settingsDirty = false;
function markDirty() {
  settingsDirty = true;
  const m = $("settings-msg");
  clearTimeout(m._t);
  m.textContent = "Unsaved changes";
  m.classList.add("is-dirty");
}

const HANDLE_SVG = '<svg viewBox="0 0 12 16" aria-hidden="true" focusable="false"><g fill="currentColor"><circle cx="3" cy="3" r="1.4"/><circle cx="9" cy="3" r="1.4"/><circle cx="3" cy="8" r="1.4"/><circle cx="9" cy="8" r="1.4"/><circle cx="3" cy="13" r="1.4"/><circle cx="9" cy="13" r="1.4"/></g></svg>';
function renderPool(key, focus) {
  const list = settings.pool[key];
  const el = $("pool-" + key);
  if (key === "not_owned") {
    el.innerHTML = list.map((name, i) => `<li class="chip">${esc(name)}<button class="rc-btn rc-btn--quiet" type="button" data-act="remove" data-i="${i}" aria-label="Remove ${esc(name)}">Remove</button></li>`).join("")
      || '<li class="pool__empty">None. You own everyone.</li>';
  } else {
    el.innerHTML = list.map((name, i) => `<li class="pool__row" draggable="true" data-i="${i}">
        <span class="pool__handle" title="Drag to reorder">${HANDLE_SVG}</span>
        <span class="pool__rank">${i + 1}</span>
        <span class="pool__name">${esc(name)}</span>
        <span class="pool__btns">
          <button class="rc-btn rc-btn--quiet" type="button" data-act="up" data-i="${i}" aria-label="Move ${esc(name)} up" ${i === 0 ? "disabled" : ""}>Up</button>
          <button class="rc-btn rc-btn--quiet" type="button" data-act="down" data-i="${i}" aria-label="Move ${esc(name)} down" ${i === list.length - 1 ? "disabled" : ""}>Down</button>
          <button class="rc-btn rc-btn--quiet" type="button" data-act="remove" data-i="${i}" aria-label="Remove ${esc(name)}">Remove</button>
        </span></li>`).join("")
      || `<li class="pool__empty">No ${key === "attack" ? "attackers" : "defenders"} yet. Add your best one first.</li>`;
  }
  if (focus) {
    const b = el.querySelector(`[data-act="${focus.act}"][data-i="${focus.i}"]`) || el.querySelector(`[data-act="remove"][data-i="${Math.min(focus.i, list.length - 1)}"]`);
    if (b && !b.disabled) b.focus(); else if (b) (el.querySelector(`[data-i="${focus.i}"] [data-act="${focus.act === "up" ? "down" : "up"}"]`) || $("add-" + key)).focus();
    else $("add-" + key).focus();
  }
}
function move(key, from, to) {
  const list = settings.pool[key];
  if (to < 0 || to >= list.length || from === to) return;
  const [x] = list.splice(from, 1);
  list.splice(to, 0, x);
  markDirty();
}
["attack", "defense", "not_owned"].forEach((key) => {
  const el = $("pool-" + key);
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const i = +b.dataset.i;
    const list = settings.pool[key];
    if (b.dataset.act === "remove") {
      const name = list[i];
      list.splice(i, 1); markDirty(); renderPool(key, { act: "remove", i });
      flash($("msg-" + key), `Removed ${name}.`);
    } else if (b.dataset.act === "up") { move(key, i, i - 1); renderPool(key, { act: "up", i: i - 1 }); }
    else if (b.dataset.act === "down") { move(key, i, i + 1); renderPool(key, { act: "down", i: i + 1 }); }
  });
  if (key === "not_owned") return;
  // Drag to reorder (mouse). Up and Down do the same from the keyboard.
  let dragFrom = null;
  el.addEventListener("dragstart", (e) => {
    const row = e.target.closest(".pool__row");
    if (!row) return;
    dragFrom = +row.dataset.i;
    row.classList.add("is-dragging");
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", settings.pool[key][dragFrom]); } catch (err) { /* old browsers */ }
  });
  const clearMarks = () => el.querySelectorAll(".is-drop-before,.is-drop-after,.is-dragging").forEach((r) => r.classList.remove("is-drop-before", "is-drop-after", "is-dragging"));
  el.addEventListener("dragover", (e) => {
    if (dragFrom === null) return;
    const row = e.target.closest(".pool__row");
    if (!row) return;
    e.preventDefault();
    const after = e.clientY > row.getBoundingClientRect().top + row.offsetHeight / 2;
    el.querySelectorAll(".is-drop-before,.is-drop-after").forEach((r) => r.classList.remove("is-drop-before", "is-drop-after"));
    row.classList.add(after ? "is-drop-after" : "is-drop-before");
  });
  el.addEventListener("drop", (e) => {
    if (dragFrom === null) return;
    const row = e.target.closest(".pool__row");
    e.preventDefault();
    if (row) {
      let to = +row.dataset.i + (row.classList.contains("is-drop-after") ? 1 : 0);
      if (to > dragFrom) to -= 1;
      move(key, dragFrom, to);
    }
    dragFrom = null; clearMarks(); renderPool(key);
  });
  el.addEventListener("dragend", () => { dragFrom = null; clearMarks(); });
});
document.querySelectorAll(".pool__add").forEach((form) => {
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const key = form.dataset.for;
    const input = form.querySelector("input");
    const msg = $("msg-" + key);
    const raw = input.value.trim();
    if (!raw) return;
    const known = key === "not_owned" ? settings.operators : settings.bySide[key] || [];
    const all = settings.operators;
    const norm = (s) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
    let name = known.find((o) => norm(o) === norm(raw));
    if (!name && all.length) {
      const other = all.find((o) => norm(o) === norm(raw));
      msg.classList.add("is-bad");
      msg.textContent = other ? `${other} is ${key === "attack" ? "a defender" : "an attacker"}.` : `No operator called "${raw}". Pick one from the list.`;
      return;
    }
    name = name || raw;
    msg.classList.remove("is-bad");
    if (settings.pool[key].some((o) => norm(o) === norm(name))) { flash(msg, `${name} is already on the list.`); return; }
    settings.pool[key].push(name);
    input.value = "";
    markDirty();
    renderPool(key);
    flash(msg, `Added ${name}.`);
  });
});

function chattinessRows(ch) {
  $("chattiness").innerHTML = Object.entries(PHASES).map(([k, p]) => `
    <div class="rc-field">
      <label class="rc-field__label" for="chat-${k}">${icon(p.icon)} ${esc(p.label)}</label>
      <input class="rc-range" type="range" min="0" max="3" step="1" id="chat-${k}" data-k="${k}" value="${ch[k] ?? 1}">
      <span class="rc-field__value" id="chat-${k}-v">${ch[k] ?? 1}</span>
    </div>`).join("");
  $("chattiness").querySelectorAll("input").forEach((i) => {
    i.addEventListener("input", () => { $(`chat-${i.dataset.k}-v`).textContent = i.value; markDirty(); });
  });
}
function renderVolume() { $("volume-value").textContent = Math.round(+$("volume").value * 100); }
$("volume").addEventListener("input", () => { renderVolume(); markDirty(); });
$("skill").addEventListener("change", markDirty);

async function loadSettings() {
  const s = await api("/api/settings");
  settings.operators = s.operators || [];
  settings.bySide = s.operators_by_side || {};
  settings.pool = { attack: [...s.operator_pool.attack], defense: [...s.operator_pool.defense], not_owned: [...s.not_owned] };
  const opts = (list) => list.map((o) => `<option value="${esc(o)}">`).join("");
  $("ops-attack").innerHTML = opts(settings.bySide.attack || settings.operators);
  $("ops-defense").innerHTML = opts(settings.bySide.defense || settings.operators);
  $("ops-all").innerHTML = opts(settings.operators);
  ["attack", "defense", "not_owned"].forEach((k) => renderPool(k));
  $("skill").value = s.skill_level;
  $("volume").value = s.volume;
  renderVolume();
  chattinessRows(s.chattiness || {});
  $("models").textContent = `Coach model: ${s.coach_model}. Screen reading: ${s.vision_model}. Change them in config.yaml.`;
  const v = s.voice || {};
  const providerName = { openai: "OpenAI", elevenlabs: "ElevenLabs", kokoro: "Kokoro (on this PC)", sapi: "Windows (offline)" }[v.provider] || sentence(v.provider || "unknown");
  const vrow = (k, val) => `<dt>${k}</dt><dd class="${val ? "" : "rc-empty"}">${val ? esc(val) : "–"}</dd>`;
  $("voice-list").innerHTML = vrow("Provider", providerName) + vrow("Voice", v.voice) + vrow("Model", v.model)
    + vrow("Output", v.output_device || "System default") + vrow("Push-to-talk key", s.ptt_key);
  settings.loaded = true;
  settingsDirty = false;
}
async function loadSetup() {
  try {
    const items = await api("/api/setup");
    $("checklist").innerHTML = items.map((it) => `<li>
      <span class="check__state ${it.ok ? "ok" : "missing"}">${it.ok ? "Ready" : "Missing"}</span>
      <span><span class="check__label">${esc(it.label)}</span><span class="check__hint">${esc(it.hint || "")}</span></span></li>`).join("");
  } catch (e) { $("checklist").innerHTML = '<li><span class="check__state missing">Error</span><span class="check__hint">Could not load the checklist.</span></li>'; }
}
$("btn-save-settings").onclick = async () => {
  const chattiness = {};
  $("chattiness").querySelectorAll("input").forEach((i) => { chattiness[i.dataset.k] = +i.value; });
  try {
    await post("/api/settings", {
      operator_pool: { attack: settings.pool.attack, defense: settings.pool.defense },
      not_owned: settings.pool.not_owned, chattiness, skill_level: $("skill").value, volume: +$("volume").value,
    });
    settingsDirty = false;
    flash($("settings-msg"), "Saved.");
    loadSetup();
  } catch (e) {
    $("settings-msg").textContent = "Couldn't save. Is the coach still running?";
  }
};
$("btn-test-voice").onclick = async () => {
  const st = await control("test_voice");
  const msg = $("test-voice-msg");
  if (!st) flash(msg, "Couldn't reach the coach.");
  else if (st.test_voice === "muted") flash(msg, "The coach is muted. Unmute to hear the test.", 4000);
  else flash(msg, settingsDirty ? "Playing a test line. Save to hear the new volume." : "Playing a test line.", 4000);
};
window.addEventListener("beforeunload", (e) => { if (settingsDirty || notesDirty) { e.preventDefault(); e.returnValue = ""; } });

// ------------------------------------------------------------------ map notes
let notesDirty = false;
let mapId = null;
async function loadMaps() {
  const maps = await api("/api/maps");
  $("map-select").innerHTML = maps.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join("");
  if (!maps.length) { $("map-sites").innerHTML = '<p class="hint-sm">No map data yet.</p>'; return; }
  const want = (currentState.map || "").toLowerCase();
  const pick = maps.find((m) => m.id === want || m.name.toLowerCase() === want) || maps[0];
  $("map-select").value = pick.id;
  showMap(pick.id);
}
async function showMap(id) {
  const m = await api("/api/maps/" + encodeURIComponent(id));
  mapId = id;
  const plan = (side, label) => side && side.plan_summary
    ? `<p class="site__line"><b>${label}:</b> ${esc(side.plan_summary)}</p>` : "";
  const ops = (side, label) => side && side.recommended_ops && side.recommended_ops.length
    ? `<p class="site__line">${label}: ${esc(side.recommended_ops.join(", "))}</p>` : "";
  $("map-sites").innerHTML = (m.data.sites || []).map((s) => `<div class="site">
      <h3 class="site__name">${icon("objective")}${esc(s.name)}</h3>
      ${plan(s.defender, "Defence")}${ops(s.defender, "Defenders")}
      ${plan(s.attacker, "Attack")}${ops(s.attacker, "Attackers")}</div>`).join("") || '<p class="hint-sm">No sites listed for this map.</p>';
  $("notes").value = m.notes || "";
  notesDirty = false;
  $("notes-msg").textContent = "";
}
$("map-select").onchange = (e) => {
  if (notesDirty && !confirm("Discard your unsaved notes for this map?")) { e.target.value = mapId; return; }
  showMap(e.target.value);
};
$("notes").addEventListener("input", () => {
  notesDirty = true;
  $("notes-msg").textContent = "Unsaved changes";
  $("notes-msg").classList.add("is-dirty");
});
$("btn-save-notes").onclick = async () => {
  try {
    await post(`/api/maps/${encodeURIComponent(mapId)}/notes`, { notes: $("notes").value });
    notesDirty = false;
    flash($("notes-msg"), "Saved.");
  } catch (e) { $("notes-msg").textContent = "Couldn't save the notes."; }
};

// ------------------------------------------------------------------ matches
async function loadMatches() {
  let ms = [];
  try { ms = await api("/api/matches"); } catch (e) { /* show the empty state */ }
  const res = (m) => (m.won == null ? null : !!m.won);
  $("matches").innerHTML = ms.map((m) => {
    const r = res(m);
    const when = m.started ? new Date(m.started * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "–";
    const score = m.score_us == null && m.score_them == null ? "–" : `${m.score_us ?? "–"} – ${m.score_them ?? "–"}`;
    return `<tr><td>${esc(when)}</td><td class="${m.map ? "" : "muted"}">${esc(m.map || "Unknown map")}</td>
      <td class="rc-num">${esc(score)}</td><td class="${r === true ? "rc-win" : r === false ? "rc-loss" : "muted"}">${r === true ? "Win" : r === false ? "Loss" : "–"}</td>
      <td><button class="rc-btn rc-btn--quiet" data-report="${esc(m.id)}">View report</button></td></tr>`;
  }).join("") || '<tr class="rc-empty-row"><td colspan="5">No matches recorded yet.</td></tr>';
  $("matches").querySelectorAll("[data-report]").forEach((b) => { b.onclick = () => showReport(b.dataset.report); });

  // Recent form: one bar per match, oldest on the left. Height follows the round margin.
  const old = ms.slice().reverse();
  $("form-strip").innerHTML = old.map((m) => {
    const r = res(m);
    const margin = Math.abs((m.score_us ?? 0) - (m.score_them ?? 0));
    const h = Math.min(80, 20 + margin * 12);
    const word = r === true ? "Win" : r === false ? "Loss" : "No result";
    return `<span class="bar ${r === true ? "won" : r === false ? "lost" : ""}" style="height:${h}px" title="${esc(m.map || "Unknown map")}: ${word}"></span>`;
  }).join("");
  const w = ms.filter((m) => res(m) === true).length;
  const l = ms.filter((m) => res(m) === false).length;
  $("form-summary").innerHTML = ms.length
    ? `Last ${ms.length}: <span class="us">${w} won</span> · <span class="them">${l} lost</span>`
    : "No matches recorded yet.";
  $("form-strip").setAttribute("aria-label", ms.length ? `Last ${ms.length} matches: ${w} won, ${l} lost` : "No matches recorded yet");
}

async function showReport(id) {
  let m;
  try { m = await api(`/api/matches/${encodeURIComponent(id)}`); } catch (e) { return; }
  const result = m.won == null ? "No result" : m.won ? "Win" : "Loss";
  const score = m.score_us == null ? "–" : `${m.score_us} – ${m.score_them}`;
  $("match-report-head").textContent = `${m.map || "Unknown map"} · ${score} · ${result}`;
  $("match-report-takeaways").innerHTML = (m.takeaways || []).map((t) => `<li>${esc(t)}</li>`).join("")
    || '<li class="muted">No takeaways for this match.</li>';
  const side = (s) => (s === "attack" ? "Attack" : s === "defense" ? "Defence" : "–");
  $("match-report-rounds").innerHTML = (m.rounds || []).map((r, i) => {
    const how = [];
    if (r.first_blood) how.push(`first blood ${r.first_blood === "us" ? "yours" : "theirs"}`);
    if (r.my_kills != null) how.push(`${r.my_kills} kill${r.my_kills === 1 ? "" : "s"}`);
    how.push(r.died_to ? `died to ${r.died_to}${r.died_in ? " in " + r.died_in : ""}` : "survived");
    if (r.planted) how.push("defuser planted");
    if ((r.enemy_ops || []).length) how.push(`they ran ${r.enemy_ops.join(", ")}`);
    const res = r.won == null ? "–" : r.won ? "Won" : "Lost";
    return `<tr><td class="rc-num">${esc(r.round_number || i + 1)}</td><td>${esc(side(r.side))} · ${esc(r.site || "site unknown")}</td>
      <td>${esc(r.operator || "–")}</td><td class="${r.won ? "rc-win" : r.won === false ? "rc-loss" : "muted"}">${res}</td>
      <td>${esc(how.join(", "))}</td></tr>`;
  }).join("") || '<tr class="rc-empty-row"><td colspan="5">No rounds recorded for this match.</td></tr>';
  const card = $("match-report");
  card.hidden = false;
  card.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  $("match-report-title").focus?.();
}

// ------------------------------------------------------------------ boot
async function boot() {
  selectTab(location.hash.slice(1) || "live");
  await loadIcons();
  try { renderStatus(await api("/api/status")); } catch (e) { console.warn("status", e); }
  try {
    for (const ev of await api("/api/history")) if (ev.topic !== "state.updated") handle(ev, false);
  } catch (e) { console.warn("history", e); }
  connect();
  refreshMemory(0);
  setInterval(async () => { try { renderStatus(await api("/api/status")); } catch (e) { /* reconnect handles it */ } }, 5000);
  loadSettings().catch((e) => console.warn("settings", e));
  loadMaps().catch((e) => console.warn("maps", e));
  initLiveMap();
}
boot();
