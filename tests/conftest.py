import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r6coach.bus import Bus  # noqa: E402
from r6coach.knowledge import Knowledge  # noqa: E402

MAP = {
    "id": "bank",
    "name": "Bank",
    "aliases": ["Bank"],
    "floors": ["basement", "1F", "2F"],
    "attacker_spawns": [{"name": "Boulevard", "notes": ""}],
    "sites": [
        {
            "id": "b_lockers_cctv",
            "name": "B Lockers / B CCTV Room",
            "aliases": [],
            "floor": "basement",
            "defender": {"reinforce": ["Lockers walls"], "plan_summary": "Hold basement."},
            "attacker": {"entry_points": ["Garage"], "plan_summary": "Take garage."},
            "common_mistakes": [],
            "uncertain": [],
        },
        {
            "id": "ceo_lounge",
            "name": "2F CEO Office / 2F Executive Lounge",
            "aliases": [],
            "floor": "2F",
            "defender": {"plan_summary": "Hold top floor."},
            "attacker": {"plan_summary": "Roof entry."},
            "common_mistakes": [],
            "uncertain": [],
        },
    ],
    "general": {"attack": ["Watch spawn peeks."], "defense": ["Don't overpeek."]},
}

OPS = {"operators": [
    {"name": "Mute", "side": "defense", "roles": ["anti_gadget"], "unique_gadget": "jammers", "comp_notes": "x"},
    {"name": "Thermite", "side": "attack", "roles": ["hard_breacher"], "unique_gadget": "exothermic charge", "comp_notes": "y"},
    {"name": "Jäger", "side": "defense", "roles": ["anti_gadget"], "unique_gadget": "ADS", "comp_notes": "z"},
]}


@pytest.fixture
def kb(tmp_path):
    data = tmp_path / "kbdata"
    (data / "maps").mkdir(parents=True)
    with open(data / "maps" / "bank.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(MAP, f)
    with open(data / "operators.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(OPS, f, allow_unicode=True)
    with open(data / "meta.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump({"ranked_map_pool": ["bank"]}, f)
    return Knowledge(notes_dir=tmp_path / "notes", data_dir=data)


@pytest.fixture
def bus():
    return Bus()
