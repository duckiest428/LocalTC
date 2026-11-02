"""The cabin crew (crew/cabin.py): what they say through a flight, in whose voice and language, and how they answer
the pilot on the intercom."""

import asyncio

import numpy as np
import pytest

from localtc.crew.cabin import CabinCrew, CabinSettings, Facts, duration_in, fill, flight_words, words
from localtc.crew.cabin_data import airline_of, city_of, describe, exits, seats
from localtc.crew.cabin_lines import FOREIGN, LINES, PEOPLE, REPLIES
from localtc.dsp.cabin import applause, boarding_music, chime, interphone_effect, made_chime, made_music, pa_effect
from localtc.sim_api import (
    AircraftSystems,
    AircraftVars,
    CabinSound,
    CabinSpeech,
    IntercomHeard,
    OwnshipState,
    PhaseChanged,
    WeatherReport,
)

MUNICH = dict(destination="EDDM", dest_lat=48.354, dest_lon=11.786)
TORONTO = dict(origin="CYYZ", origin_lat=43.677, origin_lon=-79.630)


def facts(callsign="ACA846", aircraft="A20N", **kw) -> Facts:
    return Facts(callsign=callsign, aircraft=aircraft, planned_s=8 * 3600, cruise_ft=37000, **{**TORONTO, **MUNICH, **kw})


def own(t, **kw) -> OwnshipState:
    base = dict(lat=43.677, lon=-79.63, alt_msl_ft=570, alt_indicated_ft=570, alt_agl_ft=0, altimeter_inhg=29.92,
                hdg_mag=0, hdg_true=0, ias_kt=0, gs_kt=0, vs_fpm=0, on_ground=True, squawk="1200", xpdr_mode="on",
                com1_mhz=118.0, com2_mhz=118.0, zulu_s=50000.0, local_s=50000.0 - 4 * 3600, temperature_c=9.0)
    return OwnshipState(t=t, **{**base, **kw})


def run(cabin, events) -> list:
    out = []
    for ev in events:
        out += cabin.observe(ev)
    return out


def said(out, channel=None) -> list[CabinSpeech]:
    return [o for o in out if isinstance(o, CabinSpeech) and (channel is None or o.channel == channel)]


def at_the_gate(cabin, until=300.0, step=5.0) -> list:
    return run(cabin, [AircraftSystems(t=0, seatbelt=True)] + [own(t) for t in np.arange(0, until, step)])


def flown(cabin) -> list:
    """A whole flight: boarding, the push, the taxi, a climb, the cruise, the descent, a soft landing, the gate."""
    out = run(cabin, [AircraftVars(t=0, values={"INTERACTIVE POINT OPEN:0": 1.0})])
    out += at_the_gate(cabin)
    out += run(cabin, [AircraftSystems(t=300, seatbelt=True, light_beacon=True, engines_running=2)])
    out += run(cabin, [own(t, gs_kt=12 if t > 500 else 0, engine_running=True) for t in range(300, 1000, 5)])
    out += run(cabin, [own(t, on_ground=False, alt_agl_ft=min(37000, (t - 1000) * 40), alt_msl_ft=min(37000, (t - 1000) * 40) + 500,
                           gs_kt=420, vs_fpm=2000, engine_running=True, lat=45.0) for t in range(1000, 2000, 10)])
    out += run(cabin, [AircraftSystems(t=2000, seatbelt=False, engines_running=2)])
    out += run(cabin, [own(t, on_ground=False, alt_agl_ft=37000, alt_msl_ft=37000, gs_kt=480, engine_running=True, lat=50.0)
                       for t in range(2000, 2900, 10)])
    out += run(cabin, [WeatherReport(t=2900, icao="EDDM", raw="EDDM 011450Z 24008KT 9999 -RA BKN030 12/08 Q1012",
                                     temperature_c=12.0), PhaseChanged(t=2900, previous="CRUISE", phase="ARRIVAL")])
    out += run(cabin, [own(t, on_ground=False, alt_agl_ft=max(1500, 37000 - (t - 2900) * 45) - 1500,
                           alt_msl_ft=max(1500, 37000 - (t - 2900) * 45), gs_kt=300, vs_fpm=-1800, engine_running=True,
                           lat=48.2, lon=11.6) for t in range(2900, 3800, 10)])
    out += run(cabin, [AircraftSystems(t=3800, seatbelt=True, engines_running=2)])
    out += run(cabin, [own(t, on_ground=False, alt_agl_ft=40, alt_msl_ft=1540, gs_kt=140, vs_fpm=-120, engine_running=True,
                           lat=48.35, lon=11.78, local_s=80000.0) for t in range(3800, 3900, 10)])
    out += run(cabin, [own(t, gs_kt=60 if t < 3930 else 15, on_runway=t < 3930, engine_running=True, lat=48.354,
                           lon=11.786, local_s=80000.0) for t in range(3900, 4200, 5)])
    out += run(cabin, [AircraftSystems(t=4200, seatbelt=False, engines_running=0)])
    out += run(cabin, [own(t, parking_brake=True, lat=48.354, lon=11.786, local_s=80000.0) for t in range(4200, 4600, 5)])
    return out


