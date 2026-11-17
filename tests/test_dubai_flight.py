"""Rome to Dubai in the A380: what the flight showed, each put right.

Fiumicino's real ATIS read as published (its wind, CAVOK, its notice); the arrival given for the runway in use (the filed
VUTE3E went to 12L and 12R only, with 30L landing); the base turn from the side the aircraft really is on; round
altitudes; the copilot's calls written as numbers; callsigns with letters in their number; FL390 heard as "39".
"""

import math

from localtc.atc_core import procedures, vectors
from localtc.atc_core.airlines import split
from localtc.atc_core.airport.geometry import AirportGeometry
from localtc.atc_core.atis import real
from localtc.atc_core.atis.notes import say
from localtc.atc_core.atis.observation import metar_sky
from localtc.atc_core.engine import round_altitude
from localtc.atc_core.readback.extract import cruise_altitudes
from localtc.atc_core.readback.normalize import normalize
from localtc.radiolog import written
from localtc.sim_api import Airport, ArrivalLeg, ArrivalProcedure, Runway, RunwayEnd

LIRF_ARRIVAL = ("ATIS E 2120Z. LIRF ARR E 2120Z. 16R. ZULU RWY IN USE 16R TL. 70 WIND TDZ 040/5KT VRB. BTN 010/AND 080/ "
                "CAVOK. T15 DP13 QNH 1014HPA. NOTICE FOR PILOTS. AERONAUTICAL INFORMATION. PUBLICATION SUPPLEMENT. 55 26 "
                "PHASE 2 IS ACTIVE")
LIRF_DEPARTURE = ("ATIS U 1620Z. LIRF DEP U 1620Z RWY IN USE 16R RCR AT. 0922Z. 5/5/5 WET WET WET TL 70 RWYS IN USE FOR ARR. "
                  "16L AND 16R WIND END 300/6KT CAVOK T17 DP14. QNH 1014HPA NOTICE FOR PILOTS AIP SUPPLEMENT. 55 26 PHASE 2 "
                  "IS ACTIVE WHEN READY CONTACT. FIUME RAMP 121.725")


def test_fiumicinos_atis_wind_cavok_and_notice_are_read():
    arrival = real.parse(LIRF_ARRIVAL, "arrival", source="atis.guru")
    assert arrival.landing == ("16R",)
    assert (arrival.wind.direction_mag, arrival.wind.speed_kt) == (40, 5)  # not the METAR's "calm"
    assert arrival.variable == (10, 80) and arrival.cavok
    assert arrival.remarks == ("NOTICE FOR PILOTS, AIP SUPPLEMENT 55/26 PHASE 2 IS ACTIVE",)
    shown, said = say(arrival.remarks[0])
    assert shown == "Notice for pilots, AIP supplement 55/26 phase 2 is active"
    assert "fifty-five slash twenty-six" in said
    departure = real.parse(LIRF_DEPARTURE, "departure", source="atis.guru")
    assert (departure.wind.direction_mag, departure.wind.speed_kt) == (300, 6)
    assert "WHEN READY CONTACT FIUME RAMP 121.725" in departure.remarks


def test_a_metars_cavok_and_clouds_are_read():
    assert metar_sky("METAR LIRF 162120Z VRB01KT CAVOK 15/13 Q1014 NOSIG") == ((), 5000)
    layers, clear = metar_sky("METAR KSEA 162053Z 18010KT 10SM FEW030 BKN080 12/08 A3001")
    assert [(layer.cover, layer.base_ft) for layer in layers] == [("FEW", 3000), ("BKN", 8000)] and clear is None
    assert metar_sky("") == (None, None)


def _leg(fix: str, transition: str, lat: float = 25.5, lon: float = 55.0) -> ArrivalLeg:
    return ArrivalLeg(fix=fix, lat=lat, lon=lon, transition=transition)


