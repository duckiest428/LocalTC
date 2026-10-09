"""The copilot as Pilot Monitoring on the intercom: its grammar, its safety rules, working the aircraft through the
sim, its voice, and the intercom key."""

import asyncio
import struct

import numpy as np
import pytest
from helpers.sim_fakes import FakeSimConnect
from test_stt import FakeCapture, FakeTranscriber

from localtc.bus import EventBus
from localtc.config import LiveConfig
from localtc.crew.actions import Cockpit, plan, safety
from localtc.crew.commands import Command, parse
from localtc.crew.pm import CHECK_S, PilotMonitoring
from localtc.crew.profiles import Profile, for_aircraft, load_all
from localtc.dsp.radio import intercom_effect
from localtc.radiolog import radio_line
from localtc.sim_api import (
    AircraftIdentity,
    AircraftSystems,
    CrewAction,
    CrewSpeech,
    IntercomHeard,
    OwnshipState,
    SendSimEvent,
    SetComFrequency,
    SetSimVar,
    Transcript,
)
from localtc.sim_bridge.simconnect_source import SimConnectSource
from localtc.stt.service import INTERCOM, VoiceService
from localtc.tts.player import AudioPlayer, Clip
from localtc.tts.voices import crew_choices, crew_speaker, sex_of

PROFILES = load_all()
A320 = for_aircraft(PROFILES, "Airbus A320neo Asobo", "A20N")
STOCK = for_aircraft(PROFILES, "Cessna Skyhawk", "C172")


def own(t: float = 1.0, **overrides) -> OwnshipState:
    fields = dict(t=t, lat=47.0, lon=-122.0, alt_msl_ft=5000.0, alt_indicated_ft=5000.0, alt_agl_ft=4500.0,
                  altimeter_inhg=29.92, hdg_mag=90.0, hdg_true=90.0, ias_kt=180.0, gs_kt=180.0, vs_fpm=0.0,
                  on_ground=False, squawk="4553", xpdr_mode="alt", com1_mhz=124.35, com2_mhz=121.5, flaps_index=0,
                  gear_down=False)
    fields.update(overrides)
    return OwnshipState(**fields)


def cockpit(profile: Profile = A320, systems: AircraftSystems | None = None, **overrides) -> Cockpit:
    return Cockpit(own=own(**overrides), systems=systems or AircraftSystems(t=1.0, flaps_positions=4), profile=profile)


# --- what the pilot says --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("said, read", [
    ("gear down", ["gear down"]),
    ("gear up, flaps one", ["gear up", "flaps 1"]),
    ("flaps one plus F", ["flaps 1+f"]),
    ("flaps full", ["flaps full"]),
    ("turn on the landing lights", ["light landing on"]),
    ("strobes on", ["light strobe on"]),
    ("arm the spoilers", ["spoilers arm"]),
    ("speedbrakes extend", ["spoilers extend"]),
    ("disconnect autopilot", ["autopilot off"]),
    ("approach mode", ["ap_mode approach on"]),
    ("level change", ["ap_mode flc on"]),
    ("set heading two seven zero", ["heading 270"]),
    ("altitude one zero thousand", ["altitude 10000"]),
    ("flight level two four zero", ["altitude 24000"]),
    ("speed two fifty", ["speed 250"]),
    ("vertical speed minus one thousand five hundred", ["vs -1500"]),
    ("squawk four five five three", ["squawk 4553"]),
    ("squawk 4558", []),  # not a transponder code: no 8s
    ("tune one two one point niner", ["com_active 121.9"]),
    ("standby 118.7", ["com_standby 118.7"]),
    ("altimeter two niner niner two", ["altimeter 29.92"]),
    ("QNH one zero one three", ["altimeter hpa 1013"]),
    ("set standard", ["altimeter 29.92"]),
    ("release the parking brake", ["parking_brake off"]),
    ("gear down flaps three landing lights on", ["gear down", "flaps 3", "light landing on"]),
    ("confirm", ["yes"]),
    ("negative", ["no"]),
    ("how do you hear me", ["check"]),
    ("what's our fuel", []),
])
def test_the_grammar_reads_commands(said, read):
    assert [str(c) for c in parse(said)] == read