def test_a_flight_goes_through_every_moment_in_order():
    cabin = CabinCrew(CabinSettings(crew="claire", captain="Reyes"), lambda: facts(), seed=3)
    out = flown(cabin)
    order = ["music", "boarding", "boarded", "doors_arm", "welcome", "safety", "secure_takeoff", "seatbelt_off",
             "service_call", "descent", "landing", "secure_landing", "touchdown", "after_landing", "doors_disarm",
             "goodbye", "deboarded"]
    assert all(k in cabin.done for k in order), set(order) - cabin.done
    times = [cabin._at[k] for k in order]
    assert times == sorted(times)
    english = [s.text for s in said(out) if not s.language]
    assert any("welcome aboard Air Canada flight 846 to Munich" in t for t in english)
    welcome = next(t for t in english if "on behalf of" in t)
    assert "Captain Reyes" in welcome and "Aeroplan" in welcome and "eight hours" in welcome and "Claire" in welcome
    assert any("eight emergency exits" in t for t in english)  # an A320neo's
    landed = next(t for t in english if t.startswith("Ladies and gentlemen, welcome to Munich"))
    assert "The local time is 10:13 pm" in landed and "9 degrees with some rain" in landed  # the OAT on the ground
    descent = next(t for t in english if "begun our descent into Munich" in t)
    assert "12 degrees with some rain" in descent  # the METAR's
    assert any(isinstance(o, CabinSound) and o.sound == "applause" for o in out) == ("applause" in cabin.done)


def test_a_bilingual_airline_announces_in_both_and_english_only_when_asked():
    out = flown(CabinCrew(CabinSettings(crew="claire"), lambda: facts(), seed=3))
    french = [s for s in said(out, "pa") if s.language == "fr"]
    assert french and all(s.locale == "en-CA" for s in french)
    assert any("Munich" in s.text and "descente" in s.text for s in french)
    lufthansa = flown(CabinCrew(CabinSettings(english_too=False), lambda: facts("DLH400"), seed=3))
    assert said(lufthansa, "pa") and all(s.language == "de" for s in said(lufthansa, "pa"))
    assert all(not s.language for s in said(lufthansa, "interphone"))  # to the flight deck: English


def test_each_announcement_chimes_once_before_its_first_language():
    out = flown(CabinCrew(CabinSettings(), lambda: facts("DLH400"), seed=3))
    welcome = [s for s in said(out, "pa") if "Lufthansa" in s.text and ("Flugzeit" in s.text or "flight time" in s.text
                                                                          or "tailwind" in s.text or "Flight time" in s.text)]
    assert welcome[0].chime == "pa" and all(not s.chime for s in welcome[1:])
    assert said(out, "interphone")[0].chime == "call"


def test_a_crew_of_another_country_has_its_names_and_its_english():
    cabin = CabinCrew(CabinSettings(crew="claire"), lambda: facts("AFR22"))
    person, name, english, sex = cabin.crew()
    assert english == "fr-FR" and sex == "F" and name in ("Camille", "Chloé", "Élodie", "Manon", "Julie", "Céline")
    assert CabinCrew(CabinSettings(crew="claire"), lambda: facts("ACA1")).crew()[1] == "Claire"


