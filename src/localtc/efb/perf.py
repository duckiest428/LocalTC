"""Fuel planning and weights for the EFB: the aircraft's published weights and typical cruise numbers by type, the
trip from the route's length, the reserves by the usual rules, and the weights checked against the limits.

SimBrief's own figures, when the plan came from it, are used as they are and shown as SimBrief's; the estimate here
is only for a typed plan, and says it's an estimate. Weights in kilograms (and pounds alongside). Not for real
flight planning.

The table: each type's operating empty weight, maximum zero-fuel, takeoff and landing weights and fuel capacity
(manufacturers' airport planning documents, typical configurations), its usual cruise speed and fuel burn, and
seats. A type that isn't in it gets no estimate rather than another type's.
"""

import math
from dataclasses import dataclass

LB_PER_KG = 2.20462
PAX_KG = 84.0  # a passenger with their bags (the standard mass, ICAO/EASA average)
TAXI_KG_PER_MIN = {"L": 1.0, "M": 11.0, "H": 30.0, "J": 45.0}
CONTINGENCY = 0.05  # of the trip
FINAL_RESERVE_MIN = 30  # jets and turboprops: 30 minutes at holding speed (ICAO)
FINAL_RESERVE_MIN_PISTON = 45  # a piston under IFR
HOLD_BURN = 0.8  # holding burns this share of the cruise burn
CLIMB_DESCENT_EXTRA_MIN = 12  # the climb and descent cost about this many cruise minutes more than flying it level
ALTERNATE_DEFAULT_NM = 120.0  # without an alternate given: fuel for this far


@dataclass(frozen=True)
class Type:
    name: str
    oew_kg: float
    mzfw_kg: float
    mtow_kg: float
    mlw_kg: float
    fuel_max_kg: float
    cruise_tas_kt: float
    burn_kg_h: float
    seats: int
    cargo_max_kg: float = 0.0
    piston: bool = False


