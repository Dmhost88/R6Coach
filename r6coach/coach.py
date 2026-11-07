"""The coach: turns game-state events into short spoken advice.

v1 coaches the pre-round (map ban, operator ban, operator pick, prep-phase setup) and answers
push-to-talk questions. Live-round HUD coaching arrives in v2 and plugs into the same events.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .bus import Bus, Event
from .config import Config
from .knowledge import Knowledge
from .llm import Claude, CoachLines
from .models import PRE_ROUND, GameState, Screen
from .profile import Profile
from .rounds import first_side_from, side_for
from .twin import render
from .voice.tts import Speaker

log = logging.getLogger(__name__)

LINES_FOR_LEVEL = {1: "1-2", 2: "2-4", 3: "3-5"}
# Debriefs have a fixed shape (good / bad / fix), whatever the chattiness.
FIXED_LINES = {"death": "3", "between_rounds": "2-3", "match_end": "3"}

# Screens on which advice for a phase is still worth saying when it arrives.
STILL_RELEVANT = {
    "map_ban": {Screen.map_ban},
    "operator_ban": {Screen.operator_ban},
    "operator_select": {Screen.operator_select, Screen.loadout},
    "prep_phase": {Screen.prep_phase},
    "question": set(Screen),
}

SKILL_HINTS = {
    "new": "The player is new: name rooms plainly, explain what a gadget does when you mention it.",
    "intermediate": "The player knows every operator and the main callouts. Skip basics; focus on "
                    "setups, timing and positioning.",
    "advanced": "The player is high-ranked. Assume they know every operator and kit; skip basics and give "
                "the non-obvious calls that change this round, the way a tier-1 analyst briefs a pro. "
                "Do NOT assume they know where a setup, plant or hold is on this map: always spell the "
                "location out in callouts (see Locations).",
}

SYSTEM = """You are a tier-1 Rainbow Six Siege analyst coaching one player through their headset \
during a Ranked match on PC. The player mostly solo queues, so plans must work without a coordinated team.

What makes a line worth saying:
- Every line names something concrete: a specific wall, hatch, window, angle, timing, operator \
interaction or utility trade. "Reinforce the Kids' Dorms divider and leave the hatch soft" is a \
line. "Reinforce your site", "drone first", "play with your team", "use your utility" are not: never \
say anything that would apply to any map, site or round.
- Adapt to THIS match: the score, the enemy operators revealed this round and earlier rounds, who has \
been killing the player and where, what won and lost previous rounds. If something keeps failing, \
say what to change. If the enemy keeps running an operator, plan around it (counter-pick, ban, or \
avoid its angle).
- Think about the enemy: what they're likely to run on this site given what they've shown, and the \
one thing that beats it.
- Prefer one sharp insight over a full plan. Most important line first.

Reasoning discipline (accuracy matters more than sounding smart):
- Check every gadget interaction against the OPERATOR DATABASE before you say it: what a gadget \
does, what counters it, and what it can or cannot jam, destroy or slow. If the database doesn't \
support the claim, don't make it.
- Plan around enemy operators that were actually seen this match, plus ones that are common on \
this site. Don't build a plan around an operator nobody has shown.
- Never state an inference as a fact. "Ela has been on their team every round" is a fact; \
"Ela usually roams Big Tower here" is an inference and must say "usually" or "likely".
- When there is match history, at least one line must use it (the score situation, a repeated \
death, what the enemy keeps running) and say what to do differently because of it.

Locations must be exact. "The main wall", "site walls", "the hatch" or "Laundry walls" are not \
locations:
- Name every wall by the two rooms it separates, or by the room and the side it faces: "the wall \
between Laundry and Supply", "the Kids' Dorms wall facing Big Tower".
- Name every hatch by the floor it's in and what's below: "the hatch in Kids' Dorms floor above \
Kitchen".
- Name angles from where to where: "hold Supply door from behind the shelves, looking at Laundry stairs".
- Name floors when it isn't obvious: "2F", "B".
- Every move is a route in the game's own room callouts (the names shown under the compass and in \
the map dossier's Callouts list), starting from where the player is now: "from EXT Campfire, go through \
EXT Cliffside, up the Library stairs into 2F Library". Two or three hops, each a real callout.
- Every spot is a room plus a landmark inside it: "plant in 2F Master Bedroom, behind the bed in \
the corner by the Master Bathroom door", not "the default plant", "the plant spot", "the usual \
spot", "site" or "anchor position". Never say "default", "standard" or "usual spot" without naming \
it like this.
- Every hold names where to stand and what to watch: "hold 2F Office from behind the desk, \
watching the Library doorway".

