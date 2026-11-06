"""Advice regression eval on a REAL recorded Ranked match.

Each scenario in tools/real_scenarios.yaml is a decision point from the recorded match (Chalet,
won 4-1): the game state at that moment, the match history up to it (replayed into MatchMemory),
the coach method to run, and the real frame to attach where the live coach attaches a screenshot.
A separate Claude judge scores the answer with the synthetic eval's rubric plus `grounded`: does the
advice match what the real round showed (site, operators seen, how the player actually died).

    .venv/Scripts/python tools/eval_real.py [label] [--only name[,name]] [--compare previous.json]
    .venv/Scripts/python tools/eval_real.py --dry-run          # validate + show prompts' inputs, no API
    .venv/Scripts/python tools/eval_real.py --compare-files old.json new.json   # delta only, no API

Real API calls: roughly $1.5-3 for all scenarios (stops at --cap, default $3).
Results go to data/evals/<time>_real_<label>.json.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

SCENARIOS_PATH = Path(__file__).resolve().parent / "real_scenarios.yaml"
CALIBRATION = ROOT / "data" / "calibration"
EVALS = ROOT / "data" / "evals"

METHODS = ("ban", "pick", "prep", "death", "between_rounds")
IMAGE_METHODS = {"ban", "pick", "death"}        # where the live coach attaches the screenshot
KEYS = ("specificity", "accuracy", "adaptation", "actionability", "tts_fit", "fair_play", "grounded")
SCREEN_FOR = {"ban": "operator_ban", "pick": "operator_select", "prep": "prep_phase",
              "death": "action_phase", "between_rounds": "round_end"}
KILL_TEAMS = {"us", "them", "them_tk", "us_tk"}  # *_tk = friendly fire, not counted by match memory

GROUNDED = """
- grounded: the advice is checked against what ACTUALLY happened in this real round (given below \
as GROUND TRUTH, which the coach did not see). 5 = it fits the real round: the right site, plans \
around operators that were really on the enemy team, and for a death debrief the mistake it names \
matches how the player really died (killer's operator, weapon, room, player count). 1 = it is about \
a different site, invents operators or a death that didn't happen, or contradicts the facts. Before \
the site was known (bans, attack picks), judge whether the advice would have helped in the round \
that really followed, without punishing it for not knowing the future."""


# ---------------------------------------------------------------------------------- scenarios
def load_scenarios(path: Path = SCENARIOS_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    problems = validate(data)
    if problems:
        raise ValueError("invalid scenarios file:\n  " + "\n  ".join(problems))
    return data


def validate(data: Any) -> list[str]:
    """Return a list of problems (empty = valid). Does not touch the disk or the API."""
    p: list[str] = []
    if not isinstance(data, dict):
        return ["top level must be a mapping"]
    match = data.get("match") or {}
    for k in ("session", "map", "player"):
        if not match.get(k):
            p.append(f"match.{k} missing")
    rounds = {r.get("n"): r for r in data.get("rounds") or []}
    if not rounds:
        p.append("no rounds")
    for n, r in rounds.items():
        where = f"round {n}"
        if r.get("side") not in ("attack", "defense"):
            p.append(f"{where}: side must be attack/defense")
        for k in ("site", "operator"):
            if not r.get(k):
                p.append(f"{where}: {k} missing")
        if not isinstance(r.get("won"), bool):
            p.append(f"{where}: won must be true/false")
        for i, kill in enumerate(r.get("kills") or []):
            if not (isinstance(kill, list) and len(kill) == 3 and kill[1] in KILL_TEAMS):
                p.append(f"{where}: kill {i} must be [killer, team, victim] with team in {sorted(KILL_TEAMS)}")
        d = r.get("death")
        if d:
            for k in ("killer", "room"):
                if not d.get(k):
                    p.append(f"{where}: death.{k} missing")
            victims = [k[2] for k in r.get("kills") or [] if isinstance(k, list) and len(k) == 3]
            if match.get("player") not in victims:
                p.append(f"{where}: death given but the player is not a victim in kills")
        if not (r.get("frames") or {}).get("round_end"):
            p.append(f"{where}: frames.round_end missing")
    names = set()
    for i, s in enumerate(data.get("scenarios") or []):
        name = s.get("name") or f"#{i}"
        if name in names:
            p.append(f"{name}: duplicate name")
        names.add(name)
        if s.get("method") not in METHODS:
            p.append(f"{name}: method must be one of {METHODS}")
        r = rounds.get(s.get("round"))
        if r is None:
            p.append(f"{name}: round {s.get('round')} not in rounds")
        if s.get("method") in IMAGE_METHODS and not s.get("frame"):
            p.append(f"{name}: {s.get('method')} needs a frame (the coach attaches a screenshot)")
        if s.get("method") == "death" and r is not None and not r.get("death"):
            p.append(f"{name}: death scenario for a round where the player didn't die")
        if s.get("method") == "between_rounds" and not isinstance(s.get("won"), bool):
            p.append(f"{name}: between_rounds needs won: true/false")
        try:
            game_state(data, s)
        except Exception as e:  # pydantic validation error
            p.append(f"{name}: state invalid: {e}")
    if not data.get("scenarios"):
        p.append("no scenarios")
    return p


def frame_path(data: dict, frame: str) -> Path:
    return CALIBRATION / data["match"]["session"] / "auto" / f"{frame}.jpg"


def referenced_frames(data: dict) -> list[str]:
    out = [s["frame"] for s in data["scenarios"] if s.get("frame")]
    for r in data["rounds"]:
        out += [f for f in (r.get("frames") or {}).values() if f]
    return sorted(set(out))


def game_state(data: dict, sc: dict):
    from r6coach.models import GameState, Screen

    fields = dict(sc.get("state") or {})
    fields.setdefault("map", data["match"]["map"])
    fields.setdefault("screen", Screen(SCREEN_FOR[sc["method"]]) if sc.get("method") in SCREEN_FOR else Screen.other)
    if sc.get("method") == "death":
        fields.setdefault("player_alive", False)
    return GameState(**fields)


def history_rounds(data: dict, sc: dict) -> list[dict]:
    """Rounds the coach would remember at this moment: earlier rounds, plus the one that just ended
    for a between-rounds debrief."""
    upto = sc["round"] if sc["method"] == "between_rounds" else sc["round"] - 1
    return [r for r in data["rounds"] if r["n"] <= upto]


def truth_text(data: dict, sc: dict) -> str:
    """Ground truth for the judge (never shown to the coach)."""
    r = next(r for r in data["rounds"] if r["n"] == sc["round"])
    me = data["match"]["player"]
    lines = [f"GROUND TRUTH for round {r['n']} of this real match ({data['match']['map']}, "
             f"final result {data['match'].get('result', '?')}):",
             f"- player side {r['side']}, played {r['operator']}, site {r['site']}, "
             f"round {'WON' if r['won'] else 'LOST'}" + (", defuser planted" if r.get("planted") else ""),
             f"- player's team: {', '.join(r.get('team') or []) or '?'}; bans: {', '.join(r.get('bans') or []) or 'none'}",
             f"- enemy operators confirmed this round: {', '.join(r.get('enemy_ops') or []) or 'none identified'}"
             " (others were on the team but not identified)"]
    feed = []
    for killer, team, victim in r.get("kills") or []:
        tag = " (enemy friendly fire)" if team == "them_tk" else ""
        feed.append(f"{killer} > {victim}{tag}")
    if feed:
        lines.append("- killfeed in order: " + "; ".join(feed))
    d = r.get("death")
    if d:
        alive = d.get("alive_before") or ["?", "?"]
        lines.append(f"- {me} died to {d['killer']} ({d.get('killer_operator', '?')}, {d.get('weapon', '?')}) in "
                     f"{d['room']} with about {d.get('timer', '?')}s left, at {alive[0]}v{alive[1]}")
    else:
        lines.append(f"- {me} survived the round")
    if r.get("notes"):
        lines.append("- what happened: " + " ".join(str(r["notes"]).split()))
    return "\n".join(lines)


# ---------------------------------------------------------------------------------- coach
def frame_jpeg(data: dict, frame: Optional[str], width: int) -> Optional[bytes]:
    """The real frame, downscaled and encoded exactly as the watcher does before upload."""
    if not frame:
        return None
    import cv2
    import numpy as np

    from r6coach.capture import downscale, encode_jpeg

    img = cv2.imdecode(np.fromfile(str(frame_path(data, frame)), np.uint8), cv2.IMREAD_COLOR)
    return encode_jpeg(downscale(img, width))


def replay_history(bus, state_box: dict, rounds: list[dict], player: str) -> None:
    """Publish each finished round as the live app would, so MatchMemory records it."""
    from r6coach.models import GameState

    for r in rounds:
        bus.publish("live.event", kind="round_reset")
        state_box["s"] = GameState(side=r["side"], site=r["site"], my_operator=r["operator"])
        d = r.get("death")
        for killer, team, victim in r.get("kills") or []:
            if team in ("us", "them"):
                bus.publish("live.event", kind="kill", killer_team=team, mine=killer == player,
                            killer=killer, victim=victim)
            if victim == player and d:
                state_box["s"] = state_box["s"].model_copy(update={"my_location": d["room"], "player_alive": False})
                bus.publish("player.died", killer=d["killer"])
        if r.get("planted"):
            bus.publish("live.event", kind="planted")
        bus.publish("round.ended", won=r["won"], state={
            "side": r["side"], "site": r["site"], "my_operator": r["operator"],
            "enemy_operators": r.get("enemy_ops") or [], "defuser_planted": bool(r.get("planted"))})


def build(data: dict, sc: dict, jpeg: Optional[bytes]):
    from eval_advice import NoProfile, Quiet

    from r6coach.bus import Bus
    from r6coach.coach import Coach
    from r6coach.config import load_config
    from r6coach.knowledge import Knowledge
    from r6coach.llm import Claude
    from r6coach.memory import MatchMemory
    from r6coach.models import GameState

    cfg = load_config()
    bus = Bus()
    claude = Claude(bus)
    kb = Knowledge(cfg.storage.path() / "notes")
    state = {"s": GameState()}
    memory = MatchMemory(bus, lambda: state["s"], data["match"]["player"])
    replay_history(bus, state, history_rounds(data, sc), data["match"]["player"])
    # The coach is built after the history so it doesn't react to the replayed events.
    coach = Coach(cfg, bus, claude, kb, Quiet(), NoProfile(), current_state=lambda: state["s"],
                  current_jpeg=lambda: jpeg)
    coach.memory = memory
    return cfg, coach, claude, kb, state


def run_coach(coach, sc: dict, s, killer: Optional[str]):
    m = sc["method"]
    if m == "ban":
        return coach._advise_op_ban(s)
    if m == "pick":
        return coach._advise_pick(s)
    if m == "prep":
        # Defense plans are made during operator select with deeper reasoning; attack plans aren't.
        return coach._advise_prep(s, s.side == "defense")
    if m == "death":
        return coach._advise_death(s, killer)
    return coach._advise_debrief(s, sc["won"])


def judge(claude, kb, data: dict, sc: dict, s, coach, lines: list[str], killer: Optional[str]):
    from eval_advice import JUDGE, Grade
    from pydantic import create_model

    RealGrade = create_model("RealGrade", __base__=Grade, grounded=(int, ...))
    extra = f" The player was just killed by {killer} (from the killfeed)." if sc["method"] == "death" else ""
    shot = " The coach was also shown the real screenshot from this moment." if sc["method"] in IMAGE_METHODS else ""
    prompt = (f"Scenario: {sc['name']} (coach method: {sc['method']}). State: {s.summary()}.{extra}{shot}\n"
              f"{coach._memory()}\n\n{truth_text(data, sc)}\n\n"
              "Coach said:\n" + "\n".join(f"- {ln}" for ln in lines))
    return claude._parse(model="claude-opus-5-5", effort="medium",
                         system=[JUDGE + GROUNDED, kb.operators_dossier(), kb.map_dossier(s.map)],
                         content=[{"type": "text", "text": prompt}], schema=RealGrade, purpose="judge")


# ---------------------------------------------------------------------------------- results
def means_of(results: list[dict]) -> dict:
    graded = [r["grade"] for r in results if r.get("grade")]
    out = {}
    for k in KEYS:
        vals = [g[k] for g in graded if isinstance(g.get(k), (int, float))]
        if vals:
            out[k] = round(statistics.mean(vals), 2)
    return out


def delta(old: dict, new: dict) -> dict:
    """Per-criterion and per-scenario score changes from an old result file to a new one."""
    crit = {}
    om, nm = old.get("means") or means_of(old.get("results", [])), new.get("means") or means_of(new.get("results", []))
    for k in KEYS:
        if k in om or k in nm:
            a, b = om.get(k), nm.get(k)
            crit[k] = {"old": a, "new": b, "delta": round(b - a, 2) if a is not None and b is not None else None}
    old_rows = {r["scenario"]: r for r in old.get("results", [])}
    scen = {}
    for r in new.get("results", []):
        o = old_rows.get(r["scenario"])
        if not o or not o.get("grade") or not r.get("grade"):
            scen[r["scenario"]] = None
            continue
        d = {k: r["grade"][k] - o["grade"][k] for k in KEYS if k in r["grade"] and k in o["grade"]}
        d["total"] = sum(d.values())
        scen[r["scenario"]] = d
    only_old = sorted(set(old_rows) - {r["scenario"] for r in new.get("results", [])})
    return {"criteria": crit, "scenarios": scen, "missing_in_new": only_old}


def format_delta(d: dict, old_label: str = "old", new_label: str = "new") -> str:
    short = {"specificity": "spec", "accuracy": "acc", "adaptation": "adapt", "actionability": "act",
             "tts_fit": "tts", "fair_play": "fair", "grounded": "grnd", "total": "total"}
    out = [f"DELTA {new_label} vs {old_label}", "criterion       old    new    delta"]
    for k, v in d["criteria"].items():
        fmt = lambda x: "  -  " if x is None else f"{x:5.2f}"  # noqa: E731
        sign = "  -" if v["delta"] is None else f"{v['delta']:+.2f}"
        out.append(f"{k:<14} {fmt(v['old'])}  {fmt(v['new'])}  {sign}")
    cols = [k for k in KEYS] + ["total"]
    out.append("")
    out.append(f"{'scenario':<30}" + "".join(f"{short[c]:>7}" for c in cols))
    for name, row in d["scenarios"].items():
        if row is None:
            out.append(f"{name:<30}   (no comparable grade)")
            continue
        out.append(f"{name:<30}" + "".join(f"{row[c]:>+7d}" if c in row else f"{'-':>7}" for c in cols))
    if d["missing_in_new"]:
        out.append("not in new run: " + ", ".join(d["missing_in_new"]))
    return "\n".join(out)


def load_result(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------------- main
def run(label: str, only: Optional[list[str]], compare: Optional[str], cap: float) -> Optional[Path]:
    data = load_scenarios()
    scenarios = data["scenarios"]
    if only:
        scenarios = [s for s in scenarios if s["name"] in only]
        unknown = set(only) - {s["name"] for s in scenarios}
        if unknown:
            sys.exit(f"unknown scenario(s): {', '.join(sorted(unknown))}")
    missing = [f for f in referenced_frames(data) if not frame_path(data, f).exists()]
    if missing:
        sys.exit(f"recorded frames missing ({len(missing)}), e.g. {frame_path(data, missing[0])}")

    results, total, worst = [], 0.0, 0.0
    for sc in scenarios:
        estimate = max(worst, 0.2)
        if total + estimate > cap:
            print(f"\nSTOP: spend ${total:.2f} + ~${estimate:.2f} for the next scenario would pass the ${cap:.2f} cap")
            break
        rnd = next(r for r in data["rounds"] if r["n"] == sc["round"])
        killer = (rnd.get("death") or {}).get("killer") if sc["method"] == "death" else None
        from r6coach.config import load_config
        width = load_config().capture.upload_width
        jpeg = frame_jpeg(data, sc.get("frame"), width) if sc["method"] in IMAGE_METHODS else None
        cfg, coach, claude, kb, state = build(data, sc, jpeg)
        s = game_state(data, sc)
        state["s"] = s
        t = time.time()
        out = run_coach(coach, sc, s, killer)
        secs = time.time() - t
        lines = out.lines if out else []
        grade = judge(claude, kb, data, sc, s, coach, lines, killer) if lines else None
        spent = claude.total_cost
        total += spent
        worst = max(worst, spent)
        row = {"scenario": sc["name"], "method": sc["method"], "round": sc["round"], "frame": sc.get("frame"),
               "image": jpeg is not None, "seconds": round(secs, 1), "cost": round(spent, 4), "lines": lines,
               "recommended_operator": out.recommended_operator if out else "",
               "recommended_bans": out.recommended_bans if out else [],
               "grade": grade.model_dump() if grade else None}
        results.append(row)
        g = row["grade"] or {}
        print(f"\n== {sc['name']} [{sc['method']}, R{sc['round']}] ({secs:.1f}s, ${spent:.3f}, total ${total:.2f})")
        for ln in lines:
            print("   -", ln)
        if out and out.recommended_operator:
            print("   pick:", out.recommended_operator)
        if out and out.recommended_bans:
            print("   bans:", out.recommended_bans)
        if g:
            print("   scores:", {k: g[k] for k in KEYS})
            print("   problem:", g["biggest_problem"])
            if g["wrong_locations"]:
                print("   wrong:", g["wrong_locations"])
        else:
            print("   (no advice or no grade)")

    means = means_of(results)
    overall = round(statistics.mean(means.values()), 2) if means else None
    print(f"\nMEANS ({label}):", means, f"| overall {overall if overall is not None else '-'} | cost ${total:.2f}"
          f" | {len(results)}/{len(data['scenarios'])} scenarios")
    EVALS.mkdir(parents=True, exist_ok=True)
    path = EVALS / f"{time.strftime('%Y%m%d_%H%M%S')}_real_{label}.json"
    doc = {"label": label, "kind": "real", "source": data["match"], "means": means, "overall": overall,
           "cost": round(total, 3), "only": only, "results": results}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    print("saved", path)
    if compare:
        old = load_result(compare)
        print()
        print(format_delta(delta(old, doc), old.get("label", "old"), label))
    return path


def dry_run() -> None:
    from r6coach.bus import Bus
    from r6coach.memory import MatchMemory
    from r6coach.models import GameState

    data = load_scenarios()
    missing = [f for f in referenced_frames(data) if not frame_path(data, f).exists()]
    print(f"{len(data['scenarios'])} scenarios valid; {len(missing)} referenced frames missing")
    for sc in data["scenarios"]:
        bus = Bus()
        box = {"s": GameState()}
        mem = MatchMemory(bus, lambda: box["s"], data["match"]["player"])
        replay_history(bus, box, history_rounds(data, sc), data["match"]["player"])
        print(f"\n== {sc['name']} [{sc['method']}] frame={sc.get('frame')} image={sc['method'] in IMAGE_METHODS}")
        print("   state:", game_state(data, sc).summary())
        print("   " + mem.text().replace("\n", "\n   "))


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("label", nargs="?", default="run")
    ap.add_argument("--only", help="comma-separated scenario names")
    ap.add_argument("--compare", help="previous result JSON to diff against after the run")
    ap.add_argument("--compare-files", nargs=2, metavar=("OLD", "NEW"), help="diff two result files, no API")
    ap.add_argument("--cap", type=float, default=3.0, help="stop before spending more than this (USD)")
    ap.add_argument("--dry-run", action="store_true", help="validate and show inputs, no API calls")
    a = ap.parse_args(argv)
    if a.compare_files:
        old, new = (load_result(p) for p in a.compare_files)
        print(format_delta(delta(old, new), old.get("label", "old"), new.get("label", "new")))
        return
    if a.dry_run:
        dry_run()
        return
    only = [x.strip() for x in a.only.split(",")] if a.only else None
    run(a.label, only, a.compare, a.cap)


if __name__ == "__main__":
    main()
