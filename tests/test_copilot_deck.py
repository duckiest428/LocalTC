"""The copilot as a cautious first officer: what it hears is read for what it is before anything is done, it asks when
it isn't sure, a "yes" counts only for the question just asked, and the radio copilot and the intercom copilot share
one flight deck that drops what's no longer right to say or do.

Adversarial on purpose: low-confidence speech, questions and reports that contain a command, corrections, negations,
the same call twice, a stale handoff, the pilot tuning the radio, two calls at once, a question replaced by another,
speech interrupted, a reconnect, and a model that's slow or wrong."""

from types import SimpleNamespace

import pytest
from test_crew import FakeBackend, own, said, started
from test_crew_monitor import FakeEngine, crew

from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.copilot import Copilot, Say
from localtc.crew import speech_acts
from localtc.crew.model import CrewModel, persona, unsupported_claim
from localtc.crew.pm import CONFIRM_S, PilotMonitoring
from localtc.crew.profiles import load_all
from localtc.flightdeck import FlightDeck
from localtc.sim_api import (
    TurnKnob,
    AircraftIdentity,
    AircraftSystems,
    AtcTransmission,
    ConnectionStatus,
    CopilotEvent,
    IntercomHeard,
    PttPressed,
    PttReleased,
    SendSimEvent,
    SimLifecycle,
    Transcript,
)

PROFILES = load_all()


def sent(outputs) -> list[SendSimEvent | TurnKnob]:
    return [o for o in outputs if isinstance(o, (SendSimEvent, TurnKnob))]


def heard(pm, t, text, confidence=None):
    return pm.observe(IntercomHeard(t=t, text=text, confidence=confidence))


def events(outputs, kind=None) -> list[CopilotEvent]:
    return [o for o in outputs if isinstance(o, CopilotEvent) and (kind is None or o.kind == kind)]


# --- what the words are --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "act"), [
    ("gear down", "command"),
    ("can you put the gear down", "command"),
    ("go ahead and put flaps 2 and set your QNH at 1020", "command"),
    ("did you set the gear?", "question"),
    ("is the autopilot on?", "question"),
    ("I'll go ahead and put flaps 2.", "report"),
    ("gear down, three green", "report"),
    ("flaps one, check", "report"),
    ("we've captured the glide slope, starting down now", "report"),
    ("no, flaps two", "correction"),
    ("I said heading two four zero", "correction"),
    ("No, no, no. Don't go around. We're not going around.", "negation"),
    ("never mind", "answer"),  # a "no" to what was asked
    ("check", "acknowledgement"),
    ("confirm", "answer"),
    ("negative", "answer"),
    ("Alright, fair enough. 3,000. 3,000. 3,000. 3,000. 3,000.", "unclear"),
    ("Thank you.", "unclear"),
    ("how's the weather at the destination", "question"),
    ("beautiful sunset out there", "chat"),
])
def test_each_utterance_is_read_for_what_it_is(text, act):
    assert speech_acts.read(text).act == act


def test_confidence_sets_how_sure():
    assert speech_acts.read("gear down", None).certainty == "high"  # typed
    assert speech_acts.read("gear down", 0.9).certainty == "high"
    assert speech_acts.read("gear down", 0.6).certainty == "medium"
    assert speech_acts.read("gear down", 0.4).certainty == "low"


# --- confidence-aware execution --------------------------------------------------------------------------------------


def test_a_command_heard_badly_is_asked_about_not_done():
    pm = started()
    out = heard(pm, 1.0, "heading two four zero", confidence=0.45)
    assert said(out) == ["Did you say heading 240?"] and not sent(out)
    assert [(w.name, w.target) for w in sent(heard(pm, 3.0, "affirm"))] == [("INSTRUMENT_FCU_HDG_KNOB", 240)]


def test_a_command_heard_fairly_well_is_said_back_first():
    pm = started()
    out = heard(pm, 1.0, "flaps one", confidence=0.65)
    assert said(out) == ["Flaps 1?"] and not sent(out)


def test_a_low_risk_command_heard_fairly_well_is_done():
    pm = started()
    assert sent(heard(pm, 1.0, "landing lights on", confidence=0.65)) == [SendSimEvent(name="LANDING_LIGHTS_SET", value=1)]


