"""In-round calls talk less: fights settle first, check-ins need a room change, repeats are dropped.
Live-moment review, 2026-11-07."""

import time

import pytest

import r6coach.tactical as tactical
from r6coach.config import Config
from r6coach.llm import CoachLines
from r6coach.models import GameState, Screen
from r6coach.tactical import TacticalCoach


class Claude:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def budget_level(self):
        return "ok"

    def coach(self, model, effort, system, prompt, purpose):
        self.prompts.append(prompt)
        line = self.replies.pop(0) if self.replies else ""
        return CoachLines(lines=[line] if line else [], recommended_operator="", recommended_bans=[])


@pytest.fixture
def coach(bus, kb, monkeypatch):
    monkeypatch.setattr(tactical, "KILL_SETTLE", 0.1)

    def make(replies, **state):
        cfg = Config()
        cfg.coach.chattiness["action_phase"] = 3
        cfg.coach.live_min_gap = 0.0
        st = GameState(screen=Screen.action_phase, map="Chalet", side="defense", my_operator="Mira",
                       player_alive=True, **state)
        claude, said = Claude(replies), []
        t = TacticalCoach(cfg, bus, claude, kb, lambda text, **kw: said.append(text), lambda: st, None,
                          lambda m: ["sys"])
        return t, claude, said, st
    return make


def wait_idle(t, timeout=2.0):
    end = time.time() + timeout
    time.sleep(0.05)
    while time.time() < end and (t._busy or t._pending):
        time.sleep(0.02)
    t.pool.shutdown(wait=True)


def test_a_trade_is_one_call_after_the_fight_settles(coach, bus):
    t, claude, said, _ = coach(["Hold 2F Office from the desk, watching Library doorway."])
    bus.publish("live.event", kind="kill", victim_team="us", victim="Mate_A", killer="Enemy_A")
    bus.publish("live.event", kind="vs", alive_us=4, alive_them=5)
    bus.publish("live.event", kind="kill", killer_team="us", killer="Mate_B", victim="Enemy_A")
    bus.publish("live.event", kind="vs", alive_us=4, alive_them=4)
    assert claude.prompts == []                       # nothing asked while the fight is on
    time.sleep(0.3)
    wait_idle(t)
    assert len(claude.prompts) == 1
    assert "4 v 4" in claude.prompts[0]               # about the latest state, not the first kill
    assert len(said) == 1


def test_recent_calls_go_in_the_prompt_and_repeats_are_dropped(coach):
    t, claude, said, _ = coach(["Hold 2F Office from the desk, watching Library doorway.",
                                "Hold Office from the desk and watch the Library doorway.",
                                ""])
    t._call("action phase just started", time.time(), t._round)
    t._call("1:00 left on the clock", time.time(), t._round)
    t._call("0:30 left on the clock", time.time(), t._round)
    assert said == ["Hold 2F Office from the desk, watching Library doorway."]
    assert "Already said this round" in claude.prompts[1]
    assert "Library doorway" in claude.prompts[1]
    assert "Already said this round" not in claude.prompts[0]


def test_round_reset_forgets_the_calls(coach, bus):
    t, claude, said, _ = coach(["Hold 2F Office from the desk.", "Hold 2F Office from the desk."])
    t._call("action phase just started", time.time(), t._round)
    bus.publish("live.event", kind="round_reset")
    t._call("action phase just started", time.time(), t._round)
    assert len(said) == 2


def test_check_in_only_after_moving_room(coach, bus):
    t, claude, said, st = coach(["Hold Office.", "Rotate to Library through Main Hallway."],
                                my_location="2F Office")
    t.cfg.coach.live_checkin = 0.01
    t._call("action phase just started", time.time(), t._round)
    assert said == ["Hold Office."]
    time.sleep(0.05)
    bus.publish("live.event", kind="timer", seconds=120 + 30)
    time.sleep(0.05)
    assert len(claude.prompts) == 1                   # same room: no check-in
    st.my_location = "2F Main Hallway"
    bus.publish("live.event", kind="timer", seconds=140)
    wait_idle(t)
    assert len(claude.prompts) == 2 and "moved to 2F Main Hallway" in claude.prompts[1]


def test_live_calls_are_cut_to_the_word_cap(coach):
    long = ("Push from 1F Bar through Fireplace Hall to the Library stairs, then hold the top, "
            "watching Office door and the hatch.")
    t, claude, said, _ = coach([long])
    t._call("action phase just started", time.time(), t._round)
    assert len(said[0].split()) <= tactical.LIVE_WORDS