def test_a_general_aviation_flight_has_no_cabin_crew():
    cabin = CabinCrew(CabinSettings(), lambda: facts("N172SP", aircraft="C172"))
    assert not cabin.active
    assert at_the_gate(cabin) == []
    assert not cabin.claims("cabin, a coffee please", 10.0)


def test_seatbelt_sign_chimes_and_turbulence_is_announced():
    cabin = CabinCrew(CabinSettings(), lambda: facts("UAL1"), seed=2)
    flown_part = at_the_gate(cabin)
    cabin.observe(own(400, on_ground=False, alt_agl_ft=30000, alt_msl_ft=30000, gs_kt=450))
    out = cabin.observe(AircraftSystems(t=401, seatbelt=False))
    assert any(isinstance(o, CabinSound) and o.detail == "seatbelt" for o in out)
    assert any("seatbelt sign" in s.text for s in said(out))
    out = cabin.observe(AircraftSystems(t=900, seatbelt=True))
    assert any("back on" in s.text or "switched on" in s.text for s in said(out))
    assert flown_part is not None


def test_an_aircraft_without_a_seatbelt_sign_still_gets_moving_about():
    cabin = CabinCrew(CabinSettings(), lambda: facts("SWA12", aircraft="B738"))
    run(cabin, [own(t, on_ground=False, alt_agl_ft=12000, alt_msl_ft=12000, gs_kt=300, vs_fpm=1500) for t in range(0, 300, 10)])
    assert "seatbelt_off" in cabin.done


def test_the_pilot_orders_a_coffee_and_it_arrives():
    cabin = CabinCrew(CabinSettings(), lambda: facts(), seed=5)
    at_the_gate(cabin, until=20)
    assert cabin.claims("cabin, could I get a coffee, white with two sugars please", 30.0)
    out = cabin.heard(IntercomHeard(t=30.0, text="cabin, could I get a coffee, white with two sugars please"))
    assert "coffee, white two sugars" in said(out)[0].text
    later = run(cabin, [own(t) for t in range(30, 400, 5)])
    delivered = [s for s in said(later) if s.channel == "deck"]
    assert delivered and "hot" in delivered[0].text


def test_the_answer_to_the_service_call_is_the_cabins():
    cabin = CabinCrew(CabinSettings(), lambda: facts(), seed=5)
    cabin.observe(own(0))
    cabin._asking = ("drink", 100.0)
    assert cabin.claims("a tea would be lovely", 110.0)
    assert cabin.claims("no thanks, we're good", 110.0)
    assert not cabin.claims("flaps one", 110.0)
    assert not cabin.claims("a tea would be lovely", 200.0)  # the question long gone: the copilot's


@pytest.mark.parametrize("text,key", [
    ("cabin crew, how many passengers do we have?", "count"),
    ("purser, how is everything back there?", "status"),
    ("cabin, prepare the cabin for landing", "prepare_landing"),
    ("cabin, we've got a 20 minute delay waiting for our slot", "delay"),
    ("cabin, tell the passengers we're number two for departure", "relay"),
    ("cabin, expect some turbulence in about five minutes", "turbulence"),
    ("cabin, thanks", "thanks"),
    ("cabin, what's the weather on Mars", "unclear"),
])
def test_the_pilot_talks_to_the_cabin(text, key):
    cabin = CabinCrew(CabinSettings(), lambda: facts(), seed=5)
    cabin.observe(own(0))
    assert cabin.claims(text, 10.0)
    out = cabin.heard(IntercomHeard(t=10.0, text=text))
    answer = said(out)[0].text
    options = [fill(o, {**cabin._values()[0], "drink": "", "verdict": ""}) for o in REPLIES[key]]
    assert answer in options


def test_a_relayed_message_and_a_delay_are_announced():
    cabin = CabinCrew(CabinSettings(), lambda: facts(), seed=5)
    cabin.observe(own(0))
    cabin.heard(IntercomHeard(t=10.0, text="cabin, tell the passengers we're number two for departure"))
    cabin.heard(IntercomHeard(t=11.0, text="cabin, we have a 20 minute delay for de-icing"))
    out = run(cabin, [own(t) for t in range(12, 30)])
    texts = [s.text for s in said(out, "pa")]
    assert "Ladies and gentlemen, a message from the flight deck: We're number two for departure." in texts
    assert any("delay of about twenty minutes" in t for t in texts)


