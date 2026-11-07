"""The per-match budget: level changes are announced, and the start-up cache write doesn't count."""

import time
from types import SimpleNamespace

from r6coach.bus import Bus
from r6coach.llm import Claude
from tests.test_coach import setup, wait


def usage(inp=0, out=0, write=0, read=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cache_creation_input_tokens=write,
                           cache_read_input_tokens=read)


def test_level_changes_are_published_once(monkeypatch):
    bus = Bus()
    levels = []
    bus.subscribe("budget.level", lambda ev: levels.append(ev.data["level"]))
    c = Claude(bus)
    c.budget = 1.0
    model = "claude-sonnet-5-5"            # $10 per million output tokens
    c._account(model, usage(out=10), "warm", time.time())        # first call per model
    for _ in range(8):
        c._account(model, usage(out=10_000), "x", time.time())   # $0.10 each
    assert c.budget_level() == "tight" and levels == ["tight"]
    for _ in range(3):
        c._account(model, usage(out=10_000), "x", time.time())
    assert levels == ["tight", "spent"]
    c.reset_match()
    assert c.match_cost == 0.0 and c.budget_level() == "ok"


def test_startup_cache_write_is_not_match_cost():
    c = Claude(Bus())
    model = "claude-opus-5-5"              # $4 per million input, writes at 1.25x
    c._account(model, usage(write=50_000), "first", time.time())
    assert round(c.startup_cost, 4) == 0.25 and c.match_cost == 0.0
    c._account(model, usage(write=10_000), "map change", time.time())
    assert round(c.match_cost, 4) == 0.05      # later writes (a new map's dossier) count as usual
    assert round(c.total_cost, 4) == 0.30


def test_coach_says_once_when_the_budget_closes_a_gate(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    for _ in range(2):
        bus.publish("budget.level", level="tight", match_cost=2.4, budget=3.0)
    bus.publish("budget.level", level="spent", match_cost=3.0, budget=3.0)
    assert wait(lambda: len([s for s in speaker.said if s[0] == "status"]) == 2)
    lines = [t for p, t, _ in speaker.said if p == "status"]
    assert lines == [coach.BUDGET_LINES["tight"], coach.BUDGET_LINES["spent"]]