def test_a_command_heard_clearly_is_done():
    pm = started()
    assert sent(heard(pm, 1.0, "gear down", confidence=0.95)) == [SendSimEvent(name="GEAR_DOWN")]


def test_the_same_words_heard_twice_are_done_once():
    """Speech-to-text handing the same call over twice (or the pilot repeating it before the copilot answered)."""
    pm = started()
    first = heard(pm, 1.0, "gear down", 0.95)
    again = heard(pm, 1.8, "gear down", 0.95)
    assert sent(first) and not sent(again)


def test_a_command_cut_off_mid_number_isnt_guessed():
    pm = started()
    out = heard(pm, 1.0, "set heading two", 0.9)  # the push-to-talk let go mid-word
    assert not sent(out)


@pytest.mark.parametrize("text", ["did you set the gear down?", "is the gear down?", "should we put the gear down?",
                                  "I'll put the gear down", "gear down, three green"])
def test_questions_and_reports_never_move_anything(text):
    pm = started()
    assert not sent(heard(pm, 1.0, text, confidence=0.95))


def test_a_short_question_naming_a_control_is_offered_not_done():
    pm = started()
    out = heard(pm, 1.0, "gear down?", confidence=0.95)
    assert said(out) == ["That sounded like a question. Gear's up. Want gear down?"] and not sent(out)
    assert sent(heard(pm, 2.0, "yes")) == [SendSimEvent(name="GEAR_DOWN")]


def test_a_number_against_the_atis_is_asked_about():
    """ "Set your QNH at 1020" with the ATIS at 1012: it was never set, and the copilot later said it was."""
    engine = FakeEngine()
    engine.state.phase = "APPROACH"
    engine.current_atis = lambda icao, kind=None: SimpleNamespace(weather=SimpleNamespace(altimeter_inhg=29.88))
    pm = crew(engine)
    out = heard(pm, 1.0, "set your Q and H at 1020", confidence=0.85)
    assert said(out) == ["The ATIS has QNH 1012. Set 1020?"] and not sent(out)
    assert sent(heard(pm, 2.0, "yes"))  # the captain's call


def test_several_commands_in_one_breath_are_each_handled():
    pm = started()
    out = heard(pm, 1.0, "go ahead and put flaps 2 and set your QNH at 1013", confidence=0.9)
    assert [s.name for s in sent(out)] == ["FLAPS_2"]
    assert said(out) == ["Confirm QNH 1013?"]  # the QNH used to be dropped; a spoken number nothing backs: read back
    assert [s.name for s in sent(heard(pm, 2.0, "yes"))] == ["KOHLSMAN_SET"]


def test_a_spoken_number_atc_gave_is_set_one_it_didnt_is_read_back():
    engine = FakeEngine()
    engine.state.assignments.altitude_ft = 6000
    pm = crew(engine)
    pm.observe(own(0.5, on_ground=False, alt_agl_ft=3000, alt_indicated_ft=3000))
    assert sent(heard(pm, 1.0, "altitude six thousand", 0.9))
    out = heard(pm, 2.0, "set heading two four zero", 0.9)
    assert said(out) == ["Confirm heading 240?"] and not sent(out)
    assert sent(heard(pm, 3.0, "heading two four zero"))  # typed: as written


# --- confirmations bound to their question -------------------------------------------------------------------------


def test_a_yes_counts_only_for_the_question_just_asked():
    pm = started()
    heard(pm, 1.0, "squawk 7700")  # asked
    heard(pm, 2.0, "squawk 7600")  # another question replaces it
    out = heard(pm, 3.0, "confirm")
    assert sent(out) == [SendSimEvent(name="XPNDR_SET", value=0x7600)]


def test_a_correction_replaces_what_was_asked():
    pm = started()
    assert said(heard(pm, 1.0, "flaps one", confidence=0.6)) == ["Flaps 1?"]
    out = heard(pm, 2.0, "no, flaps two", confidence=0.9)
    assert sent(out) == [SendSimEvent(name="FLAPS_2")]
    assert not sent(heard(pm, 3.0, "yes"))  # nothing waiting any more: flaps 1 isn't done on it


