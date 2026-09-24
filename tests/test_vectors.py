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
    assert 8 <= out <= 18  # established a sensible way out
    altitudes = [o for o in arrival.said if "descend and maintain" in o.text]
    assert len(altitudes) >= 2  # stepped down, not dropped to the bottom at the first call
    cleared = next(o for o in arrival.said if o.instruction_id == "approach.intercept_cleared")
    assigned = arrival.engine.state.assignments.altitude_ft
    assert 2500 <= assigned <= 5000 and f"maintain {assigned:,} until established" in cleared.text
    glideslope = 433 + out * 318
    assert arrival.own.alt_indicated_ft <= glideslope + 1000  # not left high above it
