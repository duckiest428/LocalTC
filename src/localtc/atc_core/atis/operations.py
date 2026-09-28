"""An airport's operations as its ATIS announces them: runways, approaches, notices and advisories.

**Runways.** One runway end is in use for the wind (``select_runway``, kept until its tailwind gets too strong,
never a closed one; in weather too poor for a visual, one with an instrument approach if the wind allows).
Parallel runways the same way are in use too, with simultaneous approaches when each has a localizer and
they're far enough apart for independent approaches (4,300 ft). LocalTC's own flight is given the first.

**Notices (NOTAMs).** The sim has none, so each airport gets a few of the ordinary kind, drawn once per
session from a seed: a taxiway closed or under construction, a runway closed (never the longest), an ILS or
just its glideslope out, approach or edge lights out, a VOR out, bird activity. They're acted on, not just
read: a closed runway is never used, a closed taxiway is routed around where there's another way, an ILS
out is no approach, a glideslope out makes the ILS a localizer approach, approach lights out raise the
visibility needed. ``[atc] notams = false`` turns them off.

**Advisories** follow from the weather: low visibility procedures, wind shear, runway condition codes and
braking action on a contaminated runway, de-icing, density altitude, a rapid pressure change, a runway
change in progress.
"""

import random
import zlib
from dataclasses import dataclass, field

from localtc.atc_core.airport import AirportGeometry
from localtc.atc_core.airport.approaches import NO_OUTAGES, Outages
from localtc.atc_core.airport.geometry import RunwayEndGeometry
from localtc.atc_core.atis.observation import Weather
from localtc.atc_core.values import Approach

SIMULTANEOUS_M = 1311.0  # 4,300 ft between parallel centrelines: independent simultaneous approaches (7110.65 5-9-7)
PARALLEL_DEG = 10.0


@dataclass(frozen=True)
class Notice:
    kind: str  # runway_closed, taxiway_closed, construction, ils_out, glideslope_out, approach_lights_out,
    #            edge_lights_out, navaid_out, birds
    subject: str = ""  # "16L/34R", "B", "34R", "VOR"

    def text(self, icao_style: bool) -> str:
        s = self.subject
        if icao_style:
            return {
                "runway_closed": f"runway {s} closed", "taxiway_closed": f"taxiway {s} closed",
                "construction": f"work in progress on taxiway {s}", "ils_out": f"ILS runway {s} not available",
                "glideslope_out": f"glide path runway {s} unserviceable",
                "approach_lights_out": f"approach lights runway {s} unserviceable",
                "edge_lights_out": f"runway {s} edge lights unserviceable", "navaid_out": f"{s} unserviceable",
                "birds": "bird activity reported in the vicinity of the aerodrome",
            }[self.kind]
        return {
            "runway_closed": f"runway {s} closed", "taxiway_closed": f"taxiway {s} closed",
            "construction": f"construction in progress on taxiway {s}", "ils_out": f"runway {s} ILS out of service",
            "glideslope_out": f"runway {s} ILS glideslope out of service",
            "approach_lights_out": f"runway {s} approach lights out of service",
            "edge_lights_out": f"runway {s} edge lights out of service", "navaid_out": f"{s} out of service",
            "birds": "bird activity in the vicinity of the airport",
        }[self.kind]


@dataclass(frozen=True)
class RunwayCondition:
    runway: str
    codes: tuple[int, int, int]
    contaminant: str  # "wet", "dry snow", "slush", "ice"

    @property
    def braking(self) -> str:
        return {6: "good", 5: "good", 4: "good to medium", 3: "medium", 2: "medium to poor", 1: "poor", 0: "nil"}[min(self.codes)]


@dataclass(frozen=True)
class Operations:
    landing: tuple[str, ...]
    departing: tuple[str, ...]
    approaches: dict[str, Approach] = field(default_factory=dict)  # landing runway -> the approach in use
    simultaneous: bool = False
    notices: tuple[Notice, ...] = ()
    outages: Outages = NO_OUTAGES
    closed_runways: frozenset[str] = frozenset()  # ends
    closed_taxiways: frozenset[str] = frozenset()
    low_visibility: bool = False
    wind_shear: bool = False
    condition: RunwayCondition | None = None
    deicing: bool = False
    density_altitude_ft: int | None = None
    runway_change: bool = False

    def key(self) -> tuple:
        """What makes a new ATIS when it changes (7110.65 2-9-2: runways, approaches, notices, braking)."""
        return (self.landing, self.departing, tuple((r, a.display) for r, a in sorted(self.approaches.items())),
                self.notices, self.low_visibility, self.wind_shear, self.condition, self.deicing)


