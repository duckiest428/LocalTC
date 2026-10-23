"""The copilot's personalities: their own way with the routine and conversational lines, never with a safety call."""

import random
import re

import pytest
from test_crew_monitor import FakeEngine, crew, fly, said, systems

from localtc.crew import personalities
from localtc.crew.monitor import ROUTINE, SAFETY
from localtc.crew.personalities import PERSONALITIES, STANDARD, get, word
from localtc.sim_api import PhaseChanged, ReadbackEvaluated, Transcript
from test_crew import own

SAFETY_CALLS = [("stall", "Speed, speed!"), ("overspeed", "Overspeed!"), ("minimums:1", "Minimums."),
                ("no_landing_clearance:1", "We're not cleared to land!"), ("config", "Config! Flaps!"),
                ("1000:1", "One thousand, not stable: gear."), ("windshear", "Windshear!"),
                ("gear_1000:1", "Gear! We're not down!"), ("100_above:1", "Hundred above.")]


@pytest.mark.parametrize("p", PERSONALITIES, ids=lambda p: p.id)
def test_safety_calls_keep_their_standard_words_for_every_personality(p):
    rng = random.Random(1)
    for key, text in SAFETY_CALLS:
        for priority in (SAFETY, ROUTINE):  # by priority, or by being one of the standard calls
            if priority == ROUTINE and personalities.moment(key) not in STANDARD:
                continue
            for _ in range(20):
                assert word(p, key, text, priority=priority, safety=SAFETY, rng=rng, sterile=False) == (text, False)


@pytest.mark.parametrize("p", PERSONALITIES, ids=lambda p: p.id)
def test_a_personality_never_changes_a_number(p):
    rng = random.Random(2)
    for key, text in [("toc", "Level at FL350."), ("1000:1", "One thousand, stable."), ("10k_climb", "Ten thousand."),
                      ("turb:moderate", "Moderate turbulence."), ("set_alt:8000", "8,000 set."),
                      ("lined_up:27C", "Runway 27C, checked.")]:
        for _ in range(20):
            out, _ = word(p, key, text, priority=ROUTINE, safety=SAFETY, rng=rng, sterile=False)
            assert re.findall(r"\d+", out) == re.findall(r"\d+", text), (p.id, out)
            if key.startswith("lined_up"):
                assert out.startswith(text)  # the runway check said first, as it always is


def test_each_says_the_routine_calls_their_way():
    rng = random.Random(3)
    say = lambda pid, key, text: word(get(pid), key, text, priority=ROUTINE, safety=SAFETY, rng=rng, sterile=True)[0]  # noqa: E731
    assert say("elena", "1000:1", "One thousand, stable.") == "Stable at one thousand. Continue."
    assert say("maya", "1000:1", "One thousand, stable.") == "Okay, stable at a thousand!"
    assert say("oliver", "turb:moderate", "Moderate turbulence.").startswith("Rather bumpy, I'd say.")
    assert say("neutral", "1000:1", "One thousand, stable.") == "One thousand, stable."
    assert get("nobody").neutral  # an unknown one in the settings: the neutral copilot


def test_their_own_words_wait_out_the_sterile_cockpit():
    jake = get("jake")
    rng = random.Random(4)
    below = {word(jake, "lined_up:16L", "Runway 16L, checked.", priority=ROUTINE, safety=SAFETY, rng=rng,
                  sterile=True)[0] for _ in range(30)}
    above = {word(jake, "lined_up:16L", "Runway 16L, checked.", priority=ROUTINE, safety=SAFETY, rng=rng,
                  sterile=False)[0] for _ in range(30)}
    assert below == {"Runway 16L, checked."}
    assert "Runway 16L, checked. Alright, runway's yours." in above


def test_the_copilot_says_its_thousand_call_its_way_in_flight():
    engine = FakeEngine()
    engine.clear("landing")
    pm = crew(engine, personality="elena")
    pm.observe(PhaseChanged(t=0.5, previous="ARRIVAL", phase="APPROACH"))
    pm.observe(systems(0.6, flaps_positions=4))
    stable = dict(on_ground=False, gear_down=True, flaps_index=4, vs_fpm=-750, ias_kt=140)
    words = said(fly(pm, [own(1 + i, alt_agl_ft=1100 - 20 * i, **stable) for i in range(12)]))
    assert "Stable at one thousand. Continue." in words


def test_the_instructor_says_what_to_include_next_time():
    pm = crew(personality="tom")
    pm.observe(own(0.5, on_ground=True, ias_kt=0, gs_kt=0))
    pm.observe(Transcript(t=1.0, text="cleared to land, united 12", radio=1, source="voice"))
    pm.observe(ReadbackEvaluated(t=1.5, instruction_id="tower.land", status="incomplete", missing=("runway",)))
    words = said(fly(pm, [own(2 + i, on_ground=True, ias_kt=0, gs_kt=0) for i in range(30)]))
    assert "Next time, include the runway in the readback." in words
    neutral = crew()
    neutral.observe(ReadbackEvaluated(t=1.5, instruction_id="tower.land", status="incomplete", missing=("runway",)))
    assert not [w for w in said(fly(neutral, [own(2 + i, on_ground=True) for i in range(30)])) if "Next time" in w]


def test_its_check_is_its_own():
    pm = crew(personality="jake")
    assert personalities.ack(pm.personality, random.Random(0)) in ("Check.", "Yep.", "Got it.")
    assert personalities.ack(get("neutral"), random.Random(0)) == "Check."
