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
