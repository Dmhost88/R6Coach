"""Match memory: what happened in earlier rounds, so advice can adapt within a match.

Built only from things the player saw: round results, the killfeed, their own death, enemy
operators revealed on the HUD, sites and spawns. Reset when a new match starts.
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Optional

from .bus import Bus, Event


@dataclass
class RoundRecord:
    n: int
    side: Optional[str] = None
    site: Optional[str] = None
    operator: Optional[str] = None
    won: Optional[bool] = None
    first_blood: Optional[str] = None          # "us" | "them"
    my_kills: int = 0
    team_kills: int = 0
    enemy_kills: int = 0
    died_to: Optional[str] = None
    died_in: Optional[str] = None
    planted: bool = False
    enemy_ops: list[str] = field(default_factory=list)

    def line(self) -> str:
        bits = [f"R{self.n} {self.side or '?'}"]
        if self.site:
            bits.append(f"site {self.site}")
        if self.operator:
            bits.append(f"you on {self.operator}")
        bits.append({True: "WON", False: "LOST", None: "result unknown"}[self.won])
        if self.first_blood:
            bits.append(f"first blood {'yours' if self.first_blood == 'us' else 'theirs'}")
        bits.append(f"kills us {self.team_kills} / them {self.enemy_kills}, yours {self.my_kills}")
        if self.died_to:
            bits.append(f"you died to {self.died_to}" + (f" in {self.died_in}" if self.died_in else ""))
        elif self.won is not None:
            bits.append("you survived")
        if self.planted:
            bits.append("defuser planted")
        if self.enemy_ops:
            bits.append("enemy ran " + ", ".join(self.enemy_ops))
        return "; ".join(bits)


class MatchMemory:
    def __init__(self, bus: Bus, current_state, player_name: str = "") -> None:
        self.bus = bus
        self.current_state = current_state
        self.player_name = player_name.lower()
        self._lock = threading.Lock()
        self.rounds: list[RoundRecord] = []
        self.cur = RoundRecord(n=1)
        self.killers_of_me: Counter[str] = Counter()
        bus.subscribe("match.started", lambda ev: self.reset())
        bus.subscribe("live.event", self._on_live)
        bus.subscribe("player.died", self._on_died)
        bus.subscribe("round.ended", self._on_round_ended)

    def reset(self) -> None:
        with self._lock:
            self.rounds = []
            self.cur = RoundRecord(n=1)
            self.killers_of_me = Counter()

    def _on_live(self, ev: Event) -> None:
        kind = ev.data.get("kind")
        with self._lock:
            if kind == "round_reset":
                # A round that never got a result (e.g. coach started mid-round) is simply dropped.
                self.cur = RoundRecord(n=len(self.rounds) + 1)
            elif kind == "kill":
                team = ev.data.get("killer_team")
                if self.cur.first_blood is None and team in ("us", "them"):
                    self.cur.first_blood = team
                if team == "us":
                    self.cur.team_kills += 1
                elif team == "them":
                    self.cur.enemy_kills += 1
                if ev.data.get("mine"):
                    self.cur.my_kills += 1
            elif kind == "planted":
                self.cur.planted = True

    def _on_died(self, ev: Event) -> None:
        s = self.current_state()
        with self._lock:
            self.cur.died_to = ev.data.get("killer")
            self.cur.died_in = s.my_location
            if self.cur.died_to:
                self.killers_of_me[self.cur.died_to] += 1

    def _on_round_ended(self, ev: Event) -> None:
        st = ev.data.get("state") or {}
        with self._lock:
            r = self.cur
            r.won = ev.data.get("won")
            r.side = st.get("side") or r.side
            r.site = st.get("site") or r.site
            r.operator = st.get("my_operator") or r.operator
            r.planted = r.planted or bool(st.get("defuser_planted"))
            r.enemy_ops = list(st.get("enemy_operators") or [])
            self.rounds.append(r)
            self.cur = RoundRecord(n=len(self.rounds) + 1)
            record = asdict(r)
        # Published after the record is complete, so the profile stores the full round.
        self.bus.publish("memory.round", record=record, won=ev.data.get("won"), state=st)

    # ------------------------------------------------------------------ for prompts
    def text(self) -> str:
        with self._lock:
            if not self.rounds:
                return "Match so far: first round (no history yet)."
            lines = ["Match so far (oldest first):"] + [r.line() for r in self.rounds]
            by_side: dict[str, Counter] = {"attack": Counter(), "defense": Counter()}
            for r in self.rounds:
                enemy_side = {"attack": "defense", "defense": "attack"}.get(r.side or "")
                if enemy_side:
                    by_side[enemy_side].update(r.enemy_ops)
            for side, c in by_side.items():
                if c:
                    lines.append(f"Enemy {side} operators seen (times): "
                                 + ", ".join(f"{op} x{n}" for op, n in c.most_common()))
            if self.killers_of_me:
                lines.append("Who has killed you: " + ", ".join(f"{k} x{n}" for k, n in self.killers_of_me.most_common(3)))
            deaths = [r.died_in for r in self.rounds if r.died_in]
            if deaths:
                lines.append("Where you died: " + ", ".join(deaths))
            return "\n".join(lines)
