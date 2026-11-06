from r6coach.memory import MatchMemory
from r6coach.models import GameState


def test_memory_records_rounds_and_tendencies(bus):
    state = GameState(my_location="2F Dorms Main Hall")
    mem = MatchMemory(bus, lambda: state, "dmhost88")
    assert "first round" in mem.text()

    bus.publish("live.event", kind="round_reset")
    bus.publish("live.event", kind="kill", killer="Enemy_A", killer_team="them", victim="Mate_A",
                victim_team="us", mine=False)
    bus.publish("live.event", kind="kill", killer="dmhost88", killer_team="us", victim="Enemy_D",
                victim_team="them", mine=True)
    bus.publish("player.died", killer="Enemy_E")
    bus.publish("round.ended", won=False, state={"side": "attack", "site": "2F Kids' Dorms / 2F Dorms Main Hall",
                                                 "my_operator": "Ash", "enemy_operators": ["Ela", "Kaid"]})
    bus.publish("live.event", kind="round_reset")
    bus.publish("player.died", killer="Enemy_E")
    bus.publish("round.ended", won=True, state={"side": "attack", "enemy_operators": ["Ela", "Mira"]})

    text = mem.text()
    assert "R1 attack" in text and "you on Ash" in text and "LOST" in text
    assert "first blood theirs" in text and "yours 1" in text
    assert "you died to Enemy_E in 2F Dorms Main Hall" in text
    assert "Enemy defense operators seen (times): Ela x2" in text
    assert "Who has killed you: Enemy_E x2" in text

    bus.publish("match.started", match_id="m2")
    assert "first round" in mem.text()
