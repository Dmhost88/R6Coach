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
        self.models = getattr(self, "models", []) + [(purpose, model, effort)]
        return CoachLines(lines=[f"line for {purpose}"], recommended_bans=[],
                          recommended_operator="Mute" if purpose == "coach.operator_select" else "")


class FakeSpeaker:
    def __init__(self):
        self.said = []

    def say(self, text, priority=2, ttl=20.0, phase=""):
        self.said.append((phase, text, priority))

    def clear(self, phase=None):
        pass

    def cancel(self, *phases):
        self.cancelled = getattr(self, "cancelled", []) + list(phases)


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
    picks = lambda: [p for p in claude.prompts if p[0] == "coach.operator_select"]  # noqa: E731
    assert wait(lambda: len(picks()) == 1)
    purpose, prompt, has_image = picks()[0]
    assert has_image
    # Map knowledge now lives in the cached system blocks (brief, operators, map dossier).
    blocks = coach._system("Bank")
    assert len(blocks) == 3 and "Hold basement." in blocks[2] and "Thermite" in blocks[1]
    assert "Hold basement." not in prompt      # not repeated in the per-call prompt
    assert wait(lambda: coach._recommended == "Mute")
    push(bus, state, teammate_operators=["Smoke"])
    # Defense knows the site, so the prep plan is prepared in advance during the pick.
    assert wait(lambda: any(p[0] == "coach.prep_phase" for p in claude.prompts))
    assert len(picks()) == 1                    # no repeat in the same round
    assert wait(lambda: any(p == "operator_select" for p, _, _ in speaker.said))


