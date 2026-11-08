"""Matching what the HUD's OCR reads against the names the coach knows: rooms and bomb sites.

The HUD reader gets the game's own labels ("2F Office", "BOMBS FOUND / 2F Laundry"), but OCR
mangles them ("2F Libary Hallway", "2F Office8") and drops floor prefixes. These helpers snap a
reading to a known name, or say it doesn't match, so state never holds a half-read label and an
OCR wobble never looks like the player moving.
"""

from __future__ import annotations

import difflib
import re
from typing import Iterable, Optional

FLOORS = ("B", "1F", "2F", "3F", "EXT", "ROOF")
_PREFIX = re.compile(r"^(B|1F|2F|3F|EXT|ROOF)\s+(.*)$", re.I)
ROOM_MATCH = 0.84     # similarity for an OCR'd room to count as a known room
SITE_MATCH = 0.80     # similarity for a site read to count as one of the site options
SAME_ROOM = 0.80      # two readings this similar are the same room (OCR noise, not a move)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def split_floor(label: str) -> tuple[str, str]:
    """'2F Office' -> ('2F', 'Office'); no prefix -> ('', label)."""
    m = _PREFIX.match(label.strip())
    return (m.group(1).upper(), m.group(2)) if m else ("", label.strip())


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def snap_room(raw: Optional[str], known: Iterable[str]) -> Optional[str]:
    """The known label an OCR'd room reading means, or None. A floor prefix in the reading must
    agree with the label's (EXT only matches EXT: 'EXT Balcony' is not '2F Balcony')."""
    if not raw:
        return None
    floor, name = split_floor(raw)
    best, best_score = None, 0.0
    for label in known:
        kf, kn = split_floor(label)
        if floor and kf and floor != kf:
            continue
        score = similarity(name, kn)
        if score > best_score:
            best, best_score = label, score
    return best if best_score >= ROOM_MATCH else None


def same_room(a: Optional[str], b: Optional[str]) -> bool:
    """Two room readings that are really one room (an OCR slip such as '2F Office' / '2F Office8')."""
    if not a or not b:
        return False
    fa, na = split_floor(a)
    fb, nb = split_floor(b)
    if fa and fb and fa != fb:
        return False
    return similarity(na, nb) >= SAME_ROOM


def site_rooms(option: str) -> list[str]:
    """'2F Laundry / 2F Piano Room' -> ['2F Laundry', '2F Piano Room']."""
    return [p.strip() for p in re.split(r"\s*/\s*|\s+and\s+", option) if p.strip()]


def snap_site(text: Optional[str], options: Iterable[str]) -> Optional[str]:
    """The site option a site reading means: the whole pair, or one room of it ('BOMBS FOUND'
    sometimes yields only one line). None when no option matches."""
    if not text:
        return None
    parts = site_rooms(text)
    best, best_score = None, 0.0
    for opt in options:
        score = similarity(text, opt)
        for part in parts:
            for room in site_rooms(opt):
                score = max(score, similarity(split_floor(part)[1], split_floor(room)[1]))
        if score > best_score:
            best, best_score = opt, score
    return best if best_score >= SITE_MATCH else None


def site_with_room(room: Optional[str], options: Iterable[str]) -> Optional[str]:
    """The site option that contains this room (defenders start the prep phase on their site)."""
    if not room:
        return None
    floor, name = split_floor(room)
    for opt in options:
        for r in site_rooms(opt):
            rf, rn = split_floor(r)
            if (not floor or not rf or floor == rf) and similarity(name, rn) >= ROOM_MATCH:
                return opt
    return None
