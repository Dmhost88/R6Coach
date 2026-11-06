import yaml

from r6coach.maplayout import MapLayouts


def make(tmp_path):
    lay = tmp_path / "layout"
    lay.mkdir()
    (lay / "chalet.yaml").write_text(yaml.safe_dump({"map": "chalet", "floors": [
        {"floor": "1F", "image": "f1.jpg", "size": [1600, 900], "rooms": {
            "Bar": {"box": [43, 65, 53, 90]}, "Bar Stock": {"box": [35, 51, 44, 58]},
            "Campfire": {"box": [5, 64, 22, 95]}, "Office": {"box": [1, 1, 2, 2]}}},
        {"floor": "2F", "image": "f2.jpg", "size": [1600, 900], "rooms": {
            "Office": {"box": [40, 77, 47, 91]}, "Library": {"box": [34, 68, 40, 91]},
            "Kids' Dorms": {"box": [1, 1, 5, 5]}}},
    ]}), encoding="utf-8")
    imgs = tmp_path / "maps" / "chalet"
    imgs.mkdir(parents=True)
    (imgs / "f2.jpg").write_bytes(b"x")
    return MapLayouts(lay, tmp_path / "maps")


def test_mentions_in_order_longest_first(tmp_path):
    m = make(tmp_path)
    line = "From EXT Campfire, check Bar Stock, then charge the 2F Office to Library wall."
    assert m.mentions("chalet", line) == [
        {"floor": "1F", "room": "Campfire"}, {"floor": "1F", "room": "Bar Stock"},
        {"floor": "2F", "room": "Office"}, {"floor": "2F", "room": "Library"}]
    assert m.mentions("chalet", "hold kids dorms") == [{"floor": "2F", "room": "Kids' Dorms"}]
    assert m.mentions("chalet", "Barricade it") == []          # whole words only
    assert m.mentions("bank", "Office") == []                  # no layout


def test_layout_endpoint_and_focus_event(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from r6coach.app import App
    from r6coach.config import Config
    from r6coach.dashboard.server import build
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = Config()
    cfg.storage.data_dir = str(tmp_path / "data")
    app = App(cfg, replay=tmp_path, enable_ptt=False)
    app.layouts = make(tmp_path)
    app.watcher.state.map = "Chalet"
    app.watcher.state.my_location = "2F Library"
    client = TestClient(build(app))
    lay = client.get("/api/layout").json()
    assert lay["map"] == "chalet" and [f["floor"] for f in lay["floors"]] == ["1F", "2F"]
    assert lay["you"] == {"floor": "2F", "room": "Library"}
    assert lay["floors"][0]["url"] is None and lay["floors"][1]["url"].endswith("/f2.jpg")
    assert client.get("/api/blueprint/chalet/..%2Fx.jpg").status_code == 404
    seen = []
    app.bus.subscribe("map.focus", lambda ev: seen.append(ev.data["rooms"]))
    app.bus.publish("coach.said", text="Charge the Office to Library wall.", phase="action_phase")
    assert seen == [[{"floor": "2F", "room": "Office"}, {"floor": "2F", "room": "Library"}]]


def test_locate_and_images(tmp_path):
    m = make(tmp_path)
    assert m.locate("chalet", "2F Office") == {"floor": "2F", "room": "Office"}
    assert m.locate("chalet", "1F Office") == {"floor": "1F", "room": "Office"}
    assert m.image_path("chalet", "f2.jpg") is not None
    assert m.image_path("chalet", "../secret.jpg") is None
    assert m.image_path("chalet", "f1.jpg") is None            # named but not on disk
