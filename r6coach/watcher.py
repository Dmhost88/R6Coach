"""Watches the captured frames, decides when a cloud read is worth it, and keeps GameState.

Cost control: frames are compared locally (tiny grayscale thumbnails). A screenshot is only
sent to the vision model when the screen has changed meaningfully, or when a per-screen
refresh interval has passed, and never more often than the configured minimum interval.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Optional

import cv2
import numpy as np

from .bus import Bus
from .capture import FrameSource, downscale, encode_jpeg
from .config import Config
from .frames import FrameStore
from .hud import HudRead
from .live import KillTracker
from .llm import Claude
from .models import PRE_ROUND, GameState, Screen, ScreenRead
from .screens import LocalScreen

log = logging.getLogger(__name__)

THUMB = (96, 40)
CHANGE_THRESHOLD = 14.0   # mean abs grey-level difference vs last read thumbnail
STABLE_THRESHOLD = 6.0    # difference between consecutive samples to count as "settled"
REFRESH_FACTOR = 2.5      # refresh interval = min interval * factor, even without change
MATCH_RESET_SCREENS = {Screen.main_menu, Screen.matchmaking, Screen.match_end}
ROUND_START = {Screen.operator_ban, Screen.operator_select}
IN_ROUND_SCREENS = {Screen.prep_phase, Screen.action_phase, Screen.spectating, Screen.round_end}


def thumbnail(img: np.ndarray) -> np.ndarray:
    small = cv2.resize(img, THUMB, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)


def diff(a: Optional[np.ndarray], b: Optional[np.ndarray]) -> float:
    if a is None or b is None:
        return 255.0
    return float(np.mean(np.abs(a - b)))


class ReadGate:
    """Pure decision logic, separated so it can be unit tested."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.last_read_ts = 0.0
        self.last_read_thumb: Optional[np.ndarray] = None
        self.prev_thumb: Optional[np.ndarray] = None
        self.stable_since = 0.0

    def should_read(self, now: float, thumb: np.ndarray, screen: Screen, refresh: bool = True,
                    type_changed: bool = False) -> bool:
        """refresh=False: only read on a change (used once the screen type is known locally);
        type_changed: the local classifier saw a new screen type, which counts as a change."""
        moving = diff(thumb, self.prev_thumb) > STABLE_THRESHOLD
        self.prev_thumb = thumb
        if moving:
            self.stable_since = now
        min_iv = self.cfg.vision.interval_for(screen.value)
        since = now - self.last_read_ts
        if since < min_iv:
            return False
        changed = type_changed or diff(thumb, self.last_read_thumb) > CHANGE_THRESHOLD
        # Live rounds never sit still, so they don't need to settle first.
        settled = screen in (Screen.action_phase, Screen.spectating) or now - self.stable_since >= 0.6
        if changed and settled:
            return True
        return refresh and since >= min_iv * REFRESH_FACTOR

    def mark_read(self, now: float, thumb: np.ndarray) -> None:
        self.last_read_ts = now
        self.last_read_thumb = thumb


# ---------------------------------------------------------------- local screen gating
# Screens where a cloud read adds nothing once the type is known locally: menus, loading, the
# pause menu, live rounds (the HUD reader covers timer, score, banners, killfeed, room).
LOCAL_SKIP = {Screen.main_menu, Screen.matchmaking, Screen.loading, Screen.other, Screen.round_end,
              Screen.match_end, Screen.action_phase, Screen.spectating}
# Screens whose details (bans, picks, teammates, sites, score) only the cloud read can give.
LOCAL_DETAIL = {Screen.map_ban, Screen.operator_ban, Screen.operator_select, Screen.loadout, Screen.scoreboard}
LOCAL_DETAIL_GRACE = 5.0    # s: with no working cloud reads, a detail screen is applied locally after this
ROUND_KEYS = frozenset({"prep", "scoreboard"})   # once-per-round reads made during or after a live round
LOCAL_MIN_PERIOD = 1.0      # s between classifications of a frame that hasn't changed much


