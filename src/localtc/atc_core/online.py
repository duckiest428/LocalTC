"""Online ATC (VATSIM, IVAO) beside LocalTC's: which of the networks' controllers cover the aircraft, which one the
flight should be with for its phase, and whether COM1 is on one of theirs (then LocalTC's ATC stays quiet).

Pure functions of the ``OnlineAtc`` controllers and the aircraft: nothing is fetched here (``localtc.online`` does
that, outside the ATC), so a recording replays the same.

Coverage is LocalTC's own airspace (``atc_core.airspace``: VATSpy's FIR outlines and SimAware's approach areas, the
ones LocalTC hands flights between and the Live Map draws), so the networks' sectors and LocalTC's are one map:

- a centre (CTR, FSS) works its FIR: the outline named by its callsign (EDWW_EMS_CTR: EDWW; ZSE_CTR: KZSE), else the
  one it sits in (LON_N_CTR is in EGTT);
- an approach (APP, DEP) works its approach area: the one named for its airport (SEA_APP: Seattle's), else the one it
  sits in, below FL245;
- delivery and ground work their own airport's ground; a tower its own airport and its zone round it (10 nm, below
  3,000 ft above the field), never the next field's and never an approach's airspace.

Where there's no outline for a centre or approach, a circle (the network's range) is used for them.

The networks' rule is top-down: a controller works the positions below theirs that nobody is on, inside their own
airspace, never one above. An approach with no tower or ground does all three at its airports; a tower with no ground
does ground too; a centre with nobody below does everything in its FIR; a tower never works an approach. So each phase
has the position it needs (``need``), and the flight is with the lowest one online from there up that works it here.
"""

from collections.abc import Iterable
from functools import lru_cache

from localtc.airports.world import world
from localtc.atc_core.airspace import Airspace, Area
from localtc.sim_api import OnlineController, OwnshipState
from localtc.sim_api.geo import haversine_nm as distance_nm

NETWORKS = {"vatsim": "VATSIM", "ivao": "IVAO"}

# The range each kind of position gets where the network gives none, and the most it's held to where it does.
DEFAULT_RANGE_NM = {"DEL": 10.0, "GND": 10.0, "TWR": 30.0, "APP": 40.0, "CTR": 300.0, "FSS": 600.0, "ATIS": 60.0}
# (A network's "visual range" is how far its controller's scope shows traffic, not their airspace: Zurich's approach
# says 150 nm, which reached Milan.)
MAX_RANGE_NM = {"DEL": 15.0, "GND": 15.0, "TWR": 40.0, "APP": 60.0, "CTR": 1000.0, "FSS": 1500.0, "ATIS": 100.0}
# (An ATIS is never the one to call, but COM1 on it is listening to the network: LocalTC stays quiet.)
AREA_KINDS = {"CTR": "center", "FSS": "center", "APP": "approach"}  # the positions that work an outline
PYRAMID = ("DEL", "GND", "TWR", "APP", "CTR", "FSS")  # bottom to top: each works the ones below it nobody's on
AT_FIELD_NM = 5.0  # on the ground this close to a delivery, ground or tower controller's airport: at their airport
TOWER_ZONE_NM = 10.0  # a tower's zone round its field ...
TOWER_ZONE_AGL_FT = 3000.0  # ... below this above it
APPROACH_MAX_FT = 24500.0  # an approach (or departure) controller below FL245
TUNED_SLACK = 1.5  # COM1 on a controller's frequency counts as with them out to this times their range
TUNED_AREA_NM = 30.0  # ... or this close outside their outline (just across the line, the handoff not made yet)
SAME_MHZ = 0.004  # 121.800 and 121.805 are one frequency (8.33 kHz spacing)
RELEVANT_NM = 400.0  # the controllers kept around the aircraft and its route's points (beyond their own range)

# The position each phase needs: delivery parked, ground taxiing, tower on and over the runway (and low round the
# field), approach on the way in and out, the centre en route.
NEED_BY_PHASE = {
    "PARKED": "DEL", "PUSHBACK": "GND", "TAXI_OUT": "GND", "RUNWAY_HOLD": "TWR", "TAKEOFF": "TWR",
    "DEPARTURE": "APP", "CRUISE": "CTR", "ARRIVAL": "APP", "APPROACH": "APP", "LANDING": "TWR", "TAXI_IN": "GND",
}


def network_name(network: str) -> str:
    """"VATSIM" for "vatsim"."""
    return NETWORKS.get(network, network.upper())


def kind_of(callsign: str) -> str:
    """The position's kind from its callsign's last part (LIRF_TWR: TWR; DEP is approach's), or "" (an observer)."""
    last = callsign.upper().rsplit("_", 1)[-1]
    if last == "DEP":
        return "APP"
    return last if last in DEFAULT_RANGE_NM else ""


