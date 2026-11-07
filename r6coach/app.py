"""Wires the components together."""

from __future__ import annotations

import logging
import os
import threading
import webbrowser
from pathlib import Path
from typing import Any, Optional

from .bus import Bus
from .capture import ReplaySource, WindowCaptureSource
from .coach import Coach
from .config import Config
from .frames import FrameStore
from .hud import HudThread
from .knowledge import Knowledge
from .live import LiveCoach
from .maplayout import MapLayouts
from .memory import MatchMemory
from .tactical import TacticalCoach
from .llm import Claude
from .profile import Profile
from .setups import SetupLibrary
from .voice.tts import Speaker
from .watcher import Watcher

log = logging.getLogger(__name__)


class App:
    def __init__(self, cfg: Config, replay: Optional[Path] = None, replay_seconds: float = 4.0,
                 enable_ptt: bool = True) -> None:
        self.cfg = cfg
        data = cfg.storage.path()
        data.mkdir(parents=True, exist_ok=True)
        self.bus = Bus()
        self.claude = Claude(self.bus)
        self.claude.budget = cfg.coach.budget_per_match
        self.bus.subscribe("match.started", lambda ev: self.claude.reset_match())
        self._last_room: Optional[str] = None
        self.bus.subscribe("state.updated", self._record_room)
        self.knowledge = Knowledge(notes_dir=data / "notes")
        self.layouts = MapLayouts(self.knowledge.data_dir / "layout", data / "maps")
        self.setups = SetupLibrary(self.knowledge.data_dir / "setups", data / "setups")
        self.bus.subscribe("coach.said", self._map_focus)
        self.profile = Profile(data / "profile.sqlite")
        self.frames = FrameStore(data, cfg.storage.keep_frames_days)
        self.speaker = Speaker(cfg.voice, self.bus, data / "tts_cache")
        if replay is not None:
            self.source = ReplaySource(replay, seconds_per_frame=replay_seconds)
        else:
            self.source = WindowCaptureSource(cfg.capture.window_title, cfg.capture.sample_fps)
        self.watcher = Watcher(cfg, self.bus, self.source, self.claude, self.frames)
        self.watcher.async_reads = True
        self.coach = Coach(cfg, self.bus, self.claude, self.knowledge, self.speaker, self.profile,
                           current_state=lambda: self.watcher.state, current_jpeg=self.watcher.current_jpeg)
        self.memory = MatchMemory(self.bus, lambda: self.watcher.state, cfg.player_name)
        self.coach.memory = self.memory
        self.tactical = TacticalCoach(cfg, self.bus, self.claude, self.knowledge, self.speaker.say,
                                      lambda: self.watcher.state, self.memory, self.coach._system)
        has_key = lambda: bool(os.environ.get("ANTHROPIC_API_KEY"))  # noqa: E731
        self.live = LiveCoach(cfg, self.bus, say=self.speaker.say, state=lambda: self.watcher.state,
                              ai_live=lambda: has_key() and self.tactical.enabled())
        self.hud = HudThread(self.source, self.watcher.apply_hud, cfg.hud.period, cfg.player_name) \
            if cfg.hud.enabled else None
        self.controls = {
            "mute": self.toggle_mute,
            "repeat": self.coach.repeat,
            "skip": self.speaker.skip,
            "pause": self.toggle_pause,
            "force_read": self.watcher.force_read,
            "now": lambda: self.bus.publish("ptt.question", text="What should I do right now?"),
        }
        self.ptt = None
        self.ptt_ready = False
        if enable_ptt:
            from .voice.ptt import PushToTalk
            self.ptt = PushToTalk(cfg.push_to_talk, self.bus, self.controls)
        self.bus.subscribe("ptt.ready", lambda ev: setattr(self, "ptt_ready", True))
        self._map_candidates: Optional[set] = None
        self.bus.subscribe("state.updated", self._recover_map)
        if replay is None:
            self._resume()
        self.bus.subscribe("error", lambda ev: log.warning("%s: %s", ev.data.get("source"), ev.data.get("message")))
        self.stopped = threading.Event()

    def map_id(self) -> Optional[str]:
        m = self.knowledge.find_map(self.watcher.state.map)
        return next((mid for mid, v in self.knowledge.maps.items() if v is m), None) if m else None

    def _map_focus(self, ev) -> None:
        """Tell the dashboard map which rooms the line just spoken names, in order."""
        mid = self.map_id()
        you = self.layouts.locate(mid, self.watcher.state.my_location)
        rooms = self.layouts.mentions(mid, ev.data.get("text", ""), you["floor"] if you else None)
        for r in rooms:   # which blueprint image, for maps with two images of one floor
            r["fi"] = self.layouts.floor_index(mid, r["floor"], (r["room"],))
        if rooms:
            self.bus.publish("map.focus", map=mid, rooms=rooms, text=ev.data.get("text", ""))

    def _resume(self) -> None:
        """After a restart mid-match, carry on with that match (map, bans, rounds) instead of
        guessing. Only a match with a round in the last 15 minutes and no recorded end."""
        try:
            info = self.profile.unfinished_match()
        except Exception:
            log.exception("could not read the last match from the profile")
            return
        if not info or not info["rounds"]:
            return
        self.watcher.resume(info)
        self.memory.restore(info["rounds"])

    def _recover_map(self, ev) -> None:
        """No map known in a live round (e.g. restarted mid-round): narrow it down from the room
        callouts the HUD shows until one map is left."""
        st = ev.data.get("state") or {}
        if st.get("map") or st.get("screen") not in ("prep_phase", "action_phase"):
            self._map_candidates = None
            return
        maps = self.knowledge.maps_with_room(st.get("my_location"))
        if not maps:
            return
        cands = maps if self._map_candidates is None else (self._map_candidates & maps) or maps
        self._map_candidates = cands
        if len(cands) == 1:
            mid = next(iter(cands))
            self.watcher.recover_map(self.knowledge.maps.get(mid, {}).get("name", mid))

    def _record_room(self, ev) -> None:
        """Log each new room callout from the HUD, per map, to build a verified room list."""
        st = ev.data.get("state") or {}
        room, m = st.get("my_location"), st.get("map")
        if room and m and room != self._last_room and st.get("screen") in ("prep_phase", "action_phase"):
            self._last_room = room
            self.profile.room_seen(m, room)

    def toggle_mute(self) -> None:
        muted = not self.speaker.muted
        if muted:
            self.speaker.say("Coach muted.", priority=0)
            # let the confirmation play before muting
            threading.Timer(1.2, self.speaker.set_muted, args=(True,)).start()
        else:
            self.speaker.set_muted(False)
            self.speaker.say("Coach on.", priority=0)
        self.bus.publish("status", **self.status())

    def toggle_pause(self) -> None:
        self.watcher.paused = not self.watcher.paused
        self.bus.publish("status", **self.status())

    def status(self) -> dict[str, Any]:
        return {
            "capture": self.source.status,
            "muted": self.speaker.muted,
            "paused": self.watcher.paused,
            "cost": round(self.claude.total_cost, 4),
            "match_cost": round(self.claude.match_cost, 4),
            "budget_per_match": self.cfg.coach.budget_per_match,
            "budget_level": self.claude.budget_level(),
            "startup_cost": round(self.claude.startup_cost, 4),
            "calls": self.claude.calls,
            "ptt_key": self.cfg.push_to_talk.key if self.ptt else "off",
            "ptt_ready": self.ptt_ready,
            "state": self.watcher.state.model_dump(mode="json"),
            "maps_loaded": len(self.knowledge.maps),
            "hud_ms": round(self.hud.last_ms) if self.hud else None,
            # Local screen classifier (vision.local_screens): latest type and its cost in ms.
            "local_screen": self.watcher.local.screen.value if self.watcher.local else None,
            "screens_ms": round(self.watcher.local.ms) if self.watcher.local else None,
        }

    def start(self, dashboard: bool = True) -> None:
        self.source.start()
        self.speaker.start()
        self.watcher.start()
        if self.hud:
            self.hud.start()
        if self.ptt:
            self.ptt.start()
        if dashboard:
            from .dashboard.server import serve
            serve(self)
            url = f"http://{self.cfg.dashboard.host}:{self.cfg.dashboard.port}/"
            log.info("dashboard at %s", url)
            if self.cfg.dashboard.open_browser:
                threading.Timer(1.5, webbrowser.open, args=(url,)).start()

    def stop(self) -> None:
        self.watcher.stop()
        if self.hud:
            self.hud.stop()
        self.source.stop()
        self.speaker.stop()
        if self.ptt:
            self.ptt.stop()
        self.stopped.set()