TYPES: dict[str, Type] = {
    "A319": Type("Airbus A319", 40800, 58500, 75500, 62500, 18730, 450, 2300, 140, 6000),
    "A320": Type("Airbus A320", 42600, 62500, 78000, 66000, 18730, 450, 2500, 180, 7000),
    "A20N": Type("Airbus A320neo", 44300, 64300, 79000, 67400, 18730, 450, 2200, 180, 7000),
    "A321": Type("Airbus A321", 48500, 73800, 93500, 77800, 18730, 450, 2900, 220, 8000),
    "A21N": Type("Airbus A321neo", 50100, 75600, 97000, 79200, 25700, 450, 2600, 220, 8000),
    "A332": Type("Airbus A330-200", 120600, 170000, 242000, 182000, 109200, 470, 5700, 250, 18000),
    "A333": Type("Airbus A330-300", 124500, 175000, 242000, 187000, 78000, 470, 5900, 300, 20000),
    "A339": Type("Airbus A330-900", 135000, 181000, 251000, 191000, 110000, 470, 5300, 290, 20000),
    "A359": Type("Airbus A350-900", 142400, 195700, 283000, 207000, 110500, 488, 5800, 315, 23000),
    "A35K": Type("Airbus A350-1000", 155000, 223000, 319000, 236000, 124000, 488, 6800, 365, 25000),
    "A388": Type("Airbus A380-800", 277000, 373000, 575000, 394000, 254000, 490, 11500, 520, 30000),
    "BCS3": Type("Airbus A220-300", 37100, 55800, 70900, 61000, 17100, 450, 1800, 140, 5000),
    "B737": Type("Boeing 737-700", 38100, 55200, 70100, 58600, 20900, 450, 2300, 140, 5000),
    "B738": Type("Boeing 737-800", 41400, 62700, 79000, 66400, 20900, 450, 2500, 180, 7000),
    "B739": Type("Boeing 737-900ER", 44700, 67700, 85100, 71400, 23800, 450, 2700, 200, 7500),
    "B38M": Type("Boeing 737 MAX 8", 45100, 65900, 82200, 69300, 20800, 453, 2200, 180, 7000),
    "B39M": Type("Boeing 737 MAX 9", 46900, 70000, 88300, 74300, 20800, 453, 2400, 200, 7500),
    "B744": Type("Boeing 747-400", 178800, 246100, 396900, 285800, 173000, 490, 10500, 400, 30000),
    "B748": Type("Boeing 747-8", 214500, 295300, 447700, 312100, 194500, 490, 9800, 410, 32000),
    "B752": Type("Boeing 757-200", 58400, 84400, 115700, 89800, 33800, 460, 3300, 200, 8000),
    "B763": Type("Boeing 767-300ER", 90000, 133800, 186900, 145100, 73400, 460, 5000, 250, 16000),
    "B772": Type("Boeing 777-200ER", 138100, 195000, 297500, 213000, 137500, 485, 6900, 300, 22000),
    "B77W": Type("Boeing 777-300ER", 167800, 237700, 351500, 251300, 145500, 490, 7500, 360, 25000),
    "B788": Type("Boeing 787-8", 119900, 161000, 228000, 172000, 101300, 485, 4900, 240, 18000),
    "B789": Type("Boeing 787-9", 128800, 181400, 254000, 192800, 101300, 488, 5400, 290, 20000),
    "B78X": Type("Boeing 787-10", 135500, 192800, 254000, 201800, 101300, 488, 5800, 330, 21000),
    "E190": Type("Embraer 190", 27900, 40800, 51800, 44000, 12900, 440, 1800, 100, 3500),
    "E75L": Type("Embraer 175", 21900, 31700, 38800, 34000, 9400, 430, 1500, 76, 2500),
    "CRJ9": Type("Bombardier CRJ900", 21800, 32100, 38300, 33300, 8800, 450, 1500, 76, 2500),
    "DH8D": Type("De Havilland Dash 8-400", 17200, 25900, 29600, 28000, 5300, 310, 950, 78, 2000),
    "AT76": Type("ATR 72-600", 13300, 21000, 23000, 22350, 5000, 275, 650, 70, 1500),
    "C208": Type("Cessna 208 Caravan", 2150, 3600, 3970, 3700, 1010, 170, 160, 12, 1300),
    "TBM9": Type("Daher TBM 930", 2100, 2800, 3350, 3200, 870, 320, 220, 5, 100),
    "PC12": Type("Pilatus PC-12", 2800, 4100, 4740, 4500, 1220, 270, 230, 9, 400),
    "C172": Type("Cessna 172", 750, 1100, 1110, 1110, 145, 120, 28, 3, 50, piston=True),
    "SR22": Type("Cirrus SR22", 1050, 1450, 1630, 1630, 230, 175, 50, 3, 60, piston=True),
    "DA62": Type("Diamond DA62", 1550, 2200, 2300, 2300, 230, 175, 55, 6, 100, piston=True),
}
ALIASES = {"A21N": "A21N", "A339": "A339", "B37M": "B38M", "B3XM": "B39M", "A19N": "A319", "B77L": "B772",
           "A337": "A333", "A338": "A339", "A346": "A359"}


def aircraft(code: str) -> Type | None:
    from localtc.atc_core.airport.aircraft_size import designator

    key = designator(code) or (code or "").strip().upper()
    return TYPES.get(key) or TYPES.get(ALIASES.get(key, ""))


@dataclass
class FuelPlan:
    trip_kg: float
    contingency_kg: float
    alternate_kg: float
    final_reserve_kg: float
    taxi_kg: float
    extra_kg: float
    block_kg: float
    trip_min: int
    source: str  # "estimate" or "SimBrief"

    def to_json(self) -> dict:
        def both(kg: float) -> dict:
            return {"kg": int(round(kg)), "lb": int(round(kg * LB_PER_KG))}

        return {"trip": both(self.trip_kg), "contingency": both(self.contingency_kg), "alternate": both(self.alternate_kg),
                "final_reserve": both(self.final_reserve_kg), "taxi": both(self.taxi_kg), "extra": both(self.extra_kg),
                "block": both(self.block_kg), "trip_min": self.trip_min, "source": self.source}


