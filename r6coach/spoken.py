"""Checks on a line before it is spoken.

The model sometimes reasons out loud in its answer, and those lines reached the speaker:
"The screen shows round 3 at 1-1, but the match state says round 1..." or
"Ban Jager: you're defending, so you can't ban defenders, so ban Nomad? No. Ban Hibana"
(2026-11-06). `clean_line` removes the sentences that think aloud and keeps the call, or returns
None when nothing is left to say.
"""

from __future__ import annotations

import re
from typing import Optional

# Phrases that only appear when the model talks about its inputs or argues with itself.
_PHRASES = [
    r"\bscreen shows\b", r"\bthe screen says\b", r"\bmatch state\b", r"\bthe state (says|shows)\b",
    r"\bgame state\b", r"\bthe data (says|shows)\b", r"\bthe prompt\b", r"\bi think\b", r"\blet me\b",
    r"\bwait,", r"\bhmm\b", r"\bactually,", r"\bon second thought\b", r"\bor rather\b",
]
_PHRASE_RE = re.compile("|".join(_PHRASES), re.IGNORECASE)
_BARE_ANSWER_RE = re.compile(r"^(no|yes|nope|wait|hmm|actually)[.!,]?$", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _thinking(sentence: str) -> Optional[str]:
    m = _PHRASE_RE.search(sentence)
    if m:
        return f"phrase '{m.group(0)}'"
    if _BARE_ANSWER_RE.match(sentence.strip()):
        return "answers its own question"
    return None


def clean_line(text: str) -> tuple[Optional[str], Optional[str]]:
    """(line to speak or None, why something was removed or None).

    Removes sentences that talk about the screen or the state, bare "No." answers, and questions
    followed by more of the same line (the model asking itself). A line that is one question is a
    real question to the player and stays."""
    t = text.strip()
    if not t:
        return None, None
    sentences = [s for s in _SENTENCE_RE.split(t) if s.strip()]
    keep, why = [], None
    for i, s in enumerate(sentences):
        reason = _thinking(s)
        if reason is None and s.rstrip("\"' )").endswith("?") and i < len(sentences) - 1:
            reason = "question answered in the same line"
        if reason is None and "?" in s.rstrip("?\"' )"):
            reason = "question mid-sentence"
        if reason:
            why = why or reason
        else:
            keep.append(s)
    if why is None:
        return t, None
    return (" ".join(keep) or None), why


def self_talk(text: str) -> Optional[str]:
    """Why this line thinks aloud, or None if it is a clean call."""
    return clean_line(text)[1]
