"""Saves key frames (one per screen change) for debugging detection; prunes old ones."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .models import GameState, ScreenRead


class FrameStore:
    def __init__(self, root: Path, keep_days: int) -> None:
        self.root = root / "frames"
        self.root.mkdir(parents=True, exist_ok=True)
        self.keep_days = keep_days
        self._last_screen: str | None = None
        self.prune()

    def maybe_save(self, read: ScreenRead, jpeg: bytes, state: GameState) -> None:
        screen = read.screen.value
        if screen == self._last_screen:
            return
        self._last_screen = screen
        day = time.strftime("%Y-%m-%d")
        folder = self.root / day
        folder.mkdir(exist_ok=True)
        stem = time.strftime("%H%M%S") + f"_{screen}"
        (folder / f"{stem}.jpg").write_bytes(jpeg)
        with open(folder / f"{stem}.json", "w", encoding="utf-8") as f:
            json.dump({"read": read.model_dump(mode="json"), "match_id": state.match_id}, f, indent=1)

    def prune(self) -> None:
        cutoff = time.time() - self.keep_days * 86400
        for day in self.root.iterdir():
            if day.is_dir() and day.stat().st_mtime < cutoff:
                for f in day.iterdir():
                    f.unlink()
                day.rmdir()
