"""Standard setup briefs: per map and site, where each piece of utility goes, with a picture slot.

Briefs are data (r6coach/knowledge/data/setups/<map>.yaml, format in the README there). Pictures
are the player's own (data/setups/<map>/<slot>.jpg|png|webp, git-ignored): one slot per placement,
named <brief id>-<placement number>. The library re-reads the YAML when it changes
(`reload_if_changed`), and knows each brief's line in the repository (`source`) so the dashboard's
setup library can link to it.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

import yaml

log = logging.getLogger(__name__)

SIDES = ("defense", "attack")
KINDS = {
    # defense
    "reinforce": "defense", "hatch": "defense", "open": "defense", "rotate": "defense",
    "mira": "defense", "bandit": "defense", "kaid": "defense", "castle": "defense",
    "deployable": "defense", "trap": "defense", "camera": "defense", "anchor": "any",
    # attack
    "thermite": "attack", "hibana": "attack", "maverick": "attack", "ace": "attack",
    "breach": "attack", "entry": "attack", "drone": "attack", "plant": "attack",
}
SHOT_EXTS = (".jpg", ".jpeg", ".png", ".webp")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,80}$")
SLOT_RE = re.compile(r"^([a-z0-9][a-z0-9-]{1,80})-(\d{1,2})$")
ID_LINE_RE = re.compile(r"^\s*-\s+id:\s*['\"]?([a-z0-9][a-z0-9-]{1,80})", re.M)
# The repository the briefs live in, for "view the source" links in the dashboard's setup library.
REPO_URL = "https://github.com/Dmhost88/R6Coach"
SOURCE_DIR = "r6coach/knowledge/data/setups"


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


class SetupError(ValueError):
    pass


def validate(map_id: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    """The briefs in one file, checked. Raises SetupError naming the first problem."""
    if (data.get("map") or map_id) != map_id:
        raise SetupError(f"{map_id}: map is {data.get('map')!r}")
    briefs = data.get("briefs") or []
    if not isinstance(briefs, list):
        raise SetupError(f"{map_id}: briefs must be a list")
    seen: set[str] = set()
    for b in briefs:
        bid = b.get("id")
        if not isinstance(bid, str) or not ID_RE.match(bid):
            raise SetupError(f"{map_id}: bad brief id {bid!r}")
        if bid in seen:
            raise SetupError(f"{map_id}: duplicate brief id {bid}")
        seen.add(bid)
        if b.get("side") not in SIDES:
            raise SetupError(f"{bid}: side must be defense or attack")
        for key in ("site", "title", "text"):
            if not isinstance(b.get(key), str) or not b[key].strip():
                raise SetupError(f"{bid}: {key} is missing")
        ops = b.setdefault("operators", [])
        if not isinstance(ops, list) or not all(isinstance(o, str) for o in ops):
            raise SetupError(f"{bid}: operators must be a list of names")
        places = b.get("placements") or []
        if not places:
            raise SetupError(f"{bid}: no placements")
        b["placements"] = places
        for i, p in enumerate(places, 1):
            where = f"{bid} placement {i}"
            if p.get("kind") not in KINDS:
                raise SetupError(f"{where}: unknown kind {p.get('kind')!r}")
            if not p.get("floor"):
                raise SetupError(f"{where}: floor is missing")
            if not any(k in p for k in ("at", "between", "room")):
                raise SetupError(f"{where}: needs at, between or room")
            if "at" in p and not _pair(p["at"]):
                raise SetupError(f"{where}: at must be [x, y] in 0-100")
            if "between" in p and not (isinstance(p["between"], list) and len(p["between"]) == 2):
                raise SetupError(f"{where}: between must name two rooms")
            if "nudge" in p and not _pair(p["nudge"], -100):
                raise SetupError(f"{where}: nudge must be [dx, dy]")
            if p.get("edge") not in (None, "top", "bottom", "left", "right"):
                raise SetupError(f"{where}: edge must be top, bottom, left or right")
    return briefs


def _pair(v: Any, lo: float = 0) -> bool:
    return (isinstance(v, (list, tuple)) and len(v) == 2
            and all(isinstance(x, (int, float)) and lo <= x <= 100 for x in v))


class SetupLibrary:
    def __init__(self, setups_dir: Path, shots_dir: Optional[Path] = None) -> None:
        self.setups_dir = Path(setups_dir)
        self.shots_dir = Path(shots_dir) if shots_dir else None
        self.briefs: dict[str, list[dict[str, Any]]] = {}
        self.errors: dict[str, str] = {}
        self.lines: dict[str, dict[str, int]] = {}   # map -> brief id -> line of its "- id:" in the file
        self._stamp: tuple = ()
        self._load()

    def _files(self) -> list[Path]:
        return sorted(self.setups_dir.glob("*.yaml")) if self.setups_dir.exists() else []

    def _stamp_now(self) -> tuple:
        out = []
        for p in self._files():
            try:
                out.append((p.name, p.stat().st_mtime_ns))
            except OSError:
                pass
        return tuple(out)

    def _load(self) -> None:
        briefs: dict[str, list[dict[str, Any]]] = {}
        errors: dict[str, str] = {}
        lines: dict[str, dict[str, int]] = {}
        self._stamp = self._stamp_now()
        for p in self._files():
            try:
                with p.open(encoding="utf-8") as f:
                    text = f.read()
                briefs[p.stem] = validate(p.stem, yaml.safe_load(text) or {})
                lines[p.stem] = {m.group(1): text.count("\n", 0, m.start()) + 1
                                 for m in ID_LINE_RE.finditer(text)}
            except (yaml.YAMLError, SetupError, AttributeError, TypeError) as e:
                errors[p.stem] = str(e)
                log.warning("bad setups file %s: %s", p, e)
        # Swap whole dicts so a reader on another thread sees the old library or the new one.
        self.briefs, self.errors, self.lines = briefs, errors, lines

    def reload_if_changed(self) -> bool:
        """Re-read the YAML when a file was added, removed or edited (a git pull, a hand edit), so
        the dashboard shows the repository's current setups without a restart. True when it did."""
        if self._stamp_now() == self._stamp:
            return False
        self._load()
        return True

    def source(self, map_id: str, brief_id: str) -> Optional[dict[str, Any]]:
        """Where a brief lives in the repository: its file, line and a GitHub link to it."""
        line = self.lines.get(map_id, {}).get(brief_id)
        if line is None:
            return None
        path = f"{SOURCE_DIR}/{map_id}.yaml"
        return {"file": path, "line": line, "url": f"{REPO_URL}/blob/main/{path}#L{line}"}

    def for_map(self, map_id: Optional[str]) -> list[dict[str, Any]]:
        return self.briefs.get(map_id or "", [])

    def brief(self, map_id: str, brief_id: str) -> Optional[dict[str, Any]]:
        return next((b for b in self.for_map(map_id) if b["id"] == brief_id), None)

    def pick(self, map_id: Optional[str], site_ids: Optional[list[str]] = None, side: Optional[str] = None,
             operator: Optional[str] = None) -> list[dict[str, Any]]:
        """Briefs for the situation, best first: the player's operator, then whole-team briefs, then
        the rest of the side. site_ids: candidate sites (the known site, or every possible one)."""
        out = []
        op = _norm(operator)
        for order, b in enumerate(self.for_map(map_id)):
            if side in SIDES and b["side"] != side:
                continue
            if site_ids and b["site"] not in site_ids:
                continue
            ops = [_norm(o) for o in b["operators"]]
            rank = 0 if op and op in ops else 1 if not ops else 2
            site_rank = site_ids.index(b["site"]) if site_ids else 0
            out.append((site_rank, rank, order, b))
        out.sort(key=lambda t: t[:3])
        return [t[3] for t in out]

    # ------------------------------------------------------------------ pictures
    def slot_ok(self, map_id: str, slot: str) -> bool:
        m = SLOT_RE.match(slot or "")
        if not m:
            return False
        b = self.brief(map_id, m.group(1))
        return b is not None and 1 <= int(m.group(2)) <= len(b["placements"])

    def shot_path(self, map_id: str, slot: str) -> Optional[Path]:
        if self.shots_dir is None or not self.slot_ok(map_id, slot):
            return None
        for ext in SHOT_EXTS:
            p = self.shots_dir / map_id / (slot + ext)
            if p.is_file():
                return p
        return None

    def save_shot(self, map_id: str, slot: str, data: bytes, ext: str) -> Path:
        if self.shots_dir is None or not self.slot_ok(map_id, slot) or ext not in SHOT_EXTS:
            raise SetupError("no such picture slot")
        self.clear_shot(map_id, slot)
        folder = self.shots_dir / map_id
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / (slot + ext)
        p.write_bytes(data)
        return p

    def clear_shot(self, map_id: str, slot: str) -> None:
        while (p := self.shot_path(map_id, slot)) is not None:
            p.unlink()
        m = self._marks_path(map_id, slot)
        if m is not None and m.is_file():
            m.unlink()

    # Markers drawn on a picture: where exactly the utility goes, in % of the picture.
    def _marks_path(self, map_id: str, slot: str) -> Optional[Path]:
        if self.shots_dir is None or not self.slot_ok(map_id, slot):
            return None
        return self.shots_dir / map_id / (slot + ".marks.json")

    def marks(self, map_id: str, slot: str) -> list[dict[str, Any]]:
        p = self._marks_path(map_id, slot)
        if p is None or not p.is_file():
            return []
        try:
            with p.open(encoding="utf-8") as f:
                return clean_marks(json.load(f))
        except (OSError, ValueError, SetupError):
            return []

    def save_marks(self, map_id: str, slot: str, marks: Any) -> list[dict[str, Any]]:
        p = self._marks_path(map_id, slot)
        if p is None:
            raise SetupError("no such picture slot")
        out = clean_marks(marks)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, indent=1)
        return out

    # ------------------------------------------------------------------ prompt text
    def dossier(self, map_id: Optional[str]) -> str:
        """The map's briefs as compact text for the coach's map dossier."""
        briefs = self.for_map(map_id)
        if not briefs:
            return ""
        lines = ["Standard setups (the dashboard shows each with map icons and a picture; when your advice "
                 "is one of these, name it by its title so the player can find it):"]
        for b in briefs:
            ops = ", ".join(b["operators"]) or "team"
            lines.append(f"- [{b['side']}, site {b['site']}, {ops}] {b['title']}: {b['text'].strip()}")
            for p in b["placements"]:
                lines.append("    " + describe(p))
        return "\n".join(lines)


