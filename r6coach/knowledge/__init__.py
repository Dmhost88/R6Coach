"""Strategy knowledge: map setups, operator data, and the player's own notes."""

from __future__ import annotations

import difflib
import logging
import unicodedata
from pathlib import Path
from typing import Any, Optional

import yaml

log = logging.getLogger(__name__)

DATA = Path(__file__).resolve().parent / "data"


def _norm(s: str) -> str:
    """Lowercase, strip accents (Jäger -> jager) and drop punctuation/spaces."""
    s = unicodedata.normalize("NFKD", s.replace("ø", "o").replace("Ø", "O"))
    return "".join(ch for ch in s.lower() if ch.isalnum() and not unicodedata.combining(ch))


class Knowledge:
    """Loads YAML data and the user's notes, and answers lookups with fuzzy name matching
    (screen reads are not always spelled exactly like the data)."""

    def __init__(self, notes_dir: Path, data_dir: Path = DATA) -> None:
        self.data_dir = data_dir
        self.notes_dir = notes_dir
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.reload()

    def reload(self) -> None:
        self.maps: dict[str, dict[str, Any]] = {}
        self.operators: dict[str, dict[str, Any]] = {}
        self.meta: dict[str, Any] = {}
        maps_dir = self.data_dir / "maps"
        if maps_dir.exists():
            for p in sorted(maps_dir.glob("*.yaml")):
                try:
                    with open(p, encoding="utf-8") as f:
                        m = yaml.safe_load(f) or {}
                    self.maps[m.get("id", p.stem)] = m
                except yaml.YAMLError:
                    log.exception("bad map file %s", p)
        ops_file = self.data_dir / "operators.yaml"
        if ops_file.exists():
            with open(ops_file, encoding="utf-8") as f:
                for op in (yaml.safe_load(f) or {}).get("operators", []):
                    self.operators[_norm(op["name"])] = op
        meta_file = self.data_dir / "meta.yaml"
        if meta_file.exists():
            with open(meta_file, encoding="utf-8") as f:
                self.meta = yaml.safe_load(f) or {}

    # ------------------------------------------------------------------ lookups
    def find_map(self, name: Optional[str]) -> Optional[dict[str, Any]]:
        if not name:
            return None
        key = _norm(name)
        index: dict[str, str] = {}
        for mid, m in self.maps.items():
            for alias in [mid, m.get("name", "")] + list(m.get("aliases") or []):
                if alias:
                    index[_norm(alias)] = mid
        if key in index:
            return self.maps[index[key]]
        for alias, mid in index.items():  # "Kafe" vs "Kafe Dostoyevsky"
            if key and (key in alias or alias in key):
                return self.maps[mid]
        close = difflib.get_close_matches(key, list(index), n=1, cutoff=0.75)
        return self.maps[index[close[0]]] if close else None

    def find_site(self, map_data: Optional[dict[str, Any]], site: Optional[str]) -> Optional[dict[str, Any]]:
        if not map_data or not site:
            return None
        key = _norm(site)
        best, best_score = None, 0.0
        for s in map_data.get("sites") or []:
            for alias in [s.get("name", ""), s.get("id", "")] + list(s.get("aliases") or []):
                score = difflib.SequenceMatcher(None, key, _norm(alias)).ratio()
                if score > best_score:
                    best, best_score = s, score
        return best if best_score >= 0.6 else None

    def operator(self, name: Optional[str]) -> Optional[dict[str, Any]]:
        if not name:
            return None
        key = _norm(name)
        if key in self.operators:
            return self.operators[key]
        close = difflib.get_close_matches(key, list(self.operators), n=1, cutoff=0.8)
        return self.operators[close[0]] if close else None

    def ranked_pool(self) -> list[str]:
        pool = self.meta.get("ranked_map_pool") or list(self.maps)
        return [self.maps[m]["name"] if m in self.maps else m for m in pool]

    # ------------------------------------------------------------------ user notes
    def notes_path(self, map_id: str) -> Path:
        return self.notes_dir / f"{map_id}.md"

    def notes(self, map_id: Optional[str]) -> str:
        if not map_id:
            return ""
        p = self.notes_path(map_id)
        if not p.exists():
            return ""
        with open(p, encoding="utf-8") as f:
            return f.read().strip()

    def save_notes(self, map_id: str, text: str) -> None:
        with open(self.notes_path(map_id), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    # ------------------------------------------------------------------ prompt context
    def context_for(self, map_name: Optional[str], site: Optional[str], side: Optional[str]) -> str:
        """Compact YAML of what matters for this map/site/side, plus the player's notes."""
        m = self.find_map(map_name)
        if not m:
            return f"(No local data for map '{map_name}'. Use general Siege knowledge and say so if unsure.)"
        out: dict[str, Any] = {"map": m.get("name"), "attacker_spawns": m.get("attacker_spawns")}
        s = self.find_site(m, site)
        if s:
            block = {"site": s.get("name"), "floor": s.get("floor")}
            if side in ("defense", None):
                block["defender"] = s.get("defender")
            if side in ("attack", None):
                block["attacker"] = s.get("attacker")
            block["common_mistakes"] = s.get("common_mistakes")
            block["uncertain"] = s.get("uncertain")
            out["site_plan"] = block
        else:
            out["sites"] = [x.get("name") for x in m.get("sites") or []]
        gen = m.get("general") or {}
        if side in ("attack", "defense"):
            out["general"] = gen.get(side)
        else:
            out["general"] = gen
        text = yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=120)
        notes = self.notes(m.get("id"))
        if notes:
            text += f"\nPlayer's own notes for {m.get('name')} (trust these over the database):\n{notes}\n"
        return text

    def operators_context(self, names: list[str]) -> str:
        rows = []
        for n in names:
            op = self.operator(n)
            if op:
                rows.append({k: op.get(k) for k in ("name", "side", "roles", "unique_gadget", "comp_notes")})
        return yaml.safe_dump(rows, sort_keys=False, allow_unicode=True, width=120) if rows else ""
