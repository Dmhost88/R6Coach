"""The API-facing schemas must stay flat and fully required ("Schema is too complex" otherwise)."""

from r6coach.llm import CoachLines, WireRead
from r6coach.models import Screen


def _no_optionals(schema: dict) -> list[str]:
    bad = []
    defs = schema.get("$defs", {})
    for model in [schema, *defs.values()]:
        props = model.get("properties", {})
        req = set(model.get("required", []))
        for name, spec in props.items():
            if name not in req or "anyOf" in spec:
                bad.append(f"{model.get('title')}.{name}")
    return bad


def test_wire_schemas_are_flat():
    assert _no_optionals(WireRead.model_json_schema()) == []
    assert _no_optionals(CoachLines.model_json_schema()) == []


def test_wire_converts_sentinels():
    w = WireRead(
        screen=Screen.action_phase, map="", side="attack", site="", site_options=[], spawn="",
        my_location="2F Office", my_operator="", teammate_operators=[], enemy_operators=[],
        banned_operators=[], map_ban_options=[], score_us=1, score_them=-1, round_number=-1,
        timer_seconds=161, alive_us=5, alive_them=2, team_reinforcements=-1, defuser_planted=0,
        round_won=-1, killfeed=[], pinged_enemies=[], confidence=0.8, notes="",
    )
    r = w.to_read()
    assert r.map is None and r.side == "attack" and r.my_location == "2F Office"
    assert r.score_us == 1 and r.score_them is None and r.timer_seconds == 161
    assert r.defuser_planted is False and r.round_won is None and r.notes is None
