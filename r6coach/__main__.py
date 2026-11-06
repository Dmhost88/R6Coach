"""Entry point: python -m r6coach [--replay FOLDER] [--say TEXT] [--check]"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from .config import load_config


def check(cfg) -> int:
    """Quick setup check: keys, knowledge data, audio devices, capture target."""
    from .knowledge import Knowledge

    ok = True
    def line(good: bool, msg: str) -> None:
        nonlocal ok
        ok &= good
        print(("  ok   " if good else "  FAIL ") + msg)

    print("R6 Coach setup check")
    line(bool(os.environ.get("ANTHROPIC_API_KEY")), "ANTHROPIC_API_KEY set (.env)")
    prov = cfg.voice.provider
    if prov == "openai":
        line(bool(os.environ.get("OPENAI_API_KEY")), "OPENAI_API_KEY set for voice (else offline voice is used)")
    elif prov == "elevenlabs":
        line(bool(os.environ.get("ELEVENLABS_API_KEY")) and bool(cfg.voice.elevenlabs_voice_id),
             "ELEVENLABS_API_KEY and voice id set")
    kb = Knowledge(notes_dir=cfg.storage.path() / "notes")
    line(len(kb.maps) > 0, f"map data loaded: {len(kb.maps)} maps")
    line(len(kb.operators) > 0, f"operator data loaded: {len(kb.operators)} operators")
    try:
        import sounddevice as sd
        line(sd.default.device[1] is not None, f"audio output: {sd.query_devices(kind='output')['name']}")
        line(sd.default.device[0] is not None, f"microphone: {sd.query_devices(kind='input')['name']}")
    except Exception as e:
        line(False, f"audio devices: {e}")
    print("All good." if ok else "Fix the FAIL lines above (see README).")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(prog="r6coach", description="Audio-only Rainbow Six Siege coach")
    p.add_argument("--replay", type=Path, help="play a folder of screenshots instead of capturing the game")
    p.add_argument("--replay-seconds", type=float, default=4.0, help="seconds each replay image is shown")
    p.add_argument("--say", help="speak one line with the configured voice and exit (voice test)")
    p.add_argument("--check", action="store_true", help="check keys, data and devices, then exit")
    p.add_argument("--collect", action="store_true",
                   help="voice-guided screenshot collection for calibration (no API calls)")
    p.add_argument("--quiet", action="store_true",
                   help="with --collect: no spoken checklist, just record (for real matches)")
    p.add_argument("--no-tray", action="store_true", help="run without the tray icon (Ctrl+C to quit)")
    p.add_argument("--no-ptt", action="store_true", help="disable push-to-talk and hotkeys")
    p.add_argument("--no-browser", action="store_true", help="don't open the dashboard automatically")
    p.add_argument("--port", type=int, help="dashboard port (default from config.yaml, 8765)")
    p.add_argument("--offline", action="store_true",
                   help="never call Claude (testing): ignores ANTHROPIC_API_KEY in .env and the environment")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    if args.offline:
        # An empty value (not a deleted one) stops load_env from reading the key from .env.
        # Setting it to "" in a shell deletes it on Windows, so it has to happen here.
        os.environ["ANTHROPIC_API_KEY"] = ""
        os.environ["R6COACH_OFFLINE"] = "1"

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2", "anthropic", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = load_config()
    if args.no_browser:
        cfg.dashboard.open_browser = False
    if args.port:
        cfg.dashboard.port = args.port

    if args.check:
        return check(cfg)

    if args.collect:
        from .collect import Collector
        Collector(cfg, quiet=args.quiet).run()
        return 0

    if args.say:
        from .bus import Bus
        from .voice.tts import Speaker
        sp = Speaker(cfg.voice, Bus(), cfg.storage.path() / "tts_cache")
        sp._thread.start()
        sp.say(args.say, priority=0)
        time.sleep(0.3)
        while not sp.idle():
            time.sleep(0.2)
        return 0

    from .app import App
    app = App(cfg, replay=args.replay, replay_seconds=args.replay_seconds, enable_ptt=not args.no_ptt)
    app.start()
    try:
        if args.no_tray:
            while not app.stopped.is_set():
                time.sleep(0.5)
        else:
            from .tray import run_tray
            run_tray(app)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
