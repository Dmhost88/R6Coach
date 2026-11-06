"""Guided screenshot collection for calibrating screen reading.

Run:  python -m r6coach --collect

A voice tells you which screen to reach next. Press Ctrl+Alt+P when you're there and a
full-resolution PNG is saved. Meanwhile every visually distinct frame is also saved
automatically (JPEG), so moments you couldn't time still get captured.

Hotkeys (only observed, nothing is sent to the game):
  Ctrl+Alt+P  capture the current item and move to the next
  Ctrl+Alt+K  skip this item for now (asked again at the end)
  Ctrl+Alt+B  go back one item
  Ctrl+Alt+I  repeat the instruction
  Ctrl+Alt+X  save an extra labelled screenshot without advancing
  Ctrl+Alt+Q  finish
No API calls are made in this mode. Everything stays in data/calibration/.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .bus import Bus
from .capture import WindowCaptureSource
from .config import Config
from .voice.tts import Speaker
from .watcher import diff, thumbnail

log = logging.getLogger(__name__)

AUTO_CHANGE = 12.0      # thumbnail difference that counts as a new screen
AUTO_MIN_GAP = 3.0      # seconds between automatic saves
AUTO_MAX = 3000         # stop auto-saving after this many frames (~1 MB each)


@dataclass
class Item:
    id: str
    say: str


CHECKLIST = [
    Item("main_menu", "Main menu. Press Control Alt P to capture it."),
    Item("queue", "Queue for a Ranked match. Capture while it's searching."),
    Item("map_ban", "Map ban screen. Capture once the map choices are showing."),
    Item("operator_ban", "Operator ban screen. Capture while bans are being voted."),
    Item("operator_select_first", "Operator select. Wait until a few teammates have picked, then capture. "
                                  "On defense, make sure the site choice is visible."),
    Item("loadout", "Loadout screen, with your operator's weapons and gadget showing."),
    Item("spawn_select", "Attack only: the spawn location choice. On defense, press Control Alt K to skip."),
    Item("prep_first", "Preparation phase, with the prep timer visible."),
    Item("action_first", "Action phase. Once the round starts, capture with the full HUD and timer showing."),
    Item("killfeed", "Right after someone dies, while the kill is in the killfeed."),
    Item("team_ping", "When a teammate pings an enemy and the red marker is on screen. Skip if it doesn't happen."),
    Item("defuser_planted", "When the defuser is planted. Skip if it doesn't happen."),
    Item("spectating", "When you're dead and spectating a teammate."),
    Item("round_end", "The round won or lost banner at the end of a round."),
    Item("scoreboard", "Hold Tab during a round to show the scoreboard, then capture."),
    Item("operator_select_second", "After the side switch: operator select on your other side."),
    Item("prep_second", "Preparation phase on your other side."),
    Item("action_second", "Action phase on your other side, full HUD."),
    Item("match_end", "Victory or defeat screen at the end of the match."),
    Item("match_summary", "The post-match summary screen."),
]


class Collector:
    def __init__(self, cfg: Config, quiet: bool = False) -> None:
        """quiet: no spoken checklist (for real matches). Ctrl+Alt+P / X just save a marked
        screenshot with a short "Saved." confirmation."""
        self.cfg = cfg
        self.quiet = quiet
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        self.root = cfg.storage.path() / "calibration" / stamp
        (self.root / "labeled").mkdir(parents=True, exist_ok=True)
        (self.root / "auto").mkdir(parents=True, exist_ok=True)
        self.bus = Bus()
        self.speaker = Speaker(cfg.voice, self.bus, cfg.storage.path() / "tts_cache")
        self.source = WindowCaptureSource(cfg.capture.window_title, fps=2)
        self.queue: list[Item] = list(CHECKLIST)
        self.index = 0
        self.skipped: list[Item] = []
        self.captured: dict[str, str] = {}
        self.extras = 0
        self.auto_count = 0
        self.done = threading.Event()
        self._lock = threading.Lock()
        self._warned_black = False

    # ------------------------------------------------------------------ helpers
    def _say(self, text: str) -> None:
        print(f"[coach] {text}", flush=True)
        self.speaker.say(text, priority=0, ttl=60)

    def _current(self) -> Item | None:
        return self.queue[self.index] if self.index < len(self.queue) else None

    def _frame(self) -> np.ndarray | None:
        latest = self.source.latest()
        if latest is None:
            return None
        img = latest[1]
        if float(img.mean()) < 3.0:
            if not self._warned_black:
                self._warned_black = True
                self._say("The capture is black. Switch Siege's display mode to borderless, then try again.")
            return None
        return img

    def _save_png(self, name: str, img: np.ndarray) -> Path:
        path = self.root / "labeled" / f"{name}.png"
        ok, buf = cv2.imencode(".png", img)
        if ok:
            buf.tofile(str(path))
        return path

    def _write_manifest(self) -> None:
        manifest = {
            "captured": self.captured,
            "skipped": [i.id for i in self.skipped if i.id not in self.captured],
            "remaining": [i.id for i in self.queue[self.index:]],
            "extras": self.extras,
            "auto_frames": self.auto_count,
            "resolution": None,
        }
        latest = self.source.latest()
        if latest is not None:
            h, w = latest[1].shape[:2]
            manifest["resolution"] = f"{w}x{h}"
        with open(self.root / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1)

    def _announce(self, prefix: str = "") -> None:
        if self.quiet:
            return
        item = self._current()
        if item is None:
            if prefix:
                self._say(prefix)
            self._finish_or_revisit()
            return
        n = len([i for i in CHECKLIST if i.id in self.captured])
        self._say(f"{prefix} Next, {self.index + 1} of {len(self.queue)}. {item.say}".strip())
        print(f"        captured so far: {n}/{len(CHECKLIST)}", flush=True)

    def _finish_or_revisit(self) -> None:
        missing = [i for i in self.skipped if i.id not in self.captured]
        if missing:
            self.skipped = []
            self.queue = self.queue + missing
            self._say(f"{len(missing)} skipped screens left. Capture them if they come up, or skip again.")
            self._announce()
        else:
            self._say("That's everything. Press Control Alt Q to finish, or keep playing for more automatic captures.")

    # ------------------------------------------------------------------ hotkey actions
    def capture(self) -> None:
        if self.quiet:
            self.extra()
            return
        with self._lock:
            item = self._current()
            img = self._frame()
            if img is None:
                self._say("No game frame yet. Is Siege open?")
                return
            if item is None:
                self.extra()
                return
            path = self._save_png(f"{len(self.captured) + 1:02d}_{item.id}", img)
            self.captured[item.id] = path.name
            print(f"        saved {path}", flush=True)
            self.index += 1
            self._write_manifest()
        self._announce("Got it.")

    def skip(self) -> None:
        with self._lock:
            item = self._current()
            if item is None:
                return
            self.skipped.append(item)
            self.index += 1
            self._write_manifest()
        self._announce("Skipped.")

    def back(self) -> None:
        with self._lock:
            self.index = max(0, self.index - 1)
        self._announce()

    def extra(self) -> None:
        img = self._frame()
        if img is None:
            return
        self.extras += 1
        path = self._save_png(f"marked_{self.extras:02d}_{time.strftime('%H%M%S')}", img)
        print(f"        saved marked {path}", flush=True)
        self.speaker.say("Saved.", priority=1, ttl=5)
        self._write_manifest()

    def finish(self) -> None:
        self._write_manifest()
        self._say(f"Done. {len(self.captured)} labelled screenshots and {self.auto_count} automatic frames saved.")
        self.done.set()

    # ------------------------------------------------------------------ automatic capture
    def _auto_loop(self) -> None:
        last_thumb, last_save = None, 0.0
        while not self.done.is_set():
            self.done.wait(1.0)
            if self.auto_count >= AUTO_MAX:
                continue
            latest = self.source.latest()
            if latest is None or float(latest[1].mean()) < 3.0:
                continue
            img = latest[1]
            thumb = thumbnail(img)
            now = time.time()
            if now - last_save < AUTO_MIN_GAP or diff(thumb, last_thumb) < AUTO_CHANGE:
                continue
            last_thumb, last_save = thumb, now
            self.auto_count += 1
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if ok:
                buf.tofile(str(self.root / "auto" / f"{time.strftime('%H%M%S')}_{self.auto_count:04d}.jpg"))
            if self.auto_count % 25 == 0:
                self._write_manifest()

    # ------------------------------------------------------------------ run
    def run(self) -> None:
        from pynput import keyboard

        self.source.start()
        self.speaker.start()
        hot = keyboard.GlobalHotKeys({
            "<ctrl>+<alt>+p": self.capture,
            "<ctrl>+<alt>+k": self.skip,
            "<ctrl>+<alt>+b": self.back,
            "<ctrl>+<alt>+i": lambda: self._announce(),
            "<ctrl>+<alt>+x": self.extra,
            "<ctrl>+<alt>+q": self.finish,
        })
        hot.daemon = True
        hot.start()
        threading.Thread(target=self._auto_loop, name="auto-capture", daemon=True).start()

        print(f"Saving to {self.root}", flush=True)
        deadline = time.time() + 15
        while self.source.latest() is None and time.time() < deadline:
            time.sleep(0.3)
        if self.source.latest() is None:
            self._say("I can't see the Rainbow Six window yet. Start the game; I'll keep waiting.")
        if self.quiet:
            self._say("Recording quietly. Press Control Alt X to mark a moment.")
        else:
            self._say("Screenshot collection started. I'll tell you which screen to reach. "
                      "Press Control Alt P to capture, Control Alt K to skip.")
        self._announce()
        try:
            while not self.done.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.finish()
        time.sleep(4)  # let the last line play
        hot.stop()
        self.source.stop()
        self.speaker.stop()
        print(f"Finished. Files in {self.root}", flush=True)
