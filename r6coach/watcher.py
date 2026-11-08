"""Watches the captured frames, decides when a cloud read is worth it, and keeps GameState.

Cost control: frames are compared locally (tiny grayscale thumbnails). A screenshot is only
sent to the vision model when the screen has changed meaningfully, or when a per-screen
refresh interval has passed, and never more often than the configured minimum interval.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import uuid
from typing import Callable, Optional

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
from .places import same_room, site_with_room, snap_room, snap_site
from .rounds import first_side_from, round_from_score, side_for
from .screens import LocalScreen

log = logging.getLogger(__name__)

THUMB = (96, 40)
CHANGE_THRESHOLD = 14.0   # mean abs grey-level difference vs last read thumbnail
STABLE_THRESHOLD = 6.0    # difference between consecutive samples to count as "settled"
REFRESH_FACTOR = 2.5      # refresh interval = min interval * factor, even without change
MATCH_RESET_SCREENS = {Screen.main_menu, Screen.matchmaking, Screen.match_end}
ROUND_START = {Screen.operator_ban, Screen.operator_select}
# A round counts as played once its action phase (or what follows) is seen. Prep alone is not
# enough: the in-prep loadout menu and late reads of the pick screen come during prep, and taking
# them for a new round wiped the site, the lock and the teammates 8 times in one evening (2026-11-07).
ROUND_PLAYED = {Screen.action_phase, Screen.spectating, Screen.round_end}
PREP_MAX = 50.0             # s: a pre-round screen this long after prep started is the next round
DETAIL_READ_SCREENS = {Screen.map_ban, Screen.operator_ban, Screen.operator_select, Screen.loadout}
CARD_SCREENS = {Screen.operator_ban, Screen.operator_select, Screen.loadout}
CARD_PERIOD = 0.8           # s between local operator-card reads (each costs ~30-200 ms)
IN_ROUND_SCREENS = {Screen.prep_phase, Screen.action_phase, Screen.spectating, Screen.round_end}
ROUND_CLOCK_FIELDS = {"timer_seconds", "alive_us", "alive_them"}
VS_KILL_WINDOW = 6.0        # s: a VS count read this soon after a killfeed line needs no second read


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
                 screens=None, rooms: Optional[Callable[[Optional[str]], list[str]]] = None) -> None:
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
        self._vs_pending: Optional[tuple] = None   # a VS count seen once, waiting to be confirmed
        self._last_kill_ts = 0.0
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
        self._round_n = 0                # rounds started this match (corrected by "ROUND n" reads)
        # Has the current round been played (prep/action/round end seen)? A pick screen that comes
        # back after a misclassified frame (pick -> other -> pick) is the same round, not a new one:
        # counting it cleared the lock mid-pick and put the round count one ahead (2026-11-06).
        self._round_played = False
        self._prep_ts = 0.0              # when this round's prep phase began (0: not yet)
        # Known room labels for a map (callouts), to snap OCR'd HUD rooms to; None: take them raw.
        self.rooms = rooms
        self._room_pending: Optional[str] = None   # an unknown room label seen once
        self._cloud_op_prev: Optional[str] = None  # last cloud read's my_operator on operator select
        self._first_side: Optional[str] = None   # the side the player started the match on
        self._resumed = False            # state restored from the profile after a restart (resume())
        # Cloud reads once invented a ban "carried over from context". A ban read by the cloud
        # counts when it was on the ban screen, or once two reads agree; local card reads count.
        self._ban_seen: dict[str, int] = {}
        self._once_done: set[str] = set()   # sub-screens already read this round (see ROUND_KEYS)
        self._cloud_ok_ts = 0.0          # last successful cloud read
        self.cloud_skipped = 0           # cloud reads the local classifier made unnecessary
        # Cloud reads take 3-5 s. Run them on their own thread (one at a time, so merges stay in
        # order) and keep classifying screens locally meanwhile. Tests drive reads synchronously.
        self.async_reads = False
        # Local reader of the operator cards and ban splash (opcards.py): the player's lock and
        # teammates' picks in ~30 ms instead of a 2-5 s cloud read. Built on first use.
        self.cards = None
        self._cards_failed = False
        self._cards_ts = 0.0
        self._op_local_round = -1        # round in which the local reader set my_operator
        self._cards_prev_op: Optional[str] = None
        self.last_cards = None
        self._read_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision")
        self._read_busy = False

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
        if local is not None and log.isEnabledFor(logging.DEBUG):
            seen = (local.screen, local.confident)
            if seen != getattr(self, "_logged_local", None):
                self._logged_local = seen
                log.debug("local screen: %s (%s)", local.screen.value, "confident" if local.confident else "unsure")
        if local is not None:
            with self._lock:
                self._apply_local(local, now)
            if (local.confident and local.screen in CARD_SCREENS and now - self._cards_ts >= CARD_PERIOD):
                self._cards_ts = now
                cards = self._read_cards(img, local.screen)
                if cards is not None:
                    with self._lock:
                        self._apply_cards(cards)
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
        if self._read_busy:
            return    # a read is in flight; the next tick after it lands decides again
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
        local_at = local.screen if local is not None and local.confident else None
        log.debug("cloud read sent: policy %s, key %s, local %s, state screen %s, %d KB", policy, key,
                  local_at.value if local_at else "unsure", self.state.screen.value, len(jpeg) // 1024)
        if self.async_reads:
            self._read_busy = True
            try:
                self._read_pool.submit(self._cloud_read, jpeg, policy, now, local_at, key)
            except Exception:
                self._read_busy = False   # never leave reads switched off for the session
                raise
        else:
            self._cloud_read(jpeg, policy, now, local_at, key)

    def _cloud_read(self, jpeg: bytes, policy: str, now: float, local_at: Optional[Screen],
                    key: str = "") -> None:
        try:
            v = self.cfg.vision
            if local_at in DETAIL_READ_SCREENS and v.detail_model and hasattr(self.claude, "read_detail"):
                # Ban and pick menus: short prompt, small schema, fast model (~2 s instead of 3-5 s).
                read = self.claude.read_detail(jpeg, local_at, v.detail_model, v.detail_effort,
                                               hint=self.state.summary(), player_name=self.cfg.player_name,
                                               max_width=v.detail_width)
            else:
                read = self.claude.read_screen(
                    jpeg, v.model, v.effort, hint=self.state.summary(), player_name=self.cfg.player_name,
                )
            if read is None:
                log.debug("cloud read returned nothing after %.1fs", time.time() - now)
                return
            log.debug("cloud read back in %.1fs: %s (conf %.2f)", time.time() - now, read.screen.value,
                      read.confidence)
            self._cloud_ok_ts = now
            if key == "prep" and read.screen not in IN_ROUND_SCREENS:
                # The frame was a transition (loading) taken for prep: the round's one prep read is
                # still to come. Seen 8 times in 20 rounds (2026-11-07).
                self._once_done.discard("prep")
            cur = self.local.screen if self.local is not None and self.local.confident else None
            group = lambda sc: Screen.operator_select if sc == Screen.loadout else sc  # noqa: E731
            if (self.async_reads and local_at is not None and cur is not None and group(cur) != group(local_at)
                    and cur not in (Screen.other, Screen.loading)):
                # The game moved on while this read was out (ban -> pick, pick -> prep): it
                # describes an old screen, and merging it whole would step the state backwards.
                # Keep the facts that still hold this round.
                log.info("discarding %s read: screen is now %s", read.screen.value, cur.value)
                with self._lock:
                    if self._keep_late_facts(read, local_at):
                        self.bus.publish("state.updated", state=self.state.model_dump(mode="json"))
                return
            if policy == "gate" and read.confidence >= 0.35:
                self._last_cloud_screen = read.screen
            self.frames.maybe_save(read, jpeg, self.state)
            self.apply(read)
        except Exception:
            if not self.async_reads:
                raise
            log.exception("cloud read failed")
            self.bus.publish("error", source="watcher", message="Screen read failed; see log")
        finally:
            self._read_busy = False

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
            # Mid-match the ban and pick screens can land at once: the map is known and the side
            # comes from the swap rule, so the coach starts thinking ~5 s sooner while the cloud
            # read (or the local card reader) fills in the details.
            mid_match = (s.map is not None and self._round_n >= 1 and not self._match_over
                         and prev not in PRE_ROUND)
            if self._in_prep_overlay(prev, local.screen):
                target = None   # the loadout menu opened during prep looks like the pick screens
            elif stable and mid_match and local.screen in (Screen.operator_ban, Screen.operator_select):
                target = local.screen
            elif (stable and now - self._local_since >= LOCAL_DETAIL_GRACE and now - self._cloud_ok_ts > 60
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
            if target == Screen.spectating and prev in (Screen.action_phase, Screen.prep_phase):
                self._mark_dead(s, None, "spectating")
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
            # A match resumed after a restart may have ended while the coach was off: the map vote
            # only ever opens a new match.
            or (self._resumed and screen == Screen.map_ban)
        )
        if new_match:
            self._match_over = False
            self._resumed = False
            self._ban_seen = {}
            self._round_n = 0
            self._round_played = False
            self._first_side = None
            self._op_local_round = -1
            self.state = s = GameState(match_id=uuid.uuid4().hex[:10])
            self.bus.publish("match.started", match_id=s.match_id)
            if self.kills:
                self.kills.reset_match()

        # Round boundary: the first pre-round screen (ban phase or op select) after a played round
        # starts a new round and clears round-scoped info, bans included: Siege bans per round, and
        # an operator banned last round can be picked again (2026-11-07: "Kaid is banned" while the
        # player was on Kaid).
        if screen in ROUND_PLAYED:
            self._round_played = True
        if screen == Screen.prep_phase and not self._prep_ts:
            self._prep_ts = time.time()
        if screen in ROUND_START and prev not in PRE_ROUND and self._round_over():
            self._round_played = False
            self._prep_ts = 0.0
            s.my_operator = None
            s.teammate_operators = []
            s.enemy_operators = []
            s.banned_operators = []
            self._ban_seen = {}
            self._cloud_op_prev = None
            self._room_pending = None
            s.site = None
            s.site_options = []
            s.spawn = None
            s.my_location = None
            s.team_reinforcements = None
            s.health = None
            s.player_alive = True
            s.defuser_planted = False
            s.defuser_location = None
            s.site_found_by_enemy = False
            # Counts and the clock belong to the round: R7 once opened with R6's "5 v 1".
            s.alive_us = s.alive_them = None
            s.timer_seconds = None
            self._last_timer = None
            self._vs_pending = None
            self._next_round(s)
            self.bus.publish("live.event", kind="round_reset")
            if self.kills:
                self.kills.reset_round()
            s.pinged_enemies = []
        return s

    def _round_over(self) -> bool:
        """Has the current round finished, so that a ban or pick screen now starts the next one?
        Yes once its action phase was seen, or long after its prep began (the action phase and
        round end were missed). The first round of a match has nothing to finish."""
        return (self._round_n == 0 or self._round_played
                or (self._prep_ts > 0 and time.time() - self._prep_ts >= PREP_MAX))

    def _in_prep_overlay(self, prev: Screen, screen: Screen) -> bool:
        """A pre-round screen seen during this round's prep: the loadout menu opened in prep, or a
        late read of the pick screen. It is neither a new round nor a step back to the picks."""
        return prev == Screen.prep_phase and screen in PRE_ROUND and not self._round_over()

    def _keep_late_facts(self, read: ScreenRead, local_at: Optional[Screen]) -> bool:
        """Merge what still holds from a read of a screen the game has already left. Returns True
        when something changed. Two defense rounds lost a correctly read site this way (2026-11-07)."""
        s = self.state
        changed = False
        if local_at == Screen.operator_ban:
            if read.banned_operators:
                s.banned_operators = list(dict.fromkeys(s.banned_operators + read.banned_operators))
                changed = True
            if read.round_number:
                self._round_n = read.round_number
                s.round_number = read.round_number
                changed = True
        if local_at in (Screen.operator_select, Screen.loadout):
            if read.site_options and not s.site_options:
                s.site_options = read.site_options
                changed = True
            if read.site and not s.site and (read.side or s.side) == "defense":
                s.site = snap_site(read.site, s.site_options) or read.site
                changed = True
            if read.teammate_operators and not s.teammate_operators:
                mine = (s.my_operator or "").lower()
                s.teammate_operators = [op for op in read.teammate_operators if op.lower() != mine]
                changed = True
        return changed

    def _cloud_operator(self, val: str, screen: Screen) -> bool:
        """Should a cloud read's my_operator be taken? On the operator grid the model reads the
        hovered card as the lock (5 of 9 cloud 'locks' on 2026-11-07 were never picked), and each one
        restarted the pick advice. There it only counts when no local card reader works and two
        reads in a row agree. The loadout screen and in-round top bar show the actual pick."""
        if screen != Screen.operator_select:
            return True
        if self.cards is not None and not self._cards_failed:
            return False   # the local reader watches the cards and sees real locks
        prev, self._cloud_op_prev = self._cloud_op_prev, val
        return prev is not None and prev.lower() == val.lower()

    # ------------------------------------------------------------------ local operator cards
    def _read_cards(self, img: np.ndarray, screen: Screen):
        if self._cards_failed:
            return None
        try:
            if self.cards is None:
                from .opcards import OperatorCardReader
                ocr = getattr(self.screens, "ocr", None)   # same thread as the classifier: share it
                if ocr is None:
                    self._cards_failed = True     # no local OCR engine here (e.g. a stub classifier)
                    return None
                self.cards = OperatorCardReader(player_name=self.cfg.player_name, ocr=ocr)
            cards = self.cards.read(img, screen=screen)
            self.last_cards = cards
            return cards
        except Exception:
            log.exception("local operator-card reader failed; turning it off")
            self._cards_failed = True
            return None

    def _apply_cards(self, c) -> None:
        """Merge a local card read. The cards and loadout heading are authoritative for the player's
        own pick; the READY cards for teammates (cloud reads tend to list the player among them);
        visible bans add to this round's bans. A hovered operator (source 'panel') is not a pick."""
        s = self.state
        changed = False
        # One fuzzy OCR match (Ash/Ram) must not pin a wrong lock for the whole round: take it when
        # the reader is sure, or when two reads in a row agree.
        prev, self._cards_prev_op = self._cards_prev_op, c.my_operator
        trusted = c.confidence >= 0.7 or (c.my_operator is not None and c.my_operator == prev)
        if c.source in ("cards", "loadout") and c.my_operator and trusted:
            self._op_local_round = self._round_n
            if c.my_operator != s.my_operator:
                log.info("local read: player locked %s (%.0f ms)", c.my_operator, c.ms)
                s.my_operator = c.my_operator
                changed = True
        if c.source == "cards" and c.teammates:
            mine = (s.my_operator or "").lower()
            mates = [t for t in c.teammates if t.lower() != mine]
            if mates != s.teammate_operators:
                s.teammate_operators = mates
                changed = True
        if c.banned:
            bans = list(dict.fromkeys(s.banned_operators + list(c.banned)))
            if bans != s.banned_operators:
                s.banned_operators = bans
                changed = True
        if changed:
            self.bus.publish("state.updated", state=s.model_dump(mode="json"))

    def _mark_dead(self, s: GameState, killer: Optional[str], source: str) -> None:
        """The player died: from the killfeed (with the killer), or as a backstop when the screen
        turns to spectating mid-round (killfeed name misread, or no player_name set)."""
        if not s.player_alive:
            return
        s.player_alive = False
        log.info("player died (%s, killer %s)", source, killer or "unknown")
        self.bus.publish("player.died", killer=killer, source=source)
        self.bus.publish("live.event", kind="player_died", killer=killer)

    # ------------------------------------------------------------------ round arithmetic
    def _predicted_side(self) -> Optional[str]:
        if not self._first_side or self._round_n < 1:
            return None
        return side_for(self._round_n, self._first_side, self.cfg.swap_after)

    def _next_round(self, s: GameState) -> None:
        """A round starts: count it and work out the side from the swap rule (before any screen shows it)."""
        self._round_n += 1
        s.round_number = self._round_n
        s.side = self._predicted_side()
        s.side_source = "predicted" if s.side else None

    def _observe_side(self, s: GameState, side: Optional[str], screen: Screen) -> None:
        """Reconcile a side read off the screen with the swap rule."""
        pred = self._predicted_side()
        if side not in ("attack", "defense") or screen == Screen.map_ban:
            if pred and s.side_source != "read":
                s.side, s.side_source = pred, "predicted"
            return
        if screen == Screen.operator_ban and pred and side != pred:
            # The ban screen shows no side; a read there is a guess (often last round's side).
            log.info("ban-screen side read %s overruled by swap rule (%s, round %d)", side, pred, self._round_n)
            s.side, s.side_source = pred, "predicted"
            return
        s.side = side
        # A side seen on the ban screen that agrees with the rule is still only a guess.
        s.side_source = "predicted" if screen == Screen.operator_ban else "read"
        self._first_side = first_side_from(self._round_n, side, self.cfg.swap_after) or self._first_side

    # ------------------------------------------------------------------ state merge
    def apply(self, read: ScreenRead) -> None:
        with self._lock:
            self._apply(read)

    # ------------------------------------------------------------------ local HUD merge
    def apply_hud(self, h: HudRead) -> None:
        """Merge a local HUD read. Runs about once a second; costs nothing."""
        if log.isEnabledFor(logging.DEBUG):
            # The fields this read found (empty reads log nothing).
            found = {k: v for k, v in vars(h).items()
                     if k != "ts" and v is not None and v is not False and v != [] and v != ""}
            if found:
                log.debug("hud: %s", " ".join(f"{k}={v}" for k, v in found.items())[:600])
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
            self._take_room(s, h.room)
        if h.health is not None:
            s.health = h.health
        if h.site:
            # "BOMBS FOUND": snap the OCR'd site to the listed options, so a one-line read
            # ("2F Laundry") becomes the full pair and can replace an earlier partial one.
            site = snap_site(h.site, s.site_options) or h.site
            if not s.site or (site != s.site and site in s.site_options and s.site not in s.site_options):
                s.site = site
        if h.side:   # banner subtitles ("FORTIFY BOMB SITES") are reliable
            self._observe_side(s, h.side, s.screen)
        if self._defense_site_from_room(s, new_screen or s.screen):
            log.info("defense site from the prep spawn room %s: %s", s.my_location, s.site)
        planted = "planted" in h.events or "defuser_found" in h.events
        if h.defuser_room and not s.defuser_location:
            s.defuser_location = self._snap(h.defuser_room)
        if planted and not s.defuser_planted:
            s.defuser_planted = True
            live("planted", room=s.defuser_location)
        if "enemies_found_bombs" in h.events and not s.site_found_by_enemy:
            s.site_found_by_enemy = True
            live("found_bombs")
        if "defuser_picked_up" in h.events:
            live("defuser_picked_up")

        me = self.cfg.player_name.lower()
        new_kills = self.kills.new_kills(h.kills)
        if new_kills:
            self._last_kill_ts = time.time()
        if h.alive_us is not None:
            self._take_vs(s, (h.alive_us, h.alive_them))
        for k in new_kills:
            mine = bool(me) and k.killer.lower() == me
            died = bool(me) and k.victim.lower() == me
            if died:
                self._mark_dead(s, k.killer, "killfeed")
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
            self._vs_pending = None
            self._round_played = False
            self._prep_ts = 0.0
            s.banned_operators = []
            self._ban_seen = {}
            s.defuser_planted = False
            s.defuser_location = None
            s.site_found_by_enemy = False
            s.player_alive = True
            s.health = None
            s.team_reinforcements = None
            s.pinged_enemies = []
            if not h.site:
                s.site = None
            self._next_round(s)
            if h.side:
                self._observe_side(s, h.side, new_screen)
            self.kills.reset_round()
            live("round_reset")

        if new_screen is not None and new_screen != prev_screen:
            if new_screen in ROUND_PLAYED:
                self._round_played = True   # so the next ban/pick screen starts a new round
            if new_screen == Screen.prep_phase and not self._prep_ts:
                self._prep_ts = time.time()
            s.screen = new_screen
            self.bus.publish("screen.changed", previous=prev_screen.value, screen=new_screen.value,
                             state=s.model_dump(mode="json"), source="hud")
            if new_screen == Screen.round_end:
                self.bus.publish("round.ended", won="won_round" in h.events, state=s.model_dump(mode="json"))
        self.bus.publish("state.updated", state=s.model_dump(mode="json"))

    def _known_rooms(self) -> list[str]:
        if self.rooms is None:
            return []
        try:
            return self.rooms(self.state.map) or []
        except Exception:
            log.exception("room list lookup failed")
            return []

    def _snap(self, raw: str) -> str:
        """A HUD room reading as the known label it means, else as read."""
        return snap_room(raw, self._known_rooms()) or raw

    def _take_room(self, s: GameState, raw: str) -> None:
        """Merge the HUD room label. A reading that matches a known room counts at once; one that
        doesn't (an OCR slip, or a game label the callout data lacks) only once two reads in a row
        agree. An OCR wobble of the current room ('2F Office' -> '2F Office8') is no move at all:
        28% of the room changes on 2026-11-07 were that, and each could start a check-in."""
        known = snap_room(raw, self._known_rooms())
        if known is not None:
            self._room_pending = None
            if known != s.my_location:
                s.my_location = known
            return
        if same_room(raw, s.my_location):
            self._room_pending = None
            return
        if self._room_pending is not None and same_room(raw, self._room_pending):
            s.my_location = raw
            self._room_pending = None
            return
        self._room_pending = raw

    def _defense_site_from_room(self, s: GameState, screen: Screen) -> bool:
        """Defenders spawn on their site: the first rooms of prep name it. Taken only early in prep
        (before they walk off) and only when the room belongs to one of the listed sites. This knew
        the site in 10 of 10 defense rounds on 2026-11-07, where the vote read knew it in 4."""
        if s.site or s.side != "defense" or screen != Screen.prep_phase or not s.site_options:
            return False
        if s.timer_seconds is None or s.timer_seconds < 30:
            return False
        site = site_with_room(s.my_location, s.site_options)
        if site:
            s.site = site
        return site is not None

    def _take_vs(self, s: GameState, counts: tuple) -> None:
        """Accept a new "N VS M" count only when it is confirmed: the same count read twice in a
        row, or one read right after a killfeed line. One OCR slip used to rewrite the counts and
        fire "down a player" twice in 17 s (2026-11-06)."""
        if counts == (s.alive_us, s.alive_them):
            self._vs_pending = None
            return
        recent_kill = time.time() - self._last_kill_ts <= VS_KILL_WINDOW
        if not recent_kill and self._vs_pending != counts:
            self._vs_pending = counts
            return
        self._vs_pending = None
        s.alive_us, s.alive_them = counts
        self.bus.publish("live.event", kind="vs", alive_us=counts[0], alive_them=counts[1],
                         player_alive=s.player_alive)

    def _confirmed_bans(self, read: ScreenRead, screen: Screen) -> list[str]:
        """The bans in a cloud read that count: all of them on the ban screen (the splash shows
        them), elsewhere only those another read has already reported."""
        out = []
        for op in read.banned_operators:
            n = self._ban_seen.get(op.lower(), 0) + 1
            self._ban_seen[op.lower()] = n
            if screen == Screen.operator_ban or n >= 2:
                out.append(op)
            else:
                log.info("ban %s read once on %s; waiting for a second read", op, screen.value)
        return out

    # ------------------------------------------------------------------ restart recovery
    def resume(self, info: dict) -> None:
        """Pick up a match the coach was restarted in the middle of (Profile.unfinished_match):
        match id, map and the round count, so round 3 isn't called round 1."""
        rounds = info.get("rounds") or []
        with self._lock:
            n = max([r.get("round_number") or 0 for r in rounds] + [len(rounds)])
            # Bans are per round: the stored ones belong to a round already played.
            s = GameState(match_id=info["id"], map=info.get("map"), round_number=n or None,
                          score_us=sum(1 for r in rounds if r.get("won") is True),
                          score_them=sum(1 for r in rounds if r.get("won") is False))
            self.state = s
            self._round_n = n
            self._round_played = True       # the next ban or pick screen starts the next round
            self._match_over = False
            self._resumed = True
            last = next((r for r in reversed(rounds) if r.get("side")), None)
            if last:
                self._first_side = first_side_from(last.get("round_number") or n, last["side"],
                                                   self.cfg.swap_after)
        log.info("resumed match %s on %s after %d rounds", s.match_id, s.map, n)
        self.bus.publish("state.updated", state=s.model_dump(mode="json"))

    def recover_map(self, map_name: str) -> None:
        """Set the map when no read named it (worked out from the room callouts on the HUD)."""
        with self._lock:
            if self.state.map is None and self.state.screen not in MATCH_RESET_SCREENS:
                log.info("map recovered from room callouts: %s", map_name)
                self.state.map = map_name
                self.bus.publish("state.updated", state=self.state.model_dump(mode="json"))

    def _apply(self, read: ScreenRead) -> None:
        """Merge one screen read into the match state and publish what changed."""
        s = self.state
        self.bus.publish("vision.read", read=read.model_dump(mode="json"))
        if read.confidence < 0.35:
            return
        prev_screen = s.screen
        screen = read.screen
        if self._resumed and read.map and s.map and read.map.lower() != s.map.lower():
            log.info("resumed match was on %s, the screen shows %s: a new match", s.map, read.map)
            self._match_over = True
        if self._in_prep_overlay(prev_screen, screen):
            # The loadout menu during prep, or a pick-screen read that landed after prep began.
            log.info("ignoring a %s read during prep (round %d)", screen.value, self._round_n)
            return
        s = self._enter_screen(prev_screen, screen)

        for field in ("map", "site", "spawn", "my_operator", "my_location", "round_number",
                      "timer_seconds", "alive_us", "alive_them", "team_reinforcements"):
            val = getattr(read, field)
            if field == "my_operator" and self._op_local_round == self._round_n:
                continue   # the local card reader saw the actual lock this round; it wins
            if field == "my_operator" and val is not None and not self._cloud_operator(val, screen):
                log.debug("cloud read names %s on the grid: a hover, not a lock", val)
                continue
            if field == "my_location":
                if val is not None and screen not in PRE_ROUND:
                    s.my_location = self._snap(val)   # a model read, not OCR: no second read needed
                continue
            if field == "site" and val is not None:
                val = snap_site(val, read.site_options or s.site_options) or val
            if field in ROUND_CLOCK_FIELDS and screen in PRE_ROUND:
                continue   # no round is running on a ban or pick screen; a value here is stale
            if field in ("alive_us", "alive_them"):
                continue   # confirmed below, like the HUD's counts
            if field == "site" and screen in PRE_ROUND and (read.side or s.side) == "attack":
                continue   # attackers only learn the site from their drones ("BOMBS FOUND")
            if val is not None:
                setattr(s, field, val)
        if read.round_number and screen in PRE_ROUND:
            self._round_n = read.round_number      # "ROUND n" on the ban card beats our count
        elif screen in PRE_ROUND and self._round_n:
            # The score on the ban/pick screens is the rounds played so far: it fixes a count that
            # drifted (a restart mid-match, a missed or doubled boundary).
            n = round_from_score(read.score_us, read.score_them)
            if n and n != self._round_n:
                log.info("round count %d corrected to %d from the score %s-%s",
                         self._round_n, n, read.score_us, read.score_them)
                self._round_n = s.round_number = n
        if read.alive_us is not None and read.alive_them is not None and screen not in PRE_ROUND:
            self._take_vs(s, (read.alive_us, read.alive_them))
        self._observe_side(s, read.side, screen)
        if read.teammate_operators:
            s.teammate_operators = read.teammate_operators
        if s.my_operator:
            # The top bar and READY tab include the player's own card; keep teammates to the other four.
            mine = s.my_operator.lower()
            s.teammate_operators = [op for op in s.teammate_operators if op.lower() != mine]
        if read.enemy_operators:  # reveals only grow during a round
            s.enemy_operators = list(dict.fromkeys(s.enemy_operators + read.enemy_operators))
        if read.site_options:
            s.site_options = read.site_options
        if read.banned_operators:  # bans add up over this round's screens (cleared each round)
            s.banned_operators = list(dict.fromkeys(s.banned_operators + self._confirmed_bans(read, screen)))
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
        if screen == Screen.spectating and prev_screen in (Screen.action_phase, Screen.prep_phase):
            self._mark_dead(s, None, "spectating")
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
