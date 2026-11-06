"""Where rooms are on each map's blueprint floors, and which rooms a coach line talks about.

Layouts (r6coach/knowledge/data/layout/<map>.yaml, made by tools/locate_rooms.py) hold, per floor,
the blueprint image name and each room's box in percentages of the image. The images themselves
are Ubisoft's and stay in data/maps/<map>/ (git-ignored, personal use).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml

FLOOR_PREFIXES = ("B", "1F", "2F", "3F", "EXT", "ROOF")
_PREFIX_RE = re.compile(r"^(B|1F|2F|3F|EXT|ROOF)\s+(.*)$", re.I)


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

    def locate(self, map_id: Optional[str], location: Optional[str]) -> Optional[dict[str, str]]:
        """The player's HUD room ('2F Office', 'EXT Campfire') on the layout."""
        if not location:
            return None
        hits = self.mentions(map_id, location)
        return hits[0] if hits else None