def test_the_arrival_for_the_runway_in_use_from_where_the_filed_one_starts():
    east = ArrivalProcedure(name="VUTE3E", legs=(_leg("VUTEB", "RW12L"), _leg("DB400", "RW12L"),
                                                 _leg("VUTEB", "RW12R"), _leg("DB400", "RW12R")))
    west = ArrivalProcedure(name="VUTE3W", legs=(_leg("VUTEB", "RW30L"), _leg("DB500", "RW30L"), _leg("DB501", "RW30L"),
                                                 _leg("VUTEB", "RW30R"), _leg("DB500", "RW30R")))
    other = ArrivalProcedure(name="BOXIL1W", legs=(_leg("BOXIL", "RW30L"),))
    arrivals = (east, west, other)
    assert procedures.star_for_runway(arrivals, "12R", "VUTE3E", ("VUTEB",)) is east  # it goes there: kept
    assert procedures.star_for_runway(arrivals, "30L", "VUTE3E", ("VUTEB",)) is west  # from VUTEB, to 30L
    assert procedures.star_for_runway(arrivals, "30L", "XXXX1A", ("NOWHERE",)) is None  # none from there: vectors
    route = procedures.star_route(west, "30L", "VUTEB")
    assert [leg.fix for leg in route] == ["VUTEB", "DB500", "DB501"]


def test_the_base_turn_is_towards_the_final_from_the_side_the_aircraft_is_on():
    """Dubai: the downwind (heading 119) given 4 miles south of 30L's final ended up 4 miles north of it; the base came
    as "turn left heading 029", away from the final, the pattern still taken as south."""
    rwy = Runway(lat=25.25, lon=55.36, elev_ft=62.0, heading_true=121.4, length_m=4000.0, width_m=60.0,
                 primary=RunwayEnd(number=12, designator="L"), secondary=RunwayEnd(number=30, designator="R"))
    geo = AirportGeometry(Airport(icao="OMDB", lat=25.25, lon=55.36, elev_ft=62.0, runways=(rwy,), parking=()))
    end = geo.end("30R")
    u = (math.sin(math.radians(end.heading_true)), math.cos(math.radians(end.heading_true)))
    right = (u[1], -u[0])
    out, across = 14.0 * 1852, 4.5 * 1852  # 14 miles out, 4.5 miles to the right of the landing direction
    lat, lon = geo.frame.to_latlon(end.threshold[0] - u[0] * out + right[0] * across,
                                   end.threshold[1] - u[1] * out + right[1] * across)
    leg = vectors.vector(geo, end, lat, lon, after="downwind", side=-1.0, heading_true=(end.heading_true + 180) % 360)
    to_final = (end.heading_true - 90) % 360  # from the right of the final, towards it
    assert leg.side == 1.0
    assert abs(((leg.heading_true - to_final + 540) % 360) - 180) < 20 or leg.leg == "downwind"


def test_altitudes_are_round_ones():
    assert round_altitude(62 + 2000) == 2000  # Dubai: 2,100 was "weird altitude for one hundred feet"
    assert round_altitude(3235) == 3500
    assert round_altitude(5000) == 5000


def test_the_copilots_calls_are_written_as_numbers():
    assert written("Roma Departure one three zero point niner, Emirates ninety-eight.") == "Roma Departure 130.9, Emirates 98."
    assert written("Climb to flight level three niner zero, Emirates ninety-eight.") == \
        "Climb to flight level 390, Emirates 98."
    assert written("Stand gate alpha three via kilo one one, kilo, zulu one seven, zulu, Emirates ninety-eight.") == \
        "Stand gate A3 via K11, K, Z17, Z, Emirates 98."
    assert written("Loud and clear, Emirates 98") == "Loud and clear, Emirates 98"


def test_a_callsigns_number_may_end_in_letters():
    assert split("RYR9SY") == ("RYR", "Ryanair", "9SY")
    assert split("THY 1YH") == ("THY", "Turkish", "1YH")
    assert split("IBS18AB") == ("IBS", "Iberexpress", "18AB")
    assert split("N172LT") is None and split("DP32") is None


def test_fl390_heard_as_39_before_the_minutes_is_fl390():
    assert cruise_altitudes(normalize("Expect flight level 39 20 minutes after departure."), 39000) == [39000]
    assert cruise_altitudes(normalize("Expect flight level 37 20 minutes after departure."), 39000) == [3700]
