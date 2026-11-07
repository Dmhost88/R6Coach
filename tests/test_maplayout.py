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
    assert lay["you"] == {"floor": "2F", "room": "Library", "fi": 1}
    assert lay["floors"][0]["url"] is None and lay["floors"][1]["url"].endswith("/f2.jpg")
    assert client.get("/api/blueprint/chalet/..%2Fx.jpg").status_code == 404
    seen = []
    app.bus.subscribe("map.focus", lambda ev: seen.append(ev.data["rooms"]))
    app.bus.publish("coach.said", text="Charge the Office to Library wall.", phase="action_phase")
    assert seen == [[{"floor": "2F", "room": "Office", "fi": 1}, {"floor": "2F", "room": "Library", "fi": 1}]]


def test_resolve_positions(tmp_path):
    from r6coach.maplayout import edge_point, wall_point
    m = make(tmp_path)
    # Side by side (Library 34-40, Office 40-47): the middle of the shared wall.
    assert m.resolve("chalet", {"floor": "2F", "between": ["Office", "Library"]}) == \
        {"fi": 1, "floor": "2F", "x": 40.0, "y": 84.0}
    assert m.resolve("chalet", {"floor": "2F", "room": "Library"})["x"] == 37.0
    assert m.resolve("chalet", {"floor": "1F", "at": [10, 20], "nudge": [1.5, -1]}) == \
        {"fi": 0, "floor": "1F", "x": 11.5, "y": 19.0}
    assert m.resolve("chalet", {"floor": "1F", "between": ["Bar", "exterior"], "edge": "bottom"})["y"] == 90.0
    assert m.resolve("chalet", {"floor": "2F", "room": "Bar"}) is None          # not on that floor
    assert m.resolve("chalet", {"floor": "3F", "room": "Office"}) is None       # no such floor
    assert m.resolve("bank", {"floor": "1F", "room": "Office"}) is None        # no layout
    assert wall_point([0, 0, 10, 10], [10, 20, 20, 30]) == (10.0, 15.0)       # diagonal: between centres
    assert edge_point([2, 40, 10, 60], None) == (2, 50.0)                     # nearest image edge: left


def test_duplicate_floor_names_pick_the_image_with_the_room(tmp_path):
    lay = tmp_path / "layout"
    lay.mkdir()
    (lay / "bank.yaml").write_text(yaml.safe_dump({"map": "bank", "floors": [
        {"floor": "1F", "image": "a.jpg", "rooms": {"Lobby": {"box": [1, 1, 5, 5]}}},
        {"floor": "1F", "image": "b.jpg", "rooms": {"Vault": {"box": [50, 50, 60, 60]}}},
    ]}), encoding="utf-8")
    m = MapLayouts(lay, tmp_path)
    assert m.floor_index("bank", "1F", ("Vault",)) == 1
    assert m.floor_index("bank", "1F") == 0
    assert m.resolve("bank", {"floor": "1F", "room": "Vault"})["fi"] == 1


def test_features_and_sides(tmp_path):
    lay = tmp_path / "layout"
    lay.mkdir()
    (lay / "chalet.yaml").write_text(yaml.safe_dump({"map": "chalet", "sides": {"top": "Lakeside", "up": "x"},
        "floors": [{"floor": "2F", "image": "f2.jpg", "rooms": {}, "features": [
            {"kind": "hatch", "name": "Library hatch", "at": [36, 77]},
            {"kind": "stairs", "name": "Main Stairs", "at": [50, 50]},
            {"kind": "teleporter", "at": [1, 1]},               # unknown kinds are dropped
            {"kind": "window", "name": "no position"}]}]}), encoding="utf-8")
    m = MapLayouts(lay, tmp_path)
    assert m.sides("chalet") == {"top": "Lakeside"}
    assert [(f["kind"], f["name"], f["fi"], f["x"]) for f in m.features("chalet")] == \
        [("hatch", "Library hatch", 0, 36.0), ("stairs", "Main Stairs", 0, 50.0)]
    assert m.features("bank") == [] and m.sides("bank") == {}


def test_real_layouts_load_and_features_are_on_the_image():
    from r6coach.knowledge import DATA
    m = MapLayouts(DATA / "layout", DATA / "no-images")
    assert len(m.layouts) >= 15
    for mid in m.layouts:
        for f in m.features(mid):
            assert 0 <= f["x"] <= 100 and 0 <= f["y"] <= 100, (mid, f)
        for fl in m.layouts[mid]["floors"]:
            for room, r in (fl.get("rooms") or {}).items():
                x0, y0, x1, y1 = r["box"]
                assert 0 <= x0 < x1 <= 100 and 0 <= y0 < y1 <= 100, (mid, room)


def test_locate_and_images(tmp_path):
    m = make(tmp_path)
    assert m.locate("chalet", "2F Office") == {"floor": "2F", "room": "Office"}
    assert m.locate("chalet", "1F Office") == {"floor": "1F", "room": "Office"}
    assert m.image_path("chalet", "f2.jpg") is not None
    assert m.image_path("chalet", "../secret.jpg") is None
    assert m.image_path("chalet", "f1.jpg") is None            # named but not on disk