def range_of(kind: str, given_nm: float = 0.0) -> float:
    """How far a position of ``kind`` reaches: ``given_nm`` (the network's) held to its kind's most, else its kind's."""
    if given_nm and given_nm > 0:
        return min(given_nm, MAX_RANGE_NM.get(kind, given_nm))
    return DEFAULT_RANGE_NM.get(kind, 50.0)


def same_mhz(a: float, b: float) -> bool:
    return abs(a - b) < SAME_MHZ


def area_of(c: OnlineController) -> Area | None:
    """The outline a centre or approach controller works, from LocalTC's airspace; None: a circle, then."""
    if c.kind not in AREA_KINDS:
        return None
    return _area(c.callsign, c.kind, round(c.lat, 2), round(c.lon, 2))


@lru_cache(maxsize=4096)
def _area(callsign: str, kind: str, lat: float, lon: float) -> Area | None:
    sky = Airspace.load()
    prefix = callsign.upper().split("_", 1)[0]
    if AREA_KINDS[kind] == "center":
        by_id = {a.id: a for a in sky.centers}
        named = by_id.get(prefix) or (by_id.get(f"K{prefix}") if len(prefix) == 3 else None)
        return named or sky.center_at(lat, lon)
    code = f"K{prefix}" if len(prefix) == 3 else prefix  # SEA_APP: KSEA (an approach area serves "SEA")
    area = sky.approach_for(code, lat, lon)
    return area if area is not None and (area.serves(code) or area.contains(lat, lon)) else None


def _inside(area: Area, own: OwnshipState) -> bool:
    return area.contains(own.lat, own.lon)


def airport_of(c: OnlineController) -> tuple[float, float]:
    """Where a delivery, ground or tower controller's airport is (LIRF_TWR: Fiumicino; SEA_GND: KSEA), else where they
    are."""
    return airport_field(c.callsign.upper().split("_", 1)[0]) or (c.lat, c.lon)


@lru_cache(maxsize=4096)
def airport_field(code: str) -> tuple[float, float] | None:
    for icao in (code, f"K{code}") if len(code) == 3 else (code,):
        found = next(((a.lat, a.lon) for a in world() if a.icao == icao), None)
        if found is not None:
            return found
    return None


def need(phase: str, own: OwnshipState) -> str:
    """The position the flight needs now: its phase's, and the tower's low round a field on the way out or in."""
    wanted = NEED_BY_PHASE.get(phase, "CTR")
    if wanted == "APP" and not own.on_ground and own.alt_agl_ft < TOWER_ZONE_AGL_FT:
        return "TWR"  # just off the runway, or on a short final: the tower's, where it has one (else up from there)
    return wanted


def covers(c: OnlineController, own: OwnshipState) -> bool:
    """``c``'s own airspace has the aircraft in it: their FIR, approach area, or airport and its zone."""
    if c.kind == "ATIS":
        return distance_nm(own.lat, own.lon, c.lat, c.lon) <= c.range_nm
    if c.kind in ("DEL", "GND", "TWR"):
        lat, lon = airport_of(c)
        far = distance_nm(own.lat, own.lon, lat, lon)
        if own.on_ground:
            return far <= AT_FIELD_NM
        return c.kind == "TWR" and far <= TOWER_ZONE_NM and own.alt_agl_ft < TOWER_ZONE_AGL_FT
    if c.kind == "APP" and own.alt_indicated_ft >= APPROACH_MAX_FT:
        return False
    area = area_of(c)
    if area is not None:
        return _inside(area, own)
    if distance_nm(own.lat, own.lon, c.lat, c.lon) > c.range_nm:
        return False
    # No outline: a circle, and never across into another FIR (Zurich's approach doesn't work Milan's airports).
    return c.kind != "APP" or _same_fir(airport_of(c), (own.lat, own.lon))


def _same_fir(a: tuple[float, float], b: tuple[float, float]) -> bool:
    sky = Airspace.load()
    first, second = sky.center_at(*a), sky.center_at(*b)
    return first is None or second is None or first.id == second.id


def works(c: OnlineController, needed: str, own: OwnshipState) -> bool:
    """``c`` does the ``needed`` position's job here: theirs or one below it nobody's on (top-down), in their airspace."""
    if c.kind not in PYRAMID or needed not in PYRAMID or PYRAMID.index(c.kind) < PYRAMID.index(needed):
        return False  # never a position above their own: a tower doesn't work an approach
    return covers(c, own)


def airspace_of(c: OnlineController) -> str:
    """Which airspace a controller works, the same for every sector of it: its outline ("LOVV" for Wien's
    LOVV_BU_CTR and LOVV_N_CTR alike), else its airport and kind ("LIRF TWR")."""
    area = area_of(c)
    return area.id if area is not None else f"{c.callsign.upper().split('_', 1)[0]} {c.kind}"