def test_a_late_yes_does_nothing():
    pm = started()
    heard(pm, 1.0, "squawk 7700")
    out = heard(pm, 1.0 + CONFIRM_S + 5, "yes")
    assert not sent(out) and said(out) == ["That's gone stale. Say again what you need?"]


def test_a_yes_after_the_clearance_changed_is_asked_again():
    engine = FakeEngine()
    engine.state.assignments.altitude_ft = 10000
    pm = crew(engine)
    pm.observe(own(0.5, alt_indicated_ft=8000))
    out = heard(pm, 1.0, "altitude one one thousand", confidence=0.6)
    assert not sent(out)
    engine.state.assignments.altitude_ft = 12000  # ATC changed the clearance meanwhile
    out = heard(pm, 3.0, "yes")
    assert not sent(out) and "That was before the cleared altitude changed" in said(out)[0]


def test_a_yes_with_nothing_asked_authorises_nothing():
    pm = started()
    assert not sent(heard(pm, 1.0, "yes")) and not sent(heard(pm, 2.0, "confirm"))


def test_never_mind_drops_the_question():
    pm = started()
    heard(pm, 1.0, "squawk 7700")
    assert said(heard(pm, 2.0, "never mind")) == ["Copy, leaving it."]
    assert not sent(heard(pm, 3.0, "confirm"))


def test_dont_touch_keeps_the_copilots_own_hands_off():
    pm = started()
    assert said(heard(pm, 1.0, "don't touch the flaps")) == ["Copy, I'll leave the flaps alone."]
    assert "flaps" in pm.hands_off


# --- the shared flight deck -----------------------------------------------------------------------------------------


def test_dont_go_around_holds_the_radio_copilots_readback():
    """Tower said go around, the captain said "no, don't go around", and the copilot read the go-around back anyway."""
    deck = FlightDeck()
    readback = deck.submit("radio", "readback", "tower.go_around_traffic_contact:Heathrow Tower", 1.0,
                           text="Going around, Virgin six.")
    pm = PilotMonitoring(FakeEngine(), profiles=PROFILES, deck=deck)
    out = heard(pm, 2.0, "No, no, no. Don't go around. We're not going around.")
    assert readback.cancelled and deck.check(readback, 3.0) is not None
    assert said(out) == ["Copy, I haven't read the go-around back. Tower's expecting it: tell them."]


def test_the_pilots_own_call_drops_the_copilots_queued_one():
    deck = FlightDeck()
    checkin = deck.submit("radio", "checkin", "checkin:Socal Departure", 1.0, text="Socal Departure, Virgin 6, 1,000")
    readback = deck.submit("radio", "readback", "common.climb:Socal Departure", 1.0, text="Climb 8,000, Virgin 6")
    deck.observe(Transcript(t=2.0, text="Socal Departure, Virgin 6, with you", source="voice"))
    assert checkin.cancelled == "the pilot called" and not readback.cancelled  # ATC may still want the readback


def test_the_pilot_tuning_the_radio_drops_the_copilots_tune_and_check_in():
    deck = FlightDeck()
    deck.observe(own(1.0, com1_mhz=124.35))
    tune = deck.submit("radio", "tune", "tune:Seattle Center", 1.5)
    checkin = deck.submit("radio", "checkin", "checkin:Seattle Center", 1.5, text="Seattle Center, ...")
    deck.observe(own(2.0, com1_mhz=121.5))  # the pilot dialled it
    assert tune.cancelled.startswith("the pilot tuned") and checkin.cancelled
    deck2 = FlightDeck()
    deck2.observe(own(1.0, com1_mhz=124.35))
    deck2.expect_tune(125.1, 1.2)
    item = deck2.submit("radio", "checkin", "checkin:Seattle Center", 1.2, text="...")
    deck2.observe(own(2.0, com1_mhz=125.1))  # the copilot's own tune
    assert not item.cancelled


