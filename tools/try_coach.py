"""Debug helper: run the real coach prompts on a made-up match state and print the advice.

    .venv/Scripts/python tools/try_coach.py [effort]
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
from r6coach.memory import MatchMemory  # noqa: E402
from r6coach.models import GameState, Screen  # noqa: E402

cfg = load_config()
if len(sys.argv) > 1:
    cfg.coach.effort = sys.argv[1]
bus = Bus()
bus.subscribe("error", lambda ev: print("ERROR", ev.data))
bus.subscribe("llm.usage", lambda ev: print(
    f"   usage {ev.data['purpose']}: in {ev.data.get('input_tokens')} | cache write {ev.data.get('cache_write')} "
    f"| cache read {ev.data.get('cache_read')} | out {ev.data.get('output_tokens')} | ${ev.data['cost']}"))
claude = Claude(bus)
kb = Knowledge(cfg.storage.path() / "notes")
state = {"s": GameState()}


class Quiet:
    def say(self, *a, **k):
        pass

    def clear(self, *a, **k):
        pass


class NoProfile:
    def map_record(self, m):
        return ""

    def round_ended(self, *a):
        pass


coach = Coach(cfg, bus, claude, kb, Quiet(), NoProfile(), current_state=lambda: state["s"],
              current_jpeg=lambda: None)
coach.memory = MatchMemory(bus, lambda: state["s"], cfg.player_name)

# Rounds 1-3 as they happened in the user's Oregon match (attack), simplified.
for won, killer, room, ops in [(True, None, None, ["Ela", "Kaid", "Mira"]),
                               (True, "Enemy_E", "2F Dorms Main Hall", ["Ela", "Kaid", "Valkyrie"]),
                               (False, "Enemy_E", "2F Dorms Main Hall", ["Ela", "Kaid", "Mira"])]:
    bus.publish("live.event", kind="round_reset")
    if killer:
        state["s"] = GameState(my_location=room)
        bus.publish("player.died", killer=killer)
    bus.publish("round.ended", won=won, state={"side": "attack", "site": "2F Kids' Dorms / 2F Dorms Main Hall",
                                               "my_operator": "Ash", "enemy_operators": ops})

s = GameState(screen=Screen.prep_phase, map="Oregon", side="defense", site="B Laundry Room / B Supply Room",
              my_operator="Mute", teammate_operators=["Mute", "Kaid", "Mira", "Smoke", "Valkyrie"],
              score_us=2, score_them=1, round_number=4, team_reinforcements=10)
state["s"] = s
dead = s.model_copy(update={"screen": Screen.action_phase, "my_location": "B Supply Room", "timer_seconds": 74,
                            "alive_us": 3, "alive_them": 4, "player_alive": False})
jobs = (("OPERATOR BAN (banning an attacker)", lambda: coach._advise_op_ban(s)),
        ("PREP (defense, deep)", lambda: coach._advise_prep(s, True)),
        ("DEATH DEBRIEF (no kill cam image)", lambda: coach._advise_death(dead, "Enemy_E")),
        ("ROUND DEBRIEF (deep)", lambda: coach._advise_debrief(dead, False)))
for name, fn in jobs:
    t = time.time()
    out = fn()
    print(f"\n== {name}  [{time.time() - t:.1f}s, effort={cfg.coach.effort}]")
    for line in (out.lines if out else ["<no answer>"]):
        print("  -", line)
    if out and out.recommended_bans:
        print("  bans:", out.recommended_bans)
print(f"\ncost so far ${claude.total_cost:.3f}")