# --- the aircraft's profile ------------------------------------------------------------------------------------------


def test_profiles_match_the_aircraft():
    assert A320.name == "Airbus A320neo" and STOCK.name == "stock"
    assert A320.detent_index("one plus f", 4) == 1 and A320.detent_index("full", 4) == 4
    assert A320.detent_name(1, 4, on_ground=True) == "1+F" and A320.detent_name(1, 4) == "1"
    assert STOCK.detent_index("full", 3) == 3 and STOCK.detent_index("4", 3) is None


def test_a320_flaps_by_detent_events_and_stock_by_position():
    p = plan(Command("flaps", "2"), cockpit())
    assert p.writes == (SendSimEvent(name="FLAPS_2"),) and p.done == "Flaps 2."
    p = plan(Command("flaps", "1"), cockpit(on_ground=True, ias_kt=0, gs_kt=0))
    assert p.done == "Flaps 1+F." and p.done_spoken == "Flaps 1 plus F."
    p = plan(Command("flaps", "2"), cockpit(STOCK, AircraftSystems(t=1.0, flaps_positions=3)))
    assert p.writes == (SendSimEvent(name="FLAPS_SET", value=round(2 / 3 * 16383)),)
    assert plan(Command("flaps", "5"), cockpit()) == "Unable, there's no flaps 5 on this aircraft."


def test_a_profile_can_send_an_action_another_way():
    from localtc.crew.profiles import Write

    custom = Profile(name="addon", match=("X",), actions={"light_landing": Write(lvar="L:ADDON_LDG", on=2, off=0)})
    p = plan(Command("light", "on", "landing"), cockpit(custom))
    assert p.writes == (SetSimVar(name="L:ADDON_LDG", unit="number", value=2.0),)


def test_values_go_to_the_sim_as_it_expects_them():
    c = cockpit()
    assert plan(Command("squawk", "7700"), c).writes == (SendSimEvent(name="XPNDR_SET", value=0x7700),)
    assert plan(Command("com_active", "121.9"), c).writes == (SetComFrequency(hz=121_900_000),)
    assert plan(Command("altimeter", "1013", "hpa"), c).writes == (SendSimEvent(name="KOHLSMAN_SET", value=16208, index=2),)
    assert plan(Command("vs", "-1500"), c).writes == (SendSimEvent(name="AP_VS_VAR_SET_ENGLISH", value=-1500),)
    p = plan(Command("altitude", "24000"), c)
    assert p.done == "FL240 set." and p.done_spoken == "flight level two four zero set"


# --- the safety rules --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("cmd, state, verdict, reason", [
    (Command("gear", "up"), dict(on_ground=True, ias_kt=0, gs_kt=0), "refuse", "on the ground"),
    (Command("gear", "up"), dict(alt_agl_ft=200, vs_fpm=-50), "refuse", "no positive rate"),
    (Command("gear", "up"), dict(alt_agl_ft=200, vs_fpm=1500, ias_kt=160), "ok", ""),
    (Command("gear", "down"), dict(ias_kt=270), "refuse", "gear limit 250"),
    (Command("flaps", "2"), dict(ias_kt=230), "refuse", "flaps 2 limit 200"),
    (Command("flaps", "2"), dict(ias_kt=195), "ok", ""),
    (Command("flaps", "up"), dict(ias_kt=300, flaps_index=2), "ok", ""),  # retracting is never too fast
    (Command("flaps", "1"), dict(on_ground=True, gs_kt=110, ias_kt=110), "refuse", "not on the roll"),
    (Command("spoilers", "extend"), dict(alt_agl_ft=600), "refuse", "1,000 feet"),
    (Command("spoilers", "arm"), dict(alt_agl_ft=600), "ok", ""),
    (Command("parking_brake", "on"), dict(on_ground=True, gs_kt=12), "refuse", "moving"),
    (Command("autopilot", "off"), dict(alt_agl_ft=300), "confirm", "Autopilot off at 300 feet"),
    (Command("autopilot", "off"), dict(alt_agl_ft=3000), "ok", ""),
    (Command("squawk", "7700"), {}, "confirm", "emergency"),
    (Command("squawk", "1200"), {}, "confirm", "ATC gave us 4553"),
    (Command("altitude", "11000"), {}, "confirm", "cleared to 10,000"),
    (Command("altitude", "10000"), {}, "ok", ""),
])
def test_safety_rules(cmd, state, verdict, reason):
    c = cockpit(**state)
    c.assigned_squawk, c.cleared_altitude_ft = "4553", 10000
    if cmd.action == "autopilot":
        c.systems = AircraftSystems(t=1.0, ap_master=True, flaps_positions=4)
    v = safety(cmd, c)
    assert v.kind == verdict and reason.lower() in v.reason.lower()