def test_a_new_instruction_drops_a_call_the_copilot_was_about_to_start():
    deck = FlightDeck()
    call = deck.submit("radio", "call", "departure:Paine Tower", 1.0, text="Paine Tower, ready for departure")
    deck.observe(AtcTransmission(t=2.0, station="Paine Tower", frequency_mhz=120.2, text="line up and wait",
                                 instruction_id="tower.luaw"))
    assert call.cancelled == "ATC spoke first"


def test_nothing_goes_out_while_the_pilot_is_talking():
    deck = FlightDeck()
    item = deck.submit("crew", "request", "step:37000", 1.0, text="Center, Virgin 6, request FL370")
    deck.observe(PttPressed(t=1.5))
    assert deck.check(item, 2.0) == "the pilot is on the radio" and not item.cancelled  # held, not dropped
    deck.observe(PttReleased(t=3.0))
    assert deck.check(item, 3.5) is None


def test_two_copilots_never_talk_at_once():
    """The intercom copilot's own request (a step climb) waits while the radio copilot has a readback queued."""
    deck = FlightDeck()
    pm = PilotMonitoring(FakeEngine(), profiles=PROFILES, deck=deck, radio_mode=lambda: "full")
    pm.observe(own(0.5))
    readback = deck.submit("radio", "readback", "common.climb:Center", 1.0, text="Climb FL370, Virgin six")
    pm._radio_queue.append(deck.submit("crew", "request", "step:37000", 1.0, text="Center, Virgin six, request FL370"))
    assert not [o for o in pm.observe(own(2.0)) if isinstance(o, Transcript)]
    deck.done(readback, 3.0)
    assert [o.text for o in pm.observe(own(12.0)) if isinstance(o, Transcript)] == ["Center, Virgin six, request FL370"]


def test_the_same_words_twice_are_not_said_again():
    deck = FlightDeck()
    first = deck.submit("radio", "call", "x", 1.0, text="Paine Ground, N172LT, ready to taxi")
    assert deck.check(first, 1.0) is None
    deck.done(first, 1.0)
    again = deck.submit("radio", "call", "x", 5.0, text="Paine Ground, N172LT, ready to taxi.")
    assert deck.check(again, 5.0) == "just said that"
    on_purpose = deck.submit("radio", "readback", "say_again", 6.0, text="Paine Ground, N172LT, ready to taxi", again=True)
    assert deck.check(on_purpose, 6.0) is None  # answering "say again"


def test_a_reconnect_starts_a_new_flight_and_drops_everything():
    deck = FlightDeck()
    pm = PilotMonitoring(FakeEngine(), profiles=PROFILES, deck=deck)
    pm.observe(AircraftIdentity(t=0.0, title="Airbus A320neo", atc_model="A20N"))
    pm.observe(own(0.5))
    heard(pm, 1.0, "squawk 7700")
    queued = deck.submit("radio", "checkin", "checkin:X", 1.0, text="X")
    pm.observe(ConnectionStatus(t=2.0, connected=False))
    pm.observe(ConnectionStatus(t=30.0, connected=True))
    assert deck.generation == 2 and queued.cancelled and pm.intent is None
    assert not sent(heard(pm, 31.0, "confirm"))


def test_the_radio_copilot_drops_its_check_in_when_the_pilot_calls_first():
    engine = AtcEngine(EngineConfig(callsign="N172LT"))
    deck = FlightDeck()
    copilot = Copilot(engine, mode="full", delay_s=(1.0, 1.0), deck=deck)
    facility = SimpleNamespace(station="Seattle Center", mhz=125.1, controller="center", matches=lambda mhz: True)
    queued = copilot._push("checkin", 1.0, facility=facility)
    copilot.observe(Transcript(t=1.5, text="Seattle Center, N172LT, level 5,000", source="voice"))
    assert queued.item.cancelled == "the pilot called"
    assert not [a for a in copilot.due(10.0) if isinstance(a, Say)]


def test_a_mode_change_drops_what_the_radio_copilot_had_queued():
    engine = AtcEngine(EngineConfig(callsign="N172LT"))
    copilot = Copilot(engine, mode="full", delay_s=(1.0, 1.0))
    queued = copilot._push("say", 1.0, text="Paine Ground, N172LT, ready to taxi", once="taxi")
    copilot.mode = "assist"
    assert queued.item.cancelled.startswith("the copilot's radio mode")