def notices_for(geo: AirportGeometry, seed: int | str) -> tuple[Notice, ...]:
    """The session's notices for an airport: a handful of ordinary ones, the same every time for the same seed."""
    rng = random.Random(zlib.crc32(f"{geo.airport.icao}:{seed}".encode()))
    airport = geo.airport
    found: list[Notice] = []
    taxiways = sorted({p.name for p in airport.taxi_paths if p.kind == "taxi" and p.name
                       and sum(1 for q in airport.taxi_paths if q.name == p.name) >= 3})
    if taxiways and rng.random() < 0.15:
        found.append(Notice("taxiway_closed", rng.choice(taxiways)))
    if taxiways and rng.random() < 0.10:
        found.append(Notice("construction", rng.choice(taxiways)))
    runways = sorted(airport.runways, key=lambda r: -r.length_m)
    if len(runways) >= 2 and rng.random() < 0.08:
        found.append(Notice("runway_closed", rng.choice(runways[1:]).name))
    ils_ends = sorted(e.ident for e in geo.ends if e.has_ils)
    if ils_ends and rng.random() < 0.08:
        found.append(Notice("ils_out", rng.choice(ils_ends)))
    elif ils_ends and rng.random() < 0.10:
        found.append(Notice("glideslope_out", rng.choice(ils_ends)))
    if ils_ends and rng.random() < 0.08:
        found.append(Notice("approach_lights_out", rng.choice(ils_ends)))
    if rng.random() < 0.06:
        found.append(Notice("edge_lights_out", rng.choice(runways).name.split("/")[0]))
    if any(a.kind in ("vor", "vordme") for a in airport.approaches) and rng.random() < 0.05:
        found.append(Notice("navaid_out", "VOR"))
    if rng.random() < 0.12:
        found.append(Notice("birds"))
    # Never the whole field out of use: at most one runway closed, and not every ILS gone.
    return tuple(found)


def outages(notices: tuple[Notice, ...]) -> Outages:
    of = lambda kind: frozenset(n.subject for n in notices if n.kind == kind)
    return Outages(localizer=of("ils_out"), glideslope=of("glideslope_out"), approach_lights=of("approach_lights_out"),
                   navaids=frozenset({"VOR", "VOR/DME"}) if of("navaid_out") else frozenset())


def closed_ends(geo: AirportGeometry, notices: tuple[Notice, ...]) -> frozenset[str]:
    closed = {n.subject for n in notices if n.kind == "runway_closed"}
    return frozenset(e.ident for e in geo.ends if e.runway.name in closed)


def parallels(geo: AirportGeometry, end: RunwayEndGeometry, closed: frozenset[str]) -> list[tuple[RunwayEndGeometry, float]]:
    """The other runway ends the same way as ``end`` (and open), with their distance from its centreline."""
    out = []
    for other in geo.ends:
        if other is end or other.ident in closed or other.runway is end.runway:
            continue
        if abs(((other.heading_true - end.heading_true) + 540) % 360 - 180) > PARALLEL_DEG:
            continue
        _, across = end.runway.along_across(other.runway.center)
        out.append((other, abs(across)))
    return out


def advisories(weather: Weather, runway: str) -> dict:
    """What the weather adds to the broadcast."""
    temperature = weather.temperature_c
    vis = weather.visibility_sm
    rvr = weather.rvr_ft
    ceiling = weather.ceiling_ft
    low_vis = (vis is not None and vis < 0.35) or (rvr is not None and rvr < 1200) or (ceiling is not None and ceiling < 200)
    gust = weather.gust_kt or 0
    wind_shear = gust >= 30 or (gust and gust - weather.wind.speed_kt >= 15)
    condition = None
    rate = weather.precip_rate_mm or 0.0
    freezing = temperature is not None and temperature <= 0
    if weather.precip == "rain" and freezing:
        condition = RunwayCondition(runway, (1, 1, 1), "ice")
    elif weather.precip == "snow":
        codes = (5, 5, 5) if rate < 1.0 else (3, 3, 3) if rate < 5.0 else (2, 2, 2)
        condition = RunwayCondition(runway, codes, "dry snow" if freezing else "slush")
    elif weather.precip == "rain":
        condition = RunwayCondition(runway, (5, 5, 5), "wet")
    visible_moisture = bool(weather.precip) or weather.in_cloud or (vis is not None and vis < 1)
    deicing = temperature is not None and temperature <= 3 and visible_moisture
    density = None
    if temperature is not None and weather.altimeter_inhg is not None:
        pressure_alt = weather.elevation_ft + (29.92 - weather.altimeter_inhg) * 1000
        isa = 15 - 2 * weather.elevation_ft / 1000
        da = pressure_alt + 118.8 * (temperature - isa)
        if da >= 5000 and da - weather.elevation_ft >= 1500:
            density = int(round(da / 100) * 100)
    return {"low_visibility": bool(low_vis), "wind_shear": bool(wind_shear), "condition": condition,
            "deicing": bool(deicing), "density_altitude_ft": density}