def fuel_plan(kind: Type, distance_nm: float, *, alternate_nm: float | None = None, extra_kg: float = 0.0,
              taxi_min: float = 12.0, wake: str = "M") -> FuelPlan:
    """The fuel for ``distance_nm`` (the route's length): the trip at the type's cruise burn plus the climb and
    descent, 5% contingency, the alternate, the final reserve and the taxi."""
    hours = distance_nm / kind.cruise_tas_kt + CLIMB_DESCENT_EXTRA_MIN / 60.0
    trip = hours * kind.burn_kg_h
    alt_nm = alternate_nm if alternate_nm is not None else ALTERNATE_DEFAULT_NM
    alternate = (alt_nm / kind.cruise_tas_kt + 0.25) * kind.burn_kg_h * 0.9
    reserve = (FINAL_RESERVE_MIN_PISTON if kind.piston else FINAL_RESERVE_MIN) / 60.0 * kind.burn_kg_h * HOLD_BURN
    taxi = taxi_min * TAXI_KG_PER_MIN.get(wake, 11.0) if not kind.piston else 2.0
    contingency = trip * CONTINGENCY
    block = trip + contingency + alternate + reserve + taxi + extra_kg
    return FuelPlan(trip, contingency, alternate, reserve, taxi, extra_kg, min(block, kind.fuel_max_kg),
                    int(round(hours * 60)), "estimate")


def from_simbrief(perf) -> FuelPlan | None:
    """SimBrief's own block, trip and reserve (``PlanPerf``, in pounds), as the plan has them."""
    if not perf or not perf.block_fuel_lb:
        return None
    kg = 1.0 / LB_PER_KG
    trip = max(0.0, (perf.takeoff_fuel_lb - perf.landing_fuel_lb) * kg)
    taxi = max(0.0, (perf.block_fuel_lb - perf.takeoff_fuel_lb) * kg)
    reserve = perf.reserve_fuel_lb * kg
    rest = max(0.0, perf.block_fuel_lb * kg - trip - taxi - reserve)
    return FuelPlan(trip, 0.0, rest, reserve, taxi, 0.0, perf.block_fuel_lb * kg, 0, "SimBrief")


def weights(kind: Type, *, pax: int, cargo_kg: float, block_kg: float, trip_kg: float, taxi_kg: float) -> dict:
    """The weights from the payload and the fuel, each against its limit: {name: {kg, lb, limit_kg, ok}}."""
    zfw = kind.oew_kg + pax * PAX_KG + cargo_kg
    tow = zfw + block_kg - taxi_kg
    lw = tow - trip_kg

    def row(value: float, limit: float) -> dict:
        return {"kg": int(round(value)), "lb": int(round(value * LB_PER_KG)), "limit_kg": int(limit), "ok": value <= limit}

    out = {"payload": {"kg": int(round(pax * PAX_KG + cargo_kg)), "lb": int(round((pax * PAX_KG + cargo_kg) * LB_PER_KG)),
                       "pax": pax, "seats": kind.seats, "ok": pax <= kind.seats},
           "zfw": row(zfw, kind.mzfw_kg), "tow": row(tow, kind.mtow_kg), "lw": row(lw, kind.mlw_kg),
           "fuel": {"kg": int(round(block_kg)), "lb": int(round(block_kg * LB_PER_KG)), "limit_kg": int(kind.fuel_max_kg),
                    "ok": block_kg <= kind.fuel_max_kg}}
    out["ok"] = all(v.get("ok", True) for v in out.values() if isinstance(v, dict))
    return out


def route_nm(points: list[tuple[float, float]]) -> float:
    return sum(_nm(a[0], a[1], b[0], b[1]) for a, b in zip(points, points[1:]))


def alternates(dest: tuple[float, float], airports: list[dict], *, min_runway_ft: int = 6000, count: int = 4,
               exclude: str = "") -> list[dict]:
    """The nearest airports to the destination with a runway long enough (``airports``: the app's index, each
    {icao, name, lat, lon, longest_ft}): {icao, name, nm}."""
    found = []
    for a in airports:
        if a.get("icao") == exclude or (a.get("longest_ft") or 0) < min_runway_ft:
            continue
        d = _nm(dest[0], dest[1], a["lat"], a["lon"])
        if 10 <= d <= 250:
            found.append({"icao": a["icao"], "name": a.get("name", ""), "nm": int(round(d)), "longest_ft": a.get("longest_ft")})
    return sorted(found, key=lambda x: x["nm"])[:count]


def _nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 3440.065 * math.asin(min(1.0, math.sqrt(a)))
