"""Data shapes shared across the app.

ScreenRead is what the vision model extracts from ONE screenshot. It is limited to
information visible on the player's own screen (menus and HUD). There is deliberately
no field for enemy positions seen in the 3D world: the coach never looks for enemies.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Screen(str, Enum):
    main_menu = "main_menu"
    matchmaking = "matchmaking"
    map_ban = "map_ban"
    operator_ban = "operator_ban"
    operator_select = "operator_select"
    loadout = "loadout"
    prep_phase = "prep_phase"
    action_phase = "action_phase"
    round_end = "round_end"
    match_end = "match_end"
    scoreboard = "scoreboard"
    spectating = "spectating"
    loading = "loading"
    other = "other"


PRE_ROUND = {Screen.map_ban, Screen.operator_ban, Screen.operator_select, Screen.loadout}
IN_ROUND = {Screen.prep_phase, Screen.action_phase, Screen.spectating}


class PingedEnemy(BaseModel):
    """An enemy the player's team has already marked (red ping / drone / cam spot)."""

    location: str = Field(description="Callout or room name shown with the ping, if any")
    operator: Optional[str] = Field(default=None, description="Operator name if shown")


class KillfeedEntry(BaseModel):
    killer_team: Optional[str] = Field(default=None, description="'us' or 'them'")
    killer_operator: Optional[str] = None
    victim_team: Optional[str] = Field(default=None, description="'us' or 'them'")
    victim_operator: Optional[str] = None
    headshot: Optional[bool] = None


class ScreenRead(BaseModel):
    """Everything the vision model reads from one screenshot. Null when not visible."""

    screen: Screen
    map: Optional[str] = Field(default=None, description="Map name if shown")
    side: Optional[str] = Field(default=None, description="'attack' or 'defense' for the player")
    site: Optional[str] = Field(default=None, description="Bomb site name if shown")
    spawn: Optional[str] = Field(default=None, description="Attacker spawn location name if shown")
    site_options: list[str] = Field(
        default_factory=list,
        description="Candidate bomb sites listed for attackers (ban screen 'BOMB SITES' or spawn screen list)",
    )
    my_location: Optional[str] = Field(
        default=None, description="Room callout shown under the compass, e.g. '2F Upper Hallway'"
    )
    my_operator: Optional[str] = None
    teammate_operators: list[str] = Field(default_factory=list)
    enemy_operators: list[str] = Field(
        default_factory=list,
        description="Enemy operators already revealed as icons on the top HUD bar ('?' slots are unknown: omit them)",
    )
    banned_operators: list[str] = Field(
        default_factory=list, description="Operators banned for the current round, both teams"
    )
    map_ban_options: list[str] = Field(default_factory=list)
    score_us: Optional[int] = None
    score_them: Optional[int] = None
    round_number: Optional[int] = None
    timer_seconds: Optional[int] = None
    alive_us: Optional[int] = None
    alive_them: Optional[int] = None
    team_reinforcements: Optional[int] = Field(
        default=None, description="Defense: reinforcements the team has left (wall icon + number, top-left of the HUD bar)"
    )
    defuser_planted: Optional[bool] = None
    round_won: Optional[bool] = Field(default=None, description="On round end: did the player's team win")
    killfeed: list[KillfeedEntry] = Field(default_factory=list)
    pinged_enemies: list[PingedEnemy] = Field(default_factory=list)
    confidence: float = Field(default=0.5, description="0-1, how sure the read is")
    notes: Optional[str] = None


class GameState(BaseModel):
    """The coach's running picture of the current match, merged from many reads."""

    screen: Screen = Screen.other
    match_id: Optional[str] = None
    map: Optional[str] = None
    side: Optional[str] = None
    side_source: Optional[str] = None   # "read" (seen on screen) | "predicted" (swap rule)
    site: Optional[str] = None
    spawn: Optional[str] = None
    site_options: list[str] = Field(default_factory=list)
    my_location: Optional[str] = None
    my_operator: Optional[str] = None
    teammate_operators: list[str] = Field(default_factory=list)
    enemy_operators: list[str] = Field(default_factory=list)
    banned_operators: list[str] = Field(default_factory=list)
    map_ban_options: list[str] = Field(default_factory=list)
    score_us: int = 0
    score_them: int = 0
    round_number: Optional[int] = None
    timer_seconds: Optional[int] = None
    alive_us: Optional[int] = None
    alive_them: Optional[int] = None
    team_reinforcements: Optional[int] = None
    health: Optional[int] = None
    player_alive: bool = True
    defuser_planted: bool = False
    pinged_enemies: list[PingedEnemy] = Field(default_factory=list)
    last_read_ts: float = 0.0

    def summary(self) -> str:
        parts = [f"screen={self.screen.value}"]
        for key in ("map", "side", "site", "spawn", "my_operator", "my_location", "round_number", "timer_seconds"):
            val = getattr(self, key)
            if val is not None:
                parts.append(f"{key}={val}")
        parts.append(f"score={self.score_us}-{self.score_them}")
        if self.teammate_operators:
            parts.append("teammates=" + ",".join(self.teammate_operators))
        if self.enemy_operators:
            parts.append("enemies_revealed=" + ",".join(self.enemy_operators))
        if self.banned_operators:
            parts.append("bans=" + ",".join(self.banned_operators))
        if self.site_options and not self.site:
            parts.append("possible_sites=" + " | ".join(self.site_options))
        if self.alive_us is not None or self.alive_them is not None:
            parts.append(f"alive={self.alive_us}v{self.alive_them}")
        if self.team_reinforcements is not None:
            parts.append(f"team_reinforcements_left={self.team_reinforcements}")
        if self.defuser_planted:
            parts.append("defuser_planted")
        return " ".join(parts)