def test_the_captains_own_announcement_and_the_copilots_words_are_not_answered():
    cabin = CabinCrew(CabinSettings(), lambda: facts())
    cabin.observe(own(0))
    assert cabin.claims("Ladies and gentlemen, this is your captain speaking", 5.0)
    assert cabin.heard(IntercomHeard(t=5.0, text="Ladies and gentlemen, this is your captain speaking")) == []
    for text in ("flaps one", "gear down", "set the cabin altitude", "what's our fuel", "before start checklist"):
        assert not cabin.claims(text, 5.0), text


def test_the_crew_member_is_addressed_by_name():
    cabin = CabinCrew(CabinSettings(crew="jamie"), lambda: facts())
    cabin.observe(own(0))
    assert cabin.claims("Jamie, how many on board?", 5.0)


def test_off_switches_are_honoured():
    quiet = CabinSettings(announcements=False, boarding_music=False, interphone=False, chime=False, applause=False)
    out = flown(CabinCrew(quiet, lambda: facts(), seed=3))
    assert not [o for o in out if isinstance(o, (CabinSpeech, CabinSound))]


def test_every_line_fills_for_an_ordinary_flight():
    cabin = CabinCrew(CabinSettings(captain="Reyes"), lambda: facts(), seed=1)
    cabin.observe(own(0))
    values = cabin._values()[0] | {"local": "", "weather": "", "members": "", "members_bye": "", "cruise": "FL370"}
    for key, manners in LINES.items():
        for manner, options in manners.items():
            for option in options:
                assert fill(option, values) is not None, (key, manner, option)
    for lang, lines in FOREIGN.items():
        for key, template in lines.items():
            assert fill(template, values | {"ltime": duration_in(lang, 3600)}) is not None, (lang, key)
    assert {p.manner for p in PEOPLE} == {"polished", "cheerful", "warm", "brisk", "storyteller"}


def test_numbers_are_said_as_announcements_say_them():
    assert flight_words("123") == "one twenty-three"
    assert flight_words("1234") == "twelve thirty-four"
    assert flight_words("1005") == "ten oh five"
    assert flight_words("900") == "nine hundred"
    assert words(174) == "one hundred and seventy-four"
    assert duration_in("de", 80 * 60) == "1 Stunde 20 Minuten"
    assert duration_in("pl", 2 * 3600 + 22 * 60) == "2 godziny i 20 minut"
    assert duration_in("ru", 45 * 60) == "45 минут"


def test_the_airlines_and_the_destinations():
    assert airline_of("ACA123")[1].program == "Aeroplan"
    assert airline_of("SKW5512")[1].name == "Delta Connection"
    assert airline_of("N172SP")[1] is None
    assert seats("A20N") == 180 and seats("B38M") == 175 and seats("C172") == 0
    assert exits("B77W")[0] == 10
    assert city_of(48.354, 11.786).name == "Munich"
    assert city_of(51.470, -0.454).name == "London"  # Heathrow: the big city, not the town by the runway
    assert describe(city_of(43.677, -79.630)).startswith("Toronto, Canada's largest city")


def test_the_cabins_sounds():
    rate = 22050
    for kind in ("pa", "seatbelt", "call"):
        c = chime(rate, kind)
        assert 0.5 < len(c) / rate < 3.0 and abs(np.max(np.abs(c)) - 0.6) < 0.01
    made = made_music(rate, seconds=16, seed=4)
    assert len(made) == 16 * rate and np.max(np.abs(made)) <= 0.51
    assert np.array_equal(made, made_music(rate, seconds=16, seed=4))
    assert len(made_chime(rate, "pa")) / rate > 0.5
    clap = applause(rate, seconds=4, people=10, seed=1)
    assert len(clap) == 4 * rate and np.max(np.abs(clap)) <= 0.51
    voice = np.sin(np.linspace(0, 2000, rate)).astype(np.float32)
    assert np.max(np.abs(pa_effect(voice, rate))) <= 0.76 and np.max(np.abs(interphone_effect(voice, rate))) <= 0.81


