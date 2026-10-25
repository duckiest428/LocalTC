"""Which runway each flight is given where an airport has more than one in use, as a controller gives them.

The ATIS names the runways for landing and for departing (the real one's, where there is one: "LNDG/DEPG RWYS 4/8"
is Honolulu's 4L, 4R, 8L and 8R, never its seaplane runways). From those:

- **Arrivals and departures apart.** Where the same parallels are in use for both, one lands and the other departs:
  the one with the ILS lands (both or neither: the one further from the terminal, as Los Angeles and Atlanta land
  outboard and depart inboard), the rest depart. One runway alone does both.
- **Long enough.** A heavy isn't given the short one: each runway has to be long enough for the aircraft, landing
  or taking off; where none in use is, the longest open one that the wind allows.
- **The nearest.** A departure gets the runway it can taxi to soonest (from its gate), an arrival the one whose final
  it's lined up for soonest (from where it is).
- **Spread out.** Ties go to the runway given least lately, so one runway doesn't carry every flight while its
  neighbour sits empty.

A runway once given is kept (``keep``): only a runway closing, the wind turning to a tailwind on it, or the ATIS
taking it out of use changes it, and the flight is told (the engine's job).
"""

import math
from collections.abc import Iterable, Sequence

from localtc.atc_core.airport.aircraft_size import wake_category
from localtc.atc_core.airport.geometry import AirportGeometry, RunwayEndGeometry

PARALLEL_DEG = 10.0
# Parallels closer than this are worked as one complex, one landing and the other departing (Honolulu's 8L and the
# reef runway 8R, 2 km apart; Los Angeles's pairs, 230 m); further apart, each takes both.
SEGREGATE_M = 2500.0
# Runway length needed (m), by wake category, to take off and to land: a heavy wants about 9,000 ft to go and 7,000 to
# stop (an A330 or a 777 at weight); an A320 or a 737 7,000 and 5,500; a turboprop or a light jet 4,500 and 3,500.
NEEDED_M = {"super": (3000.0, 2500.0), "heavy": (2700.0, 2100.0), "medium": (2000.0, 1600.0), "light": (900.0, 600.0)}
SMALL_TYPES = ("C15", "C17", "C18", "C20", "C21", "PA", "SR2", "DA4", "P28", "M20", "TB")  # light singles and twins
FINAL_NM = 10.0  # an arrival is compared by how far it has to go to a point this far out on each final


def expand(geo: AirportGeometry, idents: Iterable[str], closed: frozenset[str] = frozenset()) -> list[RunwayEndGeometry]:
    """The runway ends a list of idents means here, in its order: "08L" itself; "08" (a real ATIS's "RWYS 4/8") every
    08 the airport has, 08L and 08R, or its plain 08. Closed ones and ones the airport hasn't, left out."""
    out: list[RunwayEndGeometry] = []
    for ident in idents:
        exact = geo.end(ident)
        if exact is not None:
            found = [exact]
        else:
            number = ident.rstrip("LRC")
            found = [e for e in geo.ends if e.ident.rstrip("LRC") == number] if ident == number else []
        out += [e for e in found if e.ident not in closed and e not in out]
    return out


def segregate(landing: Sequence[RunwayEndGeometry], departing: Sequence[RunwayEndGeometry],
              terminal: tuple[float, float] | None = None) -> tuple[list[RunwayEndGeometry], list[RunwayEndGeometry]]:
    """Arrivals and departures on their own runways among close parallels (see the module): the landing and departing
    lists, neither ever left empty."""
    land, dep = list(landing), list(departing)
    for group in _parallel_groups(list(dict.fromkeys([*landing, *departing]))):
        lands = [e for e in group if e in land]
        departs = [e for e in group if e in dep]
        if not lands or not departs or len(group) < 2:
            continue
        only_departing = [e for e in departs if e not in lands]
        if only_departing:
            dep = [e for e in dep if e not in lands or e in only_departing]  # departures off the landing ones
            continue
        if len(lands) < 2:
            continue  # one runway doing both
        with_ils = [e for e in lands if e.has_ils]
        arrivals = with_ils if 0 < len(with_ils) < len(lands) else [max(lands, key=lambda e: _from_terminal(e, terminal))]
        land = [e for e in land if e not in lands or e in arrivals]
        dep = [e for e in dep if e not in arrivals]
    return land or list(landing), dep or list(departing)


