"""Local player profile (SQLite): matches, rounds and what the coach said.

v1 records everything; v3 will mine it for patterns. Nothing here leaves the PC except the
short summaries the coach includes in its prompts.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY, started REAL, ended REAL, map TEXT,
    score_us INTEGER, score_them INTEGER, won INTEGER
);
CREATE TABLE IF NOT EXISTS rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT, ts REAL, round_number INTEGER,
    map TEXT, side TEXT, site TEXT, spawn TEXT, operator TEXT, teammates TEXT, won INTEGER
);
CREATE TABLE IF NOT EXISTS coach_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT, ts REAL, phase TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS room_sightings (
    map TEXT, room TEXT, seen INTEGER, last REAL, PRIMARY KEY (map, room)
);
"""


class Profile:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def _exec(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self.db.execute(sql, args)
            self.db.commit()

    def _query(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    def match_started(self, match_id: str) -> None:
        self._exec("INSERT OR IGNORE INTO matches (id, started) VALUES (?, ?)", (match_id, time.time()))

    def match_ended(self, match_id: Optional[str], state: dict[str, Any]) -> None:
        if not match_id:
            return
        us, them = state.get("score_us", 0), state.get("score_them", 0)
        self._exec(
            "UPDATE matches SET ended=?, map=?, score_us=?, score_them=?, won=? WHERE id=?",
            (time.time(), state.get("map"), us, them, int(us > them), match_id),
        )

    def round_ended(self, state: dict[str, Any], won: Optional[bool]) -> None:
        self._exec(
            "INSERT INTO rounds (match_id, ts, round_number, map, side, site, spawn, operator, teammates, won)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                state.get("match_id"), time.time(), state.get("round_number"), state.get("map"),
                state.get("side"), state.get("site"), state.get("spawn"), state.get("my_operator"),
                ",".join(state.get("teammate_operators") or []), None if won is None else int(won),
            ),
        )

    def log_line(self, match_id: Optional[str], phase: str, text: str) -> None:
        self._exec("INSERT INTO coach_lines (match_id, ts, phase, text) VALUES (?,?,?,?)",
                   (match_id, time.time(), phase, text))

    # ------------------------------------------------------------------ room names seen in game
    def room_seen(self, map_name: Optional[str], room: Optional[str]) -> None:
        """Record a room callout read off the player's own HUD: the game's exact wording."""
        if not map_name or not room:
            return
        self._exec(
            "INSERT INTO room_sightings (map, room, seen, last) VALUES (lower(?), ?, 1, ?) "
            "ON CONFLICT(map, room) DO UPDATE SET seen = seen + 1, last = excluded.last",
            (map_name, room, time.time()),
        )

    def rooms_seen(self, map_name: Optional[str], min_seen: int = 2) -> list[str]:
        """Room names read at least `min_seen` times on this map (one-off reads may be OCR slips)."""
        if not map_name:
            return []
        rows = self._query("SELECT room FROM room_sightings WHERE map = lower(?) AND seen >= ? ORDER BY room",
                           (map_name, min_seen))
        return [r[0] for r in rows]

    # ------------------------------------------------------------------ summaries for prompts
    def map_record(self, map_name: Optional[str]) -> str:
        """One-line win-rate summary for a map, by side and site."""
        if not map_name:
            return ""
        rows = self._query(
            "SELECT side, site, COUNT(*), SUM(won) FROM rounds WHERE lower(map)=lower(?) AND won IS NOT NULL"
            " GROUP BY side, site ORDER BY COUNT(*) DESC LIMIT 8",
            (map_name,),
        )
        if not rows:
            return ""
        parts = [f"{side or '?'} {site or '?'}: {int(w or 0)}/{n} won" for side, site, n, w in rows]
        return "Your history on this map: " + "; ".join(parts)

    def map_win_rates(self) -> dict[str, tuple[int, int]]:
        rows = self._query("SELECT map, COUNT(*), SUM(won) FROM rounds WHERE won IS NOT NULL GROUP BY map")
        return {m: (int(w or 0), int(n)) for m, n, w in rows if m}

    def recent_matches(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT id, started, map, score_us, score_them, won FROM matches ORDER BY started DESC LIMIT ?",
            (limit,),
        )
        return [dict(zip(("id", "started", "map", "score_us", "score_them", "won"), r)) for r in rows]
