"""An emergency far from the destination: the nearest suitable airport, and vectors to it (atc_core/diversion.py).

The flight is the real San Diego to Phoenix one, an A320neo at FL350 over the desert west of Yuma, with
Phoenix 160 miles off. Yuma (a 13,300 ft runway with an ILS) is one of the airports around it the sim lists;
El Centro, 30 miles behind, was fetched on the way.
"""

from pathlib import Path

import msgspec
import pytest
from helpers.airports import make_airport

from localtc.atc_core.airport import AirportGeometry
from localtc.atc_core.diversion import diversion_at, diversions, runway_needed_ft
from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.readback.intents import match_intents, resolve
from localtc.atc_core.readback.normalize import normalize
from localtc.replay import Recording
from localtc.sim_api import (
    SIM_EVENT_TYPES,
    AirportData,
    AtcTransmission,
    NearbyAirport,
    NearbyAirports,
    OwnshipState,
    Transcript,
)

FLIGHT = Path(__file__).parent / "fixtures" / "real_ksan_kphx"
YUMA = make_airport("KNYL", "YUMA MCAS/YUMA INTL", (32.6566, -114.6060), 213, 30, ("21R", "03L"),
                    {"tower": (119.3, "YUMA TOWER")}, length_m=4054, magvar=11, ils="IYUM", ils_end="primary")
STRIP = make_airport("AZ99", "DESERT STRIP", (32.80, -114.95), 400, 90, ("09", "27"), {}, length_m=600, magvar=11)
AROUND = NearbyAirports(t=0.0, airports=tuple(NearbyAirport(icao=a.icao, lat=a.lat, lon=a.lon, elev_ft=a.elev_ft)
                                              for a in (STRIP, YUMA)))


# --- which airport ---------------------------------------------------------------------------------------------

def test_the_runway_needed_grows_with_the_weight():
    assert runway_needed_ft(2400) == 2500  # a Skyhawk
    assert runway_needed_ft(140_000) == 6000  # an A320
    assert runway_needed_ft(550_000) == 8500  # a 777
    assert runway_needed_ft(None) == 5000


def place(icao: str, lat: float, lon: float, length_m: float, *, tower: bool = False, ils: str = "") -> AirportGeometry:
    freqs = {"tower": (118.1, f"{icao} TOWER")} if tower else {}
    return AirportGeometry(make_airport(icao, f"{icao} FIELD", (lat, lon), 500, 90, ("27", "09"), freqs,
                                        length_m=length_m, ils=ils))


def test_nearest_suitable_is_near_long_enough_and_well_equipped():
    here = (33.0, -115.0)
    options = diversions([
        place("SHRT", 33.0, -115.1, 900),  # 5 nm: too short for an airliner
        place("MIDL", 33.0, -115.5, 2500, tower=True),  # 25 nm, a tower
        place("BIGG", 33.0, -115.6, 3500, tower=True, ils="IBIG"),  # 30 nm, a tower and an ILS: counts as 17
        place("FARR", 33.0, -118.5, 3500, tower=True, ils="IFAR"),  # 176 nm: out of range
    ], *here, need_ft=6000)
    assert [d.icao for d in options] == ["BIGG", "MIDL"]
    assert options[0].ils and options[0].towered and options[0].bearing == "west" and options[0].runway_ft == 11483
    assert round(options[0].distance_nm) == 30
    assert diversions([place("SHRT", 33.0, -115.1, 900)], *here, need_ft=2500)[0].icao == "SHRT"  # fine for a Cessna


def test_a_diversion_lands_into_the_wind():
    field = place("WIND", 33.0, -115.5, 3000)
    assert diversion_at(field, 33.0, -115.0, 5000, wind=(270.0, 15.0)).runway == "27"
    assert diversion_at(field, 33.0, -115.0, 5000, wind=(90.0, 15.0)).runway == "09"


# --- what the pilot says ---------------------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "fix"), [
    ("Los Angeles Center, Frontier 2084, request vectors to the nearest suitable airport", None),
    ("Frontier 2084, we need to divert, nearest airport please", None),
    ("Frontier 2084, request diversion to Palm Springs", "Palm Springs"),
    ("Frontier 2084, request vectors to Yuma", "Yuma"),
    ("Frontier 2084, divert to kilo november yankee lima", "KNYL"),
    ("Frontier 2084, unable to make Phoenix, request diversion", None),
])
def test_diversion_requests(text, fix):
    match, ambiguous = resolve(match_intents(normalize(text)))
    assert not ambiguous and match.intent == "request_diversion"
    assert match.values.get("fix") == fix


@pytest.mark.parametrize(("text", "intent"), [
    ("Frontier 2084, request vectors to final", "request_vectors"),
    ("Frontier 2084, request vectors for the ILS runway 26", "request_vectors"),
    ("Frontier 2084, we'd like to return to San Diego", "request_return"),
])
def test_other_vectors_are_not_a_diversion(text, intent):
    assert resolve(match_intents(normalize(text)))[0].intent == intent


# --- ATC -------------------------------------------------------------------------------------------------------