How you speak:
- Lines are read aloud by text-to-speech. Plain spoken English, no markdown, no lists, no emojis.
- Each line at most 24 words: room for a route or spot in callouts. Calm, even, confident; never \
hype, never filler.
- Use the in-game callouts. Numbers as digits.
- Never talk about yourself, your data or your limits ("I don't have map data", "as a coach"). \
Just give the call.

Knowledge:
- The map data below is a starting point; the player's own notes override it. Where the data is \
thin or generic, use your own expert knowledge of the current map versions: specific setups, \
reinforcement patterns, rotation holes, common roams and pro executes. Only name rooms, walls and \
hatches you are confident exist on the current version of the map; if unsure of an exact name, \
describe it by landmark ("the wall between Dorms and the stairs").
- Operator recommendations come from the player's pool unless the team needs a role no pool \
operator covers. Never recommend a banned operator, one a teammate already locked, or one the \
player does not own. Bans listed in the match state are unavailable; when the screen shows this \
round's bans, the screen wins.
- Answer with the call only. Never reason in the lines themselves: no questions to yourself, no \
"No.", no talk about the screen, the match state or the data. They are spoken aloud as written.
- The player's own operator is given separately from the teammates. The player IS that operator: \
speak to them as "you" ("you charge the Office wall", "your charges"), never "your Thermite", \
"your breacher" or "your entry" as if it were someone else; never treat it as a teammate's pick \
or tell them to support it from another operator. Name teammates by operator ("Kali lances it").

Fair play (hard rules):
- You know only what the player could see on their own screen plus what an experienced player \
would have memorised (maps, setups, kits, the meta, tendencies seen earlier this match).
- Never claim to know where enemies are right now unless the state lists them as pinged or \
spotted by the player's team. Reasoning about where enemies usually play is fine; say "usually".
- Never suggest exploits, glitches or anything against Ubisoft's rules."""