def needed_m(aircraft_type: str, *, landing: bool) -> float:
    take_off, land = NEEDED_M[category(aircraft_type)]
    return land if landing else take_off


def category(aircraft_type: str) -> str:
    """"super", "heavy", "medium" or "light" (medium when the type isn't known: an airliner's needs)."""
    code = (aircraft_type or "").upper()
    if not code:
        return "medium"
    if code.startswith(SMALL_TYPES):
        return "light"
    return {"J": "super", "H": "heavy", "L": "light"}.get(wake_category(code), "medium")


def pick(ends: Sequence[RunwayEndGeometry], *, landing: bool, aircraft_type: str = "",
         xy: tuple[float, float] | None = None, load: dict[str, int] | None = None,
         fallback: Sequence[RunwayEndGeometry] = ()) -> RunwayEndGeometry | None:
    """The runway for one flight (see the module). ``xy``: where it is now (its gate, or in the air), in the airport's
    frame; ``load``: how many flights each runway has been given lately; ``fallback``: the airport's other open
    runways that the wind allows, for when none in use is long enough."""
    if not ends:
        return None
    need = needed_m(aircraft_type, landing=landing)
    fits = [e for e in ends if e.runway.runway.length_m >= need]
    if not fits:
        longer = [e for e in fallback if e.runway.runway.length_m >= need]
        fits = longer[:1] or [max(ends, key=lambda e: e.runway.runway.length_m)]
    load = load or {}

    def cost(e: RunwayEndGeometry) -> tuple[float, float]:
        if xy is None:
            distance = 0.0
        elif landing:
            c = math.radians(e.heading_true)
            entry = (e.threshold[0] - math.sin(c) * FINAL_NM * 1852.0, e.threshold[1] - math.cos(c) * FINAL_NM * 1852.0)
            distance = math.dist(xy, entry)
        else:
            distance = math.dist(xy, e.threshold)
        # A runway that has had two more flights than another lately is worth about 2 km more taxi or 4 km more flying.
        per_flight = 1000.0 if not landing else 2000.0
        return distance + per_flight * load.get(e.ident, 0), -e.runway.runway.length_m

    return min(fits, key=cost)


def keep(given: str | None, ends: Sequence[RunwayEndGeometry]) -> bool:
    """A runway given earlier is still one in use for it."""
    return bool(given) and any(e.ident == given for e in ends)


def _parallel_groups(ends: Sequence[RunwayEndGeometry]) -> list[list[RunwayEndGeometry]]:
    """The ends the same way and close enough to work as one complex (``SEGREGATE_M``), grouped."""
    groups: list[list[RunwayEndGeometry]] = []
    for e in ends:
        for g in groups:
            same_way = abs(((g[0].heading_true - e.heading_true) + 540) % 360 - 180) <= PARALLEL_DEG
            if same_way and all(abs(o.runway.along_across(e.runway.center)[1]) <= SEGREGATE_M for o in g):
                g.append(e)
                break
        else:
            groups.append([e])
    return groups


def _from_terminal(e: RunwayEndGeometry, terminal: tuple[float, float] | None) -> float:
    centre = e.runway.center
    return math.dist(centre, terminal) if terminal is not None else 0.0


def terminal_xy(geo: AirportGeometry) -> tuple[float, float] | None:
    """The middle of the airport's gates (where the terminal is), in its frame; None with no gates."""
    spots = [geo.xy(p.lat, p.lon) for p in geo.airport.parking]
    if not spots:
        return None
    return sum(x for x, _ in spots) / len(spots), sum(y for _, y in spots) / len(spots)
