"""Offline checks for the real-match advice eval (tools/eval_real.py). No API calls."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import eval_real  # noqa: E402


@pytest.fixture(scope="module")
def data():
    return eval_real.load_scenarios()


def test_scenarios_file_loads_and_validates(data):
    assert eval_real.validate(data) == []
    names = [s["name"] for s in data["scenarios"]]
    assert 10 <= len(names) <= 14
    rounds = {s["round"] for s in data["scenarios"]}
    assert rounds == {1, 2, 3, 4, 5}
    sides = {next(r for r in data["rounds"] if r["n"] == s["round"])["side"] for s in data["scenarios"]}
    assert sides == {"attack", "defense"}
    assert {s["method"] for s in data["scenarios"]} == set(eval_real.METHODS)
    # Frame names must stay strings (YAML 1.1 reads 005548_0023 as an int).
    assert all(isinstance(f, str) for f in eval_real.referenced_frames(data))


def test_every_scenario_builds_state_and_history(data):
    for sc in data["scenarios"]:
        s = eval_real.game_state(data, sc)
        assert s.map == "Chalet"
        hist = eval_real.history_rounds(data, sc)
        expected = sc["round"] if sc["method"] == "between_rounds" else sc["round"] - 1
        assert [r["n"] for r in hist] == list(range(1, expected + 1))
        assert "GROUND TRUTH" in eval_real.truth_text(data, sc)
        if sc["method"] == "death":
            assert s.player_alive is False and s.my_location


def test_history_replays_into_match_memory(data):
    from r6coach.bus import Bus
    from r6coach.memory import MatchMemory
    from r6coach.models import GameState

    bus = Bus()
    box = {"s": GameState()}
    mem = MatchMemory(bus, lambda: box["s"], data["match"]["player"])
    eval_real.replay_history(bus, box, data["rounds"][:3], data["match"]["player"])
    assert [r.won for r in mem.rounds] == [True, True, False]
    assert mem.killers_of_me["Enemy_E"] == 2
    assert mem.rounds[1].my_kills == 1 and mem.rounds[0].planted
    text = mem.text()
    assert "B Snowmobile Garage" in text and "Maestro x3" in text


def test_validation_catches_mistakes(data):
    bad = copy.deepcopy(data)
    bad["scenarios"][0]["method"] = "dance"
    bad["scenarios"][1]["round"] = 9
    bad["scenarios"][2]["state"]["alive_us"] = "lots"
    bad["scenarios"].append(copy.deepcopy(bad["scenarios"][3]))
    problems = "\n".join(eval_real.validate(bad))
    assert "method must be" in problems
    assert "round 9 not in rounds" in problems
    assert "state invalid" in problems
    assert "duplicate name" in problems


def test_every_referenced_frame_exists(data):
    folder = eval_real.CALIBRATION / data["match"]["session"] / "auto"
    if not folder.exists():
        pytest.skip("calibration frames not present")
    missing = [f for f in eval_real.referenced_frames(data) if not eval_real.frame_path(data, f).exists()]
    assert missing == []


def _result(label, grades):
    results = [{"scenario": name, "grade": None if g is None else dict(
        zip(eval_real.KEYS, g), biggest_problem="x", wrong_locations=[])} for name, g in grades.items()]
    return {"label": label, "means": eval_real.means_of(results), "results": results}


def test_compare_delta_on_fake_result_files(tmp_path):
    old = _result("base", {"a": (3, 3, 3, 3, 3, 3, 3), "b": (2, 2, 2, 2, 2, 2, 2), "gone": (1,) * 7,
                           "ungraded": None})
    new = _result("v2", {"a": (4, 3, 2, 3, 3, 3, 5), "b": (2, 2, 2, 2, 2, 2, 1), "ungraded": (3,) * 7})
    po, pn = tmp_path / "old.json", tmp_path / "new.json"
    po.write_text(json.dumps(old), encoding="utf-8")
    pn.write_text(json.dumps(new), encoding="utf-8")

    d = eval_real.delta(eval_real.load_result(po), eval_real.load_result(pn))
    assert d["scenarios"]["a"] == {"specificity": 1, "accuracy": 0, "adaptation": -1, "actionability": 0,
                                   "tts_fit": 0, "fair_play": 0, "grounded": 2, "total": 2}
    assert d["scenarios"]["b"]["grounded"] == -1 and d["scenarios"]["b"]["total"] == -1
    assert d["scenarios"]["ungraded"] is None
    assert d["missing_in_new"] == ["gone"]
    # old means over a, b, gone: specificity (3+2+1)/3 = 2.0; new over a, b, ungraded: (4+2+3)/3 = 3.0
    assert d["criteria"]["specificity"] == {"old": 2.0, "new": 3.0, "delta": 1.0}
    assert d["criteria"]["grounded"]["delta"] == pytest.approx(3.0 - 2.0)

    table = eval_real.format_delta(d, "base", "v2")
    assert "DELTA v2 vs base" in table
    assert "+2" in table and "(no comparable grade)" in table and "not in new run: gone" in table


def test_compare_handles_old_files_without_grounded():
    """The synthetic eval's files have no `grounded`: compare must not crash on them."""
    old = _result("synthetic", {"a": (3, 3, 3, 3, 3, 3, 3)})
    for r in old["results"]:
        r["grade"].pop("grounded")
    old["means"].pop("grounded")
    new = _result("real", {"a": (4, 4, 4, 4, 4, 4, 4)})
    d = eval_real.delta(old, new)
    assert d["criteria"]["grounded"]["delta"] is None
    assert "grounded" not in d["scenarios"]["a"]
    eval_real.format_delta(d)


def test_compare_files_cli(tmp_path, capsys):
    old = _result("base", {"a": (3,) * 7})
    new = _result("v2", {"a": (4,) * 7})
    po, pn = tmp_path / "o.json", tmp_path / "n.json"
    po.write_text(json.dumps(old), encoding="utf-8")
    pn.write_text(json.dumps(new), encoding="utf-8")
    eval_real.main(["--compare-files", str(po), str(pn)])
    out = capsys.readouterr().out
    assert "DELTA v2 vs base" in out and "+7" in out