# --- the copilot at work ------------------------------------------------------------------------------------------------


def said(outputs) -> list[str]:
    return [o.text for o in outputs if isinstance(o, CrewSpeech)]


def started() -> PilotMonitoring:
    pm = PilotMonitoring(profiles=PROFILES)
    pm.observe(AircraftIdentity(t=0.0, title="Airbus A320neo", atc_model="A20N"))
    pm.observe(own(0.5))
    pm.observe(AircraftSystems(t=0.5, flaps_positions=4))
    return pm


def test_it_acts_then_says_so_once_the_sim_shows_it():
    pm = started()
    out = pm.observe(IntercomHeard(t=1.0, text="gear down, flaps one"))
    assert [o for o in out if isinstance(o, SendSimEvent)] == [SendSimEvent(name="GEAR_DOWN"), SendSimEvent(name="FLAPS_1")]
    assert said(out) == []  # nothing said until the sim shows it
    out = pm.observe(own(1.5, gear_down=True, flaps_index=1))
    assert said(out) == ["Gear down.", "Flaps 1."]
    assert [a.outcome for a in out if isinstance(a, CrewAction)] == ["done", "done"]


def test_it_says_when_a_command_didnt_take():
    pm = started()
    pm.observe(IntercomHeard(t=1.0, text="landing lights on"))
    assert said(pm.observe(own(2.0))) == []
    out = pm.observe(own(1.0 + CHECK_S + 0.1))
    assert said(out) == ["Landing lights on didn't take, check it."]
    assert any(isinstance(a, CrewAction) and a.outcome == "failed" for a in out)


def test_already_so():
    pm = started()
    out = pm.observe(IntercomHeard(t=1.0, text="flaps up"))
    assert said(out) == ["Flaps are already up."] and not [o for o in out if isinstance(o, SendSimEvent)]


def test_a_refusal_gives_the_reason_and_sends_nothing():
    pm = started()
    pm.observe(own(1.0, ias_kt=250))
    out = pm.observe(IntercomHeard(t=1.1, text="flaps three"))
    assert said(out) == ["Unable, speed 250, flaps 3 limit 185."]
    assert not [o for o in out if isinstance(o, SendSimEvent)]


def test_confirm_first_then_on_confirm():
    pm = started()
    out = pm.observe(IntercomHeard(t=1.0, text="squawk 7700"))
    assert said(out) == ["Squawk 7700, emergency, confirm?"] and not [o for o in out if isinstance(o, SendSimEvent)]
    out = pm.observe(IntercomHeard(t=3.0, text="confirm"))
    assert SendSimEvent(name="XPNDR_SET", value=0x7700) in out
    pm.observe(IntercomHeard(t=4.0, text="squawk 7600"))
    assert said(pm.observe(IntercomHeard(t=5.0, text="negative"))) == ["Copy, leaving it."]
    pm.observe(IntercomHeard(t=6.0, text="squawk 7500"))
    assert not [o for o in pm.observe(IntercomHeard(t=30.0, text="confirm")) if isinstance(o, SendSimEvent)]  # too late


