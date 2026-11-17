"""Flying the filed procedures, and when ATC lets them be flown instead of giving headings.

**Arrivals.** A controller vectors an arrival (FAA JO 7110.65 5-9-1) when nothing published takes it to the
final: no arrival procedure, one that ends in "expect radar vectors", or traffic to fit it in with. An
arrival flying a STAR whose path runs onto the final approach course (an RNAV STAR with a runway transition
that lines up with the ILS) is left to fly it, and cleared for the approach on the way (7110.65 4-8-1:
cleared for the approach from a published route that connects to it). A STAR that ends somewhere else,
beside the airport or on a downwind, is flown to its end, and the vectors start there.

- ``star_join``: the STAR fix where its path joins the runway's final, or None when it doesn't.
- ``progress``: how far the aircraft is from the path, and how much of it is left to a given fix.

**Departures.** The takeoff clearance says what to fly first (7110.65 3-9-9, 4-3-2). On an RNAV SID whose
path starts at the runway, a US tower says "RNAV to FACTS, runway 36R, cleared for takeoff"; on a SID flown
by radar vectors, or with none, the heading ("fly runway heading"). Elsewhere (ICAO, Canada) the SID itself
is the instruction: "runway 36R, cleared for takeoff".

- ``sid_first_fix``: the first fix of a SID that is a path from the runway (an RNAV SID), or None.
"""

import itertools
import math
from dataclasses import dataclass

from localtc.atc_core import vectors
from localtc.atc_core.airport import AirportGeometry
from localtc.atc_core.airport.geometry import RunwayEndGeometry
from localtc.atc_core.route import RouteFix

NM_M = 1852.0
JOIN_OUT_NM = (4.0, 30.0)  # a STAR fix this far out on the final ...
JOIN_SIDE_NM = 2.0  # ... and this close to its centreline is on it
JOIN_TRACK_DEG = 45.0  # the STAR leg into that fix has to be heading down the final, not across it
ON_PATH_NM = 6.0  # this close to the STAR's path, the aircraft is flying it
SID_PATH_NM = 15.0  # a SID whose first fix is this close to the field is a path from the runway (RNAV)


@dataclass(frozen=True)
class Progress:
    off_nm: float  # how far off the path the aircraft is
    left_nm: float  # the path still to fly to the fix asked about (0: passed it)


def star_join(geo: AirportGeometry, end: RunwayEndGeometry, fixes: tuple[RouteFix, ...]) -> RouteFix | None:
    """The first STAR fix on ``end``'s final approach course that the STAR arrives at along the final."""
    for before, fix in itertools.pairwise(fixes):
        out, side = vectors.frame(geo, end, fix.lat, fix.lon)
        if not (JOIN_OUT_NM[0] <= out <= JOIN_OUT_NM[1] and abs(side) <= JOIN_SIDE_NM):
            continue
        was_out, was_side = vectors.frame(geo, end, before.lat, before.lon)
        inbound, across = was_out - out, abs(side - was_side)
        if inbound > 0 and math.degrees(math.atan2(across, inbound)) <= JOIN_TRACK_DEG:
            return fix
    return None


def progress(geo: AirportGeometry, fixes: tuple[RouteFix, ...], lat: float, lon: float, to: RouteFix) -> Progress | None:
    """Where the aircraft is along the path through ``fixes``: its distance off it, and the distance along it
    still to fly to ``to`` (one of the fixes)."""
    if len(fixes) < 2 or to not in fixes:
        return None
    points = [geo.xy(f.lat, f.lon) for f in fixes]
    here = geo.xy(lat, lon)
    best: tuple[float, int, float] | None = None  # (off, leg, fraction along it)
    for i, (a, b) in enumerate(itertools.pairwise(points)):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        f = 0.0 if length2 == 0 else max(0.0, min(1.0, ((here[0] - a[0]) * dx + (here[1] - a[1]) * dy) / length2))
        off = math.dist(here, (a[0] + f * dx, a[1] + f * dy))
        if best is None or off < best[0]:
            best = (off, i, f)
    assert best is not None
    off, leg, f = best
    target = fixes.index(to)
    if leg >= target:
        return Progress(off / NM_M, 0.0)
    left = (1 - f) * math.dist(points[leg], points[leg + 1]) + sum(
        math.dist(points[i], points[i + 1]) for i in range(leg + 1, target))
    return Progress(off / NM_M, left / NM_M)


def sid_first_fix(geo: AirportGeometry | None, fixes: tuple[RouteFix, ...]) -> RouteFix | None:
    """The first fix of a SID flown as a path from the runway (an RNAV SID). A radar vector SID in a plan
    lists only the fixes far out where the vectors end: None."""
    if geo is None or not fixes:
        return None
    first = fixes[0]
    return first if geo.distance_nm(first.lat, first.lon) <= SID_PATH_NM else None


