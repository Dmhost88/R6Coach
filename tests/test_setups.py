import pytest
import yaml

from r6coach.setups import KINDS, SetupError, SetupLibrary, describe, validate

BRIEFS = {"map": "chalet", "briefs": [
    {"id": "chalet-office-team", "site": "master_bedroom_office", "side": "defense", "operators": [],
     "title": "Office walls", "text": "Reinforce the Office-Library wall.",
     "placements": [{"kind": "reinforce", "floor": "2F", "between": ["Office", "Library"], "note": "Both panels"},
                    {"kind": "hatch", "floor": "2F", "at": [36, 77], "name": "Library hatch"}]},
    {"id": "chalet-office-kaid", "site": "master_bedroom_office", "side": "defense", "operators": ["Kaid"],
     "title": "Claw the Library wall", "text": "Claw it low.",
     "placements": [{"kind": "kaid", "floor": "2F", "between": ["Office", "Library"], "nudge": [-1, 0]}]},
    {"id": "chalet-bar-team", "site": "bar_gaming", "side": "defense", "operators": [],
     "title": "Bar walls", "text": "Reinforce Bar.",
     "placements": [{"kind": "reinforce", "floor": "1F", "room": "Bar"}]},
    {"id": "chalet-office-hibana", "site": "master_bedroom_office", "side": "attack", "operators": ["Hibana"],
     "title": "Open the Office wall", "text": "From Library.",
     "placements": [{"kind": "hibana", "floor": "2F", "between": ["Library", "Office"]},
                    {"kind": "plant", "floor": "2F", "room": "Office"}]},
]}