class Flight:
    """The recorded flight up to ``until``, then driven by hand: time moves in whole seconds."""

    def __init__(self, until: float = 2600.0, *, nearby: bool = True) -> None:
        self.engine = AtcEngine(EngineConfig(destination="KPHX", cruise_ft=36000, callsign="FFT2084", seed=5))
        for event in Recording(FLIGHT).events():
            if event.t > until:
                break
            if isinstance(event, SIM_EVENT_TYPES):
                self.engine.handle(event)
                if isinstance(event, OwnshipState):
                    self.own = event
        self.t = self.own.t
        if nearby:
            self.engine.handle(msgspec.structs.replace(AROUND, t=self.t))
        self.said: list[AtcTransmission] = []

    def fetch(self) -> list[str]:
        """What the service does with the engine's requests: here, hand over the layouts it has."""
        asked, self.engine.airport_requests[:] = list(self.engine.airport_requests), []
        for airport in (YUMA, STRIP):
            if airport.icao in asked:
                self.engine.handle(AirportData(t=self.t, airport=airport))
        return asked

    def fly(self, seconds: int, **changes) -> list[AtcTransmission]:
        heard = []
        for _ in range(seconds):
            self.t += 1
            moved = {k: (v(self.own) if callable(v) else v) for k, v in changes.items()}
            self.own = msgspec.structs.replace(self.own, t=self.t, **moved)
            heard += [o for o in self.engine.handle(self.own) if isinstance(o, AtcTransmission)]
        self.said += heard
        return heard

    def say(self, text: str) -> list[AtcTransmission]:
        self.t += 1
        heard = [o for o in self.engine.handle(Transcript(t=self.t, text=text, source="typed")) if isinstance(o, AtcTransmission)]
        self.said += heard
        return heard

    def readback(self) -> str:
        issued = self.engine.state.last_issued
        return self.engine.library.pilot_readback(issued.instruction_id, issued.slots)

    def ids(self) -> list[str]:
        return [o.instruction_id for o in self.said]


def test_an_emergency_far_from_the_destination_is_offered_the_nearest_suitable_airport():
    f = Flight()
    f.say("Mayday mayday mayday, Los Angeles Center, Frontier 2084, engine fire")
    assert {"KNYL", "AZ99"} <= set(f.fetch())  # the layouts of the airports around, asked for at once
    f.fly(30)
    assert f.ids() == ["common.emergency", "common.emergency_nearest"]
    offer = f.said[-1].text
    assert "you are number one" in offer and "nearest suitable airport is Yuma MCAS" in offer
    assert "runway 21R, 13,300 feet, ILS available" in offer and "Say intentions" in offer
    assert "Desert" not in offer  # 600 m of runway is no place for an A320

    f.say("Frontier 2084, request vectors to the nearest suitable airport")
    f.fly(10)
    divert = f.said[-1]
    assert divert.instruction_id == "common.divert", f.ids()
    assert "vectors to Yuma MCAS" in divert.text and "descend at your discretion" in divert.text
    assert f.engine.state.flight.destination == "KNYL"
    assert f.engine.facility("tower").station == "Yuma Tower"  # the arrival flow is Yuma's now

    f.say(f.readback())
    assert f.engine.state.read_back.instruction_id == "common.divert"
    assert f.engine.state.clearances.get("approach") is None


def test_well_off_the_heading_to_the_diversion_gets_a_new_one():
    f = Flight()
    f.say("Mayday mayday mayday, Los Angeles Center, Frontier 2084, engine fire, request vectors to the nearest airport")
    f.fetch()
    f.fly(30)
    assert "common.divert" in f.ids() and "common.emergency_nearest" not in f.ids()  # asked already: no offer
    assert f.engine.state.assignments.heading == 90
    f.say(f.readback())
    f.fly(20, hdg_mag=90, hdg_true=101)
    assert f.ids()[-1] == "common.divert"  # on the heading: nothing to add
    f.fly(70, hdg_mag=40, hdg_true=51)  # wandering off to the northeast
    assert f.ids()[-1] == "common.divert_vector" and "turn right heading 090" in f.said[-1].text
    count = len(f.said)
    f.fly(90, hdg_mag=90, hdg_true=101)
    assert len(f.said) == count  # back on it


def test_a_named_airport_is_where_the_flight_goes():
    f = Flight()
    f.say("Mayday mayday mayday, Los Angeles Center, Frontier 2084, engine fire")
    f.fetch()
    f.fly(30)
    f.say("Frontier 2084, request diversion to El Centro")
    f.fly(10)
    assert f.said[-1].instruction_id == "common.divert" and "El Centro" in f.said[-1].text
    assert f.engine.state.flight.destination == "KNJK"


def test_close_to_the_destination_it_stays_the_destination():
    f = Flight(until=3661.0, nearby=False)  # 33 miles out, talking to Phoenix Approach
    f.say("Pan pan pan pan, Frontier 2084, hydraulic failure")
    f.fly(30)
    assert "common.emergency_priority" in f.ids() and "common.emergency_nearest" not in f.ids()
    assert f.engine.state.flight.destination == "KPHX"
