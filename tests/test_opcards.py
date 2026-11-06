"""Local operator-card reader: name matching (pure), real frames (skipped without RapidOCR), timing.

Fixtures in tests/fixtures/opcards/ are frames the app saved on 2026-11-06 (1600x669, as the app
stores them) plus three full-resolution 3440x1440 captures from data/calibration re-encoded
smaller. Known facts: the player is dmhost88; on Chalet he locked Thermite every round; round 2
bans were Mira and Dokkaebi; an earlier Chalet match's round 1 reveal was "MIRA / ACE".
"""

from __future__ import annotations

import statistics
from pathlib import Path

import cv2
import numpy as np
import pytest

from r6coach.opcards import OperatorMatcher, load_operator_names

FIX = Path(__file__).resolve().parent / "fixtures" / "opcards"


# ----------------------------------------------------------------------------- matcher (no OCR)
@pytest.fixture(scope="module")
def matcher():
    return OperatorMatcher(load_operator_names())


@pytest.mark.parametrize("text,expected", [
    ("THERMITE", "Thermite"),
    ("Thermite", "Thermite"),
    ("DOKKAEBI", "Dokkaebi"),
    ("XACE", "Ace"),              # team icon read as a letter (overtime summary)
    ("XLION", "Lion"),
    ("KALI贝", "Kali"),            # card badge read as a glyph
    ("ROOKJ", "Rook"),
    ("NØKK", "Nokk"),
    ("N0KK", "Nokk"),
    ("Jäger", "Jager"),
    ("SOLID SNAKE", "Solid Snake"),
    ("CAPITÃO", "Capitao"),
    ("BLACKBEARO", "Blackbeard"),  # one OCR slip
    ("NO BAN", None),
    ("NOBAN", None),
    ("READY", None),
    ("RANDOM", None),
    ("", None),
    ("L-T7", None),
    ("一色", None),
    ("DFCULTY SPED", None),
    ("MIE", None),
])
def test_matcher(matcher, text, expected):
    name, score = matcher.match(text)
    assert name == expected, (text, name, score)
    if expected:
        assert score >= 0.75


def test_matcher_accepts_plain_list():
    m = OperatorMatcher(["Ash", "Thatcher"])
    assert m.match("THATCHER") == ("Thatcher", 1.0)
    assert m.match("ASH")[0] == "Ash"
    assert m.match("THERMITE")[0] is None


# ----------------------------------------------------------------------------- real frames
@pytest.fixture(scope="module")
def reader():
    pytest.importorskip("rapidocr_onnxruntime")
    from r6coach.opcards import OperatorCardReader

    return OperatorCardReader(player_name="dmhost88")


def load(name: str) -> np.ndarray:
    p = FIX / name
    if not p.exists():
        pytest.skip(f"fixture {name} missing")
    return cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)


# file, screen, tab, my_operator, teammates, banned, extra attribute checks
FRAMES = [
    ("ready_thermite_3440.jpg", "operator_select", "ready", "Thermite", ["Fuze", "Brava"], [],
     {"source": "cards", "locked": True, "picked_slots": 3}),
    ("ready_thermite_1600.jpg", "operator_select", "ready", "Thermite", ["Kali", "Buck"], [],
     {"source": "cards", "locked": True}),
    ("ready_only_own_1600.jpg", "operator_select", "ready", "Thermite", [], [],
     {"source": "cards", "locked": True, "picked_slots": 1}),
    ("lineup_mute_1600.jpg", "operator_select", "lineup", "Mute", ["Azami", "Lesion", "Mira", "Bandit"], [],
     {"source": "cards", "locked": True}),
    ("loadout_thermite_1600.jpg", "operator_select", "loadout", "Thermite", [], [],
     {"source": "loadout", "locked": True}),
    ("operators_hover_sentry_3440.jpg", "operator_select", "operators", "Sentry", [], [],
     {"source": "panel", "locked": False}),          # hovering, own slot still "?"
    ("locations_1600.jpg", "operator_select", "locations", None, [], [], {"source": ""}),
    ("ban_splash_mira_ace_3440.jpg", "operator_ban", "", None, [], ["Mira", "Ace"], {}),
    ("ban_summary_1600.jpg", "operator_ban", "", None, [], ["Ace", "Dokkaebi", "Lion", "Mute", "Kaid", "Lesion"], {}),
    ("ban_grid_sentry_1600.jpg", "operator_ban", "", None, [], [], {"ban_candidate": "Sentry"}),
]


@pytest.mark.parametrize("name,screen,tab,my,teammates,banned,extra", FRAMES, ids=[f[0] for f in FRAMES])
def test_real_frames(reader, name, screen, tab, my, teammates, banned, extra):
    r = reader.read(load(name))
    assert r is not None, name
    assert r.screen == screen, r
    assert r.tab == tab, r
    assert r.my_operator == my, r
    assert sorted(r.teammates) == sorted(teammates), r
    assert sorted(r.banned) == sorted(banned), r
    for k, v in extra.items():
        assert getattr(r, k) == v, f"{k}={getattr(r, k)!r} ({r})"
    if my:
        assert my not in r.teammates, "the player's own card must not count as a teammate"
        assert r.confidence >= 0.4, r


def test_hint_skips_classification(reader):
    """The watcher passes its own screen type; the result must not change and no title OCR runs."""
    img = load("ready_thermite_1600.jpg")
    r = reader.read(img, screen="operator_select")
    assert r.my_operator == "Thermite" and sorted(r.teammates) == ["Buck", "Kali"]
    assert "tabs" not in r.texts and "ban_title" not in r.texts
    from r6coach.models import Screen

    r2 = reader.read(load("ban_splash_mira_ace_3440.jpg"), screen=Screen.operator_ban)
    assert r2.banned == ["Mira", "Ace"]
    assert reader.read(img, screen="prep_phase") is None


def test_non_card_screen_returns_none(reader):
    blank = np.zeros((1440, 3440, 3), np.uint8)
    assert reader.read(blank) is None


def test_time_budget(reader):
    """A full-resolution READY frame with three named cards, after one warm-up read."""
    img = load("ready_thermite_3440.jpg")
    reader.read(img)
    times = [reader.read(img, screen="operator_select").ms for _ in range(5)]
    med = statistics.median(times)
    assert med < 300, f"median {med:.0f} ms, runs {[round(t) for t in times]}"
    quick = [reader.read(load("ban_splash_mira_ace_3440.jpg")).ms for _ in range(3)]
    assert statistics.median(quick) < 200, quick