# --- recovery --------------------------------------------------------------------------------------------------------


def test_say_again_repeats_the_last_thing_said():
    pm = started()
    heard(pm, 1.0, "squawk 7700")
    assert said(heard(pm, 2.0, "say again?")) == ["Squawk 7700, emergency, confirm?"]


def test_a_radio_call_on_the_intercom_is_never_sent_unasked():
    pm = started()
    out = heard(pm, 1.0, "Seattle Tower, Alaska 123, ready for departure")
    assert not [o for o in out if isinstance(o, Transcript)]


def test_speech_to_text_going_round_in_circles_gets_no_answer():
    pm = started()
    assert said(heard(pm, 1.0, "Uh, is that? 3,000. 3,000. 3,000. 3,000. 3,000. 3,000.", 0.64)) == []


def test_back_from_a_long_pause_the_copilot_says_where_things_stand():
    engine = FakeEngine()
    pm = crew(engine)
    pm.observe(own(1.0, on_ground=False, alt_indicated_ft=5000, alt_agl_ft=4500))
    pm.observe(SimLifecycle(t=2.0, kind="paused"))
    pm.observe(AtcTransmission(t=10.0, station="Heathrow Director", frequency_mhz=119.73, text="Virgin 6, descend 4,000."))
    out = pm.observe(SimLifecycle(t=400.0, kind="unpaused"))
    words = said(out)
    assert words and words[0].startswith("Welcome back.") and "descend 4,000" in words[0]
    pm.observe(SimLifecycle(t=401.0, kind="paused"))
    assert not said(pm.observe(SimLifecycle(t=420.0, kind="unpaused")))  # a short pause: nothing


def test_a_switch_that_never_moves_on_this_aircraft_is_left_to_the_pilot():
    """An aircraft whose autopilot knobs never move for the copilot (the A350 before its profile): not "6,000 set",
    "didn't take", for nine hours."""
    pm = PilotMonitoring(FakeEngine(), profiles=PROFILES)
    pm.observe(AircraftIdentity(t=0.0, title="Generic Jet", atc_model="GJET"))  # the stock profile
    pm.observe(own(0.5, on_ground=False, alt_agl_ft=9000, alt_indicated_ft=9000))
    pm.observe(AircraftSystems(t=0.5, flaps_positions=4, ap_altitude_sel=100))
    words = []
    for n, alt in enumerate(("10000", "11000", "12000")):
        t = 1.0 + 10 * n
        words += said(heard(pm, t, f"altitude {alt}"))
        words += said(pm.observe(own(t + 4, on_ground=False, alt_agl_ft=9000, alt_indicated_ft=9000)))
    assert "Altitude 10,000 sent, but I can't see it on this aircraft. Check it." in words or any("sent, but" in w for w in words)
    assert any("inputs aren't reaching this aircraft" in w for w in words)
    assert "altitude" in pm.cockpit.dead and not sent(heard(pm, 40.0, "altitude 13000"))


def test_the_autobrake_is_the_copilots_to_set():
    """ "Turn on auto brake": "Auto brake's yours, can't from here." It's the copilot's job."""
    pm = started()
    pm.observe(own(1.0, on_ground=True, ias_kt=0, gs_kt=0))
    assert said(heard(pm, 2.0, "turn on auto brake", 0.9)) == ["Autobrake max for takeoff?"]
    assert sent(heard(pm, 3.0, "yes")) == [SendSimEvent(name="AUTOBRAKE_HI_SET")]
    assert sent(heard(pm, 5.0, "autobrake low", 0.9)) == [SendSimEvent(name="AUTOBRAKE_LO_SET")]


def test_the_fuel_predictions_stop_when_the_captain_says_theyre_off():
    pm = started()
    out = heard(pm, 1.0, "Your fuel prediction is off. Looking at the MCDU, we have enough")
    assert said(out) == ["Copy, I'll leave the fuel predictions to the box."] and pm.picture.fuel_doubted


# --- the model -------------------------------------------------------------------------------------------------------


