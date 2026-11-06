"""Eval for Claude.read_detail (fast pre-round menu reads) against saved full reads.

    .venv/Scripts/python tools/detail_eval.py [--day 2026-11-06] [--limit N]
        [--config claude-haiku-5-5:none:1600 ...] [--out results.json] [--rescore results.json]

Each saved frame data/frames/<day>/<time>_<screen>.jpg has a .json with Opus's full read_screen
result; that is the reference, after REF_FIX below (cases where the reference was checked
against the image by hand and found wrong). A config is model:effort:width; effort "none" =
thinking off where the model allows it, width 0 = send the frame as saved. Configs run in
parallel (one thread each), frames one after another within a config, so latencies are per call.
Spend stops at --cap USD in total.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from r6coach.bus import Bus  # noqa: E402
from r6coach.config import ROOT as CFG_ROOT, load_env  # noqa: E402
from r6coach.llm import Claude, _norm_op  # noqa: E402
from r6coach.models import Screen  # noqa: E402

SCREENS = ("map_ban", "operator_ban", "operator_select", "loadout")

DEFAULT_CONFIGS = [
    "claude-opus-5-5:low:0",
    "claude-sonnet-5-5:low:0",
    "claude-sonnet-5-5:none:0",
    "claude-haiku-5-5:low:0",
    "claude-haiku-5-5:none:0",
    "claude-sonnet-5-5:none:1280",
    "claude-haiku-5-5:none:1280",
]

# Reference corrections, checked by looking at the frame. Keys are frame stems; values replace
# reference fields. (The reference's own operator in teammate_operators is removed for all frames.)
#  - bans on operator select / loadout are only unlabelled icons; the reference copied them from
#    the earlier-screens hint, so the visible answer is [] (bans come from the ban screens);
#  - site_options on READY / loadout screens are not on screen (hint again);
#  - "MATCH POINT" / "OVERTIME" replace "ROUND n", so no round number is visible;
#  - the 093018 title card has no map label.
_ICON_BANS_ONLY = ("091745_operator_select", "092112_operator_select", "092120_loadout", "092129_operator_select",
                   "093044_operator_select", "093513_operator_select", "094920_operator_select",
                   "095410_operator_select")
REF_FIX: dict[str, dict] = {
    **{k: {"banned_operators": []} for k in _ICON_BANS_ONLY},
    "022646_loadout": {"site_options": []},
    "022653_operator_select": {"site_options": []},     # shows the chosen site only
    "092120_loadout": {"banned_operators": [], "site_options": [], "spawn": None},
    "092129_operator_select": {"banned_operators": [], "site_options": []},
    "093018_operator_ban": {"map": None},
    "094016_operator_ban": {"round_number": None},
    "094920_operator_select": {"banned_operators": [], "round_number": None},
}
# Not fixed: on 092052 a player-name tag covers "1F Kitchen", so "1F Dining Room" alone is a fair read.

FIELDS = ("screen", "map", "side", "round_number", "my_operator", "teammates", "banned",
          "site_options", "map_ban_options", "site", "spawn")


def norm_text(v) -> str:
    return " ".join(str(v).lower().replace("/", " / ").split()) if v else ""


def project(read: dict) -> dict:
    """The compared view of a read (a ScreenRead dump)."""
    mine = read.get("my_operator")
    team = {_norm_op(o) for o in read.get("teammate_operators") or [] if _norm_op(o)}
    if mine:
        team.discard(_norm_op(mine))
    return {
        "screen": read.get("screen"),
        "map": norm_text(read.get("map")),
        "side": read.get("side") or "",
        "round_number": read.get("round_number"),
        "my_operator": _norm_op(mine or ""),
        "teammates": sorted(team),
        "banned": sorted({_norm_op(o) for o in read.get("banned_operators") or [] if _norm_op(o)}),
        "site_options": sorted(norm_text(s) for s in read.get("site_options") or []),
        "map_ban_options": sorted(norm_text(m) for m in read.get("map_ban_options") or []),
        "site": norm_text(read.get("site")),
        "spawn": norm_text(read.get("spawn")),
    }


def reference(json_path: Path) -> dict:
    with open(json_path, encoding="utf-8") as f:
        ref = json.load(f)["read"]
    ref.update(REF_FIX.get(json_path.stem, {}))
    return project(ref)


def frames(day: str, limit: int) -> list[Path]:
    d = CFG_ROOT / "data" / "frames" / day
    out = sorted(p for p in d.glob("*.jpg") if p.stem.split("_", 1)[1] in SCREENS)
    if limit and len(out) > limit:
        step = len(out) / limit
        out = [out[int(i * step)] for i in range(limit)]
    return out


def run_config(spec: str, paths: list[Path], player: str, cap: float, spent: dict, lock, results: list) -> None:
    model, effort, width = spec.split(":")
    bus = Bus()
    claude = Claude(bus)
    last: dict = {}
    bus.subscribe("llm.usage", lambda ev: last.update(ev.data))
    bus.subscribe("error", lambda ev: print(f"[{spec}] ERROR {ev.data.get('message')}", flush=True))
    for p in paths:
        with lock:
            if spent["total"] >= cap:
                print(f"[{spec}] budget cap reached, stopping", flush=True)
                return
        screen = Screen(p.stem.split("_", 1)[1])
        last.clear()
        t = time.perf_counter()
        read = claude.read_detail(p.read_bytes(), screen, model, effort, player_name=player, max_width=int(width))
        secs = time.perf_counter() - t
        cost = float(last.get("cost") or 0.0)
        with lock:
            spent["total"] += cost
        results.append({"config": spec, "frame": p.stem, "seconds": round(secs, 2), "cost": cost,
                        "output_tokens": last.get("output_tokens"), "input_tokens": last.get("input_tokens"),
                        "read": read.model_dump(mode="json") if read else None})
        print(f"[{spec}] {p.stem} {secs:.1f}s ${cost:.4f}", flush=True)


def score(results: list[dict], day: str) -> None:
    d = CFG_ROOT / "data" / "frames" / day
    configs = list(dict.fromkeys(r["config"] for r in results))
    head = f"{'config':32} {'n':>3} {'med s':>6} {'p95 s':>6} {'out tok':>7} {'$/read':>7} " + " ".join(
        f"{f[:8]:>8}" for f in FIELDS)
    print("\n" + head)
    diffs: list[str] = []
    for c in configs:
        rows = [r for r in results if r["config"] == c]
        secs = sorted(r["seconds"] for r in rows)
        p95 = secs[min(len(secs) - 1, int(round(0.95 * (len(secs) - 1))))]
        agree = {f: 0 for f in FIELDS}
        n_ok = 0
        for r in rows:
            if not r["read"]:
                diffs.append(f"{c} {r['frame']}: NO READ")
                continue
            n_ok += 1
            ref = reference(d / f"{r['frame']}.json")
            got = project(r["read"])
            for f in FIELDS:
                if got[f] == ref[f]:
                    agree[f] += 1
                else:
                    diffs.append(f"{c} {r['frame']} {f}: got {got[f]!r} ref {ref[f]!r}")
        toks = [r["output_tokens"] or 0 for r in rows]
        cost = sum(r["cost"] for r in rows) / max(1, len(rows))
        print(f"{c:32} {len(rows):>3} {statistics.median(secs):>6.2f} {p95:>6.2f} "
              f"{statistics.median(toks):>7.0f} {cost:>7.4f} "
              + " ".join(f"{agree[f] / max(1, n_ok):>8.0%}" for f in FIELDS))
    print("\nDisagreements:")
    for line in diffs:
        print("  " + line)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-11-06")
    ap.add_argument("--limit", type=int, default=0, help="subsample to this many frames (0 = all)")
    ap.add_argument("--config", action="append", help="model:effort:width (repeatable)")
    ap.add_argument("--cap", type=float, default=4.0, help="stop when total spend reaches this (USD)")
    ap.add_argument("--player", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--rescore", default="", help="score a saved results file without calling the API")
    args = ap.parse_args()

    if args.rescore:
        with open(args.rescore, encoding="utf-8") as f:
            score(json.load(f), args.day)
        return

    load_env(CFG_ROOT / ".env")
    paths = frames(args.day, args.limit)
    configs = args.config or DEFAULT_CONFIGS
    results: list[dict] = []
    spent = {"total": 0.0}
    lock = threading.Lock()
    threads = [threading.Thread(target=run_config, args=(c, paths, args.player, args.cap, spent, lock, results))
               for c in configs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"\nTotal spend: ${spent['total']:.3f}")
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(results, f, indent=1, ensure_ascii=False)
    score(results, args.day)


if __name__ == "__main__":
    main()