def test_the_voice_says_each_line_and_plays_the_cabins_sounds():
    from localtc.bus import EventBus
    from localtc.tts.service import VoiceOut, sentences

    assert sentences("Ladies and gentlemen, welcome. Thank you. Please sit down now, everyone.") == [
        "Ladies and gentlemen, welcome. Thank you.", "Please sit down now, everyone."]

    class Synth:
        def synthesize(self, text, speaker=None):
            from localtc.tts.synth import Speech

            return Speech(np.zeros(2205, dtype=np.float32) + 0.1, 22050, 1.0)

    class Player:
        def __init__(self):
            self.clips = []

        def play(self, clip):
            self.clips.append(clip)
            clip.done.set()
            return clip

        def cut(self, kind):
            pass

    async def go():
        bus = EventBus()
        player = Player()
        voice = VoiceOut(bus, Synth(), player)
        task = asyncio.create_task(voice.run())
        await asyncio.sleep(0)
        bus.publish(CabinSpeech(t=1.0, text="Ladies and gentlemen, welcome aboard. Please sit down now, everyone.",
                                speaker="Claire", chime="pa"))
        bus.publish(CabinSound(t=2.0, sound="chime", detail="seatbelt"))
        for _ in range(50):
            await asyncio.sleep(0.01)
        task.cancel()
        return player.clips

    clips = asyncio.run(go())
    kinds = [c.kind for c in clips]
    assert kinds.count("chime") == 2 and kinds.count("pa") == 2
    assert kinds.index("chime") < kinds.index("pa")


