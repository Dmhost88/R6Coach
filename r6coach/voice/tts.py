"""Text to speech with a disk cache and a priority speech queue.

Lines are synthesised once and cached as WAV files, so repeated lines ("forty-five seconds")
play instantly. The default voice is a local neural voice (Piper, on the CPU, no API key).
Providers are tried in order: the configured one, then the local Piper voice, then the
robotic Windows SAPI voice, so the coach is never silent.

Voice models are downloaded on first use into data/models/ (see README).
List local voices with: python -m r6coach.voice.tts --voices
"""

from __future__ import annotations

import hashlib
import heapq
import io
import itertools
import json
import logging
import os
import re
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
from ..spoken import clean_line

log = logging.getLogger(__name__)

COACH_STYLE = "Calm, confident esports analyst. Even pace, low energy, concise. Like a coach speaking into a headset."

# Lines worth pre-generating so they play instantly.
COMMON_LINES = [
    "Forty-five seconds.", "Twenty seconds left.", "Defuser is down. Play time.",
    "Defuser planted. Play it from range.", "You have the numbers. Trade, don't take duels alone.",
    "You're down a player. Group up and play together.", "Last one. Slow down, play your crosshair.",
    "Reinforce now.", "Drone it out before you commit.", "Check your flank.", "Coach muted.", "Coach on.",
]

LOCAL_FALLBACK = "piper"          # the local neural voice every other provider falls back to
LOCAL_ENGINES = ("piper", "kokoro")
PROVIDERS = ("piper", "kokoro", "openai", "elevenlabs", "sapi")

PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{family}/{locale}/{speaker}/{quality}/{name}{ext}"
# md5 digests from the piper-voices catalog (voices.json), for voices we recommend.
PIPER_MD5 = {
    "en_GB-alan-medium.onnx": "8f6b35eeb8ef6269021c6cb6d2414c9b",
    "en_GB-alan-medium.onnx.json": "b11d9afd0a8f5372c42a52fbd6e021d4",
}
# Calm English male Piper voices worth trying (any name from the piper-voices catalog works).
PIPER_SUGGESTED = ["en_GB-alan-medium", "en_US-ryan-medium", "en_US-joe-medium", "en_US-norman-medium",
                   "en_GB-northern_english_male-medium", "en_US-john-medium"]

KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/{name}"
KOKORO_SHA256 = {
    "kokoro-v1.0.onnx": "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a",
    "kokoro-v1.0.fp16.onnx": "f3a290d384fbb27966d462905c71a46cef9e5fd00516b40df32a0b4afe77ac96",
    "kokoro-v1.0.int8.onnx": "ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}

SENTENCE_PAUSE = 0.08     # seconds of silence between split sentences
SILENCE_LEVEL = 0.01      # leading/trailing samples quieter than this are trimmed before playing


def trim_silence(data: np.ndarray, rate: int, keep: float = 0.02) -> np.ndarray:
    """Cut the silence voices put at the start and end of a clip (Piper adds ~0.2 s each side),
    keeping a few ms so words don't click."""
    level = np.abs(data) if data.ndim == 1 else np.abs(data).max(axis=1)
    loud = np.flatnonzero(level > SILENCE_LEVEL)
    if loud.size == 0:
        return data
    pad = int(keep * rate)
    return data[max(0, loud[0] - pad): loud[-1] + 1 + pad]
PLAY_BLOCK_S = 0.05        # audio is written in blocks this long; a cut-off lands within one block


class _Prefetch:
    """Runs one synthesis on a helper thread so the next sentence is ready when this one ends."""

    def __init__(self, fn, arg):
        self._out = None
        self._t = threading.Thread(target=self._go, args=(fn, arg), name="tts-prefetch", daemon=True)
        self._t.start()

    def _go(self, fn, arg):
        try:
            self._out = fn(arg)
        except Exception:
            log.exception("prefetch synthesis failed")

    def result(self):
        self._t.join()
        return self._out


SPLIT_MIN_CHARS = 60     # shorter lines are synthesised whole (better prosody, one cache entry)


def pcm16_to_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def float_to_wav(audio: np.ndarray, rate: int) -> bytes:
    pcm = (np.clip(np.asarray(audio, dtype=np.float32).ravel(), -1.0, 1.0) * 32767.0).astype("<i2")
    return pcm16_to_wav(pcm.tobytes(), rate)


def split_sentences(text: str) -> list[str]:
    """Split a long multi-sentence line so the first sentence can play while the rest synthesise."""
    text = text.strip()
    if len(text) < SPLIT_MIN_CHARS:
        return [text] if text else []
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[\"'(A-Z0-9])", text) if p.strip()]
    # Glue very short fragments ("Okay.") onto the next sentence: a lone word sounds clipped.
    out: list[str] = []
    for p in parts:
        if out and len(out[-1].split()) < 2:
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out or [text]


