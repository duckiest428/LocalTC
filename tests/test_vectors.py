"""Radar vectors onto the final (atc_core/vectors.py), flown for real: an aircraft that turns to every heading
approach gives and descends to every altitude, around Seattle's 16L from wherever it comes in.

From the Denver to Seattle flight (2026-09-23): approach gave one heading 25 miles out and nothing else
until the aircraft had found the final by itself. "Vectors were really bad."
"""

import gzip
import json
import math
from pathlib import Path

import msgspec
import pytest

from localtc.atc_core import vectors
from localtc.atc_core.airport import AirportGeometry
from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.sim_api import (
    Airport,
    AirportData,
    AtcTransmission,
    OwnshipState,
    Transcript,
)

FLIGHT = Path(__file__).parent / "fixtures" / "real_kden_ksea"
MAGVAR = 15.0  # east, around Seattle


def seattle() -> Airport:
    with gzip.open(FLIGHT / "session.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("type") == "airport_data" and row["airport"]["icao"] == "KSEA":
                return msgspec.convert(row["airport"], Airport)
    raise AssertionError("no KSEA in the fixture")


KSEA = seattle()


def start(bearing: float, nm: float) -> tuple[float, float]:
    lat = KSEA.lat + nm / 60 * math.cos(math.radians(bearing))
    lon = KSEA.lon + nm / 60 * math.sin(math.radians(bearing)) / math.cos(math.radians(KSEA.lat))
    return lat, lon


# --- the pattern on its own ------------------------------------------------------------------------------------

@pytest.mark.parametrize(("bearing", "nm", "heading", "first_leg"), [
    (110, 30, 290, "join"),  # from the southeast, like the flight: a downwind on the east side
    (40, 35, 250, "base"),  # from the northeast, out beyond the base turn: a base leg
    (345, 40, 170, "straight_in"),  # from the north, nearly lined up: straight in
    (270, 25, 90, "join"),  # from the west: the west downwind
])
def test_the_first_leg_depends_on_where_the_arrival_is(bearing, nm, heading, first_leg):
    geo = AirportGeometry(KSEA)
    assert vectors.vector(geo, geo.end("16L"), *start(bearing, nm)).leg == first_leg


def test_the_pattern_only_moves_on():
    geo = AirportGeometry(KSEA)
    end = geo.end("16L")
    beyond_base_on_downwind = start(12, 20)  # east of the centreline, well out
    assert vectors.vector(geo, end, *beyond_base_on_downwind, after="downwind").leg == "base"
    close_in = start(355, 8)  # nearly on the final, inside the gate
    assert vectors.vector(geo, end, *close_in, after="intercept").leg == "intercept"


def test_a_straight_in_from_afar_isnt_too_high_for_the_miles_before_the_join():
    """32 miles out at 12,500 ft, joining 10 miles out: the 22 miles before the join count. Only the join's 10 had
    the arrival sent north on a downwind, away from the airport."""
    geo = AirportGeometry(KSEA)
    leg = vectors.vector(geo, geo.end("16L"), *start(345, 32))
    assert leg.leg == "straight_in" and leg.join_nm < 20 < leg.track_nm
    assert not vectors.too_high(leg, 12500, KSEA.elev_ft)
    assert vectors.too_high(leg, 20000, KSEA.elev_ft)  # really too high still is


def test_step_down_altitudes():
    assert vectors.step_altitude(40, 433, 2500) == 10000  # 10,433 ft: the thousand below
    assert vectors.step_altitude(20, 433, 2500) == 5000
    assert vectors.step_altitude(6, 433, 2500) == 2500  # never below where the approach starts


# --- flown through the engine ----------------------------------------------------------------------------------

class Arrival:
    """An aircraft that does exactly what approach says, one second at a time."""

    def __init__(self, bearing: float, nm: float, heading: float, altitude: float = 15000) -> None:
        self.engine = AtcEngine(EngineConfig(destination="KSEA", cruise_ft=38000, callsign="DAL2543", seed=3))
        self.engine.handle(AirportData(t=0.0, airport=KSEA))
        lat, lon = start(bearing, nm)
        self.own = OwnshipState(
            t=1.0, lat=lat, lon=lon, alt_msl_ft=altitude, alt_indicated_ft=altitude, alt_agl_ft=altitude - 433,
            altimeter_inhg=30.11, hdg_mag=heading, hdg_true=(heading + MAGVAR) % 360, ias_kt=250, gs_kt=250,
            vs_fpm=-1500, on_ground=False, squawk="4553", xpdr_mode="alt", com1_mhz=119.2, com2_mhz=121.5,
            engine_running=True, altimeter_setting_inhg=30.11,
        )
        self.engine.handle(self.own)
        self.engine.state.phase = "ARRIVAL"
        self.engine.state.flags.add("descend")
        self.said: list[AtcTransmission] = []
        self.captured = False
        self.say(f"Seattle Approach, Delta 2543, {int(altitude):,} descending")

    def say(self, text: str) -> None:
        self.hear(self.engine.handle(Transcript(t=self.own.t, text=text, source="typed")))

    def hear(self, outputs) -> None:
        for o in outputs:
            if isinstance(o, AtcTransmission):
                self.said.append(o)
                issued = self.engine.state.last_issued
                if self.engine.state.pending is not None and issued is not None:
                    self.say(self.engine.library.pilot_readback(issued.instruction_id, issued.slots))

    def fly(self, seconds: int) -> None:
        geo = AirportGeometry(KSEA)
        end = geo.end("16L")
        for _ in range(seconds):
            a = self.engine.state.assignments
            hdg = self.own.hdg_mag
            out, side = vectors.frame(geo, end, self.own.lat, self.own.lon)
            if "approach" in self.engine.state.clearances and abs(side) < 0.7 and out > 1:
                self.captured = True  # the localizer
            target = (end.heading_true - MAGVAR) if self.captured else a.heading
            if target is not None:
                turn = ((target - hdg + 540) % 360) - 180
                hdg = (hdg + max(-3.0, min(3.0, turn))) % 360
            alt = self.own.alt_indicated_ft
            if a.altitude_ft is not None and alt > a.altitude_ft:
                alt = max(a.altitude_ft, alt - 30)  # 1,800 fpm
            gs = 250 if out > 15 else 180
            true = math.radians(hdg + MAGVAR)
            lat = self.own.lat + gs / 3600 / 60 * math.cos(true)
            lon = self.own.lon + gs / 3600 / 60 * math.sin(true) / math.cos(math.radians(self.own.lat))
            self.own = msgspec.structs.replace(self.own, t=self.own.t + 1, lat=lat, lon=lon, hdg_mag=hdg,
                                               hdg_true=(hdg + MAGVAR) % 360, alt_indicated_ft=alt, alt_msl_ft=alt,
                                               alt_agl_ft=alt - 433, gs_kt=gs, ias_kt=gs)
            self.hear(self.engine.handle(self.own))
            if "approach" in self.engine.state.clearances and self.captured:
                return

    def ids(self) -> list[str]:
        return [o.instruction_id for o in self.said]


@pytest.mark.parametrize(("bearing", "nm", "heading"), [(110, 30, 290), (60, 30, 250), (270, 25, 90), (180, 20, 0)])
def test_vectored_all_the_way_onto_the_final(bearing, nm, heading):
    arrival = Arrival(bearing, nm, heading)
    arrival.fly(1500)
    ids = arrival.ids()
    vectors_given = [i for i in ids if i.startswith("approach.") and i not in (
        "approach.descend", "approach.maintain", "approach.speed", "approach.handoff_tower")]
    assert vectors_given[-1] == "approach.intercept_cleared", ids  # the last heading carries the clearance
    assert 2 <= len(vectors_given) <= 6, ids  # a handful of headings, not one and not a stream
    assert arrival.captured
    geo = AirportGeometry(KSEA)
    out, _ = vectors.frame(geo, geo.end("16L"), arrival.own.lat, arrival.own.lon)
    assert 8 <= out <= 22  # established a sensible way out (at its own speed: nobody ahead to slow it for)
    altitudes = [o for o in arrival.said if "descend and maintain" in o.text]
    assert len(altitudes) >= 2  # stepped down, not dropped to the bottom at the first call
    cleared = next(o for o in arrival.said if o.instruction_id == "approach.intercept_cleared")
    assigned = arrival.engine.state.assignments.altitude_ft
    assert 2500 <= assigned <= 5000 and f"maintain {assigned:,} until established" in cleared.text
    glideslope = 433 + out * 318
    assert arrival.own.alt_indicated_ft <= glideslope + 1000  # not left high above it


def test_the_field_in_sight_from_the_wrong_side_gets_vectors_not_a_visual_there_and_then():
    """East of the field, for 16L (its final is north): "field in sight" was cleared the visual and sent
    to tower on the spot, and nobody turned it in. The vectors go on; the turn onto the final clears the visual."""
    arrival = Arrival(110, 30, 290)
    arrival.say("Seattle Approach, Delta 2543, field in sight")
    arrival.fly(15)
    assert "approach.expect_visual" in arrival.ids() and "approach" not in arrival.engine.state.clearances, arrival.ids()
    arrival.fly(1500)
    assert "approach.intercept_visual" in arrival.ids(), arrival.ids()



def test_a_visual_approachs_last_vector_is_the_final_itself():
    """Denver's 16L: "turn left heading 203, cleared visual approach" ("203 isn't the runway's heading"). The turn onto
    the final is the runway's heading, given as the turn has to start, and it rolls out on the centreline."""
    arrival = Arrival(110, 30, 290)
    arrival.say("Seattle Approach, Delta 2543, field in sight")
    arrival.fly(1500)
    cleared = next(o for o in arrival.said if o.instruction_id == "approach.intercept_visual")
    geo = AirportGeometry(KSEA)
    final = round((geo.end("16L").heading_true - MAGVAR) % 360)
    assert arrival.engine.state.assignments.heading in (final - 1, final, final + 1), cleared.text
    assert arrival.captured  # within 0.7 nm of the centreline, heading down it


def at(geo: AirportGeometry, runway: str, out: float, side: float) -> tuple[float, float]:
    """The point ``out`` miles out along ``runway``'s extended centreline, ``side`` miles off it (right of landing +)."""
    end = geo.end(runway)
    c = math.radians(end.heading_true)
    x = end.threshold[0] - out * vectors.NM_M * math.sin(c) + side * vectors.NM_M * math.cos(c)
    y = end.threshold[1] - out * vectors.NM_M * math.cos(c) - side * vectors.NM_M * math.sin(c)
    return geo.frame.to_latlon(x, y)


@pytest.mark.parametrize("visual", [True, False])
def test_a_base_more_than_square_to_the_final_still_gets_the_turn_in(visual):
    """Detroit's 21L: a base on approach's own heading, 108 degrees off the final, counted as flying away from the
    runway. No turn in came, the flight went through the centreline and was sent back on a base the other way
    ("turn left heading 306")."""
    geo = AirportGeometry(KSEA)
    end = geo.end("16L")
    base = (end.heading_true - 108) % 360  # from the right of the final, a little past square to it
    near = vectors.vector(geo, end, *at(geo, "16L", 18, 1.2), after="base", side=1.0, heading_true=base, visual=visual,
                          speed_kt=250)
    assert near.leg == "intercept"
    across = vectors.vector(geo, end, *at(geo, "16L", 19, -1.0), after="base", side=1.0, heading_true=base,
                            visual=visual, speed_kt=250)
    assert across.leg == "intercept"  # back to the final from the side it's on, never a base turned round
    assert abs(((across.heading_true - (end.heading_true + 30)) + 540) % 360 - 180) < 1