def local_cloud_policy(local, state_screen: Screen, done: frozenset | set = frozenset()) -> tuple[str, str]:
    """What to do with the cloud for one frame, given the local classifier's answer.

    Returns (decision, key). "skip": no cloud read. "once": one read of sub-screen `key` per round
    (`done` holds the keys already read this round). "detail": read on a screen type change or a
    changed screen (no timed refresh). "gate": not sure locally, use the plain thumbnail gate.
    """
    if local is None or not local.confident:
        return "gate", ""
    if local.card or local.screen == Screen.spectating:
        return "skip", ""
    if local.in_round:
        # The HUD tells prep from action; only the first prep read of a round goes to the cloud
        # (teammates' operators, early enemy reveals, the site for defenders).
        prep = state_screen == Screen.prep_phase or (
            state_screen not in (Screen.action_phase, Screen.spectating) and local.screen == Screen.prep_phase)
        return ("once", "prep") if prep and "prep" not in done else ("skip", "")
    if local.screen in LOCAL_DETAIL:
        if local.key:
            return ("once", local.key) if local.key not in done else ("skip", "")
        return "detail", ""
    if local.screen in LOCAL_SKIP:
        return "skip", ""
    return "gate", ""


class Watcher:
    def __init__(self, cfg: Config, bus: Bus, source: FrameSource, claude: Claude, frames: FrameStore,
                 screens=None) -> None:
        self.cfg = cfg
        self.bus = bus
        self.source = source
        self.claude = claude
        self.frames = frames
        self.state = GameState()
        self.gate = ReadGate(cfg)
        self.last_jpeg: Optional[bytes] = None
        self.paused = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_frame_ts = 0.0
        self.kills = KillTracker(cfg.player_name)
        self._lock = threading.RLock()   # cloud reads and HUD reads merge from different threads
        self._last_timer: Optional[int] = None
        self._hud_in_round_ts = 0.0      # last time the HUD saw a round in progress
        # Local screen classifier (screens.ScreenClassifier); built on the watcher thread if None.
        self.screens = screens
        self._screens_failed = False
        self.local: Optional[LocalScreen] = None     # latest local classification
        self._local_thumb: Optional[np.ndarray] = None
        self._local_ts = 0.0
        self._local_prev: Optional[Screen] = None    # previous local answer (two in a row = stable)
        self._local_since = 0.0                      # when the current local screen type first appeared
        self._last_cloud_screen: Optional[Screen] = None
        self._match_over = False         # a menu / match end was seen since the last match started
        self._once_done: set[str] = set()   # sub-screens already read this round (see ROUND_KEYS)
        self._cloud_ok_ts = 0.0          # last successful cloud read
        self.cloud_skipped = 0           # cloud reads the local classifier made unnecessary

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def force_read(self) -> None:
        self.gate.last_read_ts = 0.0
        self.gate.last_read_thumb = None

    def current_jpeg(self) -> Optional[bytes]:
        latest = self.source.latest()
        if latest is None:
            return None
        return encode_jpeg(downscale(latest[1], self.cfg.capture.upload_width))

    def _run(self) -> None:
        period = 1.0 / max(self.cfg.capture.sample_fps, 0.1)
        while not self._stop.is_set():
            self._stop.wait(period)
            if self.paused:
                continue
            try:
                self._tick()
            except Exception:  # keep watching even if one read fails unexpectedly
                log.exception("watcher tick failed")
                self.bus.publish("error", source="watcher", message="Screen read failed; see log")
                self._stop.wait(5)

    def _tick(self) -> None:
        latest = self.source.latest()
        if latest is None:
            return
        ts, img = latest
        if ts == self._last_frame_ts and self.gate.last_read_ts > ts:
            return  # no new frame since the last read
        self._last_frame_ts = ts
        now = time.time()
        thumb = thumbnail(img)
        local = self._classify(img, thumb, now) if self.cfg.vision.local_screens else None
        if local is not None:
            with self._lock:
                self._apply_local(local, now)
        screen = self.state.screen
        # Between rounds the last known screen can still be "spectating" or "action_phase", whose
        # long intervals would miss the short ban and pick screens. Once the HUD no longer shows a
        # round in progress, read at the default (quick) rate.
        if screen in IN_ROUND_SCREENS and now - self._hud_in_round_ts > 4:
            screen = Screen.other
        if local is not None and local.confident:
            # One-read-per-round keys: ban/pick sub-screens reset once a live round is seen, the
            # prep and scoreboard reads reset once the next ban/pick phase is seen.
            if local.in_round:
                self._once_done &= ROUND_KEYS
            elif local.screen in (Screen.map_ban, Screen.operator_ban, Screen.operator_select):
                self._once_done -= ROUND_KEYS
        policy, key = local_cloud_policy(local, self.state.screen, self._once_done)
        if policy == "skip":
            # Keep the gate's motion tracking current so the next detail screen settles normally.
            if self.gate.should_read(now, thumb, screen):
                self.cloud_skipped += 1   # ticks where the plain gate would have read
            return
        if policy == "once" and local.in_round:
            # First prep read of the round: the drone view never sits still, so don't wait to settle.
            self.gate.should_read(now, thumb, Screen.prep_phase, refresh=False)
            if now - self.gate.last_read_ts < self.cfg.vision.interval_for(Screen.prep_phase.value):
                return
            self._once_done.add(key)
            self._last_cloud_screen = Screen.prep_phase
        elif policy == "once":
            if not self.gate.should_read(now, thumb, local.screen, refresh=False, type_changed=True):
                return
            self._once_done.add(key)
            self._last_cloud_screen = local.screen
        elif policy == "detail":
            if not self.gate.should_read(now, thumb, local.screen, refresh=False,
                                         type_changed=local.screen != self._last_cloud_screen):
                return
            self._last_cloud_screen = local.screen
        elif not self.gate.should_read(now, thumb, screen):
            return
        self.gate.mark_read(now, thumb)
        jpeg = encode_jpeg(downscale(img, self.cfg.capture.upload_width))
        self.last_jpeg = jpeg
        read = self.claude.read_screen(
            jpeg, self.cfg.vision.model, self.cfg.vision.effort, hint=self.state.summary(),
            player_name=self.cfg.player_name,
        )
        if read is None:
            return
        self._cloud_ok_ts = now
        if policy == "gate" and read.confidence >= 0.35:
            self._last_cloud_screen = read.screen
        self.frames.maybe_save(read, jpeg, self.state)
        self.apply(read)

    # ------------------------------------------------------------------ local screen classifier
    def _classify(self, img: np.ndarray, thumb: np.ndarray, now: float) -> Optional[LocalScreen]:
        """Classify the frame locally; reuses the last answer while the frame barely changes."""
        if self.screens is None:
            if self._screens_failed:
                return None
            try:
                from .screens import ScreenClassifier
                self.screens = ScreenClassifier()
            except Exception:
                log.exception("local screen classifier unavailable (rapidocr_onnxruntime missing?)")
                self._screens_failed = True
                return None
        if (self.local is not None and now - self._local_ts < LOCAL_MIN_PERIOD
                and diff(thumb, self._local_thumb) <= CHANGE_THRESHOLD):
            return self.local
        try:
            local = self.screens.read(img)
        except Exception:
            log.exception("local screen classification failed")
            return None
        self._local_thumb, self._local_ts = thumb, now
        if self.local is None or local.screen != self.local.screen:
            self._local_since = now
        self._local_prev = self.local.screen if self.local is not None else None
        self.local = local
        return local

    def _apply_local(self, local: LocalScreen, now: float) -> None:
        """Merge a confident local classification: screen type (where safe) and map name."""
        s = self.state
        if not local.confident:
            return
        # One frame alone only counts when very sure; otherwise it must repeat.
        stable = local.confidence >= 0.9 or self._local_prev == local.screen
        prev = s.screen
        target: Optional[Screen] = None
        if local.screen == Screen.spectating:
            if prev in (Screen.action_phase, Screen.prep_phase) and stable:
                target = local.screen
        elif local.in_round:
            # The HUD owns prep/action/round end; only step in when coming from a non-round screen.
            if prev not in IN_ROUND_SCREENS and prev != Screen.round_end and stable:
                target = Screen.prep_phase if prev in PRE_ROUND else local.screen
        elif local.screen in LOCAL_DETAIL:
            # The cloud read for this screen brings the details and the screen change together, so
            # the coach never sees e.g. operator_select with last round's side or bans without the
            # site list. Without working cloud reads (offline, no key) the local type still lands
            # after a grace period.
            if (stable and now - self._local_since >= LOCAL_DETAIL_GRACE and now - self._cloud_ok_ts > 60
                    and local.screen != Screen.scoreboard):
                target = local.screen
        elif local.screen in (Screen.other, Screen.loading, Screen.round_end):
            # Pause menu, unknown screens and loading transitions: keep the match state as it is
            # (a "loading" between op select and prep would otherwise look like a new round to the
            # HUD). Round end belongs to the HUD reader, which also publishes round.ended.
            target = None
        elif stable:
            target = local.screen
        if target is not None and target != prev:
            s = self._enter_screen(prev, target)
            s.screen = target
            self.bus.publish("screen.changed", previous=prev.value, screen=target.value,
                             state=s.model_dump(mode="json"), source="local")
            if target == Screen.match_end:
                self.bus.publish("match.ended", state=s.model_dump(mode="json"))
        if local.map and s.map != local.map and s.screen not in MATCH_RESET_SCREENS:
            s.map = local.map
            self.bus.publish("state.updated", state=s.model_dump(mode="json"))
        elif target is not None and target != prev:
            self.bus.publish("state.updated", state=s.model_dump(mode="json"))

    def _enter_screen(self, prev: Screen, screen: Screen) -> GameState:
        """Match and round boundaries for a screen change (cloud or local). Returns the state."""
        s = self.state
        if screen in MATCH_RESET_SCREENS:
            self._match_over = True
        # New match: ban/select screens after a menu or a finished match, or a different map.
        new_match = screen in (Screen.map_ban, Screen.operator_ban, Screen.operator_select) and (
            s.match_id is None or prev in MATCH_RESET_SCREENS or self._match_over
        )
        if new_match:
            self._match_over = False
            self.state = s = GameState(match_id=uuid.uuid4().hex[:10])
            self.bus.publish("match.started", match_id=s.match_id)
            if self.kills:
                self.kills.reset_match()

        # Round boundary: the first pre-round screen (ban phase or op select) after anything else
        # starts a new round and clears round-scoped info. Bans happen every round, so they reset too.
        if screen in ROUND_START and prev not in PRE_ROUND:
            s.my_operator = None
            s.teammate_operators = []
            s.enemy_operators = []
            s.banned_operators = []
            s.site = None
            s.site_options = []
            s.spawn = None
            s.my_location = None
            s.team_reinforcements = None
            s.health = None
            s.player_alive = True
            s.defuser_planted = False
            self.bus.publish("live.event", kind="round_reset")
            if self.kills:
                self.kills.reset_round()
            s.pinged_enemies = []
        return s

    # ------------------------------------------------------------------ state merge
    def apply(self, read: ScreenRead) -> None:
        with self._lock:
            self._apply(read)

    # ------------------------------------------------------------------ local HUD merge
    def apply_hud(self, h: HudRead) -> None:
        """Merge a local HUD read. Runs about once a second; costs nothing."""
        with self._lock:
            self._apply_hud(h)

    def _apply_hud(self, h: HudRead) -> None:
        if h.timer is not None and not (self.state.screen in PRE_ROUND):
            self._hud_in_round_ts = time.time()
        s = self.state
        prev_screen = s.screen
        live = lambda kind, **kw: self.bus.publish("live.event", kind=kind, **kw)  # noqa: E731

        # Phase changes. The ban and operator-select screens show the same top-bar timer, so the
        # timer alone never moves the phase; banners and the prep -> action timer jump do.
        new_screen = None
        if "prep" in h.events and prev_screen not in (Screen.prep_phase, Screen.action_phase):
            new_screen = Screen.prep_phase
        if "action" in h.events or (
            prev_screen == Screen.prep_phase and h.timer is not None and h.timer >= 150
            and self._last_timer is not None and self._last_timer <= 15
        ):
            new_screen = Screen.action_phase
        round_over = "won_round" in h.events or "lost_round" in h.events
        if round_over:
            new_screen = Screen.round_end

        if h.timer is not None:
            if h.timer != s.timer_seconds:
                s.timer_seconds = h.timer
                live("timer", seconds=h.timer)
            self._last_timer = h.timer
        if h.in_round:
            if h.score_us is not None:
                s.score_us = h.score_us
            if h.score_them is not None:
                s.score_them = h.score_them
            if h.reinforcements is not None:
                s.team_reinforcements = h.reinforcements
        if h.room and s.screen not in PRE_ROUND:   # menus that show the top bar can put junk in the room box
            s.my_location = h.room
        if h.health is not None:
            s.health = h.health
        if h.site and not s.site:
            s.site = h.site
        if h.side:
            s.side = h.side
        if h.alive_us is not None:
            if (h.alive_us, h.alive_them) != (s.alive_us, s.alive_them):
                s.alive_us, s.alive_them = h.alive_us, h.alive_them
                live("vs", alive_us=h.alive_us, alive_them=h.alive_them, player_alive=s.player_alive)
        if "planted" in h.events and not s.defuser_planted:
            s.defuser_planted = True
            live("planted")
        if "enemies_found_bombs" in h.events:
            live("found_bombs")

        me = self.cfg.player_name.lower()
        for k in self.kills.new_kills(h.kills):
            mine = bool(me) and k.killer.lower() == me
            died = bool(me) and k.victim.lower() == me
            if died:
                s.player_alive = False
                self.bus.publish("player.died", killer=k.killer)
            live("kill", mine=mine, killer=k.killer, victim=k.victim,
                 killer_team=k.killer_team, victim_team=k.victim_team)
            self.bus.publish("killfeed", entries=[{
                "killer_operator": None, "killer_team": k.killer_team, "killer": k.killer,
                "victim_team": k.victim_team, "victim": k.victim}])

        # A prep banner straight after a round (no pre-round screen read by the cloud in between,
        # e.g. no API key, or the read was missed) still starts a new round.
        if new_screen == Screen.prep_phase and prev_screen not in PRE_ROUND | {Screen.prep_phase,
                                                                              Screen.action_phase}:
            s.alive_us = s.alive_them = None
            s.defuser_planted = False
            s.player_alive = True
            s.health = None
            s.team_reinforcements = None
            s.pinged_enemies = []
            if not h.site:
                s.site = None
            if not h.side:
                s.side = None
            self.kills.reset_round()
            live("round_reset")

        if new_screen is not None and new_screen != prev_screen:
            s.screen = new_screen
            self.bus.publish("screen.changed", previous=prev_screen.value, screen=new_screen.value,
                             state=s.model_dump(mode="json"), source="hud")
            if new_screen == Screen.round_end:
                self.bus.publish("round.ended", won="won_round" in h.events, state=s.model_dump(mode="json"))
        self.bus.publish("state.updated", state=s.model_dump(mode="json"))

    def _apply(self, read: ScreenRead) -> None:
        """Merge one screen read into the match state and publish what changed."""
        s = self.state
        self.bus.publish("vision.read", read=read.model_dump(mode="json"))
        if read.confidence < 0.35:
            return
        prev_screen = s.screen
        screen = read.screen
        s = self._enter_screen(prev_screen, screen)

        for field in ("map", "side", "site", "spawn", "my_operator", "my_location", "round_number",
                      "timer_seconds", "alive_us", "alive_them", "team_reinforcements"):
            val = getattr(read, field)
            if val is not None:
                setattr(s, field, val)
        if read.teammate_operators:
            s.teammate_operators = read.teammate_operators
        if read.enemy_operators:  # reveals only grow during a round
            s.enemy_operators = list(dict.fromkeys(s.enemy_operators + read.enemy_operators))
        if read.site_options:
            s.site_options = read.site_options
        if read.banned_operators:
            s.banned_operators = read.banned_operators
        if read.map_ban_options:
            s.map_ban_options = read.map_ban_options
        if read.score_us is not None:
            s.score_us = read.score_us
        if read.score_them is not None:
            s.score_them = read.score_them
        if read.defuser_planted is not None:
            s.defuser_planted = read.defuser_planted
        if screen in (Screen.prep_phase, Screen.action_phase, Screen.spectating):
            s.pinged_enemies = read.pinged_enemies
        s.screen = screen
        s.last_read_ts = time.time()

        if screen != prev_screen:
            self.bus.publish("screen.changed", previous=prev_screen.value, screen=screen.value,
                             state=s.model_dump(mode="json"))
        if screen == Screen.round_end and prev_screen != Screen.round_end:
            self.bus.publish("round.ended", won=read.round_won, state=s.model_dump(mode="json"))
        if screen == Screen.match_end and prev_screen != Screen.match_end:
            self.bus.publish("match.ended", state=s.model_dump(mode="json"))
        if read.killfeed:
            self.bus.publish("killfeed", entries=[k.model_dump() for k in read.killfeed])
        self.bus.publish("state.updated", state=s.model_dump(mode="json"))