def ranks_for(c: OnlineController, needed: str) -> bool:
    """``c``'s position is the one ``needed`` or above it (a radar for the cruise, an approach for the arrival):
    a pilot on their frequency a few miles early was sent there by them, and is with them."""
    return c.kind in PYRAMID and needed in PYRAMID and PYRAMID.index(c.kind) >= PYRAMID.index(needed)


def covering(controllers: Iterable[OnlineController], own: OwnshipState) -> list[OnlineController]:
    return [c for c in controllers if c.kind != "ATIS" and covers(c, own)]


def _specific(c: OnlineController) -> float:
    """How narrow a controller's airspace is (the smaller wins where two of a kind overlap: a sector over the FIR it
    was cut from, a domestic FIR under an oceanic one). Circles come after outlines."""
    area = area_of(c)
    return area.size if area is not None else float("inf")


def pick(controllers: Iterable[OnlineController], own: OwnshipState, needed: str) -> OnlineController | None:
    """Who online works the ``needed`` position where ``own`` is: the lowest position from it up that works it there
    (the most specific of that kind, then the nearest); None: nobody online does (LocalTC's, then)."""
    here = [c for c in controllers if works(c, needed, own)]
    if not here:
        return None
    return min(here, key=lambda c: (PYRAMID.index(c.kind), _specific(c),
                                    distance_nm(own.lat, own.lon, *airport_of(c)), c.callsign))


def best(controllers: Iterable[OnlineController], own: OwnshipState, phase: str) -> OnlineController | None:
    """The controller the flight should be with now: who online works the position its phase needs."""
    return pick(controllers, own, need(phase, own))


def point(lat: float, lon: float, *, ground: bool, alt_ft: float, agl_ft: float) -> OwnshipState:
    """An aircraft at a place of the flight (the frequency plan asks who works it there, as the flight will)."""
    return OwnshipState(t=0.0, lat=lat, lon=lon, alt_msl_ft=alt_ft, alt_indicated_ft=alt_ft, alt_agl_ft=agl_ft,
                        altimeter_inhg=29.92, hdg_mag=0.0, hdg_true=0.0, ias_kt=0.0, gs_kt=0.0, vs_fpm=0.0,
                        on_ground=ground, squawk="", xpdr_mode="alt", com1_mhz=0.0, com2_mhz=0.0)


def along(points: list[tuple[float, float]], step_nm: float = 30.0) -> list[tuple[float, float]]:
    """The path through ``points`` with a point at least every ``step_nm`` (straight lines: enough to tell the FIRs)."""
    out: list[tuple[float, float]] = []
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        legs = max(1, int(distance_nm(lat1, lon1, lat2, lon2) // step_nm))
        out += [(lat1 + (lat2 - lat1) * i / legs, lon1 + (lon2 - lon1) * i / legs) for i in range(legs)]
    return out + points[-1:]


def _reaches(c: OnlineController, own: OwnshipState) -> bool:
    """Within reach of ``c`` on their frequency: their outline (or just outside it), else their range with slack."""
    area = area_of(c)
    if area is not None and _inside(area, own):
        return True
    near = c.range_nm * TUNED_SLACK if area is None else TUNED_AREA_NM
    if area is not None:  # just across the line: the distance to its nearest point
        return _near_outline(area, own, near)
    return distance_nm(own.lat, own.lon, c.lat, c.lon) <= near


def _near_outline(area: Area, own: OwnshipState, nm: float) -> bool:
    lat, lon = own.lat, own.lon
    if area.wraps and lon < 0:
        lon += 360.0
    return any(distance_nm(lat, lon, p[0], p[1]) <= nm for ring in area.rings for p in ring)


def tuned_to(controllers: Iterable[OnlineController], own: OwnshipState) -> OnlineController | None:
    """The online controller COM1 (the radio transmitting) is on, within reach of them; None: none of theirs."""
    mhz = own.tx_mhz
    near = [c for c in controllers if same_mhz(c.frequency_mhz, mhz) and _reaches(c, own)]
    return min(near, key=lambda c: (_specific(c), distance_nm(own.lat, own.lon, c.lat, c.lon))) if near else None


def relevant(controllers: Iterable[OnlineController], points: Iterable[tuple[float, float]],
             nm: float = RELEVANT_NM) -> list[OnlineController]:
    """The controllers that matter to the flight: within their range plus ``nm`` of any of ``points`` (the aircraft,
    the airports, the route's fixes), or with one of the points in their outline."""
    points = list(points)

    def near(c: OnlineController) -> bool:
        if any(distance_nm(lat, lon, c.lat, c.lon) <= c.range_nm + nm for lat, lon in points):
            return True
        area = area_of(c)
        return area is not None and any(area.contains(lat, lon) for lat, lon in points)

    return [c for c in controllers if near(c)]


def frequency_text(mhz: float) -> str:
    """121.8 as "121.800"."""
    return f"{mhz:.3f}"
