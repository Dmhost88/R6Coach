from fastapi.testclient import TestClient

from r6coach import config as config_mod
from r6coach.app import App
from r6coach.config import Config
from r6coach.dashboard.server import build


def test_dashboard_endpoints(tmp_path, kb, monkeypatch):
    monkeypatch.setattr(config_mod, "config_path", lambda: tmp_path / "config.yaml")
    import r6coach.dashboard.server as server
    monkeypatch.setattr(server, "save_config", lambda c: config_mod.save_config(c, tmp_path / "config.yaml"))
    cfg = Config()
    cfg.storage.data_dir = str(tmp_path / "data")
    app = App(cfg, replay=tmp_path, enable_ptt=False)
    app.knowledge = kb
    client = TestClient(build(app))

    assert client.get("/").status_code == 200
    st = client.get("/api/status").json()
    assert st["capture"].startswith("replay")

    r = client.post("/api/settings", json={"operator_pool": {"attack": ["Ash"], "defense": ["Mute"]},
                                           "chattiness": {"prep_phase": 9}})
    assert r.status_code == 200
    assert app.cfg.coach.chattiness["prep_phase"] == 3   # clamped
    assert (tmp_path / "config.yaml").exists()

    assert client.get("/api/maps").json()[0]["id"] == "bank"
    client.post("/api/maps/bank/notes", json={"notes": "hello"})
    assert client.get("/api/maps/bank").json()["notes"] == "hello"

    asked = []
    app.bus.subscribe("ptt.question", lambda ev: asked.append(ev.data["text"]))
    client.post("/api/control/ask", json={"text": "what now?"})
    assert asked == ["what now?"]
    assert client.post("/api/control/nope").status_code == 404
    assert client.get("/api/frame.jpg").status_code == 404   # no frames in an empty replay


def _client(tmp_path, kb, monkeypatch, env=None):
    monkeypatch.setattr(config_mod, "config_path", lambda: tmp_path / "config.yaml")
    import r6coach.dashboard.server as server
    monkeypatch.setattr(server, "save_config", lambda c: config_mod.save_config(c, tmp_path / "config.yaml"))
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    cfg = Config()
    cfg.storage.data_dir = str(tmp_path / "data")
    app = App(cfg, replay=tmp_path, enable_ptt=False)
    app.knowledge = kb
    return app, TestClient(build(app))


def test_static_assets_served(tmp_path, kb, monkeypatch):
    _, client = _client(tmp_path, kb, monkeypatch)
    html = client.get("/").text
    for path in ("/static/ds/tokens.css", "/static/ds/components/bundle.css", "/static/css/app.css",
                 "/static/js/app.js"):
        assert path in html
        assert client.get(path).status_code == 200, path
    tokens = client.get("/static/ds/tokens.css").text
    assert "--signal:" in tokens and ':root[data-theme="light"]' in tokens and "@font-face" in tokens
    assert client.get("/static/ds/fonts/MonaSans.woff2").status_code == 200
    assert client.get("/static/ds/assets/Icons/phase-prep.svg").status_code == 200
    assert client.get("/static/ds/assets/Illustrations/empty-waiting.svg").status_code == 200


def test_settings_extras_and_setup_never_leak_keys(tmp_path, kb, monkeypatch):
    secret = "sk-ant-THIS-MUST-NOT-LEAK-123"
    app, client = _client(tmp_path, kb, monkeypatch, env={"ANTHROPIC_API_KEY": secret})
    app.cfg.voice.provider = "openai"
    s = client.get("/api/settings").json()
    assert s["voice"]["provider"] == app.cfg.voice.provider
    assert set(s["operators_by_side"]) == {"attack", "defense"}
    setup = client.get("/api/setup")
    assert setup.status_code == 200
    items = {i["id"]: i for i in setup.json()}
    assert items["anthropic"]["ok"] is True
    assert items["voice"]["ok"] is False          # OPENAI_API_KEY unset
    assert all(isinstance(i["ok"], bool) for i in items.values())
    for path in ("/api/setup", "/api/settings", "/api/status"):
        assert secret not in client.get(path).text, path


def test_test_voice_and_memory(tmp_path, kb, monkeypatch):
    app, client = _client(tmp_path, kb, monkeypatch)
    said = []
    monkeypatch.setattr(app.speaker, "say", lambda text, **kw: said.append((text, kw.get("phase"))))
    r = client.post("/api/control/test_voice")
    assert r.status_code == 200 and r.json()["test_voice"] == "queued"
    assert said and said[0][1] == "test"
    app.speaker.muted = True
    assert client.post("/api/control/test_voice").json()["test_voice"] == "muted"
    assert len(said) == 1

    assert client.get("/api/memory").json()["rounds"] == []
    # Feed the memory directly: publishing on the bus would also start the coach's debrief timers.
    from r6coach.bus import Event
    app.memory._on_died(Event("player.died", {"killer": "Bandit42"}))
    app.memory._on_round_ended(Event("round.ended", {"won": False, "state": {"side": "defense", "site": "CEO"}}))
    m = client.get("/api/memory").json()
    assert m["rounds"][0]["won"] is False and m["rounds"][0]["died_to"] == "Bandit42"
    assert m["rounds"][0]["site"] == "CEO" and m["current"]["n"] == 2


def test_coach_done_follows_spoken_line(tmp_path, kb, monkeypatch):
    import json
    app, client = _client(tmp_path, kb, monkeypatch)
    with client.websocket_connect("/ws") as ws:
        app.speaker._playing = True
        app.bus.publish("coach.said", text="3 v 2. They have to come to you.", phase="action_phase", audio=True)
        assert json.loads(ws.receive_text())["topic"] == "coach.said"
        app.speaker._playing = False          # playback finished
        assert json.loads(ws.receive_text())["topic"] == "coach.done"


def test_saving_settings_keeps_run_only_port(tmp_path, kb, monkeypatch):
    app, client = _client(tmp_path, kb, monkeypatch)
    import yaml
    with open(tmp_path / "config.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump({"dashboard": {"port": 8765, "open_browser": True}}, f)
    app.cfg.dashboard.port = 8775          # as if started with --port 8775 --no-browser
    app.cfg.dashboard.open_browser = False
    assert client.post("/api/settings", json={"volume": 0.5}).status_code == 200
    with open(tmp_path / "config.yaml", encoding="utf-8") as f:
        saved = yaml.safe_load(f)
    assert saved["dashboard"]["port"] == 8765 and saved["dashboard"]["open_browser"] is True
    assert saved["voice"]["volume"] == 0.5
