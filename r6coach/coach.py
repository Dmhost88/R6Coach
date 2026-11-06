"""The coach: turns game-state events into short spoken advice.

v1 coaches the pre-round (map ban, operator ban, operator pick, prep-phase setup) and answers
push-to-talk questions. Live-round HUD coaching arrives in v2 and plugs into the same events.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .bus import Bus, Event
from .config import Config
from .knowledge import Knowledge
from .llm import Claude, CoachLines
from .models import PRE_ROUND, GameState, Screen
from .profile import Profile
from .voice.tts import Speaker

log = logging.getLogger(__name__)

LINES_FOR_LEVEL = {1: "1-2", 2: "2-4", 3: "4-6"}

# Screens on which advice for a phase is still worth saying when it arrives.
STILL_RELEVANT = {
    "map_ban": {Screen.map_ban},
    "operator_ban": {Screen.operator_ban},
    "operator_select": {Screen.operator_select, Screen.loadout},
    "prep_phase": {Screen.prep_phase},
}

SKILL_HINTS = {
    "new": "The player is new: name rooms plainly, explain what a gadget does when you mention it.",
    "intermediate": "The player knows the operators and basic map layouts. Skip basics; focus on setups, timing and positioning.",
    "advanced": "The player is experienced. Use standard callouts, be terse, give pro-level specifics only.",
}

SYSTEM = """You are a calm, professional Rainbow Six Siege coach speaking into the player's headset \
during a Ranked match on PC. The player mostly solo queues, so advice must work with random teammates.

How you speak:
- Every line is read aloud by text-to-speech. Plain spoken English, no markdown, no lists, no emojis.
- Each line at most 14 words. Most important line first. Calm, even, confident; never hype.
- Use exact in-game room and site callouts. Say operator names normally.
- Be concrete: which wall, which hatch, which gadget, where to stand, what to do first.

Fair play (hard rules):
- You only know what the player could see on their own screen and what an experienced player \
would have memorised (maps, setups, operator kits, the meta).
- Never claim to know enemy positions unless the state lists them as pinged by the player's team.
- Never suggest exploits, glitches or anything against Ubisoft's rules.

