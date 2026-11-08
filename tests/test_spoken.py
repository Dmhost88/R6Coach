import pytest

import time

from r6coach.spoken import cap_words, clean_line, self_talk, too_similar

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


# ---------------------------------------------------------------- length and repeats

def test_cap_words_keeps_whole_sentences_then_clauses():
    two = "Hold 2F Office from the desk. Watch the Library doorway for the Ash rush."
    assert cap_words(two, 7) == "Hold 2F Office from the desk."
    long = "Reinforce the Laundry exterior wall first, then help cover the hatch above Kitchen from Bar."
    assert cap_words(long, 8) == "Reinforce the Laundry exterior wall first."
    blob = "Go go go push push push through the wall now right now with everyone together"
    assert len(cap_words(blob, 6).split()) == 6
    assert cap_words("Short line.", 14) == "Short line."


def test_too_similar_catches_rephrased_instructions():
    # Real near-repeats from the 2026-11-06 matches (the review's examples).
    assert too_similar("You have the numbers. Trade, don't take duels alone.",
                       "You have the numbers. Trade, don't take duels alone.")
    assert too_similar("Place your Black Mirror on the 2F Office wall facing Library.",
                       "Put the Black Mirror on the Office wall facing Library.")
    assert not too_similar("Hold 2F Office from the desk.", "Rotate to Library through Main Hallway.")


def test_speaker_cuts_long_lines_and_drops_recent_repeats(tmp_path):
    from r6coach.bus import Bus
    from r6coach.config import VoiceCfg
    from r6coach.voice.tts import Speaker
    sp = Speaker(VoiceCfg(), Bus(), tmp_path)
    words = " ".join(f"word{i}" for i in range(40)) + "."
    sp.say(words, priority=1, phase="prep_phase")
    assert len(sp._heap[0].text.split()) == Speaker.WORD_CAP
    sp.say(words, priority=1, phase="question")                 # answers are never cut
    assert any(len(i.text.split()) == 40 for i in sp._heap)
    sp._spoken.append((time.time(), "Place your Black Mirror on the 2F Office wall facing Library."))
    sp.say("Put the Black Mirror on the Office wall facing Library.", priority=1, phase="action_phase")
    assert not any("Black Mirror" in i.text for i in sp._heap)
    sp.say("Put the Black Mirror on the Office wall facing Library.", priority=0, phase="action_phase")
    assert any("Black Mirror" in i.text for i in sp._heap)      # urgent lines always play
    sp._spoken = [(time.time() - 120, "Hold the desk in Office.")]
    sp.say("Hold the desk in Office.", priority=1, phase="action_phase")
    assert any(i.text == "Hold the desk in Office." for i in sp._heap)   # said long ago: fine
