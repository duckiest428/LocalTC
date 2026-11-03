"""The flight's own names put right in what Whisper heard, and only where it can only be them."""

import pytest

from localtc.sim_bridge.traffic_speed import TrafficSpeeds
from localtc.sim_api import TrafficTarget
from localtc.stt.vocabulary import VocabularyHints, match_names

HINTS = VocabularyHints(callsign_spoken="Frontier 1469", procedures=("RADYR2", "LUCKI1"), fixes=("LYNDI", "LUCKI", "BESSY"))


@pytest.mark.parametrize("heard, written", [
    ("via the Raider 2 Departure.", "via the RADYR2 Departure."),
    ("Descend via the LU-CK-1 rifle.", "Descend via the LUCKI1 rifle."),
    ("Hold that L-Y-N-D-I as depicted.", "Hold that LYNDI as depicted."),
    ("Direct Lindy, Frontier 1469", "Direct LYNDI, Frontier 1469"),
    ("Hold at LYNDI. Front to 1469.", "Hold at LYNDI. Frontier 1469."),
    ("climbing 13,000 from to 1469", "climbing 13,000 Frontier 1469"),
    ("Commentate 17,000 for Tier 1469.", "Commentate 17,000 Frontier 1469."),
    ("Tower for 201469 holding short.", "Tower Frontier 1469 holding short."),
    ("startup gate Echo 10. Mike.", "startup gate E10. M."),
    ("We have information. Tango.", "We have information T."),
    ("via Charlie6, Charlie, Bravo5, Bravo Delta. Frontier 1469.", "via C6, C, B5, B, D. Frontier 1469."),
])
def test_what_can_only_be_the_flights_own_name(heard, written):
    assert match_names(heard, HINTS) == written


@pytest.mark.parametrize("heard", [
    "via the Rider 3 departure",  # the pilot's wrong procedure: the readback's to catch
    "Say again Frontier 1496",  # ... and wrong number
    "Delta 1469 with you",  # another airline
    "direct Boston",  # not a fix of this flight's
])
def test_a_pilots_mistake_stays_a_mistake(heard):
    assert match_names(heard, HINTS) == heard


def test_the_callsigns_own_word_stays_a_word():
    assert match_names("Delta 12 request taxi", VocabularyHints(callsign_spoken="Delta 12")) == "Delta 12 request taxi"


def target(lat, gs=0.0, ground=False):
    return TrafficTarget(object_id=7, atc_id="SWA1", airline="SWA", flight_number="1", atc_model="737", lat=lat, lon=-115.0,
                         alt_ft=2100.0, hdg_true=25.0, gs_kt=gs, on_ground=ground)


def test_traffic_the_bridge_moves_has_its_real_speed_and_isnt_flying_on_the_ground():
    speeds = TrafficSpeeds()
    speeds.apply((target(36.0),), 0.0)
    [rolled] = speeds.apply((target(36.0 + 15.0 / 60 / 3600 * 3),), 3.0)  # 15 knots for 3 s, the sim saying 0 and airborne
    assert abs(rolled.gs_kt - 15.0) < 0.5 and rolled.on_ground
    [flying] = speeds.apply((target(rolled.lat + 200.0 / 60 / 3600 * 3),), 6.0)
    assert flying.gs_kt > 150 and not flying.on_ground
    [own_speed] = speeds.apply((target(36.1, gs=250.0),), 9.0)  # the sim's own number, when it has one
    assert own_speed.gs_kt == 250.0 and not own_speed.on_ground
