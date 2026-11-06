import numpy as np

from r6coach.config import Config
from r6coach.models import Screen, ScreenRead
from r6coach.watcher import ReadGate, Watcher


class _NoFrames:
    status = "test"

    def latest(self):
        return None


class _NoSave:
    def maybe_save(self, *a):
        pass


def make_watcher(bus):
    return Watcher(Config(), bus, _NoFrames(), claude=None, frames=_NoSave())


def test_gate_respects_min_interval_and_change():
    gate = ReadGate(Config())
    a = np.zeros((40, 96), np.float32)
    b = np.full((40, 96), 120, np.float32)
    assert gate.should_read(100.0, a, Screen.other)        # first read
    gate.mark_read(100.0, a)
    assert not gate.should_read(101.0, b, Screen.other)    # too soon (min 6s)
    gate.should_read(106.5, b, Screen.other)               # settles: same frame twice
    assert gate.should_read(107.2, b, Screen.other)        # changed and settled
    gate.mark_read(107.2, b)
    assert not gate.should_read(114.0, b, Screen.other)    # unchanged, before refresh
    assert gate.should_read(123.0, b, Screen.other)        # refresh (6 * 2.5 = 15s)


def test_gate_action_phase_does_not_wait_for_settle():
    gate = ReadGate(Config())
    rng = np.random.default_rng(0)
    gate.mark_read(0.0, np.zeros((40, 96), np.float32))
    frame = rng.uniform(0, 255, (40, 96)).astype(np.float32)
    assert not gate.should_read(30.0, frame, Screen.action_phase)   # HUD reader covers live rounds
    assert gate.should_read(95.0, frame, Screen.action_phase)


def test_match_round_flow(bus):
    w = make_watcher(bus)
    events = []
    bus.subscribe("*", lambda ev: events.append(ev.topic))
    w.apply(ScreenRead(screen=Screen.main_menu, confidence=0.9))
    w.apply(ScreenRead(screen=Screen.operator_select, map="Bank", side="defense",
                       site="B Lockers / B CCTV Room", teammate_operators=["Mute"], confidence=0.9))
    assert "match.started" in events
    mid = w.state.match_id
    assert w.state.site == "B Lockers / B CCTV Room"
    w.apply(ScreenRead(screen=Screen.prep_phase, my_operator="Kaid", confidence=0.9))
    w.apply(ScreenRead(screen=Screen.round_end, round_won=True, score_us=1, score_them=0, confidence=0.9))
    assert "round.ended" in events
    # next round: op select again, same match, round-scoped fields reset
    w.apply(ScreenRead(screen=Screen.operator_select, map="Bank", side="defense", confidence=0.9))
    assert w.state.match_id == mid
    assert w.state.site is None and w.state.my_operator is None
    assert w.state.score_us == 1


def test_bans_last_the_match_and_location_is_per_round(bus):
    w = make_watcher(bus)
    w.apply(ScreenRead(screen=Screen.main_menu, confidence=0.9))
    w.apply(ScreenRead(screen=Screen.operator_ban, map="House", banned_operators=["Castle"],
                       site_options=["2F Master Bedroom / 2F Car Room", "B Garage / B Gym"], confidence=0.9))
    w.apply(ScreenRead(screen=Screen.operator_select, side="attack", confidence=0.9))
    assert w.state.banned_operators == ["Castle"]          # same round: kept
    assert len(w.state.site_options) == 2
    w.apply(ScreenRead(screen=Screen.action_phase, my_location="2F Upper Hallway", confidence=0.9))
    assert w.state.my_location == "2F Upper Hallway"
    w.apply(ScreenRead(screen=Screen.round_end, confidence=0.9))
    w.apply(ScreenRead(screen=Screen.operator_ban, banned_operators=["Goyo"], confidence=0.9))
    assert w.state.banned_operators == ["Castle", "Goyo"]  # round 1 bans still stand
    assert w.state.my_location is None and w.state.site_options == []
    w.apply(ScreenRead(screen=Screen.match_end, confidence=0.9))
    w.apply(ScreenRead(screen=Screen.map_ban, map_ban_options=["Bank", "Villa"], confidence=0.9))
    assert w.state.banned_operators == []                  # new match: cleared


def test_local_card_read_wins_over_cloud_for_the_lock(bus):
    from r6coach.opcards import OpCards
    w = make_watcher(bus)
    w.apply(ScreenRead(screen=Screen.main_menu, confidence=0.9))
    w.apply(ScreenRead(screen=Screen.operator_select, map="Chalet", side="attack", banned_operators=["Ace"],
                       confidence=0.9))
    cards = OpCards(my_operator="Thermite", teammates=["Kali", "Buck", "Thermite"], banned=["Mira"],
                    screen="operator_select", confidence=0.9, ms=30.0, source="cards")
    w._apply_cards(cards)
    assert w.state.my_operator == "Thermite" and w.state.teammate_operators == ["Kali", "Buck"]
    assert w.state.banned_operators == ["Ace", "Mira"]
    # A later cloud read that gets the lock wrong doesn't undo it this round.
    w.apply(ScreenRead(screen=Screen.operator_select, my_operator="Ash", confidence=0.9))
    assert w.state.my_operator == "Thermite"
    # An unsure single read doesn't set the lock; the same answer twice does.
    w3 = make_watcher(bus)
    unsure = OpCards(my_operator="Ram", screen="operator_select", confidence=0.5, source="cards")
    w3._apply_cards(unsure)
    assert w3.state.my_operator is None
    w3._apply_cards(unsure)
    assert w3.state.my_operator == "Ram"
    # A hovered operator on the OPERATORS panel is not a lock.
    w2 = make_watcher(bus)
    w2._apply_cards(OpCards(my_operator="Ash", teammates=[], banned=[], screen="operator_select",
                            confidence=0.5, ms=20.0, source="panel"))
    assert w2.state.my_operator is None


def test_own_operator_is_not_a_teammate(bus):
    w = make_watcher(bus)
    w.apply(ScreenRead(screen=Screen.main_menu, confidence=0.9))
    # The top bar lists the player's own card too: this read happened in the live match.
    w.apply(ScreenRead(screen=Screen.operator_select, side="attack", my_operator="Thermite",
                       teammate_operators=["Buck", "Deimos", "Thermite"], confidence=0.9))
    assert w.state.my_operator == "Thermite"
    assert w.state.teammate_operators == ["Buck", "Deimos"]
    w.apply(ScreenRead(screen=Screen.operator_select, side="attack",
                       teammate_operators=["Rauora", "Fuze", "Buck", "Deimos", "thermite"], confidence=0.9))
    assert w.state.teammate_operators == ["Rauora", "Fuze", "Buck", "Deimos"]


def test_low_confidence_reads_are_ignored(bus):
    w = make_watcher(bus)
    w.apply(ScreenRead(screen=Screen.operator_select, map="Bank", confidence=0.1))
    assert w.state.screen == Screen.other and w.state.map is None