def test_a_slow_model_gets_say_again_not_a_guess():
    class Slow(FakeBackend):
        def complete(self, request, *, timeout_s):
            from localtc.atc_core.llm.backend import LlmReply
            return LlmReply(None, timeout_s * 1000, "timeout")

    pm = started()
    pm.model = CrewModel(Slow("{}"), mode="llm")
    out = heard(pm, 1.0, "do you reckon they'll give us a shortcut")
    assert said(out) == ["Say again?"] and not sent(out)


def test_a_model_command_waits_for_yes_and_a_changed_phase_cancels_it():
    engine = FakeEngine()
    pm = crew(engine)
    pm.observe(own(0.5, on_ground=False, alt_agl_ft=3000, alt_indicated_ft=3000, ias_kt=180))
    pm.model = CrewModel(FakeBackend('{"kind": "command", "action": "gear", "value": "down", "reply": "Gear down."}'))
    out = heard(pm, 1.0, "drop the wheels for me would you")
    assert said(out) == ["Gear down, confirm?"] and not sent(out)
    engine.state.phase = "LANDING"  # moved on before the answer
    out = heard(pm, 3.0, "yes")
    assert not sent(out) and said(out)[0].startswith("Not now")


@pytest.mark.parametrize(("reply", "why"), [
    ("Copy, autopilot off.", "autopilot"),
    ("No triphord, just the QNH set at 1020.", "qnh"),
    ("272 at 24, about 12 knots crosswind.", "crosswind"),
])
def test_the_model_cant_claim_what_wasnt_done_or_make_up_wind(reply, why):
    facts = {"autopilot": "can't be read in this aircraft", "gear": "down",
             "wind on runway 27L": "23 knots headwind, 1 knot crosswind from the right"}
    assert why in (unsupported_claim(reply, facts) or "")


def test_the_first_officer_is_the_same_person_all_flight():
    a, b = persona(42, "male", "London Heathrow", "A350"), persona(42, "male", "London Heathrow", "A350")
    assert a == b and "London Heathrow" in a and "A350" in a


def test_a_copilot_failure_never_stops_the_next_event():
    pm = started()
    pm.monitor.observe = lambda ev: (_ for _ in ()).throw(RuntimeError("boom"))
    out = pm.observe(own(2.0))  # doesn't raise
    assert events(out, "error") and "boom" in events(out, "error")[0].detail


def test_a_readback_goes_before_the_other_copilots_request():
    deck = FlightDeck()
    request = deck.submit("crew", "request", "step:37000", 1.0, text="request FL370")
    readback = deck.submit("radio", "readback", "common.climb:Center", 1.0, text="climb FL350")
    assert deck.check(request, 1.5) == "a more urgent call first" and not request.cancelled
    assert deck.check(readback, 1.5) is None
    deck.done(readback, 2.0)
    assert deck.check(request, 2.5) is None


def test_every_utterance_is_kept_with_who_whom_and_the_moment():
    deck = FlightDeck()
    pm = PilotMonitoring(FakeEngine(), profiles=PROFILES, deck=deck)
    pm.observe(own(0.5, alt_indicated_ft=5000))
    pm.observe(IntercomHeard(t=1.0, text="gear down", confidence=0.91, audio_ref="audio/0036.wav"))
    pm.observe(own(1.5, alt_indicated_ft=5000, gear_down=True))  # "Gear down." once the sim shows it
    pm.observe(Transcript(t=2.0, text="Tower, Virgin 6, ten mile final", radio=1, confidence=0.8, source="voice"))
    pm.observe(AtcTransmission(t=3.0, station="Tower", frequency_mhz=118.5, text="Virgin 6, continue"))
    kept = list(deck.memory.utterances)
    mine = next(u for u in kept if u.recipient == "intercom" and u.source == "pilot")
    assert (mine.text, mine.confidence, mine.audio_ref, mine.act) == ("gear down", 0.91, "audio/0036.wav", "command")
    assert dict(mine.snapshot)["altitude"] == 5000 and mine.id
    assert [(u.source, u.recipient) for u in kept if u.recipient == "com1"] == [("pilot", "com1"), ("atc", "com1")]
    assert any(u.source == "copilot" and u.recipient == "intercom" for u in kept)  # its answer too
