"""The details of an arrival: the airport's own stations, the gates asked for, the way off the runway, the copilot's
callouts in their moment and in more than one wording, and the traffic's rollouts."""

import random

from localtc.atc_core.airport.gates import requested_area
from localtc.atc_core.engine import _article, _stops
from localtc.atc_core.facilities import airport_facilities
from localtc.atc_core.readback.normalize import normalize
from localtc.crew import personalities
from localtc.crew.monitor import MOMENT_S, ROUTINE, Call
from localtc.sim_api import Airport, Frequency
from localtc.traffic.ground import Follower, _toward


def test_the_airports_own_stations_before_a_sector_or_a_neighbours():
    freqs = [Frequency(kind="approach", mhz=122.5, name="MINHAD"), Frequency(kind="approach", mhz=120.25, name="DUBAI ARRIVALS"),
             Frequency(kind="approach", mhz=124.45, name="DUBAI ARRIVALS NORTH"), Frequency(kind="tower", mhz=118.75, name="AIR NORTH"),
             Frequency(kind="tower", mhz=119.05, name="DUBAI"), Frequency(kind="ground", mhz=118.35, name="DUBAI")]
    airport = Airport(icao="OMDB", name="Dubai Intl", lat=25.25, lon=55.36, elev_ft=62, frequencies=freqs)
    stations = {f.controller: (f.station, f.mhz) for f in airport_facilities(airport, role="arrival")}
    assert stations["tower"] == ("Dubai Tower", 119.05)
    assert stations["approach"] == ("Dubai Approach", 120.25)


def test_a_gate_area_asked_for():
    asked = requested_area(normalize("Can we get a gate in the... either a Bravo gate, an Alpha gate, or a Hotel gate, "
                                     "but not the Sierra gates, please."))
    assert asked == (frozenset("BAH"), frozenset("S"))
    assert requested_area(normalize("request taxi to a gate")) is None  # "a gate": the article
    assert requested_area(normalize("request gate Alpha 3")) is None  # a gate by its name


def test_a_callout_of_the_moment_is_said_in_it_or_not_at_all():
    late = Call("rollout:0", "Seventy knots.", ROUTINE, 100.0, moment=True)
    assert late.expires == 100.0 + MOMENT_S


def test_the_routine_calls_are_said_more_than_one_way_by_everyone():
    rng = random.Random(1)
    said = {personalities.word(personalities.NEUTRAL, "prompt:before_start", "Before start checklist when you're ready.",
                               priority=2, safety=3, rng=rng, sterile=False)[0] for _ in range(20)}
    assert len(said) > 2 and all("efore start checklist" in s for s in said)
    kept = personalities.word(personalities.NEUTRAL, "minimums:0", "Minimums.", priority=2, safety=3, rng=rng,
                              sterile=False)
    assert kept == ("Minimums.", False)  # the callouts the pilot acts on keep their words
    assert all(p.brief for p in personalities.PERSONALITIES)


def test_a_rollout_brakes_to_make_its_exit_not_to_a_crawl():
    start = (25.25, 55.37)
    exit_at = _toward(start, 120, 2000)
    off = _toward(exit_at, 210, 200)
    follower = Follower([start, exit_at, off, _toward(off, 300, 300)], gs_kt=130, exit_index=1)
    lat, lon, seconds = *start, 0
    while follower.index <= 1 and seconds < 300:
        lat, lon, _, _ = follower.step(lat, lon)
        seconds += 1
    assert seconds < 80  # two kilometres at 8 knots took three minutes


def test_atcs_words_end_right():
    assert _stops("Emirates 230, radar contact, how do you read?.") == "Emirates 230, radar contact, how do you read?"
    assert _article("Boeing 777") == "a Boeing 777" and _article("Airbus A350") == "an Airbus A350"