def test_intercom_check_and_unclear_words():
    pm = started()
    assert said(pm.observe(IntercomHeard(t=1.0, text="how do you hear me"))) == ["Loud and clear."]
    assert said(pm.observe(IntercomHeard(t=2.0, text="banana"))) == ["Say again?"]
    assert pm.observe(IntercomHeard(t=3.0, text="")) == []


def test_a_radio_call_on_the_intercom_is_offered_to_be_sent():
    pm = started()
    out = pm.observe(IntercomHeard(t=1.0, text="Seattle Tower, Alaska 123, ready for departure runway 16R"))
    assert said(out) == ["That sounded like a radio call. I haven't sent it; want me to?"]
    assert not [o for o in out if isinstance(o, Transcript)]  # never sent unasked
    out = pm.observe(IntercomHeard(t=4.0, text="yes"))
    assert out[0] == Transcript(t=4.0, text="Seattle Tower, Alaska 123, ready for departure runway 16R", source="copilot")


def test_without_the_aircraft_reporting_it_the_copilot_says_it_sent_it():
    pm = PilotMonitoring(profiles=PROFILES)
    pm.observe(own(0.5))  # no AircraftSystems yet (an add-on, or an older recording)
    assert said(pm.observe(IntercomHeard(t=1.0, text="landing lights on"))) == ["Landing lights on."]


def test_the_log_shows_the_intercom():
    assert radio_line(IntercomHeard(t=1.0, text="gear down"))["kind"] == "intercom"
    line = radio_line(CrewSpeech(t=2.0, text="Unable, speed 250.", kind="refused"))
    assert line["kind"] == "crew" and line["level"] == "warn"


# --- the sim ---------------------------------------------------------------------------------------------------------------


def test_the_bridge_sends_the_copilots_events_and_reads_the_switches():
    fake = FakeSimConnect()
    fast = LiveConfig(ownship_hz=50, traffic_interval_s=0, nearest_airport_interval_s=0, retry_max_s=0.05,
                      intercom_input="joystick:0:button:4")

    async def main():
        source = SimConnectSource(fast, dll_factory=lambda: fake)
        await asyncio.wait_for(source.start(), 3)
        await source.send(SendSimEvent(name="GEAR_DOWN"))
        await source.send(SendSimEvent(name="FLAPS_2"))
        await source.send(SendSimEvent(name="GEAR_DOWN"))
        await source.send(SetSimVar(name="L:ADDON_LDG", value=2.0))
        systems = None
        async for event in source.events():
            if isinstance(event, AircraftSystems):
                systems = event
            if systems is not None and len(fake.transmitted) >= 3 and fake.written:
                break
        await source.stop()
        return systems

    systems = asyncio.run(main())
    assert [(name, data) for name, data, *_ in fake.transmitted] == [("GEAR_DOWN", 0), ("FLAPS_2", 0), ("GEAR_DOWN", 0)]
    assert sorted(n for e, n in fake.client_events.items() if e >= 100) == ["FLAPS_2", "GEAR_DOWN"]  # mapped once each
    assert fake.written == [("L:ADDON_LDG", struct.pack("<d", 2.0))]
    assert systems.flaps_positions == 4 and systems.light_beacon and not systems.light_landing
    assert ("joystick:0:button:4", 32, 33) in fake.input_maps  # the intercom button


# --- voices and the intercom key --------------------------------------------------------------------------------------------


def test_the_copilots_voice_by_sex():
    women, men = crew_choices("female", 904), crew_choices("male", 904)
    assert len(women) == len(men) == 8 and not set(women) & set(men)
    assert all(sex_of(s) == "F" for s in women) and all(sex_of(s) == "M" for s in men)
    assert crew_speaker("male", 9, 904) == men[1]  # wraps
    assert crew_speaker("female", 0, 1) is None  # a single-speaker voice has no choice


