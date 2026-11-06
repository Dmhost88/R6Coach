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

# Columns added after v1; existing databases get them on open.
MIGRATIONS = [
    ("rounds", "first_blood", "TEXT"), ("rounds", "my_kills", "INTEGER"), ("rounds", "team_kills", "INTEGER"),
    ("rounds", "enemy_kills", "INTEGER"), ("rounds", "died_to", "TEXT"), ("rounds", "died_in", "TEXT"),
    ("rounds", "planted", "INTEGER"), ("rounds", "enemy_ops", "TEXT"),
    ("matches", "takeaways", "TEXT"),
]


class Profile:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        for table, col, kind in MIGRATIONS:
            have = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if col not in have:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
        self.db.commit()
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

    def round_ended(self, state: dict[str, Any], won: Optional[bool],
                    record: Optional[dict[str, Any]] = None) -> None:
        """record: the MatchMemory round (first blood, kills, death, plant, enemy ops)."""
        r = record or {}
        b = lambda v: None if v is None else int(bool(v))  # noqa: E731
        self._exec(
            "INSERT INTO rounds (match_id, ts, round_number, map, side, site, spawn, operator, teammates, won,"
            " first_blood, my_kills, team_kills, enemy_kills, died_to, died_in, planted, enemy_ops)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                state.get("match_id"), time.time(), state.get("round_number") or r.get("n"), state.get("map"),
                r.get("side") or state.get("side"), r.get("site") or state.get("site"), state.get("spawn"),
                r.get("operator") or state.get("my_operator"),
                ",".join(state.get("teammate_operators") or []), b(won),
                r.get("first_blood"), r.get("my_kills"), r.get("team_kills"), r.get("enemy_kills"),
                r.get("died_to"), r.get("died_in"), b(r.get("planted")),
                ",".join(r.get("enemy_ops") or []) or None,
            ),
        )

    def set_takeaways(self, match_id: Optional[str], lines: list[str]) -> None:
        if match_id:
            self._exec("UPDATE matches SET takeaways=? WHERE id=?", ("\n".join(lines), match_id))

    def match_report(self, match_id: str) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT id, started, ended, map, score_us, score_them, won, takeaways FROM matches "
                           "WHERE id=?", (match_id,))
        if not rows:
            return None
        m = dict(zip(("id", "started", "ended", "map", "score_us", "score_them", "won", "takeaways"), rows[0]))
        m["takeaways"] = [ln for ln in (m["takeaways"] or "").split("\n") if ln]
        cols = ("round_number", "side", "site", "operator", "won", "first_blood", "my_kills", "team_kills",
                "enemy_kills", "died_to", "died_in", "planted", "enemy_ops")
        m["rounds"] = [dict(zip(cols, r)) for r in self._query(
            f"SELECT {', '.join(cols)} FROM rounds WHERE match_id=? ORDER BY ts", (match_id,))]
        for r in m["rounds"]:
            r["enemy_ops"] = [o for o in (r["enemy_ops"] or "").split(",") if o]
            for k in ("won", "planted"):
                r[k] = None if r[k] is None else bool(r[k])
        return m

    # ------------------------------------------------------------------ personal stats for advice
    def stats_text(self, map_name: Optional[str], side: Optional[str] = None) -> str:
        """Short, factual summary of the player's own history, for prompts. Empty when there's none."""
        out = []
        if map_name:
            rows = self._query(
                "SELECT side, site, COUNT(*), SUM(won) FROM rounds WHERE lower(map)=lower(?) AND won IS NOT NULL"
                + (" AND side=?" if side else "") + " GROUP BY side, site HAVING COUNT(*) >= 2 ORDER BY COUNT(*) DESC",
                (map_name, side) if side else (map_name,))
            if rows:
                out.append("Your record on this map by site: " + "; ".join(
                    f"{s or '?'} {site or '?'} {int(w or 0)}/{n}" for s, site, n, w in rows[:8]))
            deaths = self._query(
                "SELECT died_in, COUNT(*) FROM rounds WHERE lower(map)=lower(?) AND died_in IS NOT NULL "
                "GROUP BY died_in HAVING COUNT(*) >= 2 ORDER BY COUNT(*) DESC LIMIT 4", (map_name,))
            if deaths:
                out.append("Rooms you die in most here: " + ", ".join(f"{r} x{n}" for r, n in deaths))
            fb = self._query(
                "SELECT COUNT(*), SUM(first_blood='us') FROM rounds WHERE lower(map)=lower(?) "
                "AND first_blood IS NOT NULL", (map_name,))
            if fb and fb[0][0] and fb[0][0] >= 3:
                out.append(f"Your team gets first blood in {int(fb[0][1] or 0)}/{fb[0][0]} rounds here")
        ops = self._query(
            "SELECT operator, COUNT(*), SUM(won), SUM(my_kills) FROM rounds WHERE operator IS NOT NULL AND won IS NOT NULL"
            + (" AND side=?" if side else "") + " GROUP BY operator HAVING COUNT(*) >= 3 ORDER BY COUNT(*) DESC LIMIT 6",
            (side,) if side else ())
        if ops:
            out.append("Your operators (rounds won/played, kills): " + "; ".join(
                f"{o} {int(w or 0)}/{n}, {int(k or 0)}k" for o, n, w, k in ops))
        return "\n".join(out)

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
