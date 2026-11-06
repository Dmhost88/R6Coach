"""End to end: a real full-resolution READY-tab frame through the watcher's local classifier and
operator-card reader, with no cloud reads."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from r6coach.config import Config
from r6coach.models import Screen
from r6coach.watcher import Watcher

FRAME = Path(__file__).parent / "fixtures" / "opcards" / "ready_thermite_3440.jpg"


class _Source:
    status = "test"

    def __init__(self, img):
        self.img = img
        self.ts = 1.0

    def latest(self):
        self.ts += 1.0
        return self.ts, self.img


class _NoCloud:
    def read_screen(self, *a, **k):
        return None

    def read_detail(self, *a, **k):
        return None


class _NoSave:
    def maybe_save(self, *a):
        pass


@pytest.mark.skipif(not FRAME.exists(), reason="fixture missing")
def test_ready_tab_lock_is_read_locally(bus):
    img = cv2.imdecode(np.fromfile(str(FRAME), np.uint8), cv2.IMREAD_COLOR)
    cfg = Config(player_name="dmhost88")
    w = Watcher(cfg, bus, _Source(img), _NoCloud(), _NoSave())
    for _ in range(3):
        w._tick()
        w._cards_ts = 0.0           # don't wait CARD_PERIOD between test ticks
    assert w.local is not None and w.local.screen in (Screen.operator_select, Screen.loadout)
    assert w.last_cards is not None and w.last_cards.my_operator == "Thermite"
    assert w.state.my_operator == "Thermite"
    assert "Thermite" not in w.state.teammate_operators
