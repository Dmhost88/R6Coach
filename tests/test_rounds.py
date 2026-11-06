"""Side tracking from the swap rule, the spectating death backstop, and in-round call framing."""

from r6coach.hud import HudRead
from r6coach.models import Screen, ScreenRead
from r6coach.rounds import first_side_from, round_from_score, side_for, swapped
from tests.test_live import make


def test_swap_rule():
    assert [swapped(n) for n in range(1, 10)] == [False, False, False, True, True, True, False, True, False]
    assert side_for(4, "attack") == "defense" and side_for(3, "attack") == "attack"
    assert first_side_from(5, "defense") == "attack" and first_side_from(2, "defense") == "defense"
    assert round_from_score(2, 1) == 4 and round_from_score(None, 1) is None


def _round(w, n, read_side, screen=Screen.operator_ban):
    """One round as the cloud saw it in the live Chalet match: ban card with 'ROUND n'."""
    w.apply(ScreenRead(screen=screen, map="Chalet", round_number=n, side=read_side, confidence=0.9))


def test_side_predicted_across_the_swap(bus):
    w, live, said = make(bus)
    w.apply(ScreenRead(screen=Screen.main_menu, confidence=0.9))
    for n in (1, 2, 3):
        _round(w, n, "attack")
        w.apply(ScreenRead(screen=Screen.action_phase, confidence=0.9))
        w.apply(ScreenRead(screen=Screen.round_end, round_won=True, confidence=0.9))
    # Round 4 ban card: the read still said "attack" (last round's side). The swap rule wins.
    _round(w, 4, "attack")
    assert w.state.round_number == 4 and w.state.side == "defense"
    # In op select the screen shows the real side; it agrees.
    w.apply(ScreenRead(screen=Screen.operator_select, side="defense", confidence=0.9))
    assert w.state.side == "defense"


def test_side_predicted_without_any_side_read(bus):
    """Rounds counted by the watcher alone (no 'ROUND n' read) still swap after three."""
    w, live, said = make(bus)
    w.apply(ScreenRead(screen=Screen.main_menu, confidence=0.9))
    w.apply(ScreenRead(screen=Screen.operator_select, map="Bank", side="defense", confidence=0.9))
    for _ in range(3):
        w.apply(ScreenRead(screen=Screen.action_phase, confidence=0.9))
        w.apply(ScreenRead(screen=Screen.round_end, confidence=0.9))
        w.apply(ScreenRead(screen=Screen.operator_ban, confidence=0.9))
    assert w.state.round_number == 4 and w.state.side == "attack"


def test_spectating_marks_the_player_dead_once(bus):
    w, live, said = make(bus, name="")          # no player name: the killfeed can't tell
    died = []
    bus.subscribe("player.died", lambda ev: died.append(ev.data))
    w.apply(ScreenRead(screen=Screen.main_menu, confidence=0.9))
    w.apply(ScreenRead(screen=Screen.operator_select, map="Chalet", side="attack", confidence=0.9))
    w.apply_hud(HudRead(ts=0, events=["action"], timer=180))
    w.apply(ScreenRead(screen=Screen.spectating, confidence=0.9))
    w.apply(ScreenRead(screen=Screen.spectating, confidence=0.9))
    assert w.state.player_alive is False
    assert len(died) == 1 and died[0]["source"] == "spectating"


def test_spectator_call_is_about_the_team(bus, kb):
    from r6coach.config import Config
    from r6coach.llm import CoachLines
    from r6coach.models import GameState
    from r6coach.tactical import TacticalCoach

    class Claude:
        prompts = []

        def budget_level(self):
            return "ok"

        def coach(self, model, effort, system, prompt, purpose):
            self.prompts.append(prompt)
            return CoachLines(lines=["Kali should plant in the Master corner."], recommended_operator="",
                              recommended_bans=[])

    cfg = Config()
    cfg.coach.chattiness["action_phase"] = 3
    st = GameState(screen=Screen.spectating, map="Chalet", side="attack", my_operator="Thermite",
                   teammate_operators=["Kali", "Buck"], player_alive=False)
    claude, said = Claude(), []
    t = TacticalCoach(cfg, bus, claude, kb, lambda text, **kw: said.append(text), lambda: st, None,
                      lambda m: ["sys"])
    t._call("teammate Buck was just killed", __import__("time").time(), t._round)
    p = claude.prompts[0]
    assert "DEAD" in p and "third person" in p and "(\"you\")" not in p
    assert said == ["Kali should plant in the Master corner."]
