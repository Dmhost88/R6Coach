"""TTS tests. No audio device is used: playback is stubbed out."""

import io
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from r6coach.config import VoiceCfg
from r6coach.voice import tts
from r6coach.voice.tts import Speaker, Synth, split_sentences

MODELS = Path(__file__).resolve().parent.parent / "data" / "models"


class FakeEngine:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def synth(self, text, speed=1.0):
        self.calls.append((text, speed))
        if self.fail:
            raise RuntimeError("boom")
        t = np.arange(int(0.2 * 22050)) / 22050
        return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 22050


def is_wav(data: bytes) -> bool:
    if not data or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return False
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() > 0 and w.getframerate() > 0


def make_synth(tmp_path, monkeypatch, engines, sapi=b"", **cfg):
    s = Synth(VoiceCfg(**cfg), tmp_path / "cache", tmp_path / "models")
    monkeypatch.setattr(s, "engine", lambda provider="piper": engines.get(provider))
    sapi_calls = []

    def fake_sapi(text):
        sapi_calls.append(text)
        return sapi or None
    monkeypatch.setattr(s, "_sapi", fake_sapi)
    return s, sapi_calls


def test_local_synth_returns_wav_and_caches(tmp_path, monkeypatch):
    eng = FakeEngine()
    s, _ = make_synth(tmp_path, monkeypatch, {"piper": eng})
    a = s.wav("Check your flank.")
    assert is_wav(a)
    b = s.wav("Check your flank.")
    assert a == b and len(eng.calls) == 1               # second call is a cache hit
    assert eng.calls[0][1] == pytest.approx(1.05)        # configured speed reaches the engine
    assert len(list((tmp_path / "cache").glob("*.wav"))) == 1


def test_cache_key_includes_provider_voice_and_speed(tmp_path):
    s = Synth(VoiceCfg(), tmp_path / "c")
    k = s._key("Reinforce now.", "piper")
    assert k != s._key("Reinforce now.", "kokoro")
    s.cfg.local_voice = "en_US-ryan-medium"
    assert k != s._key("Reinforce now.", "piper")
    s.cfg.local_voice = VoiceCfg().local_voice
    s.cfg.speed = 1.2
    assert k != s._key("Reinforce now.", "piper")


def test_default_is_local_voice():
    assert VoiceCfg().provider == "piper"
    assert Synth.chain(type("S", (), {"cfg": VoiceCfg(), "_note": lambda *a: None})()) == ["piper", "sapi"]


def test_chain_order(tmp_path):
    assert Synth(VoiceCfg(provider="openai"), tmp_path).chain() == ["openai", "piper", "sapi"]
    assert Synth(VoiceCfg(provider="kokoro"), tmp_path).chain() == ["kokoro", "piper", "sapi"]
    assert Synth(VoiceCfg(provider="sapi"), tmp_path).chain() == ["sapi", "piper"]
    assert Synth(VoiceCfg(provider="nonsense"), tmp_path).chain() == ["piper", "sapi"]


