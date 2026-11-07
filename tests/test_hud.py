"""HUD reader against real, hand-labelled frames. Skips when the capture folders are absent."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from r6coach.hud import HudReader, parse_banners, parse_timer, pretty_place

from .hud_cases import CASES

ROOT = Path(__file__).resolve().parent.parent / "data" / "calibration"
# Other players' names are placeholders in the repository (Mate_A, Enemy_A, ...). The real names
# the frames show are in a local file next to the frames: {"Mate_A": "<name on screen>", ...}.
NAMES_FILE = ROOT / "names.json"


def real_name(name: str) -> str:
    if NAMES_FILE.exists():
        import json
        return json.loads(NAMES_FILE.read_text(encoding="utf-8")).get(name, name)
    return name


def test_parsers():
    assert parse_timer("2:23") == 143
    assert parse_timer("0.43") == 43
    assert parse_timer("abc") is None
    ev, ex = parse_banners(["5VS2"])
    assert ev == ["vs"] and ex == {"alive_us": 5, "alive_them": 2}
    ev, ex = parse_banners(["BOMBS FOUND", "◆2F Master Bedroom", "◆2F Car Room"])
    assert ev == ["bombs_found"] and ex["site"] == "2F Master Bedroom / 2F Car Room"
    ev, ex = parse_banners(["BOMBSFOUND", "2FMasterBedroom", "2FCarRoom"])
    assert ex["site"] == "2F Master Bedroom / 2F Car Room"
    assert parse_banners(["WON ROUND2"])[0] == ["won_round"]
    assert parse_banners(["3VS 2"])[1] == {"alive_us": 3, "alive_them": 2}
    assert pretty_place("GYEXTFrontYard Patio") == "EXT Front Yard Patio"
    assert pretty_place("BWine Cellar") == "B Wine Cellar"
    from r6coach.hud import ROOM_RE
    assert ROOM_RE.match("Biliards Room") and ROOM_RE.match("Upper Hallway")
    assert not ROOM_RE.match("MAPSELECTED.") and not ROOM_RE.match("one tice.")
    assert parse_banners(["PREPARATIONPHASE", "FORTIFYBOMBSITES"])[1]["side"] == "defense"
    assert parse_banners(["ACTION PHASE", "PLANT THE DEFUSER AT ANY BOMB SITE"])[1]["side"] == "attack"


@pytest.fixture(scope="module")
def reader():
    pytest.importorskip("rapidocr_onnxruntime")
    return HudReader()


@pytest.mark.parametrize("session,frame,expected", CASES, ids=[c[1] for c in CASES])
def test_real_frames(reader, session, frame, expected):
    path = ROOT / session / "auto" / f"{frame}.jpg"
    if not path.exists():
        pytest.skip("capture frames not present")
    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    r = reader.read(img, force=True)
    for key, want in expected.items():
        if key == "event":
            assert want in r.events, f"events={r.events} banners={r.banners}"
        elif key == "kill":
            got = [(k.killer, k.killer_team, k.victim, k.victim_team) for k in r.kills]
            want = (real_name(want[0]), want[1], real_name(want[2]), want[3])
            assert want in got, f"kills={got}"
        elif key.endswith("~"):
            val = getattr(r, key[:-1])
            assert val and want.lower() in val.lower(), f"{key[:-1]}={val!r}"
        else:
            assert getattr(r, key) == want, f"{key}={getattr(r, key)!r}"
