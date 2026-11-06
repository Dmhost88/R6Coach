"""Advice quality eval: run fixed scenarios through the real coach, have a separate Claude judge
score each answer, and save the results so prompt changes can be compared.

    .venv/Scripts/python tools/eval_advice.py [label]

Costs roughly $0.5-1 per run (real API calls). Results go to data/evals/<time>_<label>.json.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, Field  # noqa: E402

from r6coach.bus import Bus  # noqa: E402
from r6coach.coach import Coach  # noqa: E402
from r6coach.config import load_config  # noqa: E402
from r6coach.knowledge import Knowledge  # noqa: E402
from r6coach.llm import Claude  # noqa: E402
from r6coach.memory import MatchMemory  # noqa: E402
from r6coach.models import GameState, Screen  # noqa: E402

JUDGE = """You are grading a Rainbow Six Siege voice coach's advice for a high-ranked player. You are \
strict, like a tier-1 coach reviewing an analyst. You have the map dossier and operator database. \
Score each criterion 1-5 (5 = excellent):
- specificity: every line names exact walls (by the rooms they separate), hatches (room + room below), \
angles, timings or operator interactions. 1 = generic advice that fits any map.
- accuracy: the named rooms, walls, hatches and operator interactions exist and are correct for the \
CURRENT version of this map and the current operator kits (use the dossier and your own knowledge). \
Deduct hard for invented or wrong locations.
- adaptation: uses this match's history (score, enemy operators seen, how the player died, patterns).
- actionability: the player knows exactly what to do next, in order.
- tts_fit: short spoken lines, no filler, no meta-talk, numbers as digits.
- fair_play: 5 if it never claims to know current enemy positions that weren't pinged/spotted, never \
suggests exploits; 1 if it does.
Then list the single biggest problem, and quote any location you believe is wrong."""


class Grade(BaseModel):
    specificity: int
    accuracy: int
    adaptation: int
    actionability: int
    tts_fit: int
    fair_play: int
    biggest_problem: str
    wrong_locations: list[str] = Field(description="Quoted locations believed wrong; empty if none")


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

    def log_line(self, *a):
        pass


def build(history: list[tuple]):
    """Coach with match memory pre-filled from (won, side, site, op, died_to, died_in, enemy_ops)."""
    cfg = load_config()
    bus = Bus()
    claude = Claude(bus)
    kb = Knowledge(cfg.storage.path() / "notes")
    state = {"s": GameState()}
    memory = MatchMemory(bus, lambda: state["s"], cfg.player_name)
    for won, side, site, op, died_to, died_in, ops in history:
        bus.publish("live.event", kind="round_reset")
        if died_to:
            state["s"] = GameState(my_location=died_in)
            bus.publish("player.died", killer=died_to)
        bus.publish("round.ended", won=won, state={"side": side, "site": site, "my_operator": op,
                                                   "enemy_operators": ops})
    # The coach is built after the history so it doesn't react to the replayed events.
    coach = Coach(cfg, bus, claude, kb, Quiet(), NoProfile(), current_state=lambda: state["s"],
                  current_jpeg=lambda: None)
    coach.memory = memory
    return coach, claude, kb, state


OREGON_HISTORY = [
    (True, "attack", "2F Kids' Dorms / 2F Dorms Main Hall", "Ash", None, None, ["Ela", "Kaid", "Mira"]),
    (True, "attack", "1F Kitchen / 1F Dining Hall", "Ash", "Enemy_E", "1F Dining Hall", ["Ela", "Kaid", "Valkyrie"]),
    (False, "attack", "2F Kids' Dorms / 2F Dorms Main Hall", "Thermite", "Enemy_E", "2F Dorms Main Hall",
     ["Ela", "Kaid", "Mira"]),
]
CHALET_HISTORY = [
    (True, "attack", "2F Master Bedroom / 2F Office", "Thermite", "Enemy_E", "2F Office", ["Kapkan", "Melusi"]),
    (True, "attack", "2F Master Bedroom / 2F Office", "Thermite", None, None, ["Kapkan", "Melusi", "Bandit"]),
]

SCENARIOS = [
    ("oregon def prep basement", OREGON_HISTORY, "prep",
     dict(screen=Screen.prep_phase, map="Oregon", side="defense", site="B Laundry Room / B Supply Room",
          my_operator="Mute", teammate_operators=["Mute", "Kaid", "Mira", "Smoke", "Valkyrie"],
          score_us=2, score_them=1, round_number=4, team_reinforcements=10)),
    ("oregon atk prep dorms", OREGON_HISTORY, "prep",
     dict(screen=Screen.prep_phase, map="Oregon", side="attack", site="2F Kids' Dorms / 2F Dorms Main Hall",
          my_operator="Ash", teammate_operators=["Ash", "Thermite", "Thatcher", "Buck", "Iana"],
          enemy_operators=["Mira", "Kaid"], score_us=2, score_them=1, round_number=4, spawn="Junkyard")),
    ("oregon def ban", OREGON_HISTORY, "ban",
     dict(screen=Screen.operator_ban, map="Oregon", side="defense", score_us=2, score_them=1, round_number=4,
          site_options=["2F Kids' Dorms / 2F Dorms Main Hall", "1F Kitchen / 1F Dining Hall",
                        "1F Meeting Hall / 1F Kitchen", "B Laundry Room / B Supply Room"])),
    ("chalet atk prep cellar", CHALET_HISTORY, "prep",
     dict(screen=Screen.prep_phase, map="Chalet", side="attack", site="B Wine Cellar / B Snowmobile Garage",
          my_operator="Thermite", teammate_operators=["Thermite", "Ash", "Thatcher", "Iana", "Buck"],
          enemy_operators=["Kapkan"], score_us=2, score_them=0, round_number=3, spawn="Campfire")),
    ("chalet atk pick", CHALET_HISTORY, "pick",
     dict(screen=Screen.operator_select, map="Chalet", side="attack", score_us=2, score_them=0, round_number=3,
          teammate_operators=["Ash", "Thatcher"],
          site_options=["2F Master Bedroom / 2F Office", "1F Bar / 1F Gaming Room",
                        "1F Kitchen / 1F Dining Room", "B Wine Cellar / B Snowmobile Garage"])),
    ("oregon death supply", OREGON_HISTORY, "death",
     dict(screen=Screen.action_phase, map="Oregon", side="defense", site="B Laundry Room / B Supply Room",
          my_operator="Mute", my_location="B Supply Room", timer_seconds=74, alive_us=3, alive_them=4,
          score_us=2, score_them=1, round_number=4, player_alive=False)),
]


def run(label: str) -> None:
    results = []
    total = 0.0
    for name, history, kind, fields in SCENARIOS:
        coach, claude, kb, state = build(history)
        s = GameState(**fields)
        state["s"] = s
        t = time.time()
        if kind == "prep":
            out = coach._advise_prep(s, True)
        elif kind == "ban":
            out = coach._advise_op_ban(s)
        elif kind == "pick":
            out = coach._advise_pick(s)
        else:
            out = coach._advise_death(s, "Enemy_E")
        secs = time.time() - t
        lines = out.lines if out else []
        grade = None
        if lines:
            prompt = (f"Scenario: {name}. State: {s.summary()}\n{coach._memory()}\n\n"
                      "Coach said:\n" + "\n".join(f"- {ln}" for ln in lines))
            grade = claude._parse(model="claude-opus-5-5", effort="medium",
                                  system=[JUDGE, kb.map_dossier(s.map)],
                                  content=[{"type": "text", "text": prompt}], schema=Grade, purpose="judge")
        total += claude.total_cost
        row = {"scenario": name, "seconds": round(secs, 1), "lines": lines,
               "grade": grade.model_dump() if grade else None}
        results.append(row)
        g = row["grade"] or {}
        print(f"\n== {name} ({secs:.1f}s)")
        for ln in lines:
            print("   -", ln)
        if g:
            print("   scores:", {k: g[k] for k in ("specificity", "accuracy", "adaptation", "actionability",
                                                     "tts_fit", "fair_play")})
            print("   problem:", g["biggest_problem"])
            if g["wrong_locations"]:
                print("   wrong:", g["wrong_locations"])
    keys = ("specificity", "accuracy", "adaptation", "actionability", "tts_fit", "fair_play")
    graded = [r["grade"] for r in results if r["grade"]]
    means = {k: round(statistics.mean(g[k] for g in graded), 2) for k in keys} if graded else {}
    print(f"\nMEANS ({label}):", means, f"| overall {round(statistics.mean(means.values()), 2) if means else '-'}"
          f" | cost ${total:.2f}")
    out_dir = Path(__file__).resolve().parent.parent / "data" / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{label}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"label": label, "means": means, "cost": round(total, 3), "results": results}, f, indent=1)
    print("saved", path)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "run")
