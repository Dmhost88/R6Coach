import time

import pytest

from r6coach.config import Config
from r6coach.hud import HudRead, Kill
from r6coach.live import LINES, KillTracker, LiveCoach
from r6coach.models import Screen, ScreenRead
from r6coach.watcher import Watcher
from tests.test_watcher import _NoFrames, _NoSave


def make(bus, level=2, name="dmhost88"):
    cfg = Config(player_name=name)
    cfg.coach.chattiness["action_phase"] = level
    w = Watcher(cfg, bus, _NoFrames(), claude=None, frames=_NoSave())
    said = []
    live = LiveCoach(cfg, bus, say=lambda text, **kw: said.append(text), state=lambda: w.state)
    return w, live, said


def start_round(w, side="attack"):
    w.apply(ScreenRead(screen=Screen.main_menu, confidence=0.9))
    w.apply(ScreenRead(screen=Screen.operator_select, map="Chalet", side=side, confidence=0.9))


def test_kill_tracker_dedupes_and_fixes_names():
    kt = KillTracker("dmhost88")
    k1 = Kill("Mate_B", "us", "Enemy_A", "them")
    assert len(kt.new_kills([k1])) == 1
    assert kt.new_kills([k1]) == []                                  # still on screen
    assert kt.new_kills([Kill("MateB", "us", "Enemy_A", "them")]) == []   # OCR slip
    assert kt.new_kills([Kill("Enemy_D", "them", "Enemy_A", "them")]) == []      # friendly fire


def test_hud_drives_phases_and_round_end(bus):
    w, live, said = make(bus)
    start_round(w)
    ended = []
    bus.subscribe("round.ended", lambda ev: ended.append(ev.data["won"]))
    w.apply_hud(HudRead(ts=0, timer=42, events=["prep"]))
    assert w.state.screen == Screen.prep_phase
    w.apply_hud(HudRead(ts=0, timer=5))
    w.apply_hud(HudRead(ts=0, timer=178))                      # prep -> action jump, banner missed
    assert w.state.screen == Screen.action_phase
    w.apply_hud(HudRead(ts=0, timer_hidden=True, events=["won_round"]))
    assert w.state.screen == Screen.round_end and ended == [True]
    w.apply(ScreenRead(screen=Screen.round_end, round_won=True, confidence=0.9))
    assert ended == [True]                                      # cloud read doesn't double-count


def test_timer_in_operator_select_does_not_change_phase(bus):
    w, live, said = make(bus)
    start_round(w)
    w.apply_hud(HudRead(ts=0, timer=20))
    assert w.state.screen == Screen.operator_select


def test_live_lines(bus):
    w, live, said = make(bus, level=2)
    start_round(w, side="attack")
    w.apply_hud(HudRead(ts=0, events=["action"], timer=180))
    w.apply_hud(HudRead(ts=0, timer=44))
    assert LINES["atk_45"] in said
    w.apply_hud(HudRead(ts=0, timer=43))
    assert said.count(LINES["atk_45"]) == 1                     # once per round
    w.apply_hud(HudRead(ts=0, timer=40, timer_hidden=False, events=["planted"]))
    assert LINES["atk_planted"] in said                         # urgent: no cooldown
    w.apply_hud(HudRead(ts=0, timer_hidden=True, alive_us=1, alive_them=2, events=["vs"]))
    assert LINES["clutch"] not in said                          # one read could be an OCR slip
    w.apply_hud(HudRead(ts=0, timer_hidden=True, alive_us=1, alive_them=2, events=["vs"]))
    assert LINES["clutch"] in said                              # the second agreeing read confirms it


def test_one_stray_vs_read_changes_nothing(bus):
    w, live, said = make(bus, level=2)
    start_round(w, side="attack")
    w.apply_hud(HudRead(ts=0, events=["action"], timer=180))
    w.apply_hud(HudRead(ts=0, timer=170, alive_us=5, alive_them=5, events=["vs"]))
    w.apply_hud(HudRead(ts=0, timer=169, alive_us=5, alive_them=5, events=["vs"]))
    assert (w.state.alive_us, w.state.alive_them) == (5, 5)
    w.apply_hud(HudRead(ts=0, timer=160, alive_us=4, alive_them=5, events=["vs"]))   # misread
    w.apply_hud(HudRead(ts=0, timer=159, alive_us=5, alive_them=5, events=["vs"]))
    assert (w.state.alive_us, w.state.alive_them) == (5, 5)
    assert LINES["disadvantage"] not in said
    # A count right after a killfeed line is real at once.
    w.apply_hud(HudRead(ts=0, timer=150, alive_us=4, alive_them=5, events=["vs"],
                        kills=[Kill("Enemy_A", "them", "Mate_A", "us")]))
    assert (w.state.alive_us, w.state.alive_them) == (4, 5)


