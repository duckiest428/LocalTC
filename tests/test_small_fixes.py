"""Small behaviours: an "a" in a taxi readback, Synaptic's blank checklist values, a yes at the end of muddled words,
a checklist named with "approach" in it, deboarding asked for in Whisper's spelling, the voices of a region shared."""

from localtc.atc_core.readback import extract
from localtc.atc_core.readback.normalize import normalize
from localtc.crew import cabin, checklist_db, speech_acts
from localtc.tts.persona import Casting, persona_for


def test_an_a_between_taxiways_is_speech_not_taxiway_a():
    heard = normalize("Taxi runway 32 via K and a H three, hold short runway 28. Swiss 1732.")
    assert extract.taxi_routes(heard, ("K", "H3")) == [("K", "H3")]
    assert extract.taxi_routes(normalize("taxi via K and alpha H3"), ("K", "A", "H3")) == [("K", "A", "H3")]
    assert extract.taxi_routes(normalize("via K, A, H3"), ("K", "A", "H3")) == [("K", "A", "H3")]


def test_synaptic_blanks_are_the_flights_values_or_left_out():
    assert checklist_db._blank("Altimeters", "() Set and\ncross-checked") == "{altimeter} Set and cross-checked"
    assert checklist_db._blank("FLAP", "() indicating") == "{flaps} indicating"
    assert checklist_db._blank("Minima", "() set") == "set"


def test_a_yes_at_the_end_of_muddled_words():
    assert speech_acts.read("Yeah, sure, how about. Yeah, we can do it.", 0.7).act == "answer"
    assert speech_acts.read("no. we can do it later", 0.7).act != "answer"


def test_deboarding_in_whispers_spelling_is_asked_for():
    for said in ("Why? Can we get the deborings started please?", "please get deboarding please"):
        low = cabin.DEBOARDING_HEARD.sub("deboarding", said.lower())
        m = cabin.START_BOARDING.search(low)
        assert m is not None and any(m.groups())
    assert cabin.START_BOARDING.search("has boarding started?") is None or cabin.ASKS_ABOUT.match("has boarding started?")


def test_a_controller_shares_a_voice_with_another_crew_not_with_the_purser():
    pool = ["a", "b"]
    casting = Casting()
    purser = casting.cast(persona_for("cabin Jonas", "cabin"), pool)
    other = casting.cast(persona_for("chatter Edelweiss 48A", "chatter"), pool)
    ground = casting.cast(persona_for("Zurich Ground #26", "atc"), pool)
    assert purser != other and ground != purser and ground == other


MIAMI = ("RWYS 8L, 26R CLSD, RWYS 12, 30 CLSD. TWY N CLSD BTN TWY, Z AND TWY M2, TWY P CLSD BTN TWY, Z AND TWY Q2, "
         "TWY Q CLSD BTN TWY Q1 AND TWY Q2 FOR AIRCRAFT ADG GROUP 6. TWY N LGTS BTN TWY, JJ AND TWY N12 OTS.")


def test_a_stretch_closed_with_its_ends_after_the_word_is_a_stretch():
    from localtc.atc_core.atis.operations import read_notices

    taxiways = [(s, d) for kind, s, d in read_notices(MIAMI) if kind == "taxiway_closed"]
    assert taxiways == [("N", "Z/M2"), ("P", "Z/Q2")]  # not all of N, P and Z; Q's only for the biggest aircraft
    assert [(s, d) for k, s, d in read_notices("TWY T CLSD TWY H CLSD") if k == "taxiway_closed"] == [("T", ""), ("H", "")]
    assert not [n for n in read_notices("TWY T CLSD FOR ACFT WINGSPAN MORE THAN 214 FT") if n[0] == "taxiway_closed"]


def test_the_atis_says_every_runway_of_a_list_and_short():
    from localtc.atc_core.atis.notes import say

    assert "two six right" in say("RWYS 8L, 26R CLSD")[1].lower()
    assert "short" in say("READ BACK ALL HOLD SHRT INSTRUCTIONS")[1]


def test_a_long_radio_line_is_cut_at_its_commas_for_the_voice():
    from localtc.tts.service import CLAUSE_CHARS, clauses

    line = "Air Portugal 224, runway 08R, taxi via 22, Q, Q10, T, V, S, Y, Y2, HH, Y1, JJ, M, M1, hold short runway 27."
    pieces = clauses(line)
    assert len(pieces) > 1 and " ".join(pieces) == line and all(len(p) >= CLAUSE_CHARS / 2 for p in pieces)
    assert clauses("Air Portugal 224, roger.") == ["Air Portugal 224, roger."]