def test_openai_without_key_falls_back_to_local(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    eng = FakeEngine()
    s, sapi = make_synth(tmp_path, monkeypatch, {"piper": eng}, provider="openai")
    assert is_wav(s.wav("Forty-five seconds."))
    assert len(eng.calls) == 1 and not sapi


def test_failing_provider_falls_back_to_local(tmp_path, monkeypatch):
    eng = FakeEngine()
    s, sapi = make_synth(tmp_path, monkeypatch, {"piper": eng}, provider="openai")

    def broken(text):
        raise RuntimeError("network down")
    monkeypatch.setattr(s, "_openai", broken)
    assert is_wav(s.wav("Coach on."))
    assert len(eng.calls) == 1 and not sapi


def test_kokoro_failure_falls_back_to_piper(tmp_path, monkeypatch):
    kok, pip = FakeEngine(fail=True), FakeEngine()
    s, sapi = make_synth(tmp_path, monkeypatch, {"kokoro": kok, "piper": pip}, provider="kokoro")
    assert is_wav(s.wav("Coach on."))
    assert len(kok.calls) == 1 and len(pip.calls) == 1 and not sapi


def test_local_failure_falls_back_to_sapi_uncached(tmp_path, monkeypatch):
    sapi_wav = tts.float_to_wav(np.zeros(100, dtype=np.float32), 16000)
    s, sapi = make_synth(tmp_path, monkeypatch, {"piper": FakeEngine(fail=True)}, sapi=sapi_wav)
    assert s.wav("Coach on.") == sapi_wav
    assert sapi == ["Coach on."]
    assert not list((tmp_path / "cache").glob("*.wav"))   # fallback audio isn't cached


def test_missing_engine_falls_back(tmp_path, monkeypatch):
    s, sapi = make_synth(tmp_path, monkeypatch, {}, sapi=b"")    # nothing works at all
    assert s.wav("Coach on.") is None
    assert sapi == ["Coach on."]


def test_split_sentences():
    assert split_sentences("Defuser is down. Play time.") == ["Defuser is down. Play time."]   # short: whole
    long = ("They have two roamers upstairs. Clear the stairs slowly with a drone first. "
            "Then hit site together from the hallway.")
    assert split_sentences(long) == ["They have two roamers upstairs.", "Clear the stairs slowly with a drone first.",
                                     "Then hit site together from the hallway."]
    assert split_sentences("Okay. Their Mute is jamming the hatch, so go through the wall from the garage.") == \
        ["Okay. Their Mute is jamming the hatch, so go through the wall from the garage."]
    assert split_sentences("") == []


class FakeSD:
    """Stands in for sounddevice: play() records, wait() takes a little time, stop() cuts it short."""

    def __init__(self, dur=0.15):
        self.played = []
        self.dur = dur
        self._stopped = threading.Event()

    def play(self, data, rate, device=None):
        self._stopped.clear()
        self.played.append(len(data))

    def wait(self):
        self._stopped.wait(self.dur)

    def stop(self):
        self._stopped.set()

    def query_devices(self):
        return []


class Bus:
    def __init__(self):
        self.events = []

    def publish(self, topic, **kw):
        self.events.append((topic, kw))


def make_speaker(tmp_path, monkeypatch, dur=0.15):
    fake = FakeSD(dur)
    monkeypatch.setattr(tts, "sd", fake)
    bus = Bus()
    sp = Speaker(VoiceCfg(), bus, tmp_path / "cache")
    eng = FakeEngine()
    monkeypatch.setattr(sp.synth, "engine", lambda provider="piper": eng)
    sp._thread.start()
    return sp, fake, bus, eng


def wait_idle(sp, timeout=5.0):
    end = time.time() + timeout
    time.sleep(0.05)
    while not sp.idle() and time.time() < end:
        time.sleep(0.02)


def test_speaker_plays_long_line_sentence_by_sentence(tmp_path, monkeypatch):
    sp, fake, bus, eng = make_speaker(tmp_path, monkeypatch)
    line = ("They have two roamers upstairs. Clear the stairs slowly with a drone first. "
            "Then hit site together from the hallway.")
    sp.say(line)
    wait_idle(sp)
    assert len(fake.played) == 3 and len(eng.calls) == 3
    assert [e for e in bus.events if e[0] == "coach.said"] == [("coach.said", {"text": line, "phase": "", "audio": True})]
    sp.stop()


def test_priority_zero_interrupts_remaining_sentences(tmp_path, monkeypatch):
    sp, fake, bus, eng = make_speaker(tmp_path, monkeypatch, dur=1.0)
    sp.say("They have two roamers upstairs. Clear the stairs slowly with a drone first. "
           "Then hit site together from the hallway.", priority=2)
    time.sleep(0.3)                                   # first sentence is playing
    assert len(fake.played) == 1
    sp.say("Answer: play the window.", priority=0)
    wait_idle(sp)
    said = [kw["text"] for t, kw in bus.events if t == "coach.said"]
    assert said[-1] == "Answer: play the window."
    assert len(fake.played) == 2                      # the old line's later sentences never played
    sp.stop()


def test_cancel_drops_a_phase_and_cuts_its_playing_line(tmp_path, monkeypatch):
    sp, fake, bus, eng = make_speaker(tmp_path, monkeypatch, dur=1.0)
    sp.say("Go Ace. Throw from cover. Then hold the stairs.", phase="operator_select")
    sp.say("Take Ace's claymore.", phase="operator_select")
    sp.say("Two down early.", phase="between_rounds")
    time.sleep(0.3)                                   # first sentence of the pick line is playing
    sp.cancel("operator_select", "prep_phase")
    wait_idle(sp)
    said = [kw["text"] for t, kw in bus.events if t == "coach.said"]
    assert said[-1] == "Two down early." and "Take Ace's claymore." not in said
    assert len(fake.played) == 2                      # 1 pick sentence, then the debrief line
    sp.stop()


def test_expired_lines_are_dropped(tmp_path, monkeypatch):
    sp, fake, bus, eng = make_speaker(tmp_path, monkeypatch, dur=0.6)
    sp.say("Check your flank.", priority=1)
    sp.say("Reinforce now.", priority=2, ttl=0.1)
    wait_idle(sp)
    assert len(fake.played) == 1
    sp.stop()


@pytest.mark.skipif(not (MODELS / "piper" / "en_GB-alan-medium.onnx").exists(),
                    reason="Piper voice not downloaded (python -m r6coach --say test downloads it)")
def test_real_piper_voice(tmp_path):
    s = Synth(VoiceCfg(provider="piper"), tmp_path / "cache", MODELS)
    t0 = time.perf_counter()
    data = s.wav("You have the numbers. Trade your teammate and don't take duels alone.")
    assert is_wav(data)
    with wave.open(io.BytesIO(data)) as w:
        assert 2.0 < w.getnframes() / w.getframerate() < 6.0
    t1 = time.perf_counter()
    assert s.wav("You have the numbers. Trade your teammate and don't take duels alone.") == data
    assert time.perf_counter() - t1 < 0.1 < t1 - t0      # cache hit is instant
