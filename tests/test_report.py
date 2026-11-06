"""Per-round history, the post-match report, and personal stats for prompts."""

from r6coach.memory import MatchMemory
from r6coach.models import GameState
from r6coach.profile import Profile


def test_rounds_stored_with_detail_and_report(tmp_path, bus):
    prof = Profile(tmp_path / "p.sqlite")
    prof.match_started("m1")
    state = {"s": GameState(match_id="m1", my_location="2F Office")}
    MatchMemory(bus, lambda: state["s"], "dmhost88")
    bus.subscribe("memory.round", lambda ev: prof.round_ended(
        {**ev.data["state"], "match_id": "m1", "map": "Chalet"}, ev.data["won"], ev.data["record"]))

    for won, died in ((True, None), (False, "Enemy_E"), (False, "Enemy_E")):
        bus.publish("live.event", kind="round_reset")
        bus.publish("live.event", kind="kill", killer="dmhost88", killer_team="us", victim="X",
                    victim_team="them", mine=True)
        if died:
            bus.publish("player.died", killer=died)
        bus.publish("round.ended", won=won, state={"side": "attack", "site": "2F Master Bedroom / 2F Office",
                                                   "my_operator": "Thermite", "enemy_operators": ["Kapkan"]})
    prof.match_ended("m1", {"map": "Chalet", "score_us": 1, "score_them": 2})
    prof.set_takeaways("m1", ["Repeat the Library rappel.", "Stop dying in Office.", "Practise the 90."])

    rep = prof.match_report("m1")
    assert rep["map"] == "Chalet" and rep["won"] == 0 and len(rep["takeaways"]) == 3
    assert [r["won"] for r in rep["rounds"]] == [True, False, False]
    r2 = rep["rounds"][1]
    assert r2["died_to"] == "Enemy_E" and r2["died_in"] == "2F Office" and r2["first_blood"] == "us"
    assert r2["my_kills"] == 1 and r2["enemy_ops"] == ["Kapkan"]

    stats = prof.stats_text("Chalet", "attack")
    assert "1/3" in stats                              # site record
    assert "2F Office x2" in stats                     # where you keep dying
    assert "Thermite 1/3" in stats                     # operator record
    assert prof.stats_text("Bank") == "" or "Thermite" in prof.stats_text("Bank")


def test_report_endpoint(tmp_path, kb, monkeypatch):
    from fastapi.testclient import TestClient
    from r6coach.app import App
    from r6coach.config import Config
    from r6coach.dashboard.server import build
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = Config()
    cfg.storage.data_dir = str(tmp_path / "data")
    app = App(cfg, replay=tmp_path, enable_ptt=False)
    app.profile.match_started("m9")
    app.profile.round_ended({"match_id": "m9", "map": "Bank", "side": "defense"}, True, {"my_kills": 2})
    app.profile.match_ended("m9", {"map": "Bank", "score_us": 4, "score_them": 2})
    client = TestClient(build(app))
    rep = client.get("/api/matches/m9").json()
    assert rep["map"] == "Bank" and rep["rounds"][0]["my_kills"] == 2
    assert client.get("/api/matches/nope").status_code == 404
    assert "match-report" in client.get("/").text


def test_old_database_is_migrated(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE rounds (id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT, ts REAL, "
                     "round_number INTEGER, map TEXT, side TEXT, site TEXT, spawn TEXT, operator TEXT, "
                     "teammates TEXT, won INTEGER); CREATE TABLE matches (id TEXT PRIMARY KEY, started REAL, "
                     "ended REAL, map TEXT, score_us INTEGER, score_them INTEGER, won INTEGER);")
    db.close()
    prof = Profile(path)
    prof.round_ended({"match_id": "m"}, True, {"died_in": "B Garage"})
    assert prof._query("SELECT died_in FROM rounds")[0][0] == "B Garage"