def test_repick_when_teammate_takes_recommendation(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    push(bus, state, screen=Screen.operator_select, map="Bank", side="attack")
    assert wait(lambda: coach._recommended == "Mute")
    push(bus, state, teammate_operators=["Mute"])
    assert wait(lambda: len(claude.prompts) == 2)
    assert "just took" in claude.prompts[1][1]


def test_locking_another_operator_cancels_and_readvises(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    push(bus, state, screen=Screen.operator_select, map="Bank", side="attack")
    assert wait(lambda: coach._recommended == "Mute")
    push(bus, state, my_operator="Thermite", teammate_operators=["Buck"])
    assert wait(lambda: any("LOCKED Thermite" in p for _, p, _ in claude.prompts))
    locked = [p for _, p, _ in claude.prompts if "LOCKED Thermite" in p][0]
    assert "Mute" in locked and "void" in locked
    assert "operator_select" in speaker.cancelled and "prep_phase" in speaker.cancelled
    n = len(claude.prompts)
    push(bus, state, timer_seconds=20)                      # more state updates: no second pivot
    time.sleep(0.2)
    assert len(claude.prompts) == n


def test_lock_found_in_prep_replaces_the_prep_plan(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    push(bus, state, screen=Screen.operator_select, map="Bank", side="attack")
    assert wait(lambda: coach._recommended == "Mute")
    push(bus, state, screen=Screen.prep_phase, site="B Lockers / B CCTV Room", my_operator="Thermite")
    assert wait(lambda: any(p[0] == "coach.prep_phase" and "Thermite" in p[1] for p in claude.prompts))
    assert not any("LOCKED Thermite (the coach" in p[1] for p in claude.prompts if p[0] == "coach.operator_select")


def test_only_pick_advice_sets_the_recommendation(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    push(bus, state, screen=Screen.operator_ban, map="Bank", side="attack")
    coach._deliver("operator_ban", CoachLines(lines=["Ban Mira."], recommended_operator="Ash", recommended_bans=["Mira"]))
    assert coach._recommended is None


def test_stale_advice_is_dropped(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    push(bus, state, screen=Screen.operator_select, map="Bank", side="attack")
    g = coach._gen["operator_select"]
    coach._bump("operator_select")
    before = len(speaker.said)
    coach._deliver("operator_select", CoachLines(lines=["old"], recommended_operator="Ash",
                                                 recommended_bans=[]), gen=g)
    assert len(speaker.said) == before and coach._recommended != "Ash"


def test_no_repick_when_the_player_locked_the_recommendation(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    push(bus, state, screen=Screen.operator_select, map="Bank", side="attack", banned_operators=["Ace"])
    assert wait(lambda: coach._recommended == "Mute")
    # The player took it; their own card shows up in the team's icons.
    push(bus, state, my_operator="Mute", teammate_operators=["Buck", "Mute"])
    time.sleep(0.2)
    assert len(claude.prompts) == 1
    prompt = claude.prompts[0][1]
    assert "Ace" in prompt and "Banned so far this match (unavailable" in prompt


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
    coach._run("prep_phase", lambda s: CoachLines(lines=["late"], recommended_operator="",
                                                  recommended_bans=[]), state["s"])
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
    assert wait(lambda: len([p for p in claude.prompts if p[0] == "coach.operator_select"]) == 2)
    time.sleep(0.2)
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


def test_attack_prep_ignores_a_site_left_from_the_last_round(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    # Last round's site is still in the state on the pick screen (seen live: R3 planned for R2's site).
    push(bus, state, screen=Screen.operator_select, map="Bank", side="attack", site="B Lockers / B CCTV Room")
    assert wait(lambda: len(claude.prompts) == 1)
    push(bus, state, screen=Screen.prep_phase)
    time.sleep(0.1)
    assert len(claude.prompts) == 1                 # the old site doesn't count: wait for BOMBS FOUND
    assert "operator_select" in speaker.cancelled   # leftover pick lines don't hold up the plan
    push(bus, state, site="2F CEO Office / 2F Executive Lounge")
    assert wait(lambda: len(claude.prompts) == 2)
    assert "2F CEO Office" in claude.prompts[1][1]


def test_prep_plan_after_a_relock_uses_the_fast_model(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    cfg = coach.cfg.coach
    push(bus, state, screen=Screen.operator_select, map="Bank", side="defense", site="B Lockers / B CCTV Room")
    assert wait(lambda: coach._recommended == "Mute")
    push(bus, state, teammate_operators=["Smoke"])
    assert wait(lambda: any(p == "coach.prep_phase" and m == cfg.model for p, m, _ in claude.models))
    push(bus, state, my_operator="Kaid")             # the player locked something else
    assert wait(lambda: any(p == "coach.prep_phase" and m == cfg.fast_model and e == cfg.effort
                            for p, m, e in claude.models))
    prep = [pr for pu, pr, _ in claude.prompts if pu == "coach.prep_phase"]
    assert "Kaid" in prep[-1]


def test_budget_drops_extras_first(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    claude.budget_level = lambda: "spent"
    push(bus, state, screen=Screen.prep_phase, map="Bank", side="attack", site="B Lockers / B CCTV Room")
    time.sleep(0.2)
    assert not any(p[0] == "coach.prep_phase" for p in claude.prompts)    # prep dropped when spent
    push(bus, state, screen=Screen.operator_ban)
    assert wait(lambda: any(p[0] == "coach.operator_ban" for p in claude.prompts))   # bans still run


def test_question_uses_no_image_mid_round(bus, kb, tmp_path):
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    state["s"] = state["s"].model_copy(update={"screen": Screen.action_phase, "map": "Bank"})
    bus.publish("ptt.question", text="where do I plant?")
    assert wait(lambda: claude.prompts and claude.prompts[-1][0] == "coach.question")
    assert claude.prompts[-1][2] is False
    assert wait(lambda: any(pr == 0 for _, _, pr in speaker.said))


def test_debriefs_are_one_line_on_the_fast_model(bus, kb, tmp_path):
    """Live-moment review, 2026-11-07: 3-line debriefs on the deep model played over the next pick
    screen or were cancelled before they landed, and death debriefs opened with praise."""
    coach, claude, speaker, state = setup(bus, kb, tmp_path)
    cfg = coach.cfg.coach
    s = state["s"].model_copy(update={"map": "Bank", "side": "defense", "my_operator": "Mute"})
    coach._advise_debrief(s, False)
    coach._advise_death(s, "Enemy_A")
    for purpose in ("coach.between_rounds", "coach.death"):
        assert (purpose, cfg.fast_model, cfg.effort) in claude.models
        prompt = next(pr for pu, pr, _ in claude.prompts if pu == purpose)
        assert "Reply with 1 spoken lines" in prompt and "ONE line" in prompt
        assert "what was good" not in prompt and "did well" not in prompt