def test_a_short_line_said_before_isnt_made_again():
    import numpy as np

    from localtc.tts.providers import VoiceChain
    from localtc.tts.synth import Speech

    class Voices:
        id, name, local, calls = "v", "V", True, 0

        def ready(self):
            return ""

        def synthesize(self, text, persona):
            Voices.calls += 1
            return Speech(np.full(2400, 0.2, dtype=np.float32), 24000, 90.0, "v", "x")

    chain, who = VoiceChain([Voices()]), persona_for("", "copilot")
    first, again = chain.speak("Gear up.", who), chain.speak("Gear up.", who)
    assert Voices.calls == 1 and again.latency_ms == 0.0 and np.array_equal(first.audio, again.audio)
    chain.speak("Gear up.", persona_for("Boston Tower", "atc"))
    assert Voices.calls == 2  # someone else saying it is someone else's voice


def test_speeding_up_boarding_said_to_the_cabin_goes_to_the_ramp():
    for said in ("hello can we speed up boarding please?", "can you get the passengers on faster"):
        assert cabin.SPEED_UP.search(said)
    assert cabin.SPEED_UP.search("no rush at all") is None


def test_an_announcement_is_a_few_words_in_the_log_and_one_line():
    from localtc.radiolog import radio_line
    from localtc.sim_api.events import REPEATED, CabinSpeech

    said = CabinSpeech(t=1.0, text="All right, everybody, this is the bit we practise in the mirror.", speaker="Jamie", summary="Safety briefing")
    assert radio_line(said)["summary"] == "Safety briefing" and "mirror" in radio_line(said)["text"]
    assert radio_line(CabinSpeech(t=2.0, text="Senhoras e senhores", speaker="Jamie", language="pt", summary=REPEATED)) is None


def test_the_a330s_baro_is_pulled_for_standard_and_pushed_back():
    from localtc.crew.actions import PILOT_SIDE, Cockpit, plan
    from localtc.crew.commands import Command
    from localtc.crew.profiles import load_all
    from localtc.sim_api.commands import SendSimEvent

    profile = next(p for p in load_all() if "A339" in p.match)
    assert "parking_brake" not in profile.unread and profile.reads["parking_brake"].startswith("L:A32NX_PARK_BRAKE")
    cockpit = Cockpit(own=None, profile=profile)
    names = [w.name for w in plan(Command("altimeter", "29.92"), cockpit).writes if isinstance(w, SendSimEvent)]
    assert names[:2] == ["A32NX.FCU_EFIS_L_BARO_PULL", "A32NX.FCU_EFIS_R_BARO_PULL"] and cockpit.baro_pulled
    names = [w.name for w in plan(Command("altimeter", "30.09"), cockpit).writes if isinstance(w, SendSimEvent)]
    assert names[:2] == ["A32NX.FCU_EFIS_L_BARO_PUSH", "A32NX.FCU_EFIS_R_BARO_PUSH"]
    assert "transponder" in PILOT_SIDE


def test_a_runway_on_the_route_with_no_hold_line_in_the_data_is_still_come_to():
    from types import SimpleNamespace as N

    from localtc.atc_core.engine import AtcEngine

    runway = N(name="27/09", distance_to=lambda xy: 60.0)
    far = N(name="30/12", distance_to=lambda xy: 900.0)
    airport = N(xy=lambda a, b: (0.0, 0.0), runways=(runway, far), hold_shorts=(N(runway=far, xy=(900.0, 0.0)),))
    ctx = N(hold_short=N(runway=far), hold_short_distance_m=900.0, airport=airport, on_runway=False)
    engine = N(tracker=N(context=ctx), _crossings=("27/09", "30/12"), _crossed=set())
    assert AtcEngine._crossing_near(engine, N(lat=0.0, lon=0.0)) == ("27/09", 60.0)
    engine._crossed.add("27/09")
    assert AtcEngine._crossing_near(engine, N(lat=0.0, lon=0.0)) is None


def test_asking_to_cross_a_runway_that_cant_be_one_is_still_asking_to_cross():
    from localtc.atc_core.readback import GrammarInterpreter, InterpretContext

    heard = GrammarInterpreter().interpret("TAP 224 request cross runway 224", None, InterpretContext(phase="TAXI_OUT"))
    assert heard.intent == "request_crossing"


def test_visibility_as_the_place_reports_it():
    from localtc.crew.watches import visibility_words

    assert visibility_words(2.49, "LPPT") == "4,000 metres" and visibility_words(2.5, "KMIA") == "2.5 miles"
