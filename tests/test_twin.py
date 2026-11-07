"""The shared match-state rendering, the ban pre-plan at round end, and fast-model routing."""

import time

from r6coach.config import Config
from r6coach.models import GameState, Screen
from r6coach.twin import render
from tests.test_coach import push, setup, wait


def test_render_labels_sources_and_keeps_the_player_apart():
    s = GameState(map="Chalet", round_number=4, side="defense", side_source="predicted", score_us=3,
                  my_operator="Thermite", teammate_operators=["Kali", "Buck"], banned_operators=["Ace", "Mute"])
    text = render(s, recommended="Ace")
    assert "PREDICTED" in text and "round 4" in text and "score 3-0" in text
    assert "LOCKED Thermite" in text and "Teammates (the other players only): Kali, Buck" in text
    assert "suggested Ace but they took something else" in text
    assert "Banned so far this match (unavailable; this round's ban screen wins if it differs): Ace, Mute" in text
    dead = render(s.model_copy(update={"player_alive": False}))
    assert "DEAD" in dead


def test_model_routing():
    c = Config().coach
    assert c.model_for("operator_ban") == c.fast_model and c.model_for("live") == c.fast_model
    assert c.model_for("between_rounds") == c.model and c.model_for("prep_phase") == c.model


def test_ban_is_preplanned_at_round_end_and_played_on_the_ban_screen(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    push(bus, state, screen=Screen.action_phase, map="Chalet", side="attack", round_number=3,
         banned_operators=["Ace"])
    bus.publish("round.ended", won=True, state=state["s"].model_dump(mode="json"))
    assert wait(lambda: any(p[0] == "coach.operator_ban" for p in claude.prompts), timeout=4)
    ban = [p for p in claude.prompts if p[0] == "coach.operator_ban"][0][1]
    assert "round 4" in ban and "defense" in ban and "PREDICTED" in ban    # swap after round 3
    assert not any(ph == "operator_ban" for ph, _, _ in speaker.said)       # not spoken yet
    push(bus, state, screen=Screen.operator_ban, round_number=4, side="defense")
    assert wait(lambda: any(ph == "operator_ban" for ph, _, _ in speaker.said))
    time.sleep(0.2)
    assert len([p for p in claude.prompts if p[0] == "coach.operator_ban"]) == 1   # no second call
