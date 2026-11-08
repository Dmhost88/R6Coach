"""In-round tactical calls from Claude, driven by HUD events.

At the moments that matter (action starts, the player count changes, a teammate drops, the
defuser goes down, 1:00 and 0:30 left) the coach asks Claude for ONE specific call for the next
10-20 seconds. Only HUD state goes in (no screenshot), so a call comes back in a few seconds.

Talking less (live-moment review, 2026-11-07: 45 s of speech per round alive, half of it right
after a kill, the same instruction 3+ times in 13 of 22 rounds):
- kills and count changes wait KILL_SETTLE seconds with no further kill, and one kill's several
  triggers become one call;
- the check-in fires only when the player has moved to another room since the last call;
- the last calls of the round go into the prompt, and Claude returns no line when nothing changed;
- calls are cut to LIVE_WORDS words in code and dropped when they restate a recent call;
- the gap between calls counts from the last call spoken, not from when it was asked for.

Fair play: the prompt contains only what the player's own HUD showed (timer, counts, plant, their
room, killfeed, team pings) plus match history. It never asks where enemies are; calls are about
what the player should do, not reactions to enemies they haven't seen.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from .bus import Bus, Event
from .config import Config
from .knowledge import Knowledge
from .llm import Claude
from .models import Screen
from .spoken import cap_words, too_similar
from .twin import render

log = logging.getLogger(__name__)

TIMER_MARKS = (120, 60, 30)
STALE_AFTER = 9.0   # seconds; a call that arrives later than this is dropped
KILL_SETTLE = 5.0   # seconds with no further kill before a kill or count change gets a call
LIVE_WORDS = 14     # hard cap on an in-round call, in words (coach.live_words overrides it)
RECENT = 3          # calls of this round shown to Claude so it doesn't repeat them

LIVE_TASK = """IN-ROUND CALL. Give ONE spoken line (max 14 words): the single most useful thing the \
player should do in the next 10-20 seconds, given exactly this state. Say WHERE in callouts: a \
route from the player's current room ("from 1F Bar, through 1F Fireplace Hall, to the Library \
stairs") or an exact spot (room plus landmark, and what to watch). Never "the plant spot", \
"default" or "site" on its own. It must fit the side, time, plant status and player counts. Reason about what usually happens at this point of a round on this site. Never describe \
where enemies are unless they are listed as pinged/spotted. No praise, no filler."""


SPECTATE_TASK = """SPECTATOR CALL. The player is DEAD and watching the rest of the round; they cannot act. Give ONE \
spoken line (max 14 words) that teaches them something from this moment: what the surviving \
teammates should do here and why, or how this situation relates to how the player died. Talk about \
the team in the third person ("Kali should plant in Master corner"), never as an instruction to \
the player. Name an exact location. Never describe where enemies are unless they are listed as \
pinged/spotted. No filler."""


