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
