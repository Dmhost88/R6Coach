"""Where rooms are on each map's blueprint floors, and which rooms a coach line talks about.

Layouts (r6coach/knowledge/data/layout/<map>.yaml, made by tools/locate_rooms.py) hold, per floor,
the blueprint image name and each room's box in percentages of the image. The images themselves
are Ubisoft's and stay in data/maps/<map>/ (git-ignored, personal use).

Optional orientation data, added by hand:

    sides: {top: "Lakeside", bottom: "Front Yard", left: "Cliffside", right: "Campfire"}
    floors:
      - floor: 2F
        features:                       # fixed map furniture, points in % of the image
          - {kind: stairs, name: "Library Stairs", at: [64.5, 64.0]}
          - {kind: hatch, name: "Library hatch", at: [35.5, 77.0]}
          - {kind: window, name: "Big Window", at: [58.0, 60.0]}
          - {kind: door, name: "Main Entrance", at: [50.0, 95.0]}
          - {kind: label, name: "Lakeside", at: [80.0, 10.0]}   # an outside area

`sides` says what lies beyond each edge of the images (all floors of a map share an orientation).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml

FLOOR_PREFIXES = ("B", "1F", "2F", "3F", "EXT", "ROOF")
_PREFIX_RE = re.compile(r"^(B|1F|2F|3F|EXT|ROOF)\s+(.*)$", re.I)
FEATURE_KINDS = ("stairs", "hatch", "window", "door", "label")
EDGES = ("top", "bottom", "left", "right")


def _box_centre(b: list[float]) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def wall_point(a: list[float], b: list[float]) -> tuple[float, float]:
    """Middle of the wall between two room boxes: the centre of their overlap when the boxes touch
    or overlap a little; otherwise the middle of the gap between them."""
    x0, x1 = max(a[0], b[0]), min(a[2], b[2])
    y0, y1 = max(a[1], b[1]), min(a[3], b[3])
    if x0 <= x1 and y0 <= y1:                       # boxes overlap
        return ((x0 + x1) / 2, (y0 + y1) / 2)
    if x0 <= x1:                                    # stacked: shared x span, gap in y
        return ((x0 + x1) / 2, (y0 + y1) / 2)
    if y0 <= y1:                                    # side by side: shared y span, gap in x
        return ((x0 + x1) / 2, (y0 + y1) / 2)
    ca, cb = _box_centre(a), _box_centre(b)          # diagonal neighbours
    return ((ca[0] + cb[0]) / 2, (ca[1] + cb[1]) / 2)


def edge_point(box: list[float], edge: Optional[str]) -> tuple[float, float]:
    """Middle of one side of a room box; without an edge, the side nearest the image border."""
    if edge not in EDGES:
        dist = {"left": box[0], "top": box[1], "right": 100 - box[2], "bottom": 100 - box[3]}
        edge = min(dist, key=dist.get)
    cx, cy = _box_centre(box)
    return {"top": (cx, box[1]), "bottom": (cx, box[3]), "left": (box[0], cy), "right": (box[2], cy)}[edge]


class MapLayouts:
    def __init__(self, layout_dir: Path, images_dir: Path) -> None:
        self.layout_dir = layout_dir
        self.images_dir = images_dir
        self.layouts: dict[str, dict[str, Any]] = {}
        if layout_dir.exists():
            for p in sorted(layout_dir.glob("*.yaml")):
                with p.open(encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if data.get("floors"):
                    self.layouts[p.stem] = data
        self._patterns: dict[str, list[tuple[re.Pattern, str, str]]] = {}

    def get(self, map_id: Optional[str]) -> Optional[dict[str, Any]]:
        return self.layouts.get(map_id or "")

    def image_path(self, map_id: str, image: str) -> Optional[Path]:
        """Only files a layout names, inside that map's folder."""
        lay = self.get(map_id)
        if not lay or image not in {f["image"] for f in lay["floors"]}:
            return None
        p = self.images_dir / map_id / image
        return p if p.is_file() else None

    # ------------------------------------------------------------------ mentions
    def _rooms(self, map_id: str) -> list[tuple[re.Pattern, str, list[str]]]:
        """(pattern, room, floors it's on) per room name, longest first so 'Bar Stock' beats 'Bar'."""
        if map_id not in self._patterns:
            floors_of: dict[str, list[str]] = {}
            for fl in self.layouts[map_id]["floors"]:
                for room in fl.get("rooms") or {}:
                    floors_of.setdefault(room, []).append(fl["floor"])
            out = []
            for room, floors in floors_of.items():
                # "Kids' Dorms" also matches "Kids Dorms"; any spacing between words.
                words = r"\s+".join(re.escape(w).replace("'", "'?") for w in room.split())
                prefix = r"(?:(B|1F|2F|3F|EXT|ROOF)\s+)?"
                out.append((re.compile(rf"\b{prefix}{words}\b", re.I), room, floors))
            out.sort(key=lambda t: -len(t[1]))
            self._patterns[map_id] = out
        return self._patterns[map_id]

    def mentions(self, map_id: Optional[str], text: str,
                 prefer_floor: Optional[str] = None) -> list[dict[str, str]]:
        """Rooms named in a line, in the order they're said: [{'floor': '2F', 'room': 'Office'}, ...].
        A room on two floors is placed by, in order: a floor prefix in the text ('2F Office'), the
        floor the line's other rooms are on, then prefer_floor (the player's floor)."""
        if not map_id or map_id not in self.layouts or not text:
            return []
        taken: list[tuple[int, int]] = []
        hits: list[tuple[int, str, list[str]]] = []
        for pat, room, floors in self._rooms(map_id):
            for m in pat.finditer(text):
                s, e = m.span()
                if any(s < te and e > ts for ts, te in taken):
                    continue
                said = (m.group(1) or "").upper()
                cands = [f for f in floors if f == said] if said and said != "EXT" else floors
                if not cands:
                    continue
                taken.append((s, e))
                hits.append((s, room, cands))
        hits.sort()
        sure = [c[0] for _, _, c in hits if len(c) == 1]
        out, seen = [], set()
        for _, room, cands in hits:
            if len(cands) == 1:
                floor = cands[0]
            else:
                floor = (next((f for f in sure if f in cands), None)
                         or (prefer_floor if prefer_floor in cands else None) or cands[0])
            if (floor, room) not in seen:
                seen.add((floor, room))
                out.append({"floor": floor, "room": room})
        return out

    # ------------------------------------------------------------------ positions
    def floor_index(self, map_id: Optional[str], floor: str, rooms: tuple[str, ...] = ()) -> Optional[int]:
        """Which image a floor name means. Some maps have two images with the same floor name; the
        one holding the named rooms wins."""
        lay = self.get(map_id)
        if not lay:
            return None
        cands = [i for i, f in enumerate(lay["floors"]) if f["floor"].upper() == str(floor).upper()]
        for i in cands:
            have = lay["floors"][i].get("rooms") or {}
            if rooms and all(r in have for r in rooms):
                return i
        return cands[0] if cands else None

    def resolve(self, map_id: Optional[str], place: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Where a placement or feature goes: {'fi': image index, 'floor', 'x', 'y'} in % of the
        image, or None when it can't be placed (unknown floor or room)."""
        named = tuple(r for r in (place.get("between") or []) if str(r).lower() != "exterior")
        if place.get("room"):
            named = (place["room"],)
        fi = self.floor_index(map_id, place.get("floor", ""), named)
        if fi is None:
            return None
        rooms = self.layouts[map_id]["floors"][fi].get("rooms") or {}
        pt: Optional[tuple[float, float]] = None
        if place.get("at"):
            pt = (float(place["at"][0]), float(place["at"][1]))
        elif place.get("between"):
            a, b = place["between"]
            if a in rooms and str(b).lower() == "exterior":
                pt = edge_point(rooms[a]["box"], place.get("edge"))
            elif a in rooms and b in rooms:
                pt = wall_point(rooms[a]["box"], rooms[b]["box"])
        elif place.get("room") in rooms:
            pt = _box_centre(rooms[place["room"]]["box"])
        if pt is None:
            return None
        dx, dy = place.get("nudge") or (0, 0)
        x = min(100.0, max(0.0, pt[0] + dx))
        y = min(100.0, max(0.0, pt[1] + dy))
        return {"fi": fi, "floor": self.layouts[map_id]["floors"][fi]["floor"], "x": round(x, 2), "y": round(y, 2)}

    def features(self, map_id: Optional[str]) -> list[dict[str, Any]]:
        """Stairs, hatches, windows, doors and outside labels from the layout, placed."""
        lay = self.get(map_id)
        out = []
        for fi, fl in enumerate((lay or {}).get("floors") or []):
            for feat in fl.get("features") or []:
                at = feat.get("at")
                if feat.get("kind") in FEATURE_KINDS and isinstance(at, list) and len(at) == 2:
                    out.append({"fi": fi, "floor": fl["floor"], "kind": feat["kind"], "name": feat.get("name", ""),
                                "x": float(at[0]), "y": float(at[1])})
        return out

    def sides(self, map_id: Optional[str]) -> dict[str, str]:
        s = (self.get(map_id) or {}).get("sides") or {}
        return {k: str(v) for k, v in s.items() if k in EDGES and v}

    def locate(self, map_id: Optional[str], location: Optional[str]) -> Optional[dict[str, str]]:
        """The player's HUD room ('2F Office', 'EXT Campfire') on the layout."""
        if not location:
            return None
        hits = self.mentions(map_id, location)
        return hits[0] if hits else None
