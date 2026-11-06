import time

from r6coach.coach import Coach
from r6coach.config import Config
from r6coach.llm import CoachLines
from r6coach.models import GameState, Screen
from r6coach.profile import Profile


class FakeClaude:
    def __init__(self):
        self.prompts = []

    def coach(self, model, effort, system, prompt, purpose, image_jpeg=None):
        self.prompts.append((purpose, prompt, image_jpeg is not None))
        return CoachLines(lines=[f"line for {purpose}"], recommended_operator="Mute"
                          if purpose == "coach.operator_select" else None)


class FakeSpeaker:
    def __init__(self):
        self.said = []

    def say(self, text, priority=2, ttl=20.0, phase=""):
        self.said.append((phase, text, priority))

    def clear(self, phase=None):
        pass


def setup(bus, kb, tmp_path):
    state = {"s": GameState(match_id="m1")}
    claude, speaker = FakeClaude(), FakeSpeaker()
    coach = Coach(Config(), bus, claude, kb, speaker, Profile(tmp_path / "p.sqlite"),
                  current_state=lambda: state["s"], current_jpeg=lambda: b"jpeg")
    return coach, claude, speaker, state


def push(bus, state, **fields):
    state["s"] = state["s"].model_copy(update=fields)
    bus.publish("state.updated", state=state["s"].model_dump(mode="json"))


def wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_pick_advice_once_per_round_and_waits_for_site(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    push(bus, state, screen=Screen.operator_select, map="Bank", side="defense")
    time.sleep(0.1)
    assert claude.prompts == []  # waited one read for the site
    push(bus, state, site="B Lockers / B CCTV Room")
    assert wait(lambda: len(claude.prompts) == 1)
    purpose, prompt, has_image = claude.prompts[0]
    assert purpose == "coach.operator_select" and has_image
    assert "Hold basement." in prompt          # map knowledge included
    push(bus, state, teammate_operators=["Smoke"])
    time.sleep(0.1)
    assert len(claude.prompts) == 1             # no repeat in the same round
    assert wait(lambda: any(p == "operator_select" for p, _, _ in speaker.said))


def test_repick_when_teammate_takes_recommendation(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    push(bus, state, screen=Screen.operator_select, map="Bank", side="attack")
    assert wait(lambda: coach._recommended == "Mute")
    push(bus, state, teammate_operators=["Mute"])
    assert wait(lambda: len(claude.prompts) == 2)
    assert "just took" in claude.prompts[1][1]


def test_prep_advice_text_only_and_dropped_when_stale(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    push(bus, state, screen=Screen.prep_phase, map="Bank", side="attack", my_operator="Thermite",
         site="B Lockers / B CCTV Room")
    assert wait(lambda: len(claude.prompts) == 1)
    assert claude.prompts[0][2] is False      # no screenshot uploaded mid-round
    assert wait(lambda: any(p == "prep_phase" for p, _, _ in speaker.said))

    # Advice that arrives after the screen moved on is not spoken.
    speaker.said.clear()
    state["s"] = state["s"].model_copy(update={"screen": Screen.action_phase})
    coach._run("prep_phase", lambda s: CoachLines(lines=["late"]), state["s"])
    assert speaker.said == []


def test_new_round_counter_without_round_numbers(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    for _ in range(2):
        push(bus, state, screen=Screen.operator_select, map="Bank", side="attack")
        push(bus, state, screen=Screen.prep_phase)
        push(bus, state, screen=Screen.action_phase)
        push(bus, state, screen=Screen.round_end)
    assert wait(lambda: len([p for p in claude.prompts if p[0] == "coach.operator_select"]) == 2)


def test_operator_ban_advice_every_round(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    for _ in range(2):
        push(bus, state, screen=Screen.operator_ban, map="Bank", side="attack")
        push(bus, state, screen=Screen.operator_select)
        push(bus, state, screen=Screen.prep_phase, site="B Lockers / B CCTV Room")
        push(bus, state, screen=Screen.action_phase)
        push(bus, state, screen=Screen.round_end)
    assert wait(lambda: len([p for p in claude.prompts if p[0] == "coach.operator_ban"]) == 2)
    # op select after the ban phase is the same round, not a new one
    assert len([p for p in claude.prompts if p[0] == "coach.operator_select"]) == 2


def test_attack_prep_waits_for_bombs_found(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    push(bus, state, screen=Screen.operator_select, map="Bank", side="attack")
    assert wait(lambda: len(claude.prompts) == 1)
    push(bus, state, screen=Screen.prep_phase)
    time.sleep(0.1)
    assert len(claude.prompts) == 1                 # no site yet: wait
    push(bus, state, site="B Lockers / B CCTV Room")  # "BOMBS FOUND"
    assert wait(lambda: len(claude.prompts) == 2)
    assert "B Lockers / B CCTV Room" in claude.prompts[1][1]


def test_question_uses_no_image_mid_round(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    state["s"] = state["s"].model_copy(update={"screen": Screen.action_phase, "map": "Bank"})
    bus.publish("ptt.question", text="where do I plant?")
    assert wait(lambda: claude.prompts and claude.prompts[-1][0] == "coach.question")
    assert claude.prompts[-1][2] is False
    assert wait(lambda: any(pr == 0 for _, _, pr in speaker.said))