def _same_sid(sim: str, plan: str) -> bool:
    """SimBrief's "ANJLL4" and the sim's "ANJL4": navdata names are cut to five characters, then the number."""
    sim, plan = sim.upper(), plan.upper()
    if sim == plan:
        return True
    if len(plan) < 3 or len(sim) < 3 or sim[-1] != plan[-1] or not sim[-1].isalnum():
        return False
    return plan[:-1].startswith(sim[:-1]) or sim[:-1].startswith(plan[:-1])


def _stem(name: str) -> str:
    """The name's waypoint: "UNGA" of UNGA1M, "DTY" of DTY2Y."""
    head = ""
    for c in name.upper():
        if not c.isalpha():
            break
        head += c
    return head


def _same_runway(a: str, b: str) -> bool:
    return a.upper().lstrip("0") == b.upper().lstrip("0")


def _serves(legs, runway: str, *, any_runway: bool = True) -> bool:
    """An arrival's legs go to ``runway``: one of its runway transitions is for it ("RW30L"; "RW30B" both 30L and
    30R). With no runway transitions at all, any runway (``any_runway``)."""
    runways = {leg.transition[2:].upper() for leg in legs if leg.transition.upper().startswith("RW")}
    if not runways:
        return any_runway
    want = runway.upper().lstrip("0")
    for r in runways:
        r = r.lstrip("0")
        if r == want or (r.endswith("B") and r[:-1] == want.rstrip("LRC")) or (r.isdigit() and r == want.rstrip("LRC")):
            return True
    return False


def star_for_runway(arrivals, runway: str, filed: str | None, entry: tuple[str, ...] = ()):
    """The STAR to clear an arrival to ``runway`` by, from the sim's ``arrivals`` (``ArrivalProcedure``): the one filed
    if it goes to that runway; otherwise one that does, from where the filed one starts (a fix of ``entry``: its first
    fixes), or with the filed one's waypoint in its name. None: there's none, and the flight is vectored.

    Dubai, filed VUTE3E for 12R with 30L in use: "cleared VUTE3E arrival, expect ILS runway 30L", an arrival that only
    goes to runways 12L and 12R, and vectors that started before the flight was on it."""
    found = [p for p in arrivals if filed and (p.name.upper() == filed.upper() or _same_sid(p.name, filed))]
    if found and any(_serves(p.legs, runway) for p in found):
        return found[0]
    candidates = sorted((p for p in arrivals if _serves(p.legs, runway, any_runway=False)), key=lambda p: p.name)
    wanted = {e.upper() for e in entry}
    if wanted and (hit := next((p for p in candidates if wanted & {leg.fix.upper() for leg in p.legs}), None)) is not None:
        return hit
    stem = _stem(filed or "")
    if len(stem) >= 3 and (hit := next((p for p in candidates if _stem(p.name) == stem), None)) is not None:
        return hit
    return None


def star_route(procedure, runway: str, entry: str = "") -> list:
    """An arrival's legs as flown to ``runway``: the enroute transition from ``entry`` (when it has one), the common
    part, and the transition to that runway; each fix once, in order."""
    legs = list(procedure.legs)
    enroute = [leg for leg in legs if leg.transition and not leg.transition.upper().startswith("RW")
               and leg.transition.upper() == entry.upper()]
    common = [leg for leg in legs if not leg.transition]
    to_runway = [leg for leg in legs if leg.transition.upper().startswith("RW") and _serves([leg], runway, any_runway=False)]
    if to_runway:
        first = to_runway[0].transition
        to_runway = [leg for leg in to_runway if leg.transition == first]
    out, seen = [], set()
    for leg in (*enroute, *common, *to_runway):
        if leg.fix and leg.fix.upper() not in seen:
            seen.add(leg.fix.upper())
            out.append(leg)
    return out


def sid_for_runway(departures, runway: str, filed: str | None, *, joins: tuple[str, ...] = ()) -> str | None:
    """The SID to clear a flight on from ``runway``, from the sim's ``departures``. The one filed if it's flown from that runway;
    otherwise the one from that runway going the same way: it reaches a fix of ``joins`` (where the filed route goes next), or
    has the filed one's waypoint in its name. None: there isn't one, and the clearance names no SID.

    Birmingham, filed UNGA1M off 33 with 15 in use: "cleared via UNGA1M, expect runway 15", a procedure the aircraft's FMS
    doesn't even offer for that runway."""
    def from_here(p) -> bool:
        return not p.runways or any(_same_runway(r, runway) for r in p.runways)

    if filed and (found := [p for p in departures if _same_sid(p.name, filed)]) and any(from_here(p) for p in found):
        return filed
    candidates = sorted((p for p in departures if p.runways and from_here(p)), key=lambda p: p.name)
    wanted = {j.upper() for j in joins}
    if wanted and (hit := next((p for p in candidates if wanted & {*p.fixes, *p.enroute}), None)) is not None:
        return hit.name
    stem = _stem(filed or "")
    if len(stem) >= 3 and (hit := next((p for p in candidates if _stem(p.name) == stem), None)) is not None:
        return hit.name
    return None
