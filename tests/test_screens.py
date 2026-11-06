"""Local screen classifier (real frames, skipped when absent) and the watcher's cloud gating."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from r6coach.config import Config
from r6coach.models import Screen, ScreenRead
from r6coach.screens import LocalScreen, match_map
from r6coach.watcher import Watcher, local_cloud_policy

ROOT = Path(__file__).resolve().parent.parent / "data" / "calibration"
S1 = "2026-11-06_003854"   # custom games: House, Coastline
S2 = "2026-11-06_005400"   # Ranked on Chalet

# Hand-checked on contact sheets. Expected: screen, plus optional key/card/map/in_round.
FRAMES = [
    (S2, "005402_0001", Screen.main_menu, {}),
    (S2, "005419_0004", Screen.matchmaking, {}),                        # FINDING MATCH banner
    (S2, "005519_0017", Screen.map_ban, {"key": "MAPBAN"}),
    (S2, "005545_0022", Screen.operator_ban, {"card": True}),           # ROUND 1 / BAN PHASE card
    (S2, "005548_0023", Screen.operator_ban, {"key": "BANADEFENDER", "map": "Chalet"}),
    (S2, "005604_0025", Screen.operator_ban, {"key": "result"}),        # MIRA (ban) ACE
    (S1, "004746_0137", Screen.operator_ban, {"key": "BANANATTACKER", "map": "Coastline"}),
    (S2, "005607_0026", Screen.operator_select, {"map": "Chalet", "mode": "RANKED"}),
    (S1, "003856_0001", Screen.operator_select, {"map": "House"}),
    (S1, "003908_0004", Screen.prep_phase, {"in_round": True}),          # timer 0:42
    (S2, "005823_0058", Screen.action_phase, {"in_round": True}),
    (S2, "010405_0154", Screen.spectating, {}),                          # WATCHING card
    (S1, "003947_0016", Screen.other, {}),                               # pause menu
    (S2, "010507_0162", Screen.loading, {}),
    (S2, "010359_0152", Screen.round_end, {}),                           # WON ROUND 2
    (S2, "011747_0345", Screen.match_end, {"map": "Chalet"}),
    (S2, "010517_0163", Screen.scoreboard, {"key": "scoreboard"}),
]


@pytest.fixture(scope="module")
def clf():
    pytest.importorskip("rapidocr_onnxruntime")
    from r6coach.screens import ScreenClassifier

    return ScreenClassifier()


@pytest.mark.parametrize("session,frame,screen,extra", FRAMES, ids=[f[1] for f in FRAMES])
def test_real_frames(clf, session, frame, screen, extra):
    path = ROOT / session / "auto" / f"{frame}.jpg"
    if not path.exists():
        pytest.skip("capture frames not present")
    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    r = clf.read(img)
    assert r.screen == screen, r
    assert r.confident, r
    for k, v in extra.items():
        assert getattr(r, k) == v, f"{k}={getattr(r, k)!r} ({r})"


def test_match_map():
    assert match_map("CHALET") == "Chalet"
    assert match_map("CHAIET") == "Chalet"
    assert match_map("KAFE DOSTOYEVSKY") == "Kafe Dostoyevsky"
    assert match_map("HOUSE") == "House"
    assert match_map("BOMB") is None
    assert match_map("") is None


# ---------------------------------------------------------------------------- gating decisions
def L(screen, conf=0.95, **kw):
    return LocalScreen(screen, conf, **kw)


def test_policy_skips_screens_the_cloud_adds_nothing_to():
    for s in (Screen.main_menu, Screen.matchmaking, Screen.loading, Screen.other, Screen.match_end,
              Screen.round_end):
        assert local_cloud_policy(L(s), Screen.other) == ("skip", "")
    assert local_cloud_policy(L(Screen.spectating, in_round=True), Screen.action_phase) == ("skip", "")
    assert local_cloud_policy(L(Screen.action_phase, in_round=True), Screen.action_phase) == ("skip", "")
    # title cards of detail screens carry nothing either
    assert local_cloud_policy(L(Screen.operator_ban, card=True), Screen.round_end) == ("skip", "")


def test_policy_detail_screens():
    assert local_cloud_policy(L(Screen.operator_select), Screen.other) == ("detail", "")
    assert local_cloud_policy(L(Screen.operator_ban, key="BANADEFENDER"), Screen.other) == ("once", "BANADEFENDER")
    assert local_cloud_policy(L(Screen.operator_ban, key="BANADEFENDER"), Screen.operator_ban,
                              {"BANADEFENDER"}) == ("skip", "")
    assert local_cloud_policy(L(Screen.scoreboard, key="scoreboard"), Screen.prep_phase) == ("once", "scoreboard")


def test_policy_first_prep_read_only():
    prep = L(Screen.prep_phase, conf=0.85, in_round=True, timer=40)
    assert local_cloud_policy(prep, Screen.operator_select) == ("once", "prep")
    assert local_cloud_policy(prep, Screen.prep_phase, {"prep"}) == ("skip", "")
    # The HUD says action phase: a low timer late in the round is not prep.
    assert local_cloud_policy(prep, Screen.action_phase) == ("skip", "")


def test_policy_falls_back_when_unsure():
    assert local_cloud_policy(None, Screen.other) == ("gate", "")
    assert local_cloud_policy(L(Screen.other, conf=0.3), Screen.other) == ("gate", "")


# ---------------------------------------------------------------------------- watcher wiring
class _Source:
    status = "test"

    def __init__(self):
        self.ts = 0.0
        self.img = np.zeros((144, 344, 3), np.uint8)

    def latest(self):
        return self.ts, self.img


class _Claude:
    def __init__(self, screen=Screen.operator_select):
        self.calls = 0
        self.screen = screen

    def read_screen(self, *a, **kw):
        self.calls += 1
        return ScreenRead(screen=self.screen, map="Chalet", side="attack", confidence=0.9)


class _NoSave:
    def maybe_save(self, *a):
        pass


class _Fixed:
    """Stands in for ScreenClassifier: returns whatever the test sets."""

    def __init__(self, r):
        self.r = r
        self.calls = 0

    def read(self, img):
        self.calls += 1
        return self.r


def _watcher(bus, local, claude=None, enabled=True):
    cfg = Config()
    cfg.vision.local_screens = enabled
    src = _Source()
    w = Watcher(cfg, bus, src, claude or _Claude(), _NoSave(), screens=_Fixed(local))
    return w, src


def _run(w, src, monkeypatch, t0, seconds, step=0.5):
    import types

    import r6coach.watcher as wmod

    t = t0
    while t < t0 + seconds:
        monkeypatch.setattr(wmod, "time", types.SimpleNamespace(time=lambda t=t: t))
        src.ts = t
        src.img = np.full((144, 344, 3), int(t * 7) % 255, np.uint8)   # keeps changing
        w._tick()
        t += step
    return t


def test_watcher_skips_cloud_on_menus_and_applies_screen(bus, monkeypatch):
    events = []
    bus.subscribe("screen.changed", lambda ev: events.append(ev.data))
    claude = _Claude()
    w, src = _watcher(bus, L(Screen.main_menu), claude)
    _run(w, src, monkeypatch, 1000.0, 30)
    assert claude.calls == 0
    assert w.state.screen == Screen.main_menu
    assert events and events[-1]["source"] == "local"


def test_watcher_reads_detail_screen_and_lets_the_cloud_set_it(bus, monkeypatch):
    claude = _Claude(Screen.operator_select)
    w, src = _watcher(bus, L(Screen.operator_select, map="Chalet"), claude)
    _run(w, src, monkeypatch, 1000.0, 20)
    assert claude.calls >= 1
    assert w.state.screen == Screen.operator_select and w.state.map == "Chalet"
    # far fewer than the old gate would make with a constantly changing frame (min interval 3s)
    assert claude.calls <= 7


def test_watcher_uses_the_fast_detail_read_on_menus(bus, monkeypatch):
    class _Both(_Claude):
        detail = []

        def read_detail(self, jpeg, screen, model, effort, hint="", player_name="", max_width=0):
            self.detail.append((screen, model, effort))
            return ScreenRead(screen=screen, map="Chalet", side="attack", my_operator="Ash", confidence=0.9)

    claude = _Both(Screen.operator_select)
    w, src = _watcher(bus, L(Screen.operator_select, map="Chalet"), claude)
    _run(w, src, monkeypatch, 1000.0, 10)
    assert claude.detail and claude.calls == 0
    assert claude.detail[0] == (Screen.operator_select, w.cfg.vision.detail_model, w.cfg.vision.detail_effort)
    assert w.state.my_operator == "Ash"


def test_watcher_once_per_round_key(bus, monkeypatch):
    claude = _Claude(Screen.operator_ban)
    w, src = _watcher(bus, L(Screen.operator_ban, key="BANADEFENDER", map="Chalet"), claude)
    _run(w, src, monkeypatch, 1000.0, 30)
    assert claude.calls == 1


def test_watcher_switch_off_uses_old_gate(bus, monkeypatch):
    claude = _Claude(Screen.main_menu)
    w, src = _watcher(bus, L(Screen.main_menu), claude, enabled=False)
    _run(w, src, monkeypatch, 1000.0, 30)
    assert w.screens.calls == 0
    assert claude.calls >= 3


def test_watcher_offline_applies_detail_screen_after_grace(bus, monkeypatch):
    class _Offline:
        calls = 0

        def read_screen(self, *a, **kw):
            self.calls += 1
            return None

    w, src = _watcher(bus, L(Screen.operator_ban, key="BANADEFENDER", map="Chalet"), _Offline())
    _run(w, src, monkeypatch, 1000.0, 3)
    assert w.state.screen != Screen.operator_ban           # waits for the cloud read first
    _run(w, src, monkeypatch, 1003.0, 6)
    assert w.state.screen == Screen.operator_ban and w.state.match_id   # new match started locally
    assert w.state.map == "Chalet"