def test_the_chime_and_the_music_are_the_real_recordings():
    rate = 22050
    pa, belt = chime(rate, "pa"), chime(rate, "seatbelt")
    assert 2.0 < len(pa) / rate < 2.4 and 1.0 < len(belt) / rate < 1.3  # the high-low pair, and the low note alone

    def pitch(audio):
        spectrum = np.abs(np.fft.rfft(audio[: rate // 4]))
        return np.argmax(spectrum) * rate / (rate // 4 * 2 // 2 * 2)

    assert pitch(pa) > pitch(belt)  # it starts on the high note; the seatbelt's is the low one
    music = boarding_music(rate)
    assert len(music) / rate > 300 and np.max(np.abs(music)) <= 0.51  # "Airport Lounge", five minutes


def test_no_clock_at_the_gate_nothing_happens_until_the_door_or_gsx_says():
    cabin = CabinCrew(CabinSettings(), lambda: facts("BAW1"))
    assert at_the_gate(cabin, until=1500) == []  # 25 minutes at a dark gate: not a word
    cabin = CabinCrew(CabinSettings(), lambda: facts("BAW1"))
    door = "INTERACTIVE POINT OPEN:0"
    run(cabin, [AircraftVars(t=0, values={door: 1.0})])
    out = run(cabin, [own(t) for t in range(0, 100, 5)])
    assert "boarding" in cabin.done and "boarded" not in cabin.done
    assert any(isinstance(o, CabinSound) and o.sound == "music" for o in out)
    run(cabin, [own(t) for t in range(100, 900, 5)])
    assert "boarded" not in cabin.done  # the jetway still on
    run(cabin, [AircraftVars(t=900, values={door: 0.0})])  # it's gone: boarding's over
    run(cabin, [own(t) for t in range(900, 1000, 5)])
    assert "boarded" in cabin.done and "doors_arm" in cabin.done and "welcome" in cabin.done
    assert "safety" not in cabin.done  # with the push
    run(cabin, [own(t, gs_kt=2.5) for t in range(1000, 1200, 5)])
    assert "safety" in cabin.done


def test_with_gsx_its_boarding_state_decides_not_the_door():
    cabin = CabinCrew(CabinSettings(), lambda: facts("BAW1"))
    gsx, door = "L:FSDT_GSX_BOARDING_STATE", "INTERACTIVE POINT OPEN:0"
    run(cabin, [AircraftVars(t=0, values={gsx: 1.0, door: 1.0})] + [own(t) for t in range(0, 100, 5)])
    assert "begun" not in cabin.done  # available, not begun
    run(cabin, [AircraftVars(t=100, values={gsx: 5.0})] + [own(t) for t in range(100, 200, 5)])
    assert "boarding" in cabin.done and "boarded" not in cabin.done
    run(cabin, [AircraftVars(t=200, values={gsx: 6.0})] + [own(t) for t in range(200, 210, 5)])
    assert "boarded" in cabin.done and "doors_arm" not in cabin.done  # the door's still open: at the beacon


def test_one_line_at_a_time_and_an_order_to_the_crew_in_one_language():
    cabin = CabinCrew(CabinSettings(), lambda: facts("DLH400"))
    lines = said(flown(cabin))
    assert all(a.t < b.t for a, b in zip(lines, lines[1:]))
    assert sum("Kabinenpersonal, Türen auf Automatik" in s.text or s.text.startswith("Cabin crew, doors to automatic")
               for s in lines) == 1


def test_the_aircraft_has_its_own_words():
    cabin = CabinCrew(CabinSettings(crew="sophie"), lambda: facts("SWR2027", aircraft="A220-300"))
    assert seats("A220-300") == 145 and exits("A220-300")[0] == 6
    lines = said(flown(cabin))
    assert any("Airbus A220" in s.text and "windows" in s.text for s in lines)
    assert any("exit row" in s.text.lower() for s in lines)
    assert any("smoke detectors" in s.text for s in lines)


def test_the_pilot_answers_after_their_call_and_the_copilot_keeps_its_commands():
    cabin = CabinCrew(CabinSettings(), lambda: facts())
    cabin.observe(own(0))
    cabin._called_t = 100.0
    assert cabin.claims("sounds good, thank you", 110.0)
    assert not cabin.claims("flaps one", 110.0)
    assert not cabin.claims("before start checklist", 110.0)
    assert not cabin.claims("sounds good, thank you", 200.0)


def test_whispers_spelling_of_their_name_reaches_them():
    cabin = CabinCrew(CabinSettings(crew="daniel"), lambda: facts("DLH400"))
    cabin.observe(own(0))
    name = cabin.crew()[1]
    assert cabin.claims(f"{name}, how many passengers?", 5.0)
    assert cabin.claims(f"{name}n, how many passengers?", 5.0)


def test_slewing_with_the_brake_on_is_no_flight():
    cabin = CabinCrew(CabinSettings(), lambda: facts())
    run(cabin, [own(t, on_ground=False, alt_agl_ft=200, alt_msl_ft=2000, parking_brake=True) for t in range(0, 60)])
    out = run(cabin, [own(t, parking_brake=True) for t in range(60, 200, 5)])
    assert not cabin._was_airborne and not said(out)


def test_the_model_words_the_answer_and_the_script_stands_in():
    class Result:
        def __init__(self, text):
            self.text, self.error, self.latency_ms = text, "", 5.0

    class Backend:
        model = "m"

        def __init__(self, text):
            self.text = text
            self.requests = []

        def complete(self, request, timeout_s=0):
            self.requests.append(request)
            return Result(self.text)

    cabin = CabinCrew(CabinSettings(), lambda: facts())
    cabin.observe(own(0))
    cabin.backend = Backend('{"reply": "All quiet back here, Captain."}')
    assert cabin.word("how's it going", "Everything's fine.", 1.0)[0] == "All quiet back here, Captain."
    assert "Aeroplan" in cabin.backend.requests[0].messages[-1][1]  # it knows the flight
    cabin.backend = Backend(None)
    words, records = cabin.word("how's it going", "Everything's fine.", 1.0)
    assert words == "Everything's fine." and records[0].outcome == "error"


def test_the_prompted_callsign_is_not_what_the_pilot_said():
    from localtc.stt.vocabulary import VocabularyHints, without_prompt

    hints = VocabularyHints(callsign="Swiss 2027")
    assert without_prompt("This is Swiss 2027.", hints) == ""
    assert without_prompt("This is Swiss 2027. Request pushback.", hints) == "Request pushback."
    assert without_prompt("Request pushback, Swiss 2027.", hints) == "Request pushback, Swiss 2027."
