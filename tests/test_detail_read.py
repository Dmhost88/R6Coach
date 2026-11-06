"""Claude.read_detail: per-screen wire schemas mapped into ScreenRead, with a fake client."""

from types import SimpleNamespace

import cv2
import numpy as np

from r6coach.bus import Bus
from r6coach.llm import (DETAIL_SYSTEM, VISION_SYSTEM, Claude, WireDetail, WireMapBan, WireOpBan,
                         _detail_effort)
from r6coach.models import Screen


class FakeMessages:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(usage=usage, stop_reason="end_turn", parsed_output=self.parsed)


def make_claude(parsed):
    claude = Claude(Bus())
    msgs = FakeMessages(parsed)
    claude.client = SimpleNamespace(beta=SimpleNamespace(messages=msgs))
    return claude, msgs


def jpeg(width=1600, height=669) -> bytes:
    ok, buf = cv2.imencode(".jpg", np.zeros((height, width, 3), np.uint8))
    assert ok
    return buf.tobytes()


def detail(**over) -> WireDetail:
    base = dict(screen=Screen.loadout, map="Chalet", side="attack", round_number=2, score_us=1,
                score_them=0, my_operator="Thermite", teammate_operators=[], banned_operators=["Mira", "Dokkaebi"],
                site="", site_options=["2F Master Bedroom / 2F Office"], spawn="", confidence=0.9)
    base.update(over)
    return WireDetail(**base)


def test_loadout_maps_into_screen_read_and_uses_detail_schema():
    claude, msgs = make_claude(detail())
    read = claude.read_detail(jpeg(), Screen.loadout, "claude-haiku-5-5", "low", player_name="dmhost88")
    call = msgs.calls[0]
    assert call["output_format"] is WireDetail
    assert call["system"][0]["text"] == DETAIL_SYSTEM != VISION_SYSTEM
    assert call["output_config"] == {"effort": "low"}
    assert "thinking" not in call
    assert "dmhost88" in call["messages"][0]["content"][1]["text"]
    assert read.screen == Screen.loadout and read.map == "Chalet" and read.side == "attack"
    assert read.my_operator == "Thermite" and read.round_number == 2
    assert (read.score_us, read.score_them) == (1, 0)
    assert read.banned_operators == ["Mira", "Dokkaebi"]
    assert read.site is None and read.spawn is None          # "" -> None
    assert read.killfeed == [] and read.timer_seconds is None  # not in the schema: defaults
    assert claude.calls == 1 and claude.total_cost > 0


def test_own_operator_removed_from_teammates():
    claude, _ = make_claude(detail(screen=Screen.operator_select, my_operator="Jäger",
                                   teammate_operators=["Mute", "jager", "?", "", "Mute", "Bandit"]))
    read = claude.read_detail(jpeg(), Screen.operator_select, "claude-haiku-5-5", "low")
    assert read.my_operator == "Jäger"
    assert read.teammate_operators == ["Mute", "Bandit"]


def test_teammates_kept_when_own_operator_unknown():
    claude, _ = make_claude(detail(my_operator="", teammate_operators=["Ash", "Thermite"]))
    read = claude.read_detail(jpeg(), Screen.loadout, "claude-haiku-5-5", "low")
    assert read.my_operator is None and read.teammate_operators == ["Ash", "Thermite"]


def test_unknown_numbers_and_side_become_none():
    claude, _ = make_claude(detail(side="", round_number=-1, score_us=-1, score_them=-1))
    read = claude.read_detail(jpeg(), Screen.loadout, "claude-haiku-5-5", "low")
    assert read.side is None and read.round_number is None and read.score_us is None


def test_random_spawn_is_unknown():
    claude, _ = make_claude(detail(screen=Screen.operator_select, spawn="RANDOM"))
    assert claude.read_detail(jpeg(), Screen.operator_select, "claude-sonnet-5-5", "none").spawn is None


def test_map_ban_schema():
    wire = WireMapBan(screen=Screen.map_ban, map_ban_options=["Villa", " Chalet ", ""], map="", side="attack",
                      confidence=0.8)
    claude, msgs = make_claude(wire)
    read = claude.read_detail(jpeg(), Screen.map_ban, "claude-sonnet-5-5", "low")
    assert msgs.calls[0]["output_format"] is WireMapBan
    assert read.map_ban_options == ["Villa", "Chalet"] and read.map is None and read.side == "attack"
    assert read.my_operator is None and read.teammate_operators == []


def test_operator_ban_schema_and_screen_correction():
    # The local detector said operator_ban; the model saw a loadout-like screen and says so.
    wire = WireOpBan(screen=Screen.operator_select, map="Chalet", side="defense", round_number=3, score_us=1,
                     score_them=1, banned_operators=["Castle", "castle"], site_options=[], confidence=0.7)
    claude, msgs = make_claude(wire)
    read = claude.read_detail(jpeg(), Screen.operator_ban, "claude-sonnet-5-5", "low")
    assert msgs.calls[0]["output_format"] is WireOpBan
    assert read.screen == Screen.operator_select
    assert read.banned_operators == ["Castle"] and read.round_number == 3


def test_effort_none_turns_thinking_off_where_allowed():
    assert _detail_effort("claude-haiku-5-5", "none") == ("low", {"type": "disabled"})
    assert _detail_effort("claude-sonnet-5-5", "none") == ("low", {"type": "between_tools"})
    assert _detail_effort("claude-opus-5-5", "none") == ("low", None)
    assert _detail_effort("claude-opus-5-5", "medium") == ("medium", None)
    claude, msgs = make_claude(detail())
    claude.read_detail(jpeg(), Screen.loadout, "claude-haiku-5-5", "none")
    call = msgs.calls[0]
    assert call["thinking"] == {"type": "disabled"} and call["output_config"] == {"effort": "low"}
    assert "fallbacks" not in call                         # Haiku has no server-side fallback


def test_max_width_downscales_image():
    claude, msgs = make_claude(detail())
    claude.read_detail(jpeg(), Screen.loadout, "claude-haiku-5-5", "low", max_width=1280)
    import base64
    data = base64.standard_b64decode(msgs.calls[0]["messages"][0]["content"][0]["source"]["data"])
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[1] == 1280


def test_api_failure_returns_none():
    claude, msgs = make_claude(None)

    def boom(**kwargs):
        raise TypeError("Could not resolve authentication method")
    msgs.parse = boom
    assert claude.read_detail(jpeg(), Screen.loadout, "claude-haiku-5-5", "low") is None
