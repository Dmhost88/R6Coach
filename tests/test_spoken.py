import pytest

from r6coach.spoken import clean_line, self_talk

# Lines the coach spoke on 2026-11-06 while reasoning aloud.
THINKING = [
    "The screen shows round 3 at 1-1, but the match state says round 1, so plan for round 3.",
    "Ban Jager: you're defending, so you can't ban defenders, so ban Nomad? No. Ban Hibana.",
    "Hmm, they ran Mira last round.",
    "Let me check the site first.",
    "Wait, the site is basement.",
    "Hold Office. No. Hold Library instead.",
]

# Real calls, including canned lines, that must still play.
CALLS = [
    "Ban Thatcher: their Thermite opened Office twice.",
    "From EXT Campfire, go through EXT Cliffside, up the Library stairs into 2F Library.",
    "Defuser is down. Go deny it, don't wait.",
    "You're down a player. Group up and play together.",
    "Want the Office wall or the Library wall?",
    "No ban needed on Mute: nobody plays him here.",
    "Coach muted.",
]


@pytest.mark.parametrize("line", THINKING)
def test_reasoning_aloud_is_caught(line):
    assert self_talk(line)


@pytest.mark.parametrize("line", CALLS)
def test_real_calls_pass(line):
    assert self_talk(line) is None


def test_the_call_is_kept_and_the_thinking_removed():
    assert clean_line(THINKING[1])[0] == "Ban Hibana."
    assert clean_line("Take Super 90 or ITA12L? Take the ITA12L with the Vector as backup.")[0] == \
        "Take the ITA12L with the Vector as backup."
    assert clean_line(THINKING[0])[0] is None            # nothing left to say


def test_speaker_cleans_self_talk(tmp_path):
    from r6coach.bus import Bus
    from r6coach.config import VoiceCfg
    from r6coach.voice.tts import Speaker
    sp = Speaker(VoiceCfg(), Bus(), tmp_path)
    sp.say(THINKING[0], priority=1)
    sp.say(THINKING[1], priority=1)
    sp.say(CALLS[0], priority=1)
    assert sorted(i.text for i in sp._heap) == sorted(["Ban Hibana.", CALLS[0]])
