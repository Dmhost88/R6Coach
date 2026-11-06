"""Evaluate the local operator-card reader (r6coach/opcards.py) on saved frames. No API calls.

  python tools/opcards_eval.py                 # app frames under data/frames/*/ with cloud JSON
  python tools/opcards_eval.py --calib         # also the full-res calibration frames (hand labels)
  python tools/opcards_eval.py --player dmhost88 --verbose

App frames: every HHMMSS_<screen>.jpg whose screen is operator_select / operator_ban / loadout,
compared with the cloud read in the .json next to it. The cloud's teammate_operators often
includes the player's own operator and its banned_operators are often carried "from context"
(not on screen), so both are also compared in a corrected form: teammates minus my_operator, and
bans only where the frame shows them (reveal splash / overtime summary).

Prints per-field accuracy and median / p95 time per frame.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from r6coach.opcards import OperatorCardReader  # noqa: E402

FRAMES = ROOT / "data" / "frames"
CALIB = ROOT / "data" / "calibration"
SCREENS = ("operator_select", "operator_ban", "loadout")

# Hand labels for full-res calibration frames (checked on contact sheets).
# my, teammates, banned, ban_candidate ("-" = don't check).
CALIB_LABELS = {
    "2026-11-06_005400/010037_0096": ("Thermite", ["Fuze", "Brava"], [], "-"),
    "2026-11-06_005400/010930_0233": ("Lesion", ["Thorn", "Mira", "Kapkan"], [], "-"),
    "2026-11-06_005400/011353_0296": ("Rook", ["Bandit", "Goyo", "Aruni", "Lesion"], [], "-"),
    "2026-11-06_005400/005629_0028": ("Thermite", ["Fuze", "Kali", "Ram"], [], "-"),
    "2026-11-06_003854/003901_0002": ("Ace", [], [], "-"),
    "2026-11-06_003854/004307_0065": ("Ash", [], [], "-"),
    "2026-11-06_005400/005604_0025": (None, [], ["Mira", "Ace"], "-"),
    "2026-11-06_003854/004254_0062": (None, [], ["Castle"], "-"),
    "2026-11-06_005400/005548_0023": (None, [], [], "Sentry"),
    "2026-11-06_005400/010010_0092": (None, [], [], "Doc"),
    "2026-11-06_005400/010852_0229": (None, [], [], "Striker"),
    "2026-11-06_005400/010415_0157": (None, [], [], "Sentry"),
    "2026-11-06_005400/011307_0292": (None, [], [], "Striker"),
    "2026-11-06_003854/004247_0060": (None, [], [], "Goyo"),
    "2026-11-06_003854/004746_0137": (None, [], [], "Blitz"),
    # OPERATORS tab: heading is a hover (own slot still "?"), reported with low confidence.
    "2026-11-06_005400/005616_0027": ("Thermite", [], [], "-"),
    "2026-11-06_005400/010921_0232": ("Sentry", [], [], "-"),
    "2026-11-06_003854/004300_0064": ("Striker", [], [], "-"),
    "2026-11-06_003854/004828_0140": ("Sentry", [], [], "-"),
    # Greyed headings (taken by a teammate / not unlocked): nothing to report.
    "2026-11-06_005400/010441_0160": (None, [], [], "-"),
    "2026-11-06_005400/010033_0095": (None, [], [], "-"),
    "2026-11-06_003854/003856_0001": (None, [], [], "-"),
    # LOCATIONS tab and title cards: nothing readable.
    "2026-11-06_005400/005607_0026": (None, [], [], "-"),
    "2026-11-06_005400/010028_0094": (None, [], [], "-"),
    "2026-11-06_005400/010909_0231": (None, [], [], "-"),
    "2026-11-06_005400/011326_0294": (None, [], [], "-"),
    "2026-11-06_003854/004257_0063": (None, [], [], "-"),
    "2026-11-06_003854/004759_0139": (None, [], [], "-"),
    "2026-11-06_005400/005545_0022": (None, [], [], "-"),
    "2026-11-06_005400/010023_0093": (None, [], [], "-"),
    "2026-11-06_005400/011320_0293": (None, [], [], "-"),
}


def load(path: Path) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)


def same_set(a, b) -> bool:
    return sorted(a or []) == sorted(b or [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", default=str(FRAMES), help="folder of HHMMSS_<screen>.jpg + .json pairs")
    ap.add_argument("--calib", action="store_true", help="also run the hand-labelled calibration frames")
    ap.add_argument("--player", default="dmhost88")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    reader = OperatorCardReader(player_name=args.player)
    times: list[float] = []
    rows: list[tuple] = []   # (name, field, ok, local, truth)

    def record(name: str, fld: str, ok: bool, local, truth) -> None:
        rows.append((name, fld, ok, local, truth))
        if args.verbose or not ok:
            mark = "ok " if ok else "BAD"
            print(f"  {mark} {name:<34} {fld:<14} local={local!r:<40} truth={truth!r}")

    # ---------------------------------------------------------------- app frames with cloud JSON
    paths = sorted(p for p in Path(args.frames).rglob("*.jpg") if any(p.stem.endswith(s) for s in SCREENS))
    print(f"App frames: {len(paths)} under {args.frames}")
    for p in paths:
        js = p.with_suffix(".json")
        if not js.exists():
            continue
        cloud = json.loads(js.read_text(encoding="utf-8")).get("read", {})
        img = load(p)
        reader.read(img)   # warm (first call loads sessions)
        r = reader.read(img)
        ms = r.ms if r else 0.0
        times.append(ms)
        name = f"{p.parent.name}/{p.stem}"
        screen_truth = p.stem.split("_", 1)[1]
        if screen_truth == "loadout":
            screen_truth = "operator_select"
        local_screen = r.screen if r else "unknown"
        # Title cards (BAN PHASE / ROUND n) are saved as operator_ban but carry nothing readable.
        card = cloud.get("notes", "") and not any((cloud.get("my_operator"), cloud.get("teammate_operators"),
                                                   cloud.get("banned_operators"))) and local_screen == "unknown"
        record(name, "screen", local_screen == screen_truth or bool(card), local_screen, screen_truth)
        c_my = cloud.get("my_operator")
        c_tm = [t for t in (cloud.get("teammate_operators") or [])]
        c_tm_fixed = [t for t in c_tm if t != c_my]
        c_ban = cloud.get("banned_operators") or []
        l_my = r.my_operator if r else None
        l_tm = r.teammates if r else []
        l_ban = r.banned if r else []
        record(name, "my_operator", l_my == c_my, l_my, c_my)
        record(name, "teammates", same_set(l_tm, c_tm), l_tm, c_tm)
        record(name, "teammates_fix", same_set(l_tm, c_tm_fixed), l_tm, c_tm_fixed)
        record(name, "banned", same_set(l_ban, c_ban), l_ban, c_ban)
        if l_ban or (r and r.screen == "operator_ban" and "sum0" in r.texts) or (r and "splash_l" in r.texts):
            record(name, "banned_visible", same_set(l_ban, c_ban), l_ban, c_ban)
        if args.verbose and r:
            print(f"      tab={r.tab} source={r.source} locked={r.locked} picked={r.picked_slots} "
                  f"cand={r.ban_candidate} conf={r.confidence:.2f} {ms:.0f} ms texts={r.texts}")

    # ---------------------------------------------------------------- calibration frames (hand labels)
    if args.calib:
        print(f"\nCalibration frames: {len(CALIB_LABELS)}")
        for key, (my, tm, ban, cand) in CALIB_LABELS.items():
            session, frame = key.split("/")
            p = CALIB / session / "auto" / f"{frame}.jpg"
            if not p.exists():
                print(f"  missing {p}")
                continue
            img = load(p)
            r = reader.read(img)
            times.append(r.ms if r else 0.0)
            l_my = r.my_operator if r else None
            record(key, "my_operator", l_my == my, l_my, my)
            record(key, "teammates", same_set(r.teammates if r else [], tm), r.teammates if r else [], tm)
            record(key, "banned", same_set(r.banned if r else [], ban), r.banned if r else [], ban)
            if cand != "-":
                record(key, "ban_candidate", (r.ban_candidate if r else None) == cand,
                       r.ban_candidate if r else None, cand)
            if args.verbose and r:
                print(f"      screen={r.screen} tab={r.tab} source={r.source} locked={r.locked} "
                      f"picked={r.picked_slots} conf={r.confidence:.2f} {r.ms:.0f} ms texts={r.texts}")

    # ---------------------------------------------------------------- summary
    print("\nPer-field accuracy:")
    for fld in ("screen", "my_operator", "teammates", "teammates_fix", "banned", "banned_visible", "ban_candidate"):
        sel = [ok for _, f, ok, _, _ in rows if f == fld]
        if sel:
            print(f"  {fld:<15} {sum(sel):3d}/{len(sel):<3d} {100 * sum(sel) / len(sel):5.1f}%")
    if times:
        ts = sorted(times)
        print(f"\nTiming over {len(ts)} reads: median {statistics.median(ts):.0f} ms, "
              f"p95 {ts[int(0.95 * (len(ts) - 1))]:.0f} ms, max {ts[-1]:.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