class Coach:
    def __init__(self, cfg: Config, bus: Bus, claude: Claude, knowledge: Knowledge, speaker: Speaker,
                 profile: Profile, current_state, current_jpeg) -> None:
        self.cfg = cfg
        self.bus = bus
        self.claude = claude
        self.kb = knowledge
        self.speaker = speaker
        self.profile = profile
        self.current_state = current_state      # () -> GameState
        self.current_jpeg = current_jpeg        # () -> Optional[bytes]
        self.memory = None                      # MatchMemory, set by the app
        self._tls = threading.local()           # per-worker flag: was this answer streamed/spoken?
        self._rooms_map: Optional[str] = None
        self._rooms_cache: list[str] = []
        # 4 workers: a slow debrief must never hold up the ban or pick advice behind it.
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="coach")
        self._lock = threading.Lock()
        self._done: set[str] = set()            # advice keys already given this match
        self._recommended: Optional[str] = None
        # Advice generation per phase. Bumped when the state advice was built on changes (the player
        # locked another operator, the next round's picks began): in-flight streams stop, queued
        # lines are dropped and late answers are discarded.
        self._gen: dict[str, int] = defaultdict(int)
        self._planned: dict[int, str] = {}      # round -> the operator pick/prep advice is about
        self._ban_ahead: Optional[tuple] = None  # (round number, Future) ban advice made at round end
        self._pick_pending: set[int] = set()    # rounds whose pick advice is still being written
        self._round_seq = 0
        self._prep_started: dict[int, float] = {}
        self._pick_started: dict[int, float] = {}
        self._prep_cache: dict[int, tuple] = {}   # round -> (operator, Future of a defense prep plan)
        self._last_screen = Screen.other
        self.last_lines: list[str] = []

        bus.subscribe("match.started", self._on_match_started)
        bus.subscribe("match.ended", self._on_match_ended)
        bus.subscribe("round.ended", self._on_round_ended)
        bus.subscribe("state.updated", self._on_state)
        bus.subscribe("ptt.question", self._on_question)
        bus.subscribe("coach.said", self._on_said)
        bus.subscribe("player.died", self._on_died)
        bus.subscribe("memory.round", self._on_memory_round)

    # ------------------------------------------------------------------ event handlers
    def _on_match_started(self, ev: Event) -> None:
        with self._lock:
            self._done.clear()
            self._recommended = None
            self._ban_ahead = None
            self._rooms_map = None          # re-read rooms seen for the new match's map
        self.profile.match_started(ev.data["match_id"])

    def _on_match_ended(self, ev: Event) -> None:
        st = ev.data["state"]
        self.profile.match_ended(st.get("match_id"), st)
        if self.memory is not None and self.memory.rounds and self._allowed("match_end"):
            state = self.current_state().model_copy(deep=True)
            self.pool.submit(self._run, "match_end", self._advise_match_end, state)

    def _on_memory_round(self, ev: Event) -> None:
        """MatchMemory finished a round: store it with its full detail."""
        st = dict(ev.data.get("state") or {})
        st.setdefault("match_id", self.current_state().match_id)
        self.profile.round_ended(st, ev.data.get("won"), ev.data.get("record"))

    def _on_round_ended(self, ev: Event) -> None:
        if self.memory is None:   # no match memory: store the basic round directly
            self.profile.round_ended(ev.data["state"], ev.data.get("won"))
        self.speaker.clear()
        if self.cfg.coach.level("between_rounds") and self.memory is not None and self._allowed("between_rounds"):
            # Give match memory a moment to record this round before the debrief reads it.
            state = self.current_state().model_copy(deep=True)
            won = ev.data.get("won")
            threading.Timer(1.5, lambda: self.pool.submit(
                self._run, "between_rounds", self._advise_debrief, state, won)).start()
        if self.cfg.coach.preplan_bans:
            state = self.current_state().model_copy(deep=True)
            threading.Timer(1.5, self._preplan_ban, args=(state,)).start()

    def _preplan_ban(self, s: GameState) -> None:
        """Think the next round's ban through now (map, the side after any swap, bans so far and the
        round just played are all known), so it plays the moment the ban screen opens."""
        if not s.map or not self.cfg.coach.level("operator_ban") or not self._allowed("operator_ban"):
            return
        n = s.round_number or max(self._round_seq, 1)
        nxt = n + 1
        side = None
        if s.side:
            first = first_side_from(n, s.side, self.cfg.swap_after)
            side = side_for(nxt, first, self.cfg.swap_after) if first else None
        ahead = s.model_copy(deep=True, update={
            "round_number": nxt, "side": side, "side_source": "predicted" if side else None,
            "my_operator": None, "teammate_operators": [], "enemy_operators": [], "site": None,
            "site_options": [], "spawn": None, "my_location": None, "health": None, "timer_seconds": None,
            "alive_us": None, "alive_them": None, "defuser_planted": False, "player_alive": True,
            "pinged_enemies": [], "screen": Screen.operator_ban})
        self._ban_ahead = (nxt, self.pool.submit(self._safe, self._advise_op_ban, ahead, False))
        log.info("pre-planning the round %d ban (%s)", nxt, side or "side unknown")

    def _on_died(self, ev: Event) -> None:
        if not self.cfg.coach.level("action_phase") or not self._allowed("death"):
            return
        # In-round calls queued for a live player are wrong now: drop them.
        if hasattr(self.speaker, "clear"):
            self.speaker.clear("action_phase")
        killer = ev.data.get("killer")
        kill_cam = ev.data.get("source") != "spectating"
        state = self.current_state().model_copy(deep=True)
        # From the killfeed, wait for the kill cam: it shows the killer's operator card and angle.
        # Spotted only once spectating, the kill cam is already over: go now.
        threading.Timer(1.5 if kill_cam else 0.0, lambda: self.pool.submit(
            self._run, "death", self._advise_death, state, killer, kill_cam)).start()

    def _on_said(self, ev: Event) -> None:
        self.profile.log_line(self.current_state().match_id, ev.data.get("phase", ""), ev.data["text"])

    def _once(self, key: str) -> bool:
        """True the first time a key is seen this match."""
        with self._lock:
            if key in self._done:
                return False
            self._done.add(key)
            return True

    def _on_state(self, ev: Event) -> None:
        s = self.current_state()
        # The round number isn't always readable, so count rounds ourselves: a new round starts
        # when the ban phase or operator select appears after anything other than pre-round menus.
        if s.screen in (Screen.operator_ban, Screen.operator_select) and self._last_screen not in PRE_ROUND:
            self._round_seq += 1
            self._recommended = None
            # The next round's bans and picks are on: a debrief still talking would bury them.
            self._bump("between_rounds", "death")
        self._last_screen = s.screen
        rnd = self._round_seq
        lv = self.cfg.coach.level
        if s.screen == Screen.map_ban and lv("map_ban") and s.map_ban_options and self._once("map_ban"):
            self._submit("map_ban", self._advise_map_ban, s)
        elif s.screen == Screen.operator_ban and lv("operator_ban") and s.map and self._once(f"op_ban:{rnd}"):
            # Siege now runs an operator ban phase before every round.
            ahead, self._ban_ahead = self._ban_ahead, None
            if ahead and s.round_number in (None, ahead[0]) and not ahead[1].cancelled():
                # Thought through at round end: play it now, or the moment it's ready.
                g = self._gen["operator_ban"]
                if not ahead[1].done():
                    self.bus.publish("coach.thinking", phase="operator_ban")
                ahead[1].add_done_callback(lambda f: self._deliver("operator_ban", f.result(), gen=g))
            else:
                self._submit("operator_ban", self._advise_op_ban, s)
        elif s.screen == Screen.operator_select and lv("operator_select") and s.side:
            # Defenders need the site for a useful pick: give the detail read up to 4 s to bring it.
            # (Time, not a count of updates: the HUD publishes one every second.)
            first_seen = self._pick_started.setdefault(rnd, time.time())
            if s.side == "defense" and not s.site and time.time() - first_seen < 4.0:
                return
            if self._once(f"pick:{rnd}"):
                self._submit("operator_select", self._advise_pick, s)
            elif (self._recommended and self._recommended in s.teammate_operators
                  and (s.my_operator or "").lower() != self._recommended.lower()
                  and self._once(f"repick:{rnd}")):
                self._submit("operator_select", self._advise_pick, s, True)
        elif s.screen == Screen.prep_phase and lv("prep_phase") and f"prep:{rnd}" not in self._done:
            # Attackers find the site with drones ("BOMBS FOUND"); wait up to 15s of prep for it.
            started = self._prep_started.setdefault(rnd, time.time())
            if s.side == "attack" and not s.site and time.time() - started < 15:
                return
            if self._once(f"prep:{rnd}"):
                cached = self._prep_cache.get(rnd)
                if s.side == "defense" and cached and s.my_operator in (None, cached[0]):
                    # Made during operator select with deeper reasoning: play it now (or when ready).
                    g = self._gen["prep_phase"]
                    cached[1].add_done_callback(lambda f: self._deliver("prep_phase", f.result(), gen=g))
                else:
                    self._submit("prep_phase", self._advise_prep, s)

        if s.screen in (Screen.operator_select, Screen.loadout, Screen.prep_phase) and s.my_operator:
            self._check_lock(s, rnd)

        # Defense knows the site at operator select: think the prep plan through while they pick,
        # so it plays the moment prep starts.
        if (s.screen in (Screen.operator_select, Screen.loadout) and s.side == "defense" and s.site
                and lv("prep_phase") and self._allowed("prep_phase")):
            op = s.my_operator or self._recommended
            if op and self._once(f"preplan:{rnd}:{op}"):
                plan_state = s.model_copy(deep=True, update={"my_operator": op})
                # Made ahead of time, spoken later: no streaming (live=False).
                self._prep_cache[rnd] = (op, self.pool.submit(self._safe, self._advise_prep, plan_state, True, False))

    # ------------------------------------------------------------------ pivots
    def _bump(self, *phases: str) -> None:
        """Advice for these phases is out of date: stop it wherever it is."""
        with self._lock:
            for p in phases:
                self._gen[p] += 1
        if hasattr(self.speaker, "cancel"):
            self.speaker.cancel(*phases)

    def _stale(self, phase: str, gen: Optional[int]) -> bool:
        return gen is not None and self._gen[phase] != gen

    def _check_lock(self, s: GameState, rnd: int) -> None:
        """The player's locked operator is known. If the pick or prep advice is about a different
        one (or is still being written and can't know), drop it and advise for what they took."""
        op = s.my_operator
        planned = self._planned.get(rnd) or self._recommended
        if planned and planned.lower() == op.lower():
            self._planned[rnd] = op
            return
        if not planned and rnd not in self._pick_pending:
            self._planned[rnd] = op            # no pick advice given: nothing to correct
            return
        if not self._once(f"locked:{rnd}:{op.lower()}"):
            return
        log.info("player locked %s (advice was for %s): pivoting", op, planned or "a pick still being written")
        self._planned[rnd] = op
        self._pick_pending.discard(rnd)
        self._prep_cache.pop(rnd, None)
        if s.screen == Screen.prep_phase:
            # Picks are over: replace the prep plan with one for the operator they're actually on.
            self._bump("prep_phase")
            self._submit("prep_phase", self._advise_prep, s)
        else:
            self._bump("operator_select", "prep_phase")
            self._submit("operator_select", self._advise_locked, s, planned)

    def _on_question(self, ev: Event) -> None:
        self.bus.publish("coach.thinking", question=ev.data["text"])
        self.pool.submit(self._safe_answer, ev.data["text"])

    def _safe_answer(self, question: str) -> None:
        try:
            self._answer(question)
        except Exception:
            log.exception("answering failed")

    # ------------------------------------------------------------------ dispatch
    # Under the per-match budget: 'tight' drops nice-to-haves, 'spent' keeps only bans and picks.
    _BUDGET_KEEP = {"tight": {"map_ban", "operator_ban", "operator_select", "prep_phase", "question", "match_end"},
                    "spent": {"map_ban", "operator_ban", "operator_select", "question"}}

    def _allowed(self, phase: str) -> bool:
        level = self.claude.budget_level() if hasattr(self.claude, "budget_level") else "ok"
        return level == "ok" or phase in self._BUDGET_KEEP[level]

    def _submit(self, phase: str, fn, state: GameState, *args) -> None:
        if not self._allowed(phase):
            log.info("skipping %s: per-match budget", phase)
            return
        self.bus.publish("coach.thinking", phase=phase)
        if fn == self._advise_pick:
            self._pick_pending.add(self._round_seq)
        # The generation is taken now: a pivot while this waits in the pool still cancels it.
        self.pool.submit(self._run, phase, fn, state.model_copy(deep=True), *args, gen=self._gen[phase])

    def _safe(self, fn, *args) -> Optional[CoachLines]:
        try:
            return fn(*args)
        except Exception:
            log.exception("coach call failed")
            return None

    def _run(self, phase: str, fn, state: GameState, *args, gen: Optional[int] = None) -> None:
        gen = self._gen[phase] if gen is None else gen
        self._tls.streamed = False
        self._tls.gen = (phase, gen)
        try:
            if self._stale(phase, gen):
                return
            out = self._safe(fn, state, *args)
            self._deliver(phase, out, spoken=getattr(self._tls, "streamed", False), gen=gen)
        finally:
            self._tls.gen = None
            if fn == self._advise_pick:
                self._pick_pending.discard(self._round_seq)

    def _relevant(self, phase: str) -> bool:
        """Is advice for this phase still worth saying right now?"""
        now = self.current_state().screen
        if phase in ("between_rounds", "match_end"):
            return now not in (Screen.prep_phase, Screen.action_phase)
        if phase == "death":
            # Still useful while spectating or between rounds; not once the next round's prep starts.
            return now != Screen.prep_phase and not (now == Screen.action_phase
                                                     and self.current_state().player_alive)
        return now in STILL_RELEVANT.get(phase, {Screen(phase)})

    def _deliver(self, phase: str, out: Optional[CoachLines], spoken: bool = False,
                 gen: Optional[int] = None) -> None:
        """spoken=True: the lines were already spoken one by one as they streamed in."""
        if out is None:
            return
        if self._stale(phase, gen):
            log.info("dropping %s advice: the state it was for changed", phase)
            return
        if not spoken and not self._relevant(phase):
            # Drop advice that arrives after the moment has passed.
            log.info("dropping stale %s advice (now %s)", phase, self.current_state().screen.value)
            return
        if phase == "operator_select" and out.recommended_operator:
            # Only a pick is a recommendation to lock; a ban or prep plan naming an operator isn't.
            self._recommended = out.recommended_operator
        if not spoken:
            self.speak(out.lines, phase)
        self.bus.publish("coach.advice", phase=phase, advice=out.model_dump())

    def _speak_streamed(self, phase: str):
        """Returns an on_line callback that speaks each streamed line if it's still relevant."""
        first = {"done": False}
        tag = getattr(self._tls, "gen", None)
        gen = tag[1] if tag and tag[0] == phase else None

        def on_line(text: str) -> None:
            if self._stale(phase, gen):
                return
            if phase != "question" and not self._relevant(phase):
                return
            if phase == "question":
                priority = 0 if not first["done"] else 1
            else:
                priority = self._priority(phase)
            if not first["done"]:
                self.last_lines = []
            first["done"] = True
            self.last_lines.append(text)
            self.speaker.say(text, priority=priority, ttl=40.0 if phase != "question" else 30.0, phase=phase)
        return on_line

    @staticmethod
    def _priority(phase: str) -> int:
        """Ban and pick advice has a short window: it goes ahead of queued debrief lines."""
        return 1 if phase in ("map_ban", "operator_ban", "operator_select") else 2

    def speak(self, lines: list[str], phase: str, priority: Optional[int] = None, ttl: float = 40.0) -> None:
        self.last_lines = lines
        priority = self._priority(phase) if priority is None else priority
        for line in lines:
            self.speaker.say(line, priority=priority, ttl=ttl, phase=phase)

    def repeat(self) -> None:
        if self.last_lines:
            self.speak(self.last_lines, "repeat", priority=1)

    # ------------------------------------------------------------------ prompts
    def _system(self, map_name: Optional[str] = None) -> list[str]:
        """System blocks, most stable first, each cached: the brief, the operator database, and the
        current map's dossier."""
        brief = SYSTEM + "\n\n" + SKILL_HINTS.get(self.cfg.coach.skill_level, SKILL_HINTS["intermediate"])
        m = map_name or self.current_state().map
        # Rooms seen are refreshed once per match (not per call) so the cached dossier stays stable.
        if m != self._rooms_map:
            self._rooms_map = m
            self._rooms_cache = self.profile.rooms_seen(m) if hasattr(self.profile, "rooms_seen") else []
        return [brief, self.kb.operators_dossier(), self.kb.map_dossier(m, self._rooms_cache)]

    @staticmethod
    def _focus(s: GameState) -> str:
        return (f"Focus: {s.map or 'unknown map'}, {s.side or 'unknown side'}, "
                f"site {s.site or 'not known yet'} (full map dossier in the system prompt).")

    def _ask(self, phase: str, prompt: str, image: bool = False, deep: bool = False,
             map_name: Optional[str] = None, live: bool = True,
             effort: Optional[str] = None) -> Optional[CoachLines]:
        """live=True: the answer is spoken now, so stream it and start speaking at line 1."""
        level = self.cfg.coach.level(phase)
        n = FIXED_LINES.get(phase) or LINES_FOR_LEVEL.get(level, "2-4")
        if phase == "between_rounds" and phase in self.cfg.coach.chattiness and level == 1:
            n = "1"   # quiet setting: one line between rounds, so it never runs into the ban phase
        prompt += f"\n\nReply with {n} spoken lines."
        jpeg = self.current_jpeg() if image else None
        effort = effort or (self.cfg.coach.deep_effort if deep else self.cfg.coach.effort)
        system = self._system(map_name)
        model = self.cfg.coach.model_for(phase)
        if live and self.cfg.coach.stream and hasattr(self.claude, "coach_stream"):
            self._tls.streamed = True
            tag = getattr(self._tls, "gen", None)
            gen = tag[1] if tag and tag[0] == phase else None
            return self.claude.coach_stream(model, effort, system, prompt,
                                            purpose=f"coach.{phase}", image_jpeg=jpeg,
                                            on_line=self._speak_streamed(phase),
                                            cancelled=lambda: self._stale(phase, gen))
        return self.claude.coach(model, effort, system, prompt,
                                 purpose=f"coach.{phase}", image_jpeg=jpeg)

    def _pool_text(self, side: Optional[str], state: GameState) -> str:
        pool = self.cfg.operator_pool.attack if side == "attack" else self.cfg.operator_pool.defense
        return (
            f"Player's operator pool ({side}), best first: {', '.join(pool) or 'not set'}\n"
            f"Not owned: {', '.join(self.cfg.not_owned) or 'none'}\n"
            f"Operator data:\n{self.kb.operators_context(pool + state.teammate_operators)}"
        )

    def _state(self, s: GameState, memory: bool = True) -> str:
        """The one rendering of the match state every prompt uses (see twin.py)."""
        return render(s, self._recommended, self._memory() if memory else "")

    def _advise_map_ban(self, s: GameState) -> Optional[CoachLines]:
        rates = self.profile.map_win_rates()
        record = ", ".join(f"{m}: {w}/{n} rounds won" for m, (w, n) in rates.items()) or "no history yet"
        per_map = "\n".join(f"- {m}: {t}" for m in s.map_ban_options
                            if (t := self.profile.stats_text(m).replace("\n", "; ")))
        prompt = (
            f"MAP BAN phase. Maps on screen: {', '.join(s.map_ban_options)}.\n"
            f"Player's round history by map: {record}\n"
            + (f"Detail for the maps on screen:\n{per_map}\n" if per_map else "")
            + f"Player's pools: attack {self.cfg.operator_pool.attack}, defense {self.cfg.operator_pool.defense}\n"
            "Recommend which map to ban (and which to keep), with a one-line reason grounded in the history "
            "above when there is any. Put the map to ban in recommended_bans."
        )
        return self._ask("map_ban", prompt, image=True)

    def _memory(self) -> str:
        return self.memory.text() if self.memory else ""

    def _advise_op_ban(self, s: GameState, live: bool = True) -> Optional[CoachLines]:
        if s.side in ("attack", "defense"):
            # On attack the screen reads "BAN A DEFENDER"; on defense "BAN AN ATTACKER".
            ban, own = ("a defender", "an attacker") if s.side == "attack" else ("an attacker", "a defender")
            who = (f"The player is on {s.side}, so they ban one of the enemy's operators: name {ban} only, "
                   f"never {own}.")
        else:
            who = ("The player's side isn't known yet: name one attacker to ban if they defend this round and "
                   "one defender to ban if they attack, each in its own line.")
        prompt = (
            f"OPERATOR BAN phase before round {s.round_number or '?'}.\n"
            f"{who}\n"
            f"{self._state(s)}\n"
            f"Player's pools: attack {self.cfg.operator_pool.attack}, defense {self.cfg.operator_pool.defense}\n"
            f"{self._focus(s)}\n"
            "Never recommend an operator that is already banned. Name the one ban "
            "that most hurts the enemy here. Base it on what the enemy has actually run this match if anything, "
            "else on this map's meta. At ban time nobody has picked yet: never plan around specific teammates' "
            "operators or assume who your team will run; reason about the enemy, the possible sites and the "
            "player's own pool only. Say why in one line. Put it in recommended_bans."
        )
        # Mid-match the ban advice starts on the "ROUND n" title card, which shows nothing useful:
        # skip the screenshot (faster, and nothing to misread). Round 1 sends the ban screen.
        first = (s.round_number or 1) <= 1
        return self._ask("operator_ban", prompt, image=first and live, map_name=s.map, live=live)

    def _advise_pick(self, s: GameState, repick: bool = False) -> Optional[CoachLines]:
        head = "A teammate just took the operator you recommended. " if repick else ""
        prompt = (
            f"{head}OPERATOR SELECT.\n"
            f"{self._state(s)}\n"
            f"{self._pool_text(s.side, s)}\n"
            f"{self._focus(s)}\n"
            f"{self._stats(s, s.side)}\n"
            "Pick the operator from the player's pool that fills what this team composition is missing for "
            "this site and counters what the enemy has shown (set recommended_operator). Then give their "
            "concrete job on the most likely site: the exact wall to breach or gadget placement, in what "
            "order, and the one loadout choice that matters."
        )
        return self._ask("operator_select", prompt, image=True, map_name=s.map,
                         effort=self.cfg.coach.pick_effort)

    def _advise_locked(self, s: GameState, planned: Optional[str]) -> Optional[CoachLines]:
        """The player locked a different operator from the one the advice was about."""
        prompt = (
            f"The player just LOCKED {s.my_operator}"
            + (f" (the coach had suggested {planned}; that advice is void, never mention {planned} again)"
               if planned else "") +
            ".\n"
            f"{render(s, None, self._memory())}\n{self._focus(s)}\n"
            f"Don't comment on the choice. Give the player's job as {s.my_operator} this round: the exact "
            "wall, hatch or gadget placement on the most likely site and the route there in callouts, "
            "then the one loadout choice that matters."
        )
        return self._ask("operator_select", prompt, map_name=s.map, effort=self.cfg.coach.effort)

    def _stats(self, s: GameState, side: Optional[str] = None) -> str:
        text = self.profile.stats_text(s.map, side) if hasattr(self.profile, "stats_text") else ""
        return f"Player's own history (facts from past matches):\n{text}" if text else ""

    def _advise_match_end(self, s: GameState) -> Optional[CoachLines]:
        prompt = (
            f"MATCH OVER on {s.map}, final score {s.score_us}-{s.score_them}.\n"
            f"{self._memory()}\n{self._stats(s)}\n"
            "Give the 3 takeaways from this match a coach would write on the whiteboard: one thing that "
            "worked and should be repeated, the biggest recurring mistake (with the exact location or "
            "decision), and one concrete thing to practise before the next match. Use only facts above."
        )
        out = self._ask("match_end", prompt, deep=True, map_name=s.map)
        if out and out.lines:
            self.profile.set_takeaways(s.match_id, out.lines)
        return out

    def _advise_death(self, s: GameState, killer: Optional[str], kill_cam: bool = True) -> Optional[CoachLines]:
        trail = self.memory.death_trail() if self.memory is not None else ""
        me = s.my_operator or "unknown op"
        screenshot = (
            "The screenshot is the kill cam: read the killer's operator card and weapon. Describe the angle "
            "or position they shot from only if it is clearly visible; "
            if kill_cam else
            "The screenshot is the spectator view after the kill cam: it does not show how the player died; "
        )
        prompt = (
            f"THE PLAYER JUST DIED as {me}, killed by {killer or 'unknown'} in "
            f"{s.my_location or 'unknown room'}. State just before the death:\n"
            f"{render(s.model_copy(update={'player_alive': True}), None, self._memory())}\n"
            + (f"The player's last seconds (room and health from their HUD, oldest first): {trail}\n" if trail else "")
            + "If their health was already low, say what they should have done about that first.\n"
            f"{self._focus(s)}\n"
            + screenshot +
            "never invent details (props, open walls, holes) you can't see.\n"
            f"The player IS the {me}: talk to them as 'you' ('you walked Trophy Stairs alone'), never as "
            f"'your {me}' or 'your breacher'.\n"
            "Death debrief, like a coach on comms right after a death: "
            "Line 1: what was good in how the player played the round, if anything was (be honest). "
            "Line 2: the actual mistake: the angle, timing or decision that got them killed, using the room "
            "they died in and the killer's operator. Line 3: the fix next time: an exact spot to play "
            "instead (room plus what to hold from there), and why it beats that operator. If they keep "
            "dying the same way, say so. No consolation, no filler."
        )
        return self._ask("death", prompt, image=True, map_name=s.map)

    def _advise_debrief(self, s: GameState, won: Optional[bool]) -> Optional[CoachLines]:
        result = {True: "WON", False: "LOST", None: "ended"}[won]
        prompt = (
            f"ROUND JUST {result}. State at the end of the round:\n"
            f"{render(s, None, self._memory())}\n"
            f"{self._focus(s)}\n"
            "Round debrief in the gap between rounds. Line 1: what decided the round, from the facts above "
            "(first blood, trades, the plant, where the player died). Line 2: what the player did well, "
            "if anything. Line 3: what to change next round, with an exact location. If something keeps "
            "happening across rounds, name the pattern. No praise inflation, no consolation."
        )
        return self._ask("between_rounds", prompt, deep=True, map_name=s.map)

    def _advise_prep(self, s: GameState, deep: bool = False, live: bool = True) -> Optional[CoachLines]:
        me = s.my_operator or "their operator"
        if s.side == "defense":
            reinf = (f" The team has {s.team_reinforcements} reinforcements left."
                     if s.team_reinforcements is not None else "")
            task = (f"PREP PHASE on defense, player is {me}. Give this round's specific job IN TIME ORDER, one "
                    "step per line: the exact walls or hatches to reinforce or open, exactly where their "
                    "gadget goes and how it works with the team's other utility on that wall, where to stand "
                    "when action starts, and when to rotate or fall back. If the enemy's attackers haven't "
                    f"been seen yet, plan for this site's common attack, and say 'likely'.{reinf}")
        else:
            task = (f"PREP PHASE on attack, player is {me}. Give {me}'s own job this round, IN TIME ORDER, one "
                    "step per line: what to drone for now (and from where a drone can actually see it), the "
                    f"roam to clear first, what {me} does on the entry with their own gadget (not another "
                    "operator's job), then the plant spot and where to hold it. If an enemy operator from "
                    "earlier rounds threatens this site (a camera, trap or anchor that killed the player), say "
                    f"how {me} deals with it.")
        prompt = (
            f"{task}\n"
            f"{render(s, None, self._memory())}\n"
            f"{self._stats(s, s.side)}\n"
            f"{self._focus(s)}"
        )
        return self._ask("prep_phase", prompt, deep=deep, map_name=s.map, live=live)

    def _answer(self, question: str) -> None:
        s = self.current_state()
        # Screenshots go to the cloud only on static screens, never mid-round.
        static = s.screen not in (Screen.prep_phase, Screen.action_phase, Screen.spectating)
        prompt = (
            f'The player asks: "{question}"\n'
            f"Screen now: {s.screen.value}.\n"
            f"{self._state(s)}\n"
            f"{self._pool_text(s.side, s)}\n"
            f"{self._focus(s)}\n"
            "Answer directly for this exact moment. If it's mid-round, keep it very short."
        )
        started = time.time()
        self._tls.streamed = False
        out = self._ask("question", prompt, image=static)
        if out is None or not out.lines:
            self.speaker.say("Couldn't get an answer. Try again.", priority=0, phase="question")
            return
        if not getattr(self._tls, "streamed", False):   # streamed lines were already spoken
            self.last_lines = out.lines
            for i, line in enumerate(out.lines):
                self.speaker.say(line, priority=0 if i == 0 else 1, ttl=30, phase="question")
        self.bus.publish("coach.advice", phase="question", question=question, advice=out.model_dump(),
                         seconds=round(time.time() - started, 2))
