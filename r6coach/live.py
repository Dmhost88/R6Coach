"""Live in-round coaching from the local HUD reader: instant, free, rule-based.

Lines are short and fixed so their audio is pre-generated and plays with no delay. The rules use
only what the HUD shows: timer, plant status, player counts, the killfeed and your own room.

Chattiness (coach.chattiness.action_phase):
  0 silent
  1 essentials: defuser planted, last-player clutch, 20 seconds left
  2 adds: man-advantage changes (once a round each), 45 seconds, reinforce reminder

Dropped after the live-moment review (2026-11-07): "Nice. Reposition." after your kill and "They
know the site" said nothing specific, and the reinforce reminder cut in 15 s after the prep plan had
named the walls; it now stays quiet when a line in the last REINFORCE_QUIET seconds said reinforce.
"""

from __future__ import annotations

import difflib
import logging
import threading
import time
from typing import Callable, Optional

from .bus import Bus, Event
from .config import Config
from .hud import Kill

log = logging.getLogger(__name__)

LINES = {
    "atk_45": "Forty-five seconds. Start your execute.",
    "atk_20": "Twenty seconds left. Get the plant down.",
    "def_planted": "Defuser is down. Go deny it, don't wait.",
    "atk_planted": "Defuser planted. Play it from range.",
    "advantage": "You have the numbers. Trade, don't take duels alone.",
    "disadvantage": "You're down a player. Group up and play together.",
    "clutch": "Last one. Slow down, play your crosshair.",
    "reinforce": "Reinforce now. Walls first.",
}
# (level needed, urgent) per line. Urgent lines skip the cooldown and the gunfight hold.
RULES = {
    "atk_45": (2, False), "atk_20": (1, True), "def_planted": (1, True), "atk_planted": (1, True),
    "advantage": (2, False), "disadvantage": (2, False), "clutch": (1, True),
    "reinforce": (2, False),
}
# Generic lines that Claude's in-round calls replace with specific ones when they're available.
AI_COVERED = {"advantage", "disadvantage", "atk_45"}
COOLDOWN = 5.0     # seconds between non-urgent live lines
GUNFIGHT_HOLD = 3.0  # wait this long after a kill before non-urgent lines
REINFORCE_QUIET = 30.0  # no reinforce reminder if a line this recent already said to reinforce


class KillTracker:
    """Deduplicates killfeed lines (they stay on screen for seconds) and fixes OCR name slips
    against names already seen this match."""

    def __init__(self, player_name: str = "") -> None:
        self.player_name = player_name
        self.names: dict[str, str] = {}   # lower -> canonical
        self.seen: set[tuple[str, str]] = set()
        if player_name:
            self.names[player_name.lower()] = player_name

    def reset_round(self) -> None:
        self.seen.clear()

    def reset_match(self) -> None:
        self.seen.clear()
        self.names = {self.player_name.lower(): self.player_name} if self.player_name else {}

    def _canon(self, name: str) -> str:
        key = name.lower()
        if key in self.names:
            return self.names[key]
        close = difflib.get_close_matches(key, list(self.names), n=1, cutoff=0.8)
        if close:
            return self.names[close[0]]
        self.names[key] = name
        return name

    def new_kills(self, kills: list[Kill]) -> list[Kill]:
        out = []
        for k in kills:
            killer, victim = self._canon(k.killer), self._canon(k.victim)
            if (killer, victim) in self.seen:
                continue
            self.seen.add((killer, victim))
            if k.killer_team == k.victim_team:
                continue  # friendly-fire lines
            out.append(Kill(killer, k.killer_team, victim, k.victim_team, k.headshot))
        return out