Knowledge:
- Prefer the provided map data and the player's own notes. If the data does not cover something, \
use general Siege knowledge, and keep it generic rather than inventing specific callouts.
- Operator recommendations must come from the player's pool unless the team clearly needs a role \
nobody in the pool covers. Never recommend a banned operator, one a teammate already locked, or one \
the player does not own."""


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
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="coach")
        self._lock = threading.Lock()
        self._done: set[str] = set()            # advice keys already given this match
        self._recommended: Optional[str] = None
        self._round_seq = 0
        self._prep_started: dict[int, float] = {}
        self._last_screen = Screen.other
        self.last_lines: list[str] = []

        bus.subscribe("match.started", self._on_match_started)
        bus.subscribe("match.ended", self._on_match_ended)
        bus.subscribe("round.ended", self._on_round_ended)
        bus.subscribe("state.updated", self._on_state)
        bus.subscribe("ptt.question", self._on_question)
        bus.subscribe("coach.said", self._on_said)

    # ------------------------------------------------------------------ event handlers
    def _on_match_started(self, ev: Event) -> None:
        with self._lock:
            self._done.clear()
            self._recommended = None
        self.profile.match_started(ev.data["match_id"])

    def _on_match_ended(self, ev: Event) -> None:
        st = ev.data["state"]
        self.profile.match_ended(st.get("match_id"), st)

    def _on_round_ended(self, ev: Event) -> None:
        self.profile.round_ended(ev.data["state"], ev.data.get("won"))
        self.speaker.clear()

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
        self._last_screen = s.screen
        rnd = self._round_seq
        lv = self.cfg.coach.level
        if s.screen == Screen.map_ban and lv("map_ban") and s.map_ban_options and self._once("map_ban"):
            self._submit("map_ban", self._advise_map_ban, s)
        elif s.screen == Screen.operator_ban and lv("operator_ban") and s.map and self._once(f"op_ban:{rnd}"):
            # Siege now runs an operator ban phase before every round.
            self._submit("operator_ban", self._advise_op_ban, s)
        elif s.screen == Screen.operator_select and lv("operator_select") and s.side:
            # Defenders need the site for a useful pick: wait one extra read for it.
            if s.side == "defense" and not s.site and self._once(f"pick_wait:{rnd}"):
                return
            if self._once(f"pick:{rnd}"):
                self._submit("operator_select", self._advise_pick, s)
            elif (self._recommended and self._recommended in s.teammate_operators
                  and self._once(f"repick:{rnd}")):
                self._submit("operator_select", self._advise_pick, s, True)
        elif s.screen == Screen.prep_phase and lv("prep_phase") and f"prep:{rnd}" not in self._done:
            # Attackers find the site with drones ("BOMBS FOUND"); wait up to 15s of prep for it.
            started = self._prep_started.setdefault(rnd, time.time())
            if s.side == "attack" and not s.site and time.time() - started < 15:
                return
            if self._once(f"prep:{rnd}"):
                self._submit("prep_phase", self._advise_prep, s)

    def _on_question(self, ev: Event) -> None:
        self.bus.publish("coach.thinking", question=ev.data["text"])
        self.pool.submit(self._safe_answer, ev.data["text"])

    def _safe_answer(self, question: str) -> None:
        try:
            self._answer(question)
        except Exception:
            log.exception("answering failed")

    # ------------------------------------------------------------------ dispatch
    def _submit(self, phase: str, fn, state: GameState, *args) -> None:
        self.bus.publish("coach.thinking", phase=phase)
        self.pool.submit(self._run, phase, fn, state.model_copy(deep=True), *args)

    def _run(self, phase: str, fn, state: GameState, *args) -> None:
        try:
            out: Optional[CoachLines] = fn(state, *args)
        except Exception:
            log.exception("coach %s failed", phase)
            return
        if out is None:
            return
        now = self.current_state().screen
        # Drop advice that arrives after the moment has passed.
        if now not in STILL_RELEVANT.get(phase, {Screen(phase)}):
            log.info("dropping stale %s advice (now %s)", phase, now.value)
            return
        if out.recommended_operator:
            self._recommended = out.recommended_operator
        self.speak(out.lines, phase)
        self.bus.publish("coach.advice", phase=phase, advice=out.model_dump())

    def speak(self, lines: list[str], phase: str, priority: int = 2, ttl: float = 40.0) -> None:
        self.last_lines = lines
        for line in lines:
            self.speaker.say(line, priority=priority, ttl=ttl, phase=phase)

    def repeat(self) -> None:
        if self.last_lines:
            self.speak(self.last_lines, "repeat", priority=1)

    # ------------------------------------------------------------------ prompts
    def _system(self) -> str:
        return SYSTEM + "\n\n" + SKILL_HINTS.get(self.cfg.coach.skill_level, SKILL_HINTS["intermediate"])

    def _ask(self, phase: str, prompt: str, image: bool = False) -> Optional[CoachLines]:
        n = LINES_FOR_LEVEL.get(self.cfg.coach.level(phase), "2-4")
        prompt += f"\n\nReply with {n} spoken lines."
        jpeg = self.current_jpeg() if image else None
        return self.claude.coach(self.cfg.coach.model, self.cfg.coach.effort, self._system(), prompt,
                                 purpose=f"coach.{phase}", image_jpeg=jpeg)

    def _pool_text(self, side: Optional[str], state: GameState) -> str:
        pool = self.cfg.operator_pool.attack if side == "attack" else self.cfg.operator_pool.defense
        return (
            f"Player's operator pool ({side}), best first: {', '.join(pool) or 'not set'}\n"
            f"Not owned: {', '.join(self.cfg.not_owned) or 'none'}\n"
            f"Banned this match: {', '.join(state.banned_operators) or 'unknown'}\n"
            f"Teammates' picks so far: {', '.join(state.teammate_operators) or 'none yet'}\n"
            f"Operator data:\n{self.kb.operators_context(pool + state.teammate_operators)}"
        )

    def _advise_map_ban(self, s: GameState) -> Optional[CoachLines]:
        rates = self.profile.map_win_rates()
        record = ", ".join(f"{m}: {w}/{n} rounds won" for m, (w, n) in rates.items()) or "no history yet"
        prompt = (
            f"MAP BAN phase. Maps on screen: {', '.join(s.map_ban_options)}.\n"
            f"Player's round history by map: {record}\n"
            f"Player's pools: attack {self.cfg.operator_pool.attack}, defense {self.cfg.operator_pool.defense}\n"
            "Recommend which map to ban (and which to keep), with a one-line reason. Put the map to ban "
            "in recommended_bans."
        )
        return self._ask("map_ban", prompt, image=True)

    def _advise_op_ban(self, s: GameState) -> Optional[CoachLines]:
        prompt = (
            f"OPERATOR BAN phase before round {s.round_number or '?'} on {s.map}. Bans happen every round. "
            f"Player side this round: {s.side or 'unknown'}. Score {s.score_us}-{s.score_them}.\n"
            f"Bans visible so far: {', '.join(s.banned_operators) or 'none'}\n"
            f"Possible bomb sites this round: {' | '.join(s.site_options) or 'unknown'}\n"
            f"Player's pools: attack {self.cfg.operator_pool.attack}, defense {self.cfg.operator_pool.defense}\n"
            f"Map data:\n{self.kb.context_for(s.map, None, None)}\n"
            "The screen shows which side the player's team is banning (e.g. 'BAN A DEFENDER'). Recommend "
            "the one ban for that side that hurts the enemy most on this map, avoiding the player's own pool. "
            "Put it in recommended_bans."
        )
        return self._ask("operator_ban", prompt, image=True)

    def _advise_pick(self, s: GameState, repick: bool = False) -> Optional[CoachLines]:
        head = "A teammate just took the operator you recommended. " if repick else ""
        prompt = (
            f"{head}OPERATOR SELECT, round {s.round_number or '?'}, {s.map}, player is {s.side}. "
            f"Site: {s.site or 'not known yet'}. Possible sites: {' | '.join(s.site_options) or 'unknown'}. "
            f"Score {s.score_us}-{s.score_them}.\n"
            f"{self._pool_text(s.side, s)}\n"
            f"Map data:\n{self.kb.context_for(s.map, s.site, s.side)}\n"
            f"{self.profile.map_record(s.map)}\n"
            "Recommend the player's pick for this team composition (set recommended_operator), say why in "
            "a few words, and name the gadget or loadout choice that matters."
        )
        return self._ask("operator_select", prompt, image=True)

    def _advise_prep(self, s: GameState) -> Optional[CoachLines]:
        if s.side == "defense":
            reinf = (f" The team has {s.team_reinforcements} reinforcements left."
                     if s.team_reinforcements is not None else "")
            task = ("PREP PHASE on defense. Tell the player exactly what to do in the next 45 seconds as "
                    f"{s.my_operator or 'their operator'}: what to reinforce or open, where to place their "
                    f"gadget, and where to set up when action starts.{reinf}")
        else:
            task = ("PREP PHASE on attack. Tell the player how to use the drone phase and their plan as "
                    f"{s.my_operator or 'their operator'}: drone route, entry point from the spawn, what "
                    "utility to save for the site, and where to plant.")
        prompt = (
            f"{task}\nMap {s.map}, site {s.site or 'unknown'} "
            f"(possible: {' | '.join(s.site_options) or 'unknown'}), spawn {s.spawn or 'unknown'}, "
            f"player is in {s.my_location or 'unknown room'}, "
            f"round {s.round_number or '?'}, score {s.score_us}-{s.score_them}.\n"
            f"Teammates: {', '.join(s.teammate_operators) or 'unknown'}\n"
            f"Enemy operators revealed so far: {', '.join(s.enemy_operators) or 'none'}\n"
            f"Map data:\n{self.kb.context_for(s.map, s.site, s.side)}"
        )
        return self._ask("prep_phase", prompt)

    def _answer(self, question: str) -> None:
        s = self.current_state()
        # Screenshots go to the cloud only on static screens, never mid-round.
        static = s.screen not in (Screen.prep_phase, Screen.action_phase, Screen.spectating)
        pinged = ", ".join(p.location + (f" ({p.operator})" if p.operator else "") for p in s.pinged_enemies)
        prompt = (
            f'The player asks: "{question}"\n'
            f"Current state: {s.summary()}\n"
            f"Enemies pinged by the team: {pinged or 'none'}\n"
            f"{self._pool_text(s.side, s)}\n"
            f"Map data:\n{self.kb.context_for(s.map, s.site, s.side)}\n"
            "Answer directly for this exact moment. If it's mid-round, keep it very short."
        )
        started = time.time()
        out = self._ask("question", prompt, image=static)
        if out is None:
            self.speaker.say("Couldn't get an answer. Try again.", priority=0, phase="question")
            return
        self.last_lines = out.lines
        for i, line in enumerate(out.lines):
            self.speaker.say(line, priority=0 if i == 0 else 1, ttl=30, phase="question")
        self.bus.publish("coach.advice", phase="question", question=question, advice=out.model_dump(),
                         seconds=round(time.time() - started, 2))
