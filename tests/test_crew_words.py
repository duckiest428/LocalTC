"""The copilot and the pilot's words: what quiets it and what doesn't, two things asked at once, a call asked for on
the radio, ATC's altimeter set, the stock A320neo's stall warning, its spoilers, and the plan's times."""

from localtc.atc_core.values import Callsign
from localtc.config import PlanPerf
from localtc.crew.answers import _scheduled
from localtc.crew.commands import parse
from localtc.crew.personalities import ways_of
from localtc.crew.pm import _declines
from localtc.crew import speech_acts
from localtc.sim_api import AtcTransmission, CrewAction, IntercomHeard, PhaseChanged, SendSimEvent, Transcript

from test_crew_monitor import FakeEngine, crew, fly, own, said, systems


def test_fair_enough_and_i_know_in_a_sentence_dont_quiet_the_copilot():
    """ "Fair enough, thank you a lot" and "...I mean I know it's a hub..." each quieted it for 15 minutes: the
    cockpit preparation checklist asked for wasn't read, and the takeoff had no callouts."""
    for text in ("Fair enough. Thank you a lot.", "Why are there so many Alaska planes in Seattle? I mean I know it's a "
                 "hub but I didn't realize there's this many.", "We have enough fuel for the hold, right"):
        assert not _declines(text, speech_acts.read(text, 0.9).act), text
    for text in ("shut up", "Okay, enough.", "not now", "I know", "Chill out man"):
        assert _declines(text, speech_acts.read(text, 0.9).act), text


def test_a_checklist_asked_for_is_read_even_after_shut_up():
    pm = crew()
    pm.observe(PhaseChanged(t=0.5, previous="PARKED", phase="PARKED"))
    pm.observe(systems(0.6))
    pm.observe(own(1, parking_brake=True))
    pm.observe(IntercomHeard(t=2, text="shut up"))
    assert said(pm.observe(IntercomHeard(t=30, text="before start checklist")))[0].startswith("Before start checklist.")


def test_the_takeoff_callouts_are_said_after_shut_up():
    pm = crew(perf=PlanPerf(v1=140, vr=145, v2=150), plan_source="simbrief")
    pm.observe(PhaseChanged(t=0.5, previous="RUNWAY_HOLD", phase="TAKEOFF"))
    pm.observe(systems(0.6))
    pm.observe(IntercomHeard(t=0.7, text="shut up"))
    words = said(fly(pm, [own(1 + i, on_runway=True, ias_kt=ias, gs_kt=ias) for i, ias in enumerate(range(20, 160, 10))]))
    assert any(w.startswith(ways_of("One hundred knots.")) for w in words)
    assert any(w.startswith(ways_of("Rotate.")) for w in words)


def test_two_things_asked_at_once_are_both_done():
    reading = speech_acts.read("Yeah, put down flaps 3 and let's do the approach checklist.", 0.9)
    assert reading.act == "command" and [str(c) for c in reading.commands] == ["flaps 3", "checklist approach"]
    assert speech_acts.read("Spoilers are armed.", 0.9).act == "report"  # the captain's word, not "that's yours"


def test_going_around_in_a_chat_at_the_gate_isnt_a_go_around():
    pm = crew()
    pm.observe(systems(0.6))
    pm.observe(own(1))
    out = pm.observe(IntercomHeard(t=2, text="It's a little tough just going around on different airlines like you "
                                                "never get a senior position."))
    assert not [w for w in said(out) if "around" in w.lower()]


def test_asked_to_ask_atc_the_copilot_asks_on_the_radio():
    assert [str(c) for c in parse("Hey, can you ask Clearance for a radio check?")] == ["radio for a radio check ask"]
    assert parse("Can you ask clearance what the temperature is outside?")[0].target == "what the temperature is outside"
    engine = FakeEngine()
    engine.facility = lambda controller: None
    engine.state.comms.tuned = type("F", (), {"station": "Seattle Clearance", "mhz": 121.85})()
    engine.state.flight.callsign = Callsign.named("FFT4204")
    pm = crew(engine)
    pm.observe(own(1))
    out = pm.observe(IntercomHeard(t=2, text="Can you ask clearance for a radio check?"))
    assert said(out) == ["Asking."]
    out += fly(pm, [own(3 + i) for i in range(20)])
    calls = [o.text for o in out if isinstance(o, Transcript) and o.source == "copilot"]
    assert calls and calls[0].startswith("Seattle Clearance") and calls[0].endswith("request radio check.")


def test_the_altimeter_atc_gives_is_set_and_none_at_the_gate_by_itself():
    pm = crew()
    pm.observe(systems(0.6))
    out = fly(pm, [own(1 + i, altimeter_inhg=29.92, altimeter_setting_inhg=29.92) for i in range(30)])
    assert not [o for o in out if isinstance(o, CrewAction) and o.action == "altimeter"]  # the pilot's, at the gate
    out = pm.observe(AtcTransmission(t=40, station="Seattle Ground", frequency_mhz=121.7, text="Frontier 4204, taxi to "
                                     "runway 16L via B and C, information Bravo is current, altimeter 29.99.",
                                     instruction_id="ground.taxi_out_at"))
    out += fly(pm, [own(41 + i, altimeter_inhg=29.92, altimeter_setting_inhg=29.92) for i in range(30)])
    assert [o for o in out if isinstance(o, SendSimEvent) and o.name == "KOHLSMAN_SET" and o.value == round(29.99 * 33.8639 * 16)]


def test_the_sims_stall_warning_on_final_at_151_knots_isnt_speed_speed():
    """The stock A320neo's stall warning came on at 151 knots, flaps 2 and the gear down."""
    pm = crew()
    pm.observe(PhaseChanged(t=0.5, previous="ARRIVAL", phase="APPROACH"))
    pm.observe(systems(0.6, stall_warning=True, vs0_kt=121.0, vs1_kt=179.0))
    out = fly(pm, [own(1 + i, on_ground=False, alt_indicated_ft=3500 - 30 * i, alt_agl_ft=3000 - 30 * i, ias_kt=151,
                       gs_kt=151, flaps_index=2, gear_down=True) for i in range(10)])
    assert "Speed, speed!" not in said(out)
    out = fly(pm, [own(20 + i, on_ground=False, alt_indicated_ft=3000, alt_agl_ft=2500, ias_kt=130 - i, gs_kt=130,
                       flaps_index=2, gear_down=True) for i in range(5)])
    assert "Speed, speed!" in said(out)  # near the stall speed, it's believed


def test_the_plans_times_are_worked_out_for_the_copilot():
    out = _scheduled(1793055600, (1793055600 % 86400) - 7 * 60)  # 7 minutes before it
    assert out.endswith("Zulu, in 7 minutes") and len(out.split()[0]) == 4