def test_counts_and_timer_reset_each_round(bus):
    w, live, said = make(bus, level=2)
    start_round(w, side="attack")
    w.apply_hud(HudRead(ts=0, events=["action"], timer=180))
    for _ in range(2):
        w.apply_hud(HudRead(ts=0, timer=175, alive_us=5, alive_them=1, events=["vs"]))
    assert (w.state.alive_us, w.state.alive_them) == (5, 1)
    w.apply_hud(HudRead(ts=0, timer_hidden=True, events=["won_round"]))
    # The next round's ban screen: the cloud read still carries the old clock and counts.
    w.apply(ScreenRead(screen=Screen.operator_ban, timer_seconds=175, alive_us=5, alive_them=1,
                       confidence=0.9))
    assert w.state.alive_us is None and w.state.alive_them is None
    assert w.state.timer_seconds is None


def test_my_kill_says_nothing_and_my_death_blocks_clutch(bus):
    w, live, said = make(bus, level=3)
    start_round(w, side="defense")
    w.apply_hud(HudRead(ts=0, events=["action"], timer=180))
    w.apply_hud(HudRead(ts=0, timer=170, kills=[Kill("dmhost88", "us", "Enemy_D", "them")]))
    time.sleep(0.05)
    assert said == []                                           # "Nice. Reposition." is gone
    w.apply_hud(HudRead(ts=0, timer=160, kills=[Kill("Enemy_A", "them", "dmhost88", "us")]))
    assert w.state.player_alive is False
    w.apply_hud(HudRead(ts=0, timer=150, alive_us=1, alive_them=3, events=["vs"]))
    assert LINES["clutch"] not in said                          # it's a teammate's clutch, not ours
    w.apply_hud(HudRead(ts=0, timer=140, events=["planted"]))
    assert LINES["def_planted"] not in said                     # dead: the coach stays quiet


def test_hud_alone_starts_new_rounds(bus):
    """With no cloud reads at all, prep banners still reset per-round lines."""
    w, live, said = make(bus, level=2)
    for _ in range(2):
        w.apply_hud(HudRead(ts=0, timer=44, events=["prep"], side="attack"))
        w.apply_hud(HudRead(ts=0, timer=180, events=["action"]))
        w.apply_hud(HudRead(ts=0, timer=44))
        w.apply_hud(HudRead(ts=0, timer_hidden=True, events=["lost_round"]))
    assert said.count(LINES["atk_45"]) == 2
    assert w.state.player_alive is True


def test_quick_cloud_reads_between_rounds(bus):
    """After a death the screen stays 'spectating' (60s interval); once the HUD shows no round,
    the watcher must read at the quick default rate so it catches the ban and pick screens."""
    import numpy as np
    w, live, said = make(bus)
    start_round(w)
    w.apply(ScreenRead(screen=Screen.spectating, confidence=0.9))
    w.gate.mark_read(time.time() - 20, np.zeros((40, 96), np.float32))   # 20s > quick refresh, < 60s
    w._hud_in_round_ts = time.time() - 30          # HUD hasn't seen a round for 30s
    reads = []
    w.claude = type("C", (), {"read_screen": lambda *a, **k: reads.append(1)})()
    img = np.full((1440, 3440, 3), 128, np.uint8)
    w.source = type("S", (), {"latest": lambda self: (time.time(), img)})()
    w._tick()
    assert reads == [1]


def test_advantage_said_once_a_round(bus, monkeypatch):
    import r6coach.live as live_mod
    monkeypatch.setattr(live_mod, "COOLDOWN", 0.0)
    monkeypatch.setattr(live_mod, "GUNFIGHT_HOLD", 0.0)
    w, live, said = make(bus, level=2)
    start_round(w, side="attack")
    w.apply_hud(HudRead(ts=0, events=["action"], timer=180))
    for us, them in ((5, 4), (4, 4), (4, 3), (3, 3), (3, 2)):     # the lead swings back and forth
        for _ in range(2):
            w.apply_hud(HudRead(ts=0, timer=150, alive_us=us, alive_them=them, events=["vs"]))
    assert said.count(LINES["advantage"]) == 1


@pytest.mark.parametrize("plan_said_it", [False, True])
def test_reinforce_reminder_quiet_after_the_plan_said_it(bus, plan_said_it):
    w, live, said = make(bus, level=2)
    start_round(w, side="defense")
    w.apply_hud(HudRead(ts=0, timer=44, events=["prep"]))
    w.state.team_reinforcements = 10
    if plan_said_it:
        bus.publish("coach.said", text="Reinforce the Laundry exterior wall first.", phase="prep_phase")
    w.apply_hud(HudRead(ts=0, timer=18))
    assert (LINES["reinforce"] in said) is not plan_said_it


def test_silent_at_level_zero(bus):
    w, live, said = make(bus, level=0)
    start_round(w)
    w.apply_hud(HudRead(ts=0, events=["action"], timer=180))
    w.apply_hud(HudRead(ts=0, timer=18))
    w.apply_hud(HudRead(ts=0, events=["planted"]))
    time.sleep(0.05)
    assert said == []
