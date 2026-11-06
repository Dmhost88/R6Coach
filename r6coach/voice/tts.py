"""Text to speech with a disk cache and a priority speech queue.

Lines are synthesised once and cached as WAV files, so repeated lines ("forty-five seconds")
play instantly with no network call. If the cloud provider fails, the Windows offline voice
is used so the coach is never silent.
"""

from __future__ import annotations

import hashlib
import heapq
import io
import itertools
import logging
import os
import tempfile
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx
import numpy as np
import sounddevice as sd
import soundfile as sf

from ..bus import Bus
from ..config import VoiceCfg

log = logging.getLogger(__name__)

COACH_STYLE = "Calm, confident esports analyst. Even pace, low energy, concise. Like a coach speaking into a headset."

# Lines worth pre-generating so they play instantly.
COMMON_LINES = [
    "Forty-five seconds.", "Twenty seconds left.", "Defuser is down. Play time.",
    "Defuser planted. Play it from range.", "You have the numbers. Trade, don't take duels alone.",
    "You're down a player. Group up and play together.", "Last one. Slow down, play your crosshair.",
    "Reinforce now.", "Drone it out before you commit.", "Check your flank.", "Coach muted.", "Coach on.",
]


def pcm16_to_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class Synth:
    def __init__(self, cfg: VoiceCfg, cache_dir: Path) -> None:
        self.cfg = cfg
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.http = httpx.Client(timeout=20.0)
        self._sapi_lock = threading.Lock()

    def _key(self, text: str) -> str:
        voice = {"openai": self.cfg.openai_voice, "elevenlabs": self.cfg.elevenlabs_voice_id}.get(self.cfg.provider, "")
        return hashlib.sha1(f"{self.cfg.provider}|{voice}|{text}".encode("utf-8")).hexdigest()

    def wav(self, text: str) -> Optional[bytes]:
        path = self.cache_dir / f"{self._key(text)}.wav"
        if path.exists():
            return path.read_bytes()
        data = None
        try:
            if self.cfg.provider == "openai":
                data = self._openai(text)
            elif self.cfg.provider == "elevenlabs":
                data = self._elevenlabs(text)
        except Exception as e:
            log.warning("cloud TTS failed (%s), using offline voice", e)
        if data is None:
            data = self._sapi(text)
            if data is None:
                return None
            return data  # don't cache fallback audio under the cloud key
        path.write_bytes(data)
        return data

    def _openai(self, text: str) -> Optional[bytes]:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            return None
        r = self.http.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": self.cfg.openai_model, "voice": self.cfg.openai_voice, "input": text,
                  "instructions": COACH_STYLE, "response_format": "wav"},
        )
        r.raise_for_status()
        return r.content

    def _elevenlabs(self, text: str) -> Optional[bytes]:
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key or not self.cfg.elevenlabs_voice_id:
            return None
        r = self.http.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.cfg.elevenlabs_voice_id}",
            params={"output_format": "pcm_24000"},
            headers={"xi-api-key": key},
            json={"text": text, "model_id": self.cfg.elevenlabs_model},
        )
        r.raise_for_status()
        return pcm16_to_wav(r.content, 24000)

    def _sapi(self, text: str) -> Optional[bytes]:
        try:
            import pyttsx3
        except ImportError:
            return None
        with self._sapi_lock:
            fd, tmp = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                engine = pyttsx3.init()
                engine.setProperty("rate", 185)
                engine.save_to_file(text, tmp)
                engine.runAndWait()
                engine.stop()
                return Path(tmp).read_bytes()
            except Exception:
                log.exception("offline TTS failed")
                return None
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


@dataclass(order=True)
class _Item:
    priority: int
    seq: int
    text: str = field(compare=False)
    expires: float = field(compare=False)
    phase: str = field(compare=False)


class Speaker:
    """Speaks queued lines one at a time. Lower priority number = more urgent.

    Priority 0 interrupts whatever is playing (used for push-to-talk answers).
    Lines older than their ttl are dropped: stale advice is worse than none.
    """

    def __init__(self, cfg: VoiceCfg, bus: Bus, cache_dir: Path) -> None:
        self.cfg = cfg
        self.bus = bus
        self.synth = Synth(cfg, cache_dir)
        self.muted = False
        self._heap: list[_Item] = []
        self._seq = itertools.count()
        self._cv = threading.Condition()
        self._stop = False
        self._playing = False
        self._thread = threading.Thread(target=self._run, name="speaker", daemon=True)

    def start(self) -> None:
        self._thread.start()
        threading.Thread(target=self._warm_cache, name="tts-warm", daemon=True).start()

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        sd.stop()

    def say(self, text: str, priority: int = 2, ttl: float = 20.0, phase: str = "") -> None:
        text = text.strip()
        if not text or self.muted:
            return
        if priority == 0:
            self.clear()
            sd.stop()
        with self._cv:
            heapq.heappush(self._heap, _Item(priority, next(self._seq), text, time.time() + ttl, phase))
            self._cv.notify()

    def clear(self, phase: Optional[str] = None) -> None:
        """Drop queued lines (optionally only those from one phase)."""
        with self._cv:
            self._heap = [i for i in self._heap if phase is not None and i.phase != phase]
            heapq.heapify(self._heap)

    def skip(self) -> None:
        sd.stop()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if muted:
            self.clear()
            sd.stop()

    def _device(self) -> Optional[int]:
        if not self.cfg.output_device:
            return None
        want = self.cfg.output_device.lower()
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0 and want in d["name"].lower():
                return i
        return None

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._heap and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                item = heapq.heappop(self._heap)
                self._playing = True
            try:
                if time.time() > item.expires or self.muted:
                    continue
                wav = self.synth.wav(item.text)
                if wav is None:
                    self.bus.publish("coach.said", text=item.text, phase=item.phase, audio=False)
                    continue
                data, rate = sf.read(io.BytesIO(wav), dtype="float32")
                data = np.clip(data * float(self.cfg.volume), -1.0, 1.0)
                self.bus.publish("coach.said", text=item.text, phase=item.phase, audio=True)
                sd.play(data, rate, device=self._device())
                sd.wait()
            except Exception:
                log.exception("playback failed")
            finally:
                self._playing = False

    def idle(self) -> bool:
        with self._cv:
            return not self._heap and not self._playing

    def _warm_cache(self) -> None:
        if self.cfg.provider not in ("openai", "elevenlabs"):
            return
        from ..live import LINES

        for line in COMMON_LINES + list(LINES.values()):
            if self._stop:
                return
            self.synth.wav(line)