MARK_SHAPES = ("circle", "arrow", "box")


def clean_marks(marks: Any) -> list[dict[str, Any]]:
    """Markers as saved: at most 12, each {x, y, shape, label?} with x/y in 0-100 (arrow: x2/y2 too)."""
    if not isinstance(marks, list) or len(marks) > 12:
        raise SetupError("marks must be a list of up to 12")
    out = []
    for m in marks:
        if not isinstance(m, dict) or m.get("shape", "circle") not in MARK_SHAPES:
            raise SetupError("bad mark")
        keys = ("x", "y", "x2", "y2") if m.get("shape") in ("arrow", "box") else ("x", "y")
        if not all(isinstance(m.get(k), (int, float)) and 0 <= m[k] <= 100 for k in keys):
            raise SetupError("mark outside the picture")
        o: dict[str, Any] = {"shape": m.get("shape", "circle"), **{k: round(float(m[k]), 2) for k in keys}}
        if m.get("label"):
            o["label"] = str(m["label"])[:40]
        out.append(o)
    return out


def describe(p: dict[str, Any]) -> str:
    """One placement in words: '2F reinforce Office | Library wall (both panels)'."""
    if "between" in p:
        a, b = p["between"]
        where = f"{a} exterior wall" if _norm(b) == "exterior" else f"{a} | {b} wall"
    elif "room" in p:
        where = p["room"]
    else:
        where = p.get("name") or "marked spot"
    note = f" ({p['note']})" if p.get("note") else ""
    return f"{p['floor']} {p['kind']}: {where}{note}"
