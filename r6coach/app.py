"""Wires the components together."""

from __future__ import annotations

import logging
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
from .llm import Claude
from .profile import Profile
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
        self.knowledge = Knowledge(notes_dir=data / "notes")
        self.profile = Profile(data / "profile.sqlite")
        self.frames = FrameStore(data, cfg.storage.keep_frames_days)
        self.speaker = Speaker(cfg.voice, self.bus, data / "tts_cache")
        if replay is not None:
            self.source = ReplaySource(replay, seconds_per_frame=replay_seconds)
        else:
            self.source = WindowCaptureSource(cfg.capture.window_title, cfg.capture.sample_fps)
        self.watcher = Watcher(cfg, self.bus, self.source, self.claude, self.frames)
        self.coach = Coach(cfg, self.bus, self.claude, self.knowledge, self.speaker, self.profile,
                           current_state=lambda: self.watcher.state, current_jpeg=self.watcher.current_jpeg)
        self.live = LiveCoach(cfg, self.bus, say=self.speaker.say, state=lambda: self.watcher.state)
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
        self.bus.subscribe("error", lambda ev: log.warning("%s: %s", ev.data.get("source"), ev.data.get("message")))
        self.stopped = threading.Event()

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
            "calls": self.claude.calls,
            "ptt_key": self.cfg.push_to_talk.key if self.ptt else "off",
            "ptt_ready": self.ptt_ready,
            "state": self.watcher.state.model_dump(mode="json"),
            "maps_loaded": len(self.knowledge.maps),
            "hud_ms": round(self.hud.last_ms) if self.hud else None,
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