def test_the_intercom_sounds_dry_and_waits_for_the_radio():
    t = np.arange(16000) / 16000
    audio = intercom_effect(np.sin(2 * np.pi * 440 * t).astype(np.float32), 16000)
    assert len(audio) == 16000 and 0.7 < np.max(np.abs(audio)) <= 0.81  # no squelch tail, no hiss added

    player = AudioPlayer.__new__(AudioPlayer)  # the queue only; nothing played
    import queue as q
    player._queue, player._started = q.Queue(), True
    player.start = lambda: None
    crew1, crew2, atc = (Clip(audio, 16000, "intercom"), Clip(audio, 16000, "intercom"), Clip(audio, 16000, "atc"))
    for clip in (crew1, crew2, atc):
        player.play(clip)
    assert [player._queue.get_nowait() for _ in range(3)] == [atc, crew1, crew2]


def test_the_intercom_key_goes_to_the_copilot_not_atc():
    async def main():
        bus = EventBus()
        transcriber = FakeTranscriber("Flaps two")
        clock = iter(range(100))
        service = VoiceService(bus, lambda: float(next(clock)), transcriber, FakeCapture(1.0), tail_s=0.0)
        heard, radio = bus.subscribe(IntercomHeard), bus.subscribe(Transcript)
        task = asyncio.create_task(service.run())
        await asyncio.sleep(0)
        service.press(INTERCOM)
        await asyncio.sleep(0.01)
        service.press()  # the radio key while the intercom is held: one microphone, ignored
        service.release()
        service.release(INTERCOM)
        await asyncio.sleep(0.05)
        bus.close()
        await task
        return [e async for e in heard], [e async for e in radio], transcriber.calls

    heard, radio, calls = asyncio.run(main())
    assert [h.text for h in heard] == ["Flaps two"] and radio == []
    assert "Gear down" in calls[0][1] and "Runway 34L" not in calls[0][1]  # primed with crew words, not ATC's


# --- questions and the language model ---------------------------------------------------------------------------


def test_common_questions_are_answered_from_the_data():
    pm = started()
    pm.observe(own(2.0, fuel_lb=41300.0, fuel_flow_pph=2500.0))
    pm.observe(AircraftSystems(t=2.0, flaps_positions=4, engines_running=2))
    assert said(pm.observe(IntercomHeard(t=3.0, text="How much fuel do we have on board?"))) == \
        ["We've got 41,300 pounds, about 8 hours 16 minutes at this burn."]
    from localtc.sim_api import AtcTransmission
    pm.observe(AtcTransmission(t=4.0, station="Zurich Approach", frequency_mhz=120.755, text="EDW87, fly heading 150."))
    assert said(pm.observe(IntercomHeard(t=5.0, text="what did ATC say?"))) == \
        ["Zurich Approach said: EDW87, fly heading 150."]
    assert said(pm.observe(IntercomHeard(t=6.0, text="Go ahead and set the altimeter to 30.10"))) == \
        ["Altimeter 30.10 set on my side."]  # a command, on the copilot's own altimeter


class FakeBackend:
    model = "fake"

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def complete(self, request, *, timeout_s):
        from localtc.atc_core.llm.backend import LlmReply
        self.asked.append(request)
        return LlmReply(self.answer, 50.0)


def test_the_model_answers_from_the_facts_and_never_makes_numbers_up():
    from localtc.crew.model import CrewModel

    pm = started()
    pm.model = CrewModel(FakeBackend('{"kind": "reply", "reply": "Smooth so far, nothing on the radar."}'))
    out = pm.observe(IntercomHeard(t=2.0, text="how's the ride looking"))
    assert said(out) == ["Smooth so far, nothing on the radar."]
    assert pm.model.backend.asked[0].purpose == "crew" and "altitude: 5,000 feet" in pm.model.backend.asked[0].prompt
    pm.model = CrewModel(FakeBackend('{"kind": "reply", "reply": "We land in 42 minutes."}'))
    assert said(pm.observe(IntercomHeard(t=3.0, text="anything interesting coming up"))) == ["Say again?"]  # 42 isn't known