def lib(tmp_path, data=BRIEFS, shots=True):
    d = tmp_path / "setups"
    d.mkdir(exist_ok=True)
    (d / "chalet.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return SetupLibrary(d, tmp_path / "shots" if shots else None)


def test_pick_ranks_operator_then_team(tmp_path):
    s = lib(tmp_path)
    assert [b["id"] for b in s.pick("chalet", ["master_bedroom_office"], "defense", "KAID")] == \
        ["chalet-office-kaid", "chalet-office-team"]
    assert [b["id"] for b in s.pick("chalet", ["master_bedroom_office"], "defense", "Mira")] == \
        ["chalet-office-team", "chalet-office-kaid"]
    # Several possible sites: in the order given.
    assert [b["id"] for b in s.pick("chalet", ["bar_gaming", "master_bedroom_office"], "defense")][0] == "chalet-bar-team"
    assert [b["id"] for b in s.pick("chalet", None, "attack")] == ["chalet-office-hibana"]
    assert len(s.pick("chalet")) == 4
    assert s.pick("bank", None, "defense") == []


def test_validate_rejects_bad_briefs():
    def bad(**change):
        b = dict(BRIEFS["briefs"][0], **change)
        with pytest.raises(SetupError):
            validate("chalet", {"map": "chalet", "briefs": [b]})
    bad(side="offense")
    bad(id="Bad Id")
    bad(text="")
    bad(placements=[])
    bad(placements=[{"kind": "nuke", "floor": "2F", "room": "Office"}])
    bad(placements=[{"kind": "mira", "floor": "2F"}])
    bad(placements=[{"kind": "mira", "floor": "2F", "at": [120, 3]}])
    bad(placements=[{"kind": "mira", "floor": "2F", "between": ["Office"]}])
    bad(placements=[{"kind": "mira", "floor": "2F", "room": "Office", "edge": "north"}])
    with pytest.raises(SetupError):
        validate("chalet", {"map": "chalet", "briefs": [BRIEFS["briefs"][0], BRIEFS["briefs"][0]]})
    with pytest.raises(SetupError):
        validate("bank", BRIEFS)


def test_bad_file_is_reported_not_raised(tmp_path):
    s = lib(tmp_path, {"map": "chalet", "briefs": [{"id": "x"}]})
    assert s.for_map("chalet") == [] and "chalet" in s.errors


def test_shot_slots(tmp_path):
    s = lib(tmp_path)
    assert s.slot_ok("chalet", "chalet-office-team-2")
    assert not s.slot_ok("chalet", "chalet-office-team-3")      # only two placements
    assert not s.slot_ok("chalet", "../chalet-office-team-1")
    assert s.shot_path("chalet", "chalet-office-team-1") is None
    p = s.save_shot("chalet", "chalet-office-team-1", b"\xff\xd8\xffjpeg", ".jpg")
    assert p == tmp_path / "shots" / "chalet" / "chalet-office-team-1.jpg"
    s.save_shot("chalet", "chalet-office-team-1", b"\x89PNG", ".png")          # replaces, not adds
    assert s.shot_path("chalet", "chalet-office-team-1").suffix == ".png"
    assert not p.exists()
    s.clear_shot("chalet", "chalet-office-team-1")
    assert s.shot_path("chalet", "chalet-office-team-1") is None
    with pytest.raises(SetupError):
        s.save_shot("chalet", "nope-1", b"x", ".jpg")
    with pytest.raises(SetupError):
        s.save_shot("chalet", "chalet-office-team-1", b"x", ".exe")


def test_marks(tmp_path):
    from r6coach.setups import clean_marks
    s = lib(tmp_path)
    slot = "chalet-office-team-1"
    assert s.marks("chalet", slot) == []
    saved = s.save_marks("chalet", slot, [{"shape": "circle", "x": 40, "y": 55.561},
                                         {"shape": "arrow", "x": 10, "y": 10, "x2": 30, "y2": 40, "label": "x" * 99}])
    assert saved[0] == {"shape": "circle", "x": 40.0, "y": 55.56}
    assert len(saved[1]["label"]) == 40
    assert s.marks("chalet", slot) == saved
    for bad in ([{"shape": "star", "x": 1, "y": 1}], [{"x": 101, "y": 1}], [{"shape": "box", "x": 1, "y": 1}],
                "nope", [{"x": 1, "y": 1}] * 13):
        with pytest.raises(SetupError):
            clean_marks(bad)
    s.save_shot("chalet", slot, b"\xff\xd8\xff", ".jpg")     # a new picture drops the old markers
    assert s.marks("chalet", slot) == []
    with pytest.raises(SetupError):
        s.save_marks("chalet", "nope-1", [])


def test_dossier_text(tmp_path):
    s = lib(tmp_path)
    text = s.dossier("chalet")
    assert "Claw the Library wall" in text and "Kaid" in text
    assert "2F reinforce: Office | Library wall (Both panels)" in text
    assert "2F hatch: Library hatch" in text
    assert s.dossier("bank") == ""
    assert describe({"kind": "bandit", "floor": "B", "between": ["Snowmobile Garage", "exterior"]}) == \
        "B bandit: Snowmobile Garage exterior wall"


def test_shipped_setups_are_valid_and_land_on_the_map():
    """Every brief in the repo loads, names a real site and every placement is on the blueprint."""
    from r6coach.knowledge import DATA
    from r6coach.maplayout import MapLayouts
    s = SetupLibrary(DATA / "setups")
    assert not s.errors, s.errors
    lays = MapLayouts(DATA / "layout", DATA / "no-images")
    for mid, briefs in s.briefs.items():
        with open(DATA / "maps" / f"{mid}.yaml", encoding="utf-8") as f:
            sites = {x["id"] for x in (yaml.safe_load(f) or {}).get("sites") or []}
        for b in briefs:
            assert b["site"] in sites, (mid, b["id"], b["site"])
            for i, p in enumerate(b["placements"], 1):
                assert p["kind"] in KINDS
                assert lays.resolve(mid, p) is not None, (mid, b["id"], i, describe(p))


def test_dossier_includes_briefs(tmp_path):
    from r6coach.knowledge import DATA, Knowledge
    kb = Knowledge(notes_dir=tmp_path / "notes", data_dir=DATA)
    kb.setups = lib(tmp_path)
    assert "Standard setups" in kb.map_dossier("Chalet")


def test_setups_endpoint_and_pictures(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from r6coach.app import App
    from r6coach.config import Config
    from r6coach.dashboard.server import build
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = Config()
    cfg.storage.data_dir = str(tmp_path / "data")
    app = App(cfg, replay=tmp_path, enable_ptt=False)
    app.setups = lib(tmp_path)
    st = app.watcher.state
    st.map, st.side, st.site, st.my_operator = "Chalet", "defense", "Master Bedroom", "Kaid"
    client = TestClient(build(app))
    d = client.get("/api/setups").json()
    assert d["map"] == "chalet" and d["site"] == "master_bedroom_office" and d["side"] == "defense"
    assert [b["id"] for b in d["briefs"]] == ["chalet-office-kaid", "chalet-office-team"]
    kaid = d["briefs"][0]["placements"][0]
    assert kaid["slot"] == "chalet-office-kaid-1" and kaid["shot"] is None and kaid["team"] == "defense"
    assert kaid["pos"] is not None and kaid["pos"]["floor"] == "2F"     # real Chalet layout
    # Named situation instead of the game's.
    d = client.get("/api/setups?map=chalet&side=attack").json()
    assert [b["id"] for b in d["briefs"]] == ["chalet-office-hibana"]

    url = "/api/setups/chalet/shot/chalet-office-kaid-1"
    assert client.put(url, content=b"not an image").status_code == 415
    assert client.put("/api/setups/chalet/shot/chalet-office-kaid-9", content=b"\xff\xd8\xff").status_code == 404
    r = client.put(url, content=b"\x89PNG\r\n\x1a\n....")
    assert r.status_code == 200 and r.json()["shot"].startswith(url)
    assert client.get(url).content.startswith(b"\x89PNG")
    assert client.get("/api/setups").json()["briefs"][0]["placements"][0]["shot"].startswith(url)
    # Markers: only once there's a picture; bad ones are refused; they come back with the brief.
    r = client.put(url + "/marks", json={"marks": [{"shape": "circle", "x": 50, "y": 50}]})
    assert r.status_code == 200 and r.json()["marks"][0]["x"] == 50
    assert client.put(url + "/marks", json={"marks": [{"shape": "circle", "x": 500, "y": 50}]}).status_code == 422
    assert client.get("/api/setups").json()["briefs"][0]["placements"][0]["marks"] == [
        {"shape": "circle", "x": 50.0, "y": 50.0}]
    assert client.delete(url).json() == {"shot": None}
    assert client.get(url).status_code == 404
    assert client.put(url + "/marks", json={"marks": []}).status_code == 404

    # Grab the game screen: refused with no frame, saved when there is one.
    monkeypatch.setattr(app.watcher, "current_jpeg", lambda: None)
    assert client.post(url + "/grab").status_code == 409
    monkeypatch.setattr(app.watcher, "current_jpeg", lambda: b"\xff\xd8\xffgame")
    assert client.post(url + "/grab").status_code == 200 and client.get(url).content == b"\xff\xd8\xffgame"
    client.delete(url)

    # A saved game frame as the picture; the frame list infers the map from the match's other reads.
    day = tmp_path / "data" / "frames" / "2026-11-06"
    day.mkdir(parents=True, exist_ok=True)
    import json
    for stem, read in (("100000_operator_select", {"screen": "operator_select", "map": "Chalet"}),
                       ("100100_prep_phase", {"screen": "prep_phase", "my_location": "2F Office"}),
                       ("100200_main_menu", {"screen": "main_menu"})):
        (day / f"{stem}.jpg").write_bytes(b"\xff\xd8\xffframe")
        (day / f"{stem}.json").write_text(json.dumps({"read": read, "match_id": "m1"}), encoding="utf-8")
    frames = client.get("/api/frames?map=chalet").json()
    assert [f["frame"] for f in frames] == ["2026-11-06/100100_prep_phase", "2026-11-06/100000_operator_select"]
    assert frames[0]["map"] == "Chalet" and frames[0]["where"] == "2F Office"
    assert client.get("/api/frames?map=oregon").json() == []
    assert client.get(frames[0]["url"]).content == b"\xff\xd8\xffframe"
    assert client.get("/api/frames/2026-11-06/..%2Fsecret.jpg").status_code == 404
    r = client.post(url + "/frame", json={"frame": "2026-11-06/100100_prep_phase"})
    assert r.status_code == 200 and client.get(url).content == b"\xff\xd8\xffframe"
    assert client.post(url + "/frame", json={"frame": "../../x"}).status_code == 404


def test_reload_when_the_yaml_changes_and_source_lines(tmp_path):
    import os
    s = lib(tmp_path)
    assert s.reload_if_changed() is False
    src = s.source("chalet", "chalet-office-kaid")
    path = tmp_path / "setups" / "chalet.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[src["line"] - 1].strip() == "- id: chalet-office-kaid"
    assert src["file"] == "r6coach/knowledge/data/setups/chalet.yaml"
    assert src["url"].endswith(f"/blob/main/r6coach/knowledge/data/setups/chalet.yaml#L{src['line']}")
    assert s.source("chalet", "nope") is None
    # An edit (a git pull) shows without a restart.
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["briefs"] = data["briefs"][:1]
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert s.reload_if_changed() is True
    assert [b["id"] for b in s.for_map("chalet")] == ["chalet-office-team"]
    (tmp_path / "setups" / "bank.yaml").write_text("map: bank\nbriefs: []\n", encoding="utf-8")
    assert s.reload_if_changed() is True and "bank" in s.briefs


def test_library_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from r6coach.app import App
    from r6coach.config import Config
    from r6coach.dashboard.server import build
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = Config()
    cfg.storage.data_dir = str(tmp_path / "data")
    app = App(cfg, replay=tmp_path, enable_ptt=False)
    app.setups = lib(tmp_path)
    client = TestClient(build(app))
    client.put("/api/setups/chalet/shot/chalet-office-hibana-2", content=b"\x89PNG\r\n\x1a\n....")
    d = client.get("/api/library").json()
    assert [m["id"] for m in d["maps"]] == ["chalet"]
    assert any(s["id"] == "master_bedroom_office" for s in d["maps"][0]["sites"])
    hib = next(b for b in d["briefs"] if b["id"] == "chalet-office-hibana")
    assert hib["operators"] == ["Hibana"] and hib["steps"] == 2 and hib["shots"] == 1
    assert hib["kinds"] == ["hibana", "plant"] and hib["side"] == "attack"
    assert d["repo"]["url"].startswith("https://github.com/")
    b = client.get("/api/library/chalet/chalet-office-hibana").json()
    assert b["map_name"] == "Chalet" and b["source"]["line"] > 1
    assert b["placements"][1]["shot"] and b["placements"][0]["shot"] is None
    assert all("fi" in f and f["url"].startswith("/api/blueprint/chalet/") for f in b["floors"])
    assert client.get("/api/library/chalet/nope").status_code == 404
    assert client.get("/api/library/bank/chalet-office-hibana").status_code == 404


def test_layout_endpoint_has_sites_features_and_sides(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from r6coach.app import App
    from r6coach.config import Config
    from r6coach.dashboard.server import build
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = Config()
    cfg.storage.data_dir = str(tmp_path / "data")
    app = App(cfg, replay=tmp_path, enable_ptt=False)
    app.watcher.state.map = "Chalet"
    app.watcher.state.site = "2F Master Bedroom / 2F Office"
    lay = TestClient(build(app)).get("/api/layout").json()
    assert lay["map"] == "chalet" and lay["name"] == "Chalet"
    cur = [s for s in lay["sites"] if s["current"]]
    assert len(cur) == 1 and cur[0]["id"] == "master_bedroom_office"
    assert {r["room"] for r in cur[0]["rooms"]} >= {"Master Bedroom", "Office"}
    assert cur[0]["areas"] and all(0 <= v <= 100 for v in cur[0]["areas"][0]["box"])
    assert isinstance(lay["features"], list) and isinstance(lay["sides"], dict)