def _download(url: str, dest: Path, md5: Optional[str] = None, sha256: Optional[str] = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    log.info("Downloading voice model %s -> %s (one time, please wait)", url, dest)
    h = hashlib.md5() if md5 else hashlib.sha256() if sha256 else None
    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                if h:
                    h.update(chunk)
    want = md5 or sha256
    if h and h.hexdigest() != want:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch for {dest.name}")
    os.replace(tmp, dest)
    log.info("Voice model ready: %s (%.0f MB)", dest.name, dest.stat().st_size / 1e6)


def _ort_session(path: Path, device: str, threads: int):
    import onnxruntime as rt

    so = rt.SessionOptions()
    if threads:
        so.intra_op_num_threads = threads
    so.inter_op_num_threads = 1
    # Idle worker threads sleep instead of spinning, so the voice doesn't eat CPU the game wants.
    so.add_session_config_entry("session.intra_op.allow_spinning", "0")
    providers = ["CPUExecutionProvider"]
    if device == "cuda":
        if hasattr(rt, "preload_dlls"):
            try:
                rt.preload_dlls()
            except Exception:
                pass
        if "CUDAExecutionProvider" in rt.get_available_providers():
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        else:
            log.warning("voice.local_device is cuda but onnxruntime-gpu is not installed; using CPU")
    return rt.InferenceSession(str(path), sess_options=so, providers=providers)


_espeak_lock = threading.Lock()   # espeak-ng has process-global state


class PiperVoice:
    """Piper (VITS) voice run directly on onnxruntime, phonemised with espeak-ng."""

    def __init__(self, models_dir: Path, name: str, device: str = "cpu", threads: int = 4) -> None:
        self.name = name
        d = models_dir / "piper"
        model, meta = d / f"{name}.onnx", d / f"{name}.onnx.json"
        for p in (meta, model):
            if not p.exists():
                _download(self.url(name, p.name[len(name):]), p, md5=PIPER_MD5.get(p.name))
        with open(meta, encoding="utf-8") as f:
            self.meta = json.load(f)
        self.ids = self.meta["phoneme_id_map"]
        self.rate = int(self.meta["audio"]["sample_rate"])
        lang = self.meta.get("espeak", {}).get("voice", "en-us")
        self.lang = "en-us" if lang == "en" else lang
        self.sess = _ort_session(model, device, threads)
        self._inputs = {i.name for i in self.sess.get_inputs()}
        self._backend = None
        self._lock = threading.Lock()

    @staticmethod
    def url(name: str, ext: str) -> str:
        locale, speaker, quality = name.split("-", 2)
        return PIPER_URL.format(family=locale.split("_")[0], locale=locale, speaker=speaker,
                                quality=quality, name=name, ext=ext)

    def _phonemize(self, text: str) -> str:
        with _espeak_lock:
            if self._backend is None:
                import espeakng_loader
                from phonemizer.backend import EspeakBackend
                from phonemizer.backend.espeak.wrapper import EspeakWrapper

                EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
                EspeakWrapper.set_library(espeakng_loader.get_library_path())
                logging.getLogger("phonemizer").setLevel(logging.ERROR)
                self._backend = EspeakBackend(self.lang, preserve_punctuation=True, with_stress=True,
                                              logger=logging.getLogger("phonemizer"))
            return self._backend.phonemize([text], strip=True)[0]

    def synth(self, text: str, speed: float = 1.0) -> tuple[np.ndarray, int]:
        ph = self._phonemize(text)
        ids = list(self.ids["^"])
        for ch in ph:
            if ch in self.ids:
                ids += self.ids[ch] + self.ids["_"]
        ids += self.ids["$"]
        inf = self.meta.get("inference", {})
        x = np.array([ids], dtype=np.int64)
        feed = {
            "input": x,
            "input_lengths": np.array([x.shape[1]], dtype=np.int64),
            "scales": np.array([inf.get("noise_scale", 0.667), inf.get("length_scale", 1.0) / max(speed, 0.1),
                                inf.get("noise_w", 0.8)], dtype=np.float32),
        }
        if "sid" in self._inputs:
            feed["sid"] = np.array([0], dtype=np.int64)
        with self._lock:
            audio = self.sess.run(None, feed)[0]
        return np.asarray(audio, dtype=np.float32).ravel(), self.rate


class KokoroVoice:
    """Kokoro-82M via kokoro-onnx. More natural than Piper but ~10x slower on CPU."""

    def __init__(self, models_dir: Path, voice: str, model: str = "kokoro-v1.0.onnx",
                 device: str = "cpu", threads: int = 4) -> None:
        from kokoro_onnx import Kokoro

        d = models_dir / "kokoro"
        paths = {}
        for name in (model, "voices-v1.0.bin"):
            p = d / name
            if not p.exists():
                _download(KOKORO_URL.format(name=name), p, sha256=KOKORO_SHA256.get(name))
            paths[name] = p
        self.kokoro = Kokoro.from_session(_ort_session(paths[model], device, threads), str(paths["voices-v1.0.bin"]))
        if voice not in self.kokoro.get_voices():
            raise ValueError(f"unknown Kokoro voice {voice!r}")
        self.voice = voice
        self._lock = threading.Lock()

    def synth(self, text: str, speed: float = 1.0) -> tuple[np.ndarray, int]:
        with self._lock, _espeak_lock:
            audio, rate = self.kokoro.create(text, voice=self.voice, speed=speed)
        return np.asarray(audio, dtype=np.float32), rate


class Synth:
    LOAD_RETRY_S = 300.0

    def __init__(self, cfg: VoiceCfg, cache_dir: Path, models_dir: Optional[Path] = None) -> None:
        self.cfg = cfg
        self.cache_dir = cache_dir
        self.models_dir = models_dir or cache_dir.parent / "models"
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.http = httpx.Client(timeout=20.0)
        self._sapi_lock = threading.Lock()
        self._load_lock = threading.Lock()
        self._engines: dict[tuple, object] = {}
        self._load_failed: dict[tuple, float] = {}
        self._noted: set[str] = set()

    # ---- provider order and cache keys

    def chain(self) -> list[str]:
        p = (self.cfg.provider or "").lower()
        if p not in PROVIDERS:
            self._note(f"bad-{p}", "unknown voice.provider %r, using the local voice", p)
        order = [p] if p in PROVIDERS else []
        for q in (LOCAL_FALLBACK, "sapi"):
            if q not in order:
                order.append(q)
        return order

    def _voice_id(self, provider: str) -> str:
        return {"openai": self.cfg.openai_voice, "elevenlabs": self.cfg.elevenlabs_voice_id,
                "piper": self.cfg.local_voice, "kokoro": f"{self.cfg.kokoro_model}/{self.cfg.kokoro_voice}",
                }.get(provider, "")

    def _key(self, text: str, provider: Optional[str] = None) -> str:
        provider = provider or self.chain()[0]
        raw = f"{provider}|{self._voice_id(provider)}|{float(self.cfg.speed):.3f}|{text}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _note(self, tag: str, msg: str, *args) -> None:
        if tag not in self._noted:
            self._noted.add(tag)
            log.warning(msg, *args)

    # ---- synthesis

    def wav(self, text: str) -> Optional[bytes]:
        for provider in self.chain():
            path = self.cache_dir / f"{self._key(text, provider)}.wav"
            if path.exists():
                return path.read_bytes()
            try:
                data = self._synth(provider, text)
            except Exception as e:
                log.warning("%s TTS failed (%s), trying the next voice", provider, e)
                data = None
            if data is None:
                continue
            if provider != "sapi":
                path.write_bytes(data)
            return data
        return None

    def _synth(self, provider: str, text: str) -> Optional[bytes]:
        if provider == "openai":
            return self._openai(text)
        if provider == "elevenlabs":
            return self._elevenlabs(text)
        if provider in LOCAL_ENGINES:
            engine = self.engine(provider)
            if engine is None:
                return None
            audio, rate = engine.synth(text, float(self.cfg.speed))
            return float_to_wav(audio, rate)
        if provider == "sapi":
            return self._sapi(text)
        return None

    def engine(self, provider: str = LOCAL_FALLBACK):
        """The loaded local voice engine (loads, and downloads, it on first use). None if unavailable."""
        c = self.cfg
        key = (provider, c.local_voice if provider == "piper" else (c.kokoro_model, c.kokoro_voice),
               c.local_device, c.local_threads)
        eng = self._engines.get(key)
        if eng is not None:
            return eng
        with self._load_lock:
            eng = self._engines.get(key)
            if eng is not None:
                return eng
            if time.time() - self._load_failed.get(key, -1e9) < self.LOAD_RETRY_S:
                return None
            t0 = time.perf_counter()
            try:
                if provider == "piper":
                    eng = PiperVoice(self.models_dir, c.local_voice, c.local_device, c.local_threads)
                else:
                    eng = KokoroVoice(self.models_dir, c.kokoro_voice, c.kokoro_model, c.local_device, c.local_threads)
            except Exception as e:
                self._load_failed[key] = time.time()
                log.warning("local voice %s could not be loaded (%s)", provider, e)
                return None
            log.info("local voice %s/%s loaded in %.1f s", provider, key[1], time.perf_counter() - t0)
            self._engines[key] = eng
            return eng

    def _openai(self, text: str) -> Optional[bytes]:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            self._note("nokey-openai", "no OPENAI_API_KEY, using the local voice")
            return None
        r = self.http.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": self.cfg.openai_model, "voice": self.cfg.openai_voice, "input": text,
                  "instructions": COACH_STYLE, "response_format": "wav", "speed": float(self.cfg.speed)},
        )
        r.raise_for_status()
        return r.content

    def _elevenlabs(self, text: str) -> Optional[bytes]:
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key or not self.cfg.elevenlabs_voice_id:
            self._note("nokey-eleven", "no ELEVENLABS_API_KEY or voice id, using the local voice")
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

    Priority 0 interrupts whatever is playing (used for push-to-talk answers and urgent live
    calls). It drops older queued lines of its own phase only; a line from another phase that it
    cut off plays again afterwards.
    Lines older than their ttl are dropped: stale advice is worse than none.
    Long multi-sentence lines play sentence by sentence; the next sentence is
    synthesised while the current one plays.
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
        self._playing_phase: Optional[str] = None
        self._playing_item: Optional[_Item] = None
        self._epoch = 0                  # bumped to cut off the line being played
        self._play_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="speaker", daemon=True)

    def start(self) -> None:
        self._thread.start()
        threading.Thread(target=self._warm_cache, name="tts-warm", daemon=True).start()

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        self._interrupt()

    def _interrupt(self) -> None:
        # Only the speaker thread touches PortAudio: calling sd.stop() from another thread while
        # the speaker is in sd.play/sd.wait corrupted the heap and killed the process (0xc0000374).
        # Bumping the epoch makes the speaker stop writing within one block.
        with self._play_lock:
            self._epoch += 1

    def say(self, text: str, priority: int = 2, ttl: float = 20.0, phase: str = "") -> None:
        text = text.strip()
        if not text or self.muted:
            return
        cleaned, reason = clean_line(text)
        if reason:
            log.info("line thinks aloud (%s): %r -> %r", reason, text, cleaned)
            if not cleaned:
                return
            text = cleaned
        if priority == 0:
            self._cut_in(phase)
        with self._cv:
            heapq.heappush(self._heap, _Item(priority, next(self._seq), text, time.time() + ttl, phase))
            self._cv.notify()

    def _cut_in(self, phase: str) -> None:
        """An urgent line (a push-to-talk answer, an urgent live call) plays next. Older queued
        lines of its own phase are out of date and go; everything else stays queued. The line
        playing now is cut off and, if it is from another phase (a prep step), played again after.
        It used to clear the whole queue, so asking a question in prep lost the prep plan."""
        with self._cv:
            self._heap = [i for i in self._heap if i.phase != phase]
            heapq.heapify(self._heap)
        with self._play_lock:
            cur = self._playing_item
            self._epoch += 1
            if cur is not None and cur.phase != phase and time.time() < cur.expires:
                with self._cv:
                    heapq.heappush(self._heap, cur)

    def clear(self, phase: Optional[str] = None) -> None:
        """Drop queued lines (optionally only those from one phase)."""
        with self._cv:
            self._heap = [i for i in self._heap if phase is not None and i.phase != phase]
            heapq.heapify(self._heap)

    def cancel(self, *phases: str) -> None:
        """Advice for these phases is out of date: drop it from the queue and cut off the line
        playing now if it is one of them."""
        with self._cv:
            self._heap = [i for i in self._heap if i.phase not in phases]
            heapq.heapify(self._heap)
        with self._play_lock:
            # Checked under the play lock, so a line that started in the meantime isn't cut by mistake.
            if self._playing_phase in phases:
                self._epoch += 1           # the speaker thread stops writing; see _interrupt

    def skip(self) -> None:
        self._interrupt()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if muted:
            self.clear()
            self._interrupt()

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
                self._playing_phase = item.phase
            with self._play_lock:
                self._playing_item = item
                epoch = self._epoch
            try:
                if time.time() > item.expires or self.muted:
                    continue
                self._speak(item, epoch)
            except Exception:
                log.exception("playback failed")
            finally:
                with self._play_lock:
                    self._playing_item = None
                self._playing = False
                self._playing_phase = None

    def _speak(self, item: _Item, epoch: int) -> None:
        chunks = split_sentences(item.text)
        nxt = self.synth.wav(chunks[0])
        announced = False
        for i in range(len(chunks)):
            wav = nxt
            if wav is None:
                nxt = self.synth.wav(chunks[i + 1]) if i + 1 < len(chunks) else None
                continue
            data, rate = sf.read(io.BytesIO(wav), dtype="float32")
            data = trim_silence(np.clip(data * float(self.cfg.volume), -1.0, 1.0), rate)
            if i + 1 < len(chunks):
                pad = np.zeros((int(SENTENCE_PAUSE * rate),) + data.shape[1:], dtype=data.dtype)
                data = np.concatenate([data, pad])
            with self._play_lock:
                if epoch != self._epoch or self.muted:
                    return
                if not announced:
                    self.bus.publish("coach.said", text=item.text, phase=item.phase, audio=True)
                    announced = True
            # Synthesise the next sentence while this one plays.
            nxt_job = None
            if i + 1 < len(chunks):
                nxt_job = _Prefetch(self.synth.wav, chunks[i + 1])
            ok = self._play_blocking(data, rate, epoch)
            nxt = nxt_job.result() if nxt_job else None
            if not ok or epoch != self._epoch:
                return
        if not announced:
            self.bus.publish("coach.said", text=item.text, phase=item.phase, audio=False)

    def _play_blocking(self, data: np.ndarray, rate: int, epoch: int) -> bool:
        """Play on a stream this thread owns, in short blocks, so a cut-off only stops writing.
        Returns False if the line was cut off."""
        if data.ndim == 1:
            data = data[:, None]
        data = np.ascontiguousarray(data, dtype=np.float32)
        block = max(256, int(rate * PLAY_BLOCK_S))
        with sd.OutputStream(samplerate=rate, channels=data.shape[1], dtype="float32",
                             device=self._device()) as stream:
            for s in range(0, len(data), block):
                if epoch != self._epoch or self.muted or self._stop:
                    stream.abort()
                    return False
                stream.write(data[s:s + block])
        return True

    def idle(self) -> bool:
        with self._cv:
            return not self._heap and not self._playing

    def _warm_cache(self) -> None:
        provider = self.synth.chain()[0]
        if provider == "sapi":
            return
        cloud_ready = (provider == "openai" and os.environ.get("OPENAI_API_KEY")) or \
                      (provider == "elevenlabs" and os.environ.get("ELEVENLABS_API_KEY"))
        if not cloud_ready:   # load the local model now, not on the first live line
            self.synth.engine(provider if provider in LOCAL_ENGINES else LOCAL_FALLBACK)
        from ..live import LINES

        for line in COMMON_LINES + list(LINES.values()):
            for chunk in split_sentences(line):
                while not self._stop and not self.idle():   # live lines go first
                    time.sleep(0.2)
                if self._stop:
                    return
                self.synth.wav(chunk)


