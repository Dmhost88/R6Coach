"""The match "twin" as every prompt sees it: one rendering of the game state, the same for every call.

Each fact says where it came from, so the model can weigh it: READ (seen on the player's screen or
HUD), PREDICTED (worked out, e.g. the side from the swap rule), or unknown. The player's own
operator is never mixed with the teammates', and "LOCKED" (the player's actual pick) is kept apart
from what the coach suggested.
"""

from __future__ import annotations

from typing import Optional

from .models import GameState


def _list(xs) -> str:
    return ", ".join(xs) if xs else "none"


def render(s: GameState, recommended: Optional[str] = None, memory: str = "") -> str:
    lines = ["MATCH STATE (only what the player's own screen and HUD showed):"]
    side = s.side or "unknown"
    if s.side and getattr(s, "side_source", None) == "predicted":
        side += " (PREDICTED from the round number and the side swap; not yet shown on screen)"
    lines.append(f"- Map {s.map or 'unknown'}, round {s.round_number or '?'}, "
                 f"score {s.score_us}-{s.score_them} (player's team first), player on {side}.")
    if s.site:
        lines.append(f"- Site: {s.site}.")
    else:
        lines.append(f"- Site: not known yet. Possible: {' | '.join(s.site_options) or 'unknown'}.")
    if s.spawn:
        lines.append(f"- Spawn: {s.spawn}.")
    if s.my_operator:
        me = f"LOCKED {s.my_operator} (this is the player; talk to them as 'you')"
    else:
        me = "not locked yet"
    if recommended and (not s.my_operator or recommended.lower() != s.my_operator.lower()):
        me += f"; the coach suggested {recommended}" + (" but they took something else: that suggestion is void"
                                                        if s.my_operator else "")
    lines.append(f"- Player's operator: {me}.")
    lines.append(f"- Teammates (the other players only): {_list(s.teammate_operators)}.")
    lines.append(f"- Banned so far this match (unavailable; this round's ban screen wins if it differs): "
                 f"{_list(s.banned_operators)}.")
    lines.append(f"- Enemy operators revealed this round: {_list(s.enemy_operators)}.")
    live = []
    if s.timer_seconds is not None:
        live.append(f"timer {s.timer_seconds}s")
    if s.alive_us is not None and s.alive_them is not None:
        live.append(f"alive {s.alive_us} v {s.alive_them}")
    if s.defuser_planted:
        live.append("DEFUSER PLANTED")
    if live:
        lines.append("- Round: " + ", ".join(live) + ".")
    if not s.player_alive:
        lines.append("- The player is DEAD and spectating.")
    elif s.my_location or s.health is not None:
        lines.append(f"- Player is in {s.my_location or 'an unknown room'}"
                     + (f" with {s.health} health" if s.health is not None else "") + ".")
    if s.pinged_enemies:
        lines.append("- Pinged/spotted by the team: "
                     + ", ".join(p.location + (f" ({p.operator})" if p.operator else "") for p in s.pinged_enemies) + ".")
    if memory:
        lines.append(memory)
    return "\n".join(lines)