def test_a_command_in_other_words_waits_for_confirm():
    from localtc.crew.model import CrewModel

    pm = started()
    pm.model = CrewModel(FakeBackend('{"kind": "command", "action": "gear", "value": "down", "reply": "Gear down."}'))
    out = pm.observe(IntercomHeard(t=2.0, text="drop the wheels for me would you"))
    assert said(out) == ["Gear down, confirm?"] and not [o for o in out if isinstance(o, SendSimEvent)]
    assert SendSimEvent(name="GEAR_DOWN") in pm.observe(IntercomHeard(t=4.0, text="affirm"))
    pm.model = CrewModel(FakeBackend('{"kind": "command", "action": "heading", "value": "310", "reply": "Heading 310."}'))
    assert "Say again?" in said(pm.observe(IntercomHeard(t=6.0, text="bring us round a bit")))  # 310 was not said
    pm.model = CrewModel(FakeBackend("{}"), mode="off")
    assert "Say again?" in said(pm.observe(IntercomHeard(t=8.0, text="suck the wheels up")))  # no model: no guess


def test_a_shortcut_on_the_intercom_key_sends_nothing():
    from localtc.stt.ptt import KeyboardPtt

    calls = []
    ptt = KeyboardPtt.__new__(KeyboardPtt)
    ptt.key, ptt.key_name, ptt.what, ptt._held, ptt._listener = "ALT", "alt_l", "x", False, None
    ptt._on_down, ptt._on_up, ptt._on_cancel = (lambda: calls.append("down")), (lambda: calls.append("up")), \
        (lambda: calls.append("cancel"))
    ptt._modifier, ptt._combo = True, False
    ptt._matches = lambda key: key == "ALT"
    ptt._press("ALT"), ptt._press("TAB"), ptt._release("TAB"), ptt._release("ALT")
    ptt._press("ALT"), ptt._release("ALT")
    assert calls == ["down", "cancel", "down", "up"]


def test_mostly_llm_lets_the_model_word_and_read_beyond_short_commands():
    from localtc.crew.model import CrewModel, effective_mode

    class Rich(FakeBackend):
        rich = True

    assert effective_mode("auto", Rich("{}")) == "mostly_llm" and effective_mode("auto", FakeBackend("{}")) == "scripted"
    pm = started()
    pm.model = CrewModel(Rich('{"kind": "reply", "reply": "Check."}'))
    # A plain statement goes to the model (it says "Check."), a short command is done at once, no model needed.
    assert said(pm.observe(IntercomHeard(t=2.0, text="engine two is started and stable"))) == ["Check."]
    asked = len(pm.model.backend.asked)
    out = pm.observe(IntercomHeard(t=3.0, text="gear down"))
    assert SendSimEvent(name="GEAR_DOWN") in out and len(pm.model.backend.asked) == asked
    # The conversation is remembered for the next question.
    pm.observe(IntercomHeard(t=4.0, text="what did I just ask you"))
    assert "engine two is started and stable" in pm.model.backend.asked[-1].prompt


def test_check_and_set_are_a_yes_to_a_question():
    from localtc.crew.model import CrewModel

    pm = started()
    pm.model = CrewModel(FakeBackend('{"kind": "command", "action": "gear", "value": "down", "reply": "Gear down."}'))
    pm.observe(IntercomHeard(t=2.0, text="drop the wheels for me would you"))
    assert SendSimEvent(name="GEAR_DOWN") in pm.observe(IntercomHeard(t=3.0, text="yep, check"))


def test_a_reworded_call_keeps_every_number_and_name():
    from localtc.crew.model import check_reworded

    assert check_reworded('{"reply": "Squawk 2711 and 6,000 are in."}', "Squawk 2711, initial 6,000 set.")[0]
    assert check_reworded('{"reply": "Squawk set, 6,000 in."}', "Squawk 2711, initial 6,000 set.")[0] is None
    assert check_reworded('{"reply": "We\'re on the departure."}', "We're planned on the RADYR2.")[0] is None