class LiveCoach:
    def __init__(self, cfg: Config, bus: Bus, say: Callable[..., None], state: Callable,
                 ai_live: Callable[[], bool] = lambda: False) -> None:
        self.cfg = cfg
        self.ai_live = ai_live    # True when Claude's in-round calls are running
        self.bus = bus
        self.say = say            # say(text, priority, ttl, phase)
        self.state = state        # () -> GameState
        self._said_round: set[str] = set()
        self._last_line = 0.0
        self._last_kill = 0.0
        self._balance = 0
        self._said_reinforce = 0.0
        self._lock = threading.Lock()
        bus.subscribe("live.event", self._on_event)
        bus.subscribe("coach.said", self._on_said)

    def _on_said(self, ev: Event) -> None:
        if "reinforc" in (ev.data.get("text") or "").lower() and ev.data.get("text") != LINES["reinforce"]:
            self._said_reinforce = time.time()

    def reset_round(self) -> None:
        with self._lock:
            self._said_round.clear()
            self._balance = 0
            self._last_line = 0.0
            self._last_kill = 0.0

    def _level(self) -> int:
        return self.cfg.coach.level("action_phase")

    def _emit(self, key: str, once: bool = True) -> None:
        need, urgent = RULES[key]
        if self._level() < need:
            log.debug("fixed line %s skipped: level %d < %d", key, self._level(), need)
            return
        if not self.state().player_alive:
            log.debug("fixed line %s skipped: player dead", key)
            return  # you're spectating: in-round instructions can't help any more
        if key in AI_COVERED and self.ai_live():
            log.debug("fixed line %s skipped: Claude's live calls cover it", key)
            return  # Claude gives a specific version of this call instead
        now = time.time()
        with self._lock:
            if once and key in self._said_round:
                log.debug("fixed line %s skipped: already said this round", key)
                return
            if not urgent:
                if now - self._last_line < COOLDOWN:
                    log.debug("fixed line %s skipped: %.1fs since the last fixed line", key, now - self._last_line)
                    return
                hold = GUNFIGHT_HOLD - (now - self._last_kill)
                if hold > 0:
                    log.debug("fixed line %s held %.1fs for the gunfight", key, hold)
                    threading.Timer(hold, self._emit_late, args=(key, now)).start()
                    return
            self._said_round.add(key)
            self._last_line = now
        log.debug("fixed line %s said%s", key, " (urgent)" if urgent else "")
        self.say(LINES[key], priority=0 if urgent else 1, ttl=6.0, phase="action_phase")

    def _emit_late(self, key: str, queued: float) -> None:
        # Only if no newer kill happened while we held the line.
        if self._last_kill <= queued:
            self._emit(key)

    def _on_event(self, ev: Event) -> None:
        kind = ev.data.get("kind")
        s = self.state()
        side = s.side
        if kind == "round_reset":
            self.reset_round()
        elif kind == "timer":
            t = ev.data["seconds"]
            if s.screen.value == "action_phase" and side == "attack" and not s.defuser_planted:
                if 40 <= t <= 45:
                    self._emit("atk_45")
                elif 15 <= t <= 20:
                    self._emit("atk_20")
            elif s.screen.value == "prep_phase" and side == "defense" and 12 <= t <= 20:
                if (s.team_reinforcements or 0) >= 4 and time.time() - self._said_reinforce > REINFORCE_QUIET:
                    self._emit("reinforce")
                elif (s.team_reinforcements or 0) >= 4:
                    log.debug("fixed line reinforce skipped: a line said to reinforce %.0fs ago",
                              time.time() - self._said_reinforce)
        elif kind == "planted":
            self._emit("def_planted" if side == "defense" else "atk_planted")
        elif kind == "kill":
            self._last_kill = time.time()
        elif kind == "vs":
            us, them = ev.data["alive_us"], ev.data["alive_them"]
            balance = (us > them) - (us < them)
            changed = balance != self._balance
            self._balance = balance
            if us == 1 and ev.data.get("player_alive", True):
                self._emit("clutch")
            elif changed and balance > 0:
                self._emit("advantage")
            elif changed and balance < 0:
                self._emit("disadvantage")