def list_voices(models_dir: Path) -> str:
    out = ["Piper voices (voice.provider: piper, voice.local_voice: <name>):"]
    have = sorted(p.stem for p in (models_dir / "piper").glob("*.onnx"))
    for name in dict.fromkeys(have + PIPER_SUGGESTED):
        out.append(f"  {name}{'  (downloaded)' if name in have else ''}")
    out.append("  ...any other name from https://huggingface.co/rhasspy/piper-voices")
    out.append("Kokoro voices (voice.provider: kokoro, voice.kokoro_voice: <name>):")
    vf = models_dir / "kokoro" / "voices-v1.0.bin"
    if vf.exists():
        names = sorted(np.load(vf).keys())
        out.append("  " + ", ".join(n for n in names if n[:2] in ("am", "bm", "af", "bf")))
    else:
        out.append("  (downloaded on first use: am_michael, am_onyx, am_fenrir, bm_george, bm_fable, ...)")
    return "\n".join(out)


if __name__ == "__main__":
    import argparse
    import sys

    from ..config import load_config

    ap = argparse.ArgumentParser(prog="python -m r6coach.voice.tts")
    ap.add_argument("--voices", action="store_true", help="list local voices")
    ap.add_argument("--bench", metavar="TEXT", help="time synthesis of TEXT (no audio is played)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    models = cfg.storage.path() / "models"
    if a.voices:
        print(list_voices(models))
    elif a.bench:
        s = Synth(cfg.voice, Path(tempfile.mkdtemp()), models)
        prov = s.chain()[0] if s.chain()[0] in LOCAL_ENGINES else LOCAL_FALLBACK
        t0 = time.perf_counter()
        s.engine(prov)
        print(f"{prov}: load {1000 * (time.perf_counter() - t0):.0f} ms")
        for n in range(4):
            t0 = time.perf_counter()
            s._synth(prov, a.bench)
            print(f"  synth #{n + 1}: {1000 * (time.perf_counter() - t0):.0f} ms")
    else:
        ap.print_help()
        sys.exit(1)
