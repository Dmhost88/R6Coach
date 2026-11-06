"""Debug helper: time-to-first-line vs full answer for streamed coach advice (real API call).

    .venv/Scripts/python tools/try_stream.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r6coach.bus import Bus  # noqa: E402
from r6coach.coach import Coach  # noqa: E402
from r6coach.config import load_config  # noqa: E402
from r6coach.knowledge import Knowledge  # noqa: E402
from r6coach.llm import Claude  # noqa: E402
from r6coach.models import GameState, Screen  # noqa: E402

cfg = load_config()
if len(sys.argv) > 1:
    cfg.coach.effort = sys.argv[1]
bus = Bus()
claude = Claude(bus)
kb = Knowledge(cfg.storage.path() / "notes")
s = GameState(screen=Screen.prep_phase, map="Oregon", side="defense", site="B Laundry Room / B Supply Room",
              my_operator="Mute", teammate_operators=["Mute", "Kaid", "Mira", "Smoke", "Valkyrie"],
              score_us=2, score_them=1, round_number=4, team_reinforcements=10)


class Timing:
    def __init__(self):
        self.t0 = time.time()

    def say(self, text, priority=2, ttl=20.0, phase=""):
        print(f"  +{time.time() - self.t0:5.1f}s  {text}")

    def clear(self, *a):
        pass


class NoProfile:
    def map_record(self, m):
        return ""

    def round_ended(self, *a):
        pass


spk = Timing()
coach = Coach(cfg, bus, claude, kb, spk, NoProfile(), current_state=lambda: s, current_jpeg=lambda: None)
bus.subscribe("llm.usage", lambda ev: print(f"  usage: cache read {ev.data['cache_read']} write {ev.data['cache_write']} "
                                             f"${ev.data['cost']}  total {ev.data['seconds']}s"))
for attempt in (1, 2):
    print(f"\nattempt {attempt} (prep, {cfg.coach.effort} effort, streamed):")
    spk.t0 = time.time()
    coach._run("prep_phase", coach._advise_prep, s)