class TacticalCoach:
    def __init__(self, cfg: Config, bus: Bus, claude: Claude, kb: Knowledge, say: Callable[..., None],
                 state: Callable, memory, system: Callable[..., list[str]]) -> None:
        self.cfg = cfg
        self.bus = bus
        self.claude = claude
        self.kb = kb
        self.say = say
        self.state = state
        self.memory = memory
        self.system = system
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tactical")
        self._lock = threading.Lock()
        self._busy = False
        self._last_call = 0.0
        self._round = 0
        self._marks_done: set[int] = set()
        self._dead_teammates = 0
        self._recent: list[str] = []          # calls spoken this round, oldest first
        self._last_room: Optional[str] = None  # the player's room at the last call
        self._last_kill = 0.0
        self._pending: Optional[tuple[str, bool]] = None   # (reason, important) waiting for the fight to settle
        bus.subscribe("live.event", self._on_live)
        bus.subscribe("screen.changed", self._on_screen)

    def enabled(self) -> bool:
        return self.cfg.coach.level("action_phase") >= 2 and self.claude.budget_level() == "ok"

    def _gap(self) -> float:
        g = self.cfg.coach.live_min_gap
        return g if self.cfg.coach.level("action_phase") >= 3 else g * 2

    # ------------------------------------------------------------------ triggers
    def _on_screen(self, ev: Event) -> None:
        if ev.data.get("screen") == Screen.action_phase.value:
            threading.Timer(3.0, self._trigger, args=("action phase just started", True)).start()

    def _on_live(self, ev: Event) -> None:
        kind = ev.data.get("kind")
        if kind == "round_reset":
            with self._lock:
                self._round += 1
                self._marks_done.clear()
                self._dead_teammates = 0
                self._last_call = 0.0
                self._recent = []
                self._last_room = None
                self._last_kill = 0.0
                self._pending = None
        elif kind == "timer":
            t = ev.data["seconds"]
            if self.state().screen != Screen.action_phase:
                return
            for mark in TIMER_MARKS:
                if t <= mark and mark not in self._marks_done and t > mark - 8:
                    self._marks_done.add(mark)
                    self._trigger(f"{mark // 60}:{mark % 60:02d} left on the clock")
                    return
            # Check in after a quiet spell, but only once the player has moved: a new room is a
            # new situation; the same room is the same call again (the review's repetition engine).
            checkin = self.cfg.coach.live_checkin
            s = self.state()
            if (checkin and s.player_alive and self._last_call and s.my_location
                    and s.my_location != self._last_room and time.time() - self._last_call >= checkin):
                self._trigger(f"check-in: {t}s left, the player has moved to {s.my_location}")
        elif kind == "vs":
            self._settle(f"player count just changed to {ev.data['alive_us']} v {ev.data['alive_them']}")
        elif kind == "planted":
            self._trigger("the defuser was just planted", important=True)
        elif kind == "found_bombs":
            self._trigger("the attackers just found the bombs (site known to them)")
        elif kind == "kill":
            self._last_kill = time.time()
            if ev.data.get("mine"):
                self._settle(f"the player just killed {ev.data.get('victim')}: say what to do with the advantage",
                             important=True)
            elif ev.data.get("victim_team") == "us":
                self._settle(f"teammate {ev.data.get('victim')} was just killed by {ev.data.get('killer')}")
            elif ev.data.get("killer_team") == "us":
                self._settle(f"teammate {ev.data.get('killer')} just killed {ev.data.get('victim')}")

    # ------------------------------------------------------------------ fights settle first
    def _settle(self, reason: str, important: bool = False) -> None:
        """A kill or a count change: wait until KILL_SETTLE seconds pass with no further kill, then
        make one call about the latest of them. A trade fires 2-3 events; they become one call."""
        with self._lock:
            prev = self._pending
            # The player's own kill outranks a count change that came with it.
            if prev and prev[1] and not important:
                reason, important = prev
            self._pending = (reason, important)
            stamp = self._last_kill = max(self._last_kill, time.time())
        threading.Timer(KILL_SETTLE, self._settled, args=(stamp,)).start()

    def _settled(self, stamp: float) -> None:
        with self._lock:
            if self._last_kill > stamp or self._pending is None:
                return   # a newer kill restarted the wait; its timer makes the call
            reason, important = self._pending
            self._pending = None
        self._trigger(reason, important=important)

    def _trigger(self, reason: str, important: bool = False) -> None:
        if not self.enabled():
            return
        s = self.state()
        if s.screen not in (Screen.action_phase, Screen.spectating):
            return
        now = time.time()
        gap = self._gap() * (1.0 if s.player_alive else 1.5)   # a bit calmer while spectating
        with self._lock:
            if self._busy or (not important and now - self._last_call < gap):
                return
            self._busy = True
            self._last_call = now
            rnd = self._round
        self.pool.submit(self._call, reason, now, rnd)

    # ------------------------------------------------------------------ the call
    def _call(self, reason: str, started: float, rnd: int) -> None:
        try:
            s = self.state()
            alive = s.player_alive
            with self._lock:
                recent = list(self._recent[-RECENT:])
            said = ""
            if recent:
                said = ("\nAlready said this round, oldest first:\n" + "\n".join(f"- {r}" for r in recent) +
                        "\nDon't repeat or rephrase these. If none of them is out of date and nothing new "
                        "needs saying, reply with an empty lines list: silence beats a repeat.\n")
            prompt = (
                f"{LIVE_TASK if alive else SPECTATE_TASK}\n\nWhat just happened: {reason}.\n{said}"
                f"{render(s, None, self.memory.text() if self.memory else '')}\n"
                f"Focus: site {s.site or 'not known yet'} (full map dossier in the system prompt)."
            )
            out = self.claude.coach(self.cfg.coach.model_for("live"), self.cfg.coach.live_effort, self.system(s.map), prompt,
                                    purpose="coach.live")
            if not out or not out.lines:
                return
            now_s = self.state()
            if (time.time() - started > STALE_AFTER or rnd != self._round
                    or now_s.screen not in (Screen.action_phase, Screen.spectating)
                    or (alive and not now_s.player_alive)):
                log.info("dropping stale live call (%.1fs)", time.time() - started)
                return
            line = cap_words(out.lines[0], self.cfg.coach.live_words or LIVE_WORDS)
            if not line:
                return
            with self._lock:
                if any(too_similar(line, r) for r in self._recent):
                    log.info("dropping live call that repeats this round: %r", line)
                    return
                self._recent.append(line)
                self._last_room = now_s.my_location
                self._last_call = time.time()   # the gap counts from the call spoken, not asked for
            self.say(line, priority=1, ttl=8.0, phase="action_phase")
        except Exception:
            log.exception("live call failed")
        finally:
            with self._lock:
                self._busy = False
