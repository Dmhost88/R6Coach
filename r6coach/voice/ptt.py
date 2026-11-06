"""Push-to-talk questions and control hotkeys.

Hold the PTT key, ask a question, release. Speech is transcribed locally with faster-whisper,
so audio never leaves the PC; only the transcribed text goes to the coach.

The listeners only observe key presses. Nothing here sends input to the game.

Fixed control hotkeys:
  Ctrl+Alt+M  mute / unmute the coach
  Ctrl+Alt+R  repeat the last line
  Ctrl+Alt+S  skip the line being spoken
  Ctrl+Alt+N  "what should I do now?" (same as asking it by voice)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from ..bus import Bus
from ..config import PttCfg

log = logging.getLogger(__name__)

RATE = 16000


def _parse_key(name: str):
    from pynput import keyboard, mouse

    name = name.strip().lower()
    if name.startswith("mouse_"):
        return ("mouse", getattr(mouse.Button, name[len("mouse_"):]))
    if len(name) == 1:
        return ("key", keyboard.KeyCode.from_char(name))
    return ("key", getattr(keyboard.Key, name))


class PushToTalk:
    def __init__(self, cfg: PttCfg, bus: Bus, controls: dict[str, Callable[[], None]]) -> None:
        self.cfg = cfg
        self.bus = bus
        self.controls = controls
        self._model = None
        self._model_lock = threading.Lock()
        self._recording = False
        self._chunks: list[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None
        self._listeners: list = []
        self._ptt = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        from pynput import keyboard, mouse

        try:
            self._ptt = _parse_key(self.cfg.key)
        except AttributeError:
            self.bus.publish("error", source="ptt", message=f"Unknown push-to-talk key '{self.cfg.key}'")
            self._ptt = None

        kb = keyboard.Listener(on_press=self._on_key_press, on_release=self._on_key_release)
        ms = mouse.Listener(on_click=self._on_click)
        hot = keyboard.GlobalHotKeys({
            "<ctrl>+<alt>+m": self.controls.get("mute", lambda: None),
            "<ctrl>+<alt>+r": self.controls.get("repeat", lambda: None),
            "<ctrl>+<alt>+s": self.controls.get("skip", lambda: None),
            "<ctrl>+<alt>+n": self.controls.get("now", lambda: None),
        })
        for listener in (kb, ms, hot):
            listener.daemon = True
            listener.start()
            self._listeners.append(listener)
        threading.Thread(target=self._load_model, name="whisper-load", daemon=True).start()

    def stop(self) -> None:
        for listener in self._listeners:
            listener.stop()

    def _load_model(self) -> None:
        from faster_whisper import WhisperModel

        with self._model_lock:
            if self._model is not None:
                return
            try:
                self._model = WhisperModel(self.cfg.whisper_model, device=self.cfg.whisper_device,
                                           compute_type="float16" if self.cfg.whisper_device == "cuda" else "int8")
                # Probe once: missing CUDA libraries only fail at first use.
                list(self._model.transcribe(np.zeros(RATE, dtype=np.float32))[0])
            except Exception as e:
                log.warning("Whisper on %s failed (%s); falling back to CPU", self.cfg.whisper_device, e)
                self._model = WhisperModel(self.cfg.whisper_model, device="cpu", compute_type="int8")
        self.bus.publish("ptt.ready")

    # ------------------------------------------------------------------ input events
    def _matches(self, kind: str, value) -> bool:
        return self._ptt is not None and self._ptt[0] == kind and self._ptt[1] == value

    def _on_key_press(self, key) -> None:
        if self._matches("key", key):
            self._begin()

    def _on_key_release(self, key) -> None:
        if self._matches("key", key):
            self._end()

    def _on_click(self, x, y, button, pressed) -> None:  # noqa: ANN001
        if self._matches("mouse", button):
            self._begin() if pressed else self._end()

    # ------------------------------------------------------------------ recording
    def _begin(self) -> None:
        if self._recording:
            return
        self._recording = True
        self._chunks = []
        try:
            self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                          callback=lambda data, *_: self._chunks.append(data.copy()))
            self._stream.start()
            self.bus.publish("ptt.listening")
        except Exception as e:
            self._recording = False
            self.bus.publish("error", source="ptt", message=f"Microphone error: {e}")

    def _end(self) -> None:
        if not self._recording:
            return
        self._recording = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        audio = np.concatenate(self._chunks)[:, 0] if self._chunks else np.zeros(0, dtype=np.float32)
        threading.Thread(target=self._transcribe, args=(audio,), name="whisper", daemon=True).start()

    def _transcribe(self, audio: np.ndarray) -> None:
        if len(audio) < RATE * 0.3:
            return  # accidental tap
        if self._model is None:
            self._load_model()
        started = time.time()
        segments, _ = self._model.transcribe(audio, language="en", beam_size=1, vad_filter=True,
                                             initial_prompt="Rainbow Six Siege callouts, operators and maps.")
        text = " ".join(s.text.strip() for s in segments).strip()
        if text:
            self.bus.publish("ptt.question", text=text, seconds=round(time.time() - started, 2))
