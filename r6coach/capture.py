"""Screen capture.

Uses the Windows Graphics Capture API (the same one OBS uses for window capture) through
the `windows-capture` package. It only receives finished frames from Windows; it never
opens, reads or injects into the game process.

A ReplaySource reads saved screenshots from a folder instead, for testing without the game.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional, Protocol

import cv2
import numpy as np

log = logging.getLogger(__name__)


class FrameSource(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def latest(self) -> Optional[tuple[float, np.ndarray]]: ...
    @property
    def status(self) -> str: ...


class WindowCaptureSource:
    """Captures the game window, keeping only the newest frame (BGR, full resolution)."""

    def __init__(self, window_title: str, fps: float = 2.0) -> None:
        self.window_title = window_title
        self.interval_ms = max(50, int(1000 / max(fps, 0.1)))
        self._frame: Optional[tuple[float, np.ndarray]] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._control = None
        self._status = "not started"
        self._last_copy = 0.0

    @property
    def status(self) -> str:
        return self._status

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._control is not None:
            try:
                self._control.stop()
            except Exception:
                pass

    def latest(self) -> Optional[tuple[float, np.ndarray]]:
        with self._lock:
            return self._frame

    def _run(self) -> None:
        from windows_capture import WindowsCapture

        while not self._stop.is_set():
            try:
                cap = WindowsCapture(
                    cursor_capture=False,
                    draw_border=False,
                    minimum_update_interval=self.interval_ms,
                    window_name=self.window_title,
                )
            except Exception as e:  # window not open yet
                self._status = f"waiting for window '{self.window_title}'"
                log.debug("capture not ready: %s", e)
                self._stop.wait(5)
                continue

            @cap.event
            def on_frame_arrived(frame, capture_control):  # noqa: ANN001
                now = time.time()
                if now - self._last_copy < self.interval_ms / 1000:
                    return
                self._last_copy = now
                # frame_buffer is a zero-copy view into native memory: copy it out.
                bgr = np.ascontiguousarray(frame.frame_buffer[:, :, :3]).copy()
                with self._lock:
                    self._frame = (now, bgr)
                self._status = f"capturing {frame.width}x{frame.height}"
                if self._stop.is_set():
                    capture_control.stop()

            @cap.event
            def on_closed():
                self._status = "capture closed"

            try:
                self._control = cap.start_free_threaded()
                self._status = "capturing"
                self._control.wait()
            except Exception as e:
                self._status = f"waiting for window '{self.window_title}'"
                log.debug("capture stopped: %s", e)
            self._control = None
            if not self._stop.is_set():
                self._stop.wait(3)


class ReplaySource:
    """Plays a folder of screenshots (sorted by name) as if they were live frames."""

    def __init__(self, folder: Path, seconds_per_frame: float = 4.0, loop: bool = False) -> None:
        exts = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
        self.files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in exts)
        self.seconds_per_frame = seconds_per_frame
        self.loop = loop
        self._start = 0.0
        self._cache: dict[int, np.ndarray] = {}
        self.finished = False

    @property
    def status(self) -> str:
        if not self.files:
            return "replay: no images found"
        return f"replay: {self._index() + 1}/{len(self.files)}"

    def start(self) -> None:
        self._start = time.time()

    def stop(self) -> None:
        pass

    def _index(self) -> int:
        i = int((time.time() - self._start) / self.seconds_per_frame)
        if self.loop:
            return i % len(self.files)
        if i >= len(self.files):
            self.finished = True
        return min(i, len(self.files) - 1)

    def latest(self) -> Optional[tuple[float, np.ndarray]]:
        if not self.files:
            return None
        i = self._index()
        if i not in self._cache:
            img = cv2.imdecode(np.fromfile(str(self.files[i]), dtype=np.uint8), cv2.IMREAD_COLOR)
            self._cache = {i: img}
        return (self._start + i * self.seconds_per_frame, self._cache[i])


def downscale(img: np.ndarray, width: int) -> np.ndarray:
    h, w = img.shape[:2]
    if w <= width:
        return img
    return cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)


def encode_jpeg(img: np.ndarray, quality: int = 85) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()
