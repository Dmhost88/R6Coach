"""In-round tactical calls from Claude, driven by HUD events.

At the moments that matter (action starts, the player count changes, a teammate drops, the
defuser goes down, 1:00 and 0:30 left) the coach asks Claude for ONE specific call for the next
10-20 seconds. Only HUD state goes in (no screenshot), so a call comes back in a few seconds.

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

log = logging.getLogger(__name__)

TIMER_MARKS = (120, 60, 30)
STALE_AFTER = 9.0   # seconds; a call that arrives later than this is dropped

LIVE_TASK = """IN-ROUND CALL. Give ONE spoken line (max 14 words): the single most useful thing the \
player should do in the next 10-20 seconds, given exactly this state. It must name a specific \
location (room, wall, hatch, doorway or angle) and fit the side, time, plant status and player \
counts. Reason about what usually happens at this point of a round on this site. Never describe \
where enemies are unless they are listed as pinged/spotted. No praise, no filler."""


SPECTATE_TASK = """SPECTATOR CALL. The player is dead and watching the rest of the round. Give ONE \
spoken line (max 16 words) that teaches them something from this moment: what the surviving \
teammates should do here and why, or how this situation relates to how the player died. Name an \
exact location. Never describe where enemies are unless they are listed as pinged/spotted. No filler."""


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
        elif kind == "timer":
            t = ev.data["seconds"]
            if self.state().screen != Screen.action_phase:
                return
            for mark in TIMER_MARKS:
                if t <= mark and mark not in self._marks_done and t > mark - 8:
                    self._marks_done.add(mark)
                    self._trigger(f"{mark // 60}:{mark % 60:02d} left on the clock")
        elif kind == "vs":
            self._trigger(f"player count just changed to {ev.data['alive_us']} v {ev.data['alive_them']}")
        elif kind == "planted":
            self._trigger("the defuser was just planted", important=True)
        elif kind == "kill" and ev.data.get("victim_team") == "us" and not ev.data.get("mine"):
            self._trigger(f"teammate {ev.data.get('victim')} was just killed by {ev.data.get('killer')}")

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
            pinged = ", ".join(p.location for p in s.pinged_enemies) or "none"
            prompt = (
                f"{LIVE_TASK if alive else SPECTATE_TASK}\n\nWhat just happened: {reason}.\n"
                f"State: map {s.map}, side {s.side}, site {s.site or 'unknown'}, "
                f"timer {s.timer_seconds}s, players {s.alive_us} v {s.alive_them}, "
                f"defuser planted: {s.defuser_planted}, player is {s.my_operator or 'unknown op'} "
                f"in {s.my_location or 'unknown room'} with {s.health or '?'} health, "
                f"score {s.score_us}-{s.score_them}.\n"
                f"Teammates: {', '.join(s.teammate_operators) or 'unknown'}. "
                f"Enemy operators revealed: {', '.join(s.enemy_operators) or 'none'}. "
                f"Pinged/spotted by the team: {pinged}.\n"
                f"{self.memory.text() if self.memory else ''}\n"
                f"Focus: site {s.site or 'not known yet'} (full map dossier in the system prompt)."
            )
            out = self.claude.coach(self.cfg.coach.model, self.cfg.coach.live_effort, self.system(s.map), prompt,
                                    purpose="coach.live")
            if not out or not out.lines:
                return
            now_s = self.state()
            if (time.time() - started > STALE_AFTER or rnd != self._round
                    or now_s.screen not in (Screen.action_phase, Screen.spectating)
                    or (alive and not now_s.player_alive)):
                log.info("dropping stale live call (%.1fs)", time.time() - started)
                return
            self.say(out.lines[0], priority=1, ttl=8.0, phase="action_phase")
        except Exception:
            log.exception("live call failed")
        finally:
            with self._lock:
                self._busy = False
