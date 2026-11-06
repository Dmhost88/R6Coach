"""Streaming advice: lines are spoken as they arrive, once, and only while still relevant."""

import time

from r6coach.coach import Coach
from r6coach.config import Config
from r6coach.llm import CoachLines, _take_stream_line
from r6coach.models import GameState, Screen
from r6coach.profile import Profile


def test_stream_line_parser():
    lines, meta, heard = [], {}, []
    for raw in ["> Ban Thatcher.", "", "not a line", "> Kaid claws stay live.",
                'META: {"recommended_operator": "", "recommended_bans": ["Thatcher"]}']:
        _take_stream_line(raw, lines, meta, heard.append)
    assert lines == heard == ["Ban Thatcher.", "Kaid claws stay live."]
    assert meta["recommended_bans"] == ["Thatcher"]
    _take_stream_line("META: {broken", lines, meta, heard.append)   # tolerated


class StreamingClaude:
    def __init__(self, state_ref, flip_after=None):
        self.state_ref = state_ref
        self.flip_after = flip_after
        self.calls = []

    def coach(self, *a, **k):          # non-streamed path (prep made ahead of time)
        self.calls.append(("coach", k.get("purpose")))
        return CoachLines(lines=["prepared"], recommended_operator="", recommended_bans=[])

    def coach_stream(self, model, effort, system, prompt, purpose, image_jpeg=None, on_line=None):
        self.calls.append(("stream", purpose))
        out = []
        for i, text in enumerate(["line one", "line two", "line three"]):
            if self.flip_after is not None and i == self.flip_after:
                self.state_ref["s"] = self.state_ref["s"].model_copy(update={"screen": Screen.action_phase})
            out.append(text)
            on_line(text)
        return CoachLines(lines=out, recommended_operator="Mute", recommended_bans=["Thatcher"])


class Spk:
    def __init__(self):
        self.said = []

    def say(self, text, priority=2, ttl=20.0, phase=""):
        self.said.append((text, priority, phase))

    def clear(self, *a):
        pass


def make(tmp_path, kb, bus, flip_after=None):
    state = {"s": GameState(match_id="m", screen=Screen.operator_ban, map="Bank", side="defense")}
    claude = StreamingClaude(state, flip_after)
    spk = Spk()
    coach = Coach(Config(), bus, claude, kb, spk, Profile(tmp_path / "p.sqlite"),
                  current_state=lambda: state["s"], current_jpeg=lambda: None)
    return coach, claude, spk, state


def test_streamed_lines_spoken_once(tmp_path, kb, bus):
    coach, claude, spk, state = make(tmp_path, kb, bus)
    coach._run("operator_ban", coach._advise_op_ban, state["s"])
    assert [t for t, _, _ in spk.said] == ["line one", "line two", "line three"]   # not repeated at the end
    assert ("stream", "coach.operator_ban") in claude.calls


def test_streamed_lines_stop_when_moment_passes(tmp_path, kb, bus):
    coach, claude, spk, state = make(tmp_path, kb, bus, flip_after=1)
    coach._run("operator_ban", coach._advise_op_ban, state["s"])
    assert [t for t, _, _ in spk.said] == ["line one"]       # ban screen ended after line 1


def test_question_first_line_interrupts(tmp_path, kb, bus):
    coach, claude, spk, state = make(tmp_path, kb, bus)
    coach._answer("what now?")
    assert [p for _, p, _ in spk.said] == [0, 1, 1]


def test_prep_made_ahead_is_not_streamed(tmp_path, kb, bus):
    coach, claude, spk, state = make(tmp_path, kb, bus)
    coach._advise_prep(state["s"], True, False)
    assert claude.calls == [("coach", "coach.prep_phase")] and spk.said == []
