"""Check the setup briefs and layout features: every file loads, every brief names a real site,
and every placement lands on the map.

    .venv\\Scripts\\python tools\\check_setups.py [map_id ...]

Exits 1 on any problem. Unplaced icons are problems: fix the room name, or give `at`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r6coach.knowledge import DATA, Knowledge  # noqa: E402
from r6coach.maplayout import FEATURE_KINDS, MapLayouts  # noqa: E402
from r6coach.setups import SetupLibrary, describe  # noqa: E402


def main(only: list[str]) -> int:
    kb = Knowledge(notes_dir=ROOT / "data" / "notes")
    lays = MapLayouts(DATA / "layout", ROOT / "data" / "maps")
    lib = SetupLibrary(DATA / "setups")
    bad = 0
    for mid, err in lib.errors.items():
        if not only or mid in only:
            print(f"{mid}: FILE ERROR {err}")
            bad += 1
    for mid in sorted(set(lays.layouts) | set(lib.briefs)):
        if only and mid not in only:
            continue
        sites = {s.get("id") for s in (kb.maps.get(mid) or {}).get("sites") or []}
        briefs = lib.for_map(mid)
        lay = lays.get(mid) or {}
        for fl in lay.get("floors") or []:
            for f in fl.get("features") or []:
                if f.get("kind") not in FEATURE_KINDS or not isinstance(f.get("at"), list):
                    print(f"{mid}: bad feature on {fl['floor']}: {f}")
                    bad += 1
        n_feat = len(lays.features(mid))
        per_side = {s: sum(1 for b in briefs if b["side"] == s) for s in ("defense", "attack")}
        print(f"{mid}: {len(briefs)} briefs ({per_side['defense']} def, {per_side['attack']} att), "
              f"{n_feat} features, sides={lays.sides(mid) or '-'}")
        for b in briefs:
            if b["site"] not in sites:
                print(f"  {b['id']}: site {b['site']!r} not in maps/{mid}.yaml ({sorted(sites)})")
                bad += 1
            for i, p in enumerate(b["placements"], 1):
                if lays.resolve(mid, p) is None:
                    print(f"  {b['id']} #{i}: not on the map: {describe(p)}")
                    bad += 1
        covered = {b["site"] for b in briefs}
        missing = sorted(sites - covered)
        if briefs and missing:
            print(f"  sites with no brief: {missing}")
    print("OK" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
