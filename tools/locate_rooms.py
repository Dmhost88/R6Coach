"""Locate each callout room on Ubisoft's blueprint images, once per map, with Claude vision.

The blueprints (tools/fetch_blueprints.py) have no room labels, so for every floor image this
draws a labelled percentage grid over it and asks Claude which floor it is and where each of our
callout rooms (r6coach/knowledge/data/callouts/<map>.yaml) sits, as a box in grid percentages.
Writes r6coach/knowledge/data/layout/<map>.yaml (our own data: no image content) plus a preview
image with the boxes drawn, in data/maps/<map>/preview/, to check by eye.

Costs one Claude call per floor image (~$0.05-0.10). Usage:
    python tools/locate_rooms.py chalet [oregon ...]   (or --all)
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from r6coach.bus import Bus  # noqa: E402
from r6coach.capture import encode_jpeg  # noqa: E402
from r6coach.config import load_env  # noqa: E402
from r6coach.llm import Claude  # noqa: E402

MAPS = ROOT / "data" / "maps"
CALLOUTS = ROOT / "r6coach" / "knowledge" / "data" / "callouts"
LAYOUT = ROOT / "r6coach" / "knowledge" / "data" / "layout"
WIDTH = 1600          # images are scaled to this width for the call and for the dashboard
MODEL = "claude-opus-5-5"


class RoomBox(BaseModel):
    name: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float


class FloorRead(BaseModel):
    floor: str          # B | 1F | 2F | 3F | ROOF
    notes: str
    rooms: list[RoomBox]


SYSTEM = """You map Rainbow Six Siege blueprint images to the game's room callouts.

The image is one floor of an official Ubisoft blueprint, seen from above, with a percentage grid \
drawn over it: x from 0 (left) to 100 (right), y from 0 (top) to 100 (bottom), labelled every 10 \
with thin lines every 5. The building interior is outlined in white; red hatching is line of sight; \
yellow dashed lines are breakable walls, yellow squares are floor hatches.

Floors: Ubisoft's files go BASEMENT, GROUND, 1ST FLOOR, 2ND FLOOR, ROOF. In-game callouts call the \
ground floor 1F and the next floor up 2F. Answer floor as one of B, 1F, 2F, 3F, ROOF (use the \
in-game naming). Outdoor areas (EXT) belong to the ground floor image (1F); a map whose ground \
level is the basement (no B) still uses the in-game prefixes in the room list.

For each room in the list that is on THIS floor, give its box (x0,y0 top-left, x1,y1 bottom-right) \
in grid percentages, read off the grid. Use furniture, stairs and the room's shape and position \
from your knowledge of the map to place it. Leave out rooms that are not on this floor. If you \
are unsure where a room is, still give your best box with low confidence (0.0-1.0). Never invent \
rooms that are not in the list. notes: one line on how sure the floor call is."""


def grid(img: np.ndarray) -> np.ndarray:
    out = img.copy()
    h, w = out.shape[:2]
    for p in range(0, 101, 5):
        major = p % 10 == 0
        col = (0, 255, 255) if major else (200, 200, 200)
        th = 2 if major else 1
        x, y = int(w * p / 100), int(h * p / 100)
        cv2.line(out, (x, 0), (x, h), col, th)
        cv2.line(out, (0, y), (w, y), col, th)
        if major:
            for (tx, ty) in ((x + 3, 18), (x + 3, h - 6)):
                cv2.putText(out, str(p), (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
                cv2.putText(out, str(p), (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            for (tx, ty) in ((3, y - 3), (w - 30, y - 3)):
                cv2.putText(out, str(p), (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
                cv2.putText(out, str(p), (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    return out


def load(path: Path) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    if w != WIDTH:
        img = cv2.resize(img, (WIDTH, round(h * WIDTH / w)), interpolation=cv2.INTER_AREA)
    return img


def locate(claude: Claude, map_id: str) -> None:
    callouts = yaml.safe_load((CALLOUTS / f"{map_id}.yaml").read_text(encoding="utf-8"))
    rooms = callouts.get("rooms") or {}
    room_list = "\n".join(f"{floor}: " + ", ".join(names) for floor, names in rooms.items())
    images = sorted(p for p in (MAPS / map_id).iterdir() if p.suffix.lower() in (".jpg", ".png", ".webp"))
    floors = []
    preview_dir = MAPS / map_id / "preview"
    preview_dir.mkdir(exist_ok=True)
    for i, path in enumerate(images, 1):
        img = load(path)
        prompt = (f"Map: {map_id.replace('_', ' ').title()}. Image {i} of {len(images)} "
                  f"(Ubisoft files are usually in floor order, lowest first).\n"
                  f"Room callouts by floor (in-game names):\n{room_list}")
        read = claude._parse(model=MODEL, effort="high", system=SYSTEM, purpose="layout",
                             schema=FloorRead, max_tokens=8000, content=[
                                 {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                              "data": __import__("base64").b64encode(
                                                                  encode_jpeg(grid(img), 85)).decode()}},
                                 {"type": "text", "text": prompt}])
        if read is None:
            print(f"  {path.name}: no answer")
            continue
        h, w = img.shape[:2]
        prev = img.copy()
        for r in read.rooms:
            p0 = (int(r.x0 * w / 100), int(r.y0 * h / 100))
            p1 = (int(r.x1 * w / 100), int(r.y1 * h / 100))
            col = (0, 220, 0) if r.confidence >= 0.6 else (0, 160, 255)
            cv2.rectangle(prev, p0, p1, col, 2)
            cv2.putText(prev, r.name, (p0[0] + 4, p0[1] + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
            cv2.putText(prev, r.name, (p0[0] + 4, p0[1] + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.imencode(".jpg", prev)[1].tofile(str(preview_dir / f"{read.floor}_{path.stem}.jpg"))
        floors.append({
            "floor": read.floor, "image": path.name, "size": [w, h], "notes": read.notes,
            "rooms": {r.name: {"box": [round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)],
                               "confidence": round(r.confidence, 2)} for r in read.rooms},
        })
        print(f"  {path.name}: {read.floor}, {len(read.rooms)} rooms ({read.notes})")
    LAYOUT.mkdir(exist_ok=True)
    out = {"map": map_id,
           "source": "Room boxes located by Claude on Ubisoft's official blueprints (grid percentages, "
                     "x left->right, y top->bottom). Check against data/maps/<map>/preview/.",
           "floors": floors}
    (LAYOUT / f"{map_id}.yaml").write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=140),
                                            encoding="utf-8")
    print(f"{map_id}: wrote layout/{map_id}.yaml, cost so far ${claude.total_cost:.2f}")


if __name__ == "__main__":
    load_env(ROOT / ".env")
    claude = Claude(Bus())
    ids = sorted(p.name for p in MAPS.iterdir() if p.is_dir()) if "--all" in sys.argv else sys.argv[1:]
    for m in ids:
        print(m)
        locate(claude, m)
