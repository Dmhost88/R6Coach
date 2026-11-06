"""Round arithmetic: which round it is and which side the player is on, from the score.

Ranked swaps sides after `swap_after` rounds (3), and in overtime (from round 7) every round.
Once one side has been seen in any round, every other round's side follows, so the coach knows
it's defending before the ban phase shows anything.
"""

from __future__ import annotations

from typing import Optional

OTHER = {"attack": "defense", "defense": "attack"}


def round_from_score(score_us: Optional[int], score_them: Optional[int]) -> Optional[int]:
    """The round about to be played: every finished round gives one team a point."""
    if score_us is None or score_them is None:
        return None
    return score_us + score_them + 1


def swapped(n: int, swap_after: int = 3) -> bool:
    """Is round n played on the opposite side to round 1?"""
    if n <= swap_after:
        return False
    if n <= 2 * swap_after:
        return True
    return (n - 2 * swap_after) % 2 == 0   # overtime: round 7 back on the first side, then alternate


def side_for(n: int, first_side: str, swap_after: int = 3) -> str:
    return OTHER[first_side] if swapped(n, swap_after) else first_side


def first_side_from(n: int, side: str, swap_after: int = 3) -> Optional[str]:
    """The side the player started the match on, given they are on `side` in round n."""
    if side not in OTHER or n < 1:
        return None
    return OTHER[side] if swapped(n, swap_after) else side
