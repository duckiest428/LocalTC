"""What the Live Map draws for ATC: every controller the flight talks to, and where each one's airspace is.

It draws what the engine uses, not a picture of it: the centre outlines ATC hands over at, the approach
areas that decide when departure lets go and approach takes over, and the stretch of final where approach
clears the approach and sends the flight to tower. Before a flight starts, the same comes from the flight
plan, so the zones can be looked at while planning.

For the VFR map it also gives the airspace class around every airport in view (``atc_core/airport/classes.py``):
Class B shelves, Class C's inner and outer circles, Class D, or an ICAO control zone or traffic zone, each
with its floor and ceiling the way a VFR chart prints them.
"""

import math
from collections.abc import Callable
from itertools import pairwise
from typing import Any

from localtc.atc_core.airport import AirportGeometry, classes
from localtc.atc_core.airspace import Airspace, Area
from localtc.atc_core.engine import JOIN_FINAL_NM, JOIN_LATERAL_NM
from localtc.atc_core.facilities import airport_facilities
from localtc.atc_core.region import region_for
from localtc.sim_api import Airport
from localtc.sim_api.geo import unit

TOWER_NM = 5.0  # a tower's control zone as drawn: the usual surface area around a towered field
ROUTE_SAMPLE_NM = 20.0  # how finely the route is walked to find the centres it passes through
VFR_MAX_AREA = 60.0  # square degrees: zoomed out further, the VFR layer shows only the flight's own airports
VFR_MAX_AIRPORTS = 400


def zones(engine: Any, plan: Any, airport: Callable[[str], Airport | None],
          bounds: tuple[float, float, float, float] | None, known: list[dict] | None = None) -> dict[str, Any]:
    """The map's ATC layer. ``engine`` is the running flight's (None before one starts), ``plan`` the flight
    plan, ``airport`` finds an airport's data, ``bounds`` (south, west, north, east) is the map's view, and
    ``known`` the airports LocalTC has data for (the lookup index: icao, name, lat, lon, elev_ft, tower,
    approach), for the VFR layer."""
    airspace = engine.airspace if engine is not None else Airspace.load()
    origin, destination = _ends(engine, plan)
    own = engine.state.aircraft if engine is not None else None
    tuned = engine.state.comms.tuned if engine is not None else None
    expected = engine.state.comms.expected if engine is not None else None

    route = _route_points(plan, [airport(icao) for icao in (origin, destination) if icao])
    if own is not None and not own.on_ground:
        route.append((own.lat, own.lon))
    on_route = {a.id for lat, lon in route if (a := airspace.center_at(lat, lon)) is not None}
    here = airspace.center_at(own.lat, own.lon) if own is not None else None
    shown = {a.id: a for a in airspace.centers if a.id in on_route}
    if bounds is not None:
        south, west, north, east = bounds
        if (north - south) * (east - west) < 3000:  # beyond about a continent in view, just the route's
            shown.update({a.id: a for a in airspace.near(south, west, north, east)})

    center_station = engine.current_center().station if engine is not None else None
    centers = [_area(a, active=here is not None and a.id == here.id, route=a.id in on_route,
                     working=center_station == a.name) for a in shown.values()]
    # A centre's own label point is the middle of it, often hundreds of miles off the route; the ones the
    # flight passes through are labelled where it passes through them instead.
    for c in centers:
        inside = [p for p in route if shown[c["id"]].contains(*p)]
        if inside:
            c["label"] = list(inside[len(inside) // 2])

    airports, terminals, final = [], [], None
    for icao, role in ((origin, "departure"), (destination, "arrival")):
        found = airport(icao) if icao else None
        if found is None or (role == "arrival" and icao == origin and airports):
            continue
        facilities = ([f for f in engine.facilities if f.airport == icao] if engine is not None
                      else airport_facilities(found, role=role))
        airports.append({
            "icao": icao, "name": found.name, "lat": found.lat, "lon": found.lon, "role": role,
            "tower_nm": TOWER_NM if any(f.controller == "tower" for f in facilities) else 0,
            "stations": [{"controller": f.controller, "station": f.station, "mhz": f.mhz,
                          "tuned": tuned == f, "next": expected == f and tuned != f} for f in facilities],
        })
        area = _terminal(engine, airspace, found, "departure" if role == "departure" else "approach")
        if area is not None:
            working = tuned is not None and tuned.controller in ("departure", "approach") and tuned.airport == icao
            terminals.append({**_area(area, active=own is not None and area.contains(own.lat, own.lon),
                                      working=working), "icao": icao, "role": role})
        if role == "arrival":
            final = _final(engine, found, plan)

    return {
        "gate": _gate(engine),
        "centers": centers, "terminals": terminals, "airports": airports, "final": final,
        "tuned": {"station": tuned.station, "controller": tuned.controller, "mhz": tuned.mhz} if tuned else None,
        "next": {"station": expected.station, "controller": expected.controller, "mhz": expected.mhz}
        if expected is not None and expected != tuned else None,
        "center": here.name if here is not None else None,
        "rules": _rules(engine, plan),
        "classes": vfr_classes(known or [], bounds, [a["icao"] for a in airports]),
    }


def _rules(engine: Any, plan: Any) -> str:
    """IFR or VFR: the running flight's, else the flight plan's. The map opens in the matching view."""
    if engine is not None:
        return getattr(engine.state.flight, "rules", "IFR") or "IFR"
    return getattr(plan, "rules", "IFR") if plan is not None else "IFR"


def vfr_classes(known: list[dict], bounds: tuple[float, float, float, float] | None,
                always: list[str] = ()) -> list[dict[str, Any]]:
    """The controlled airspace around the airports in view, and the other airports as plain markers.

    Each ring has a radius and the floor and ceiling in feet MSL (floor 0 is the surface), for labels like
    a VFR chart's "100/SFC". ``always`` are the flight's own airports, drawn even zoomed far out."""
    if bounds is not None:
        south, west, north, east = bounds
        wide = (north - south) * (east - west) > VFR_MAX_AREA

        def inside(a: dict) -> bool:
            lon = a["lon"] if west <= east or a["lon"] >= west else a["lon"] + 360  # across the date line
            return south <= a["lat"] <= north and west <= lon <= (east if west <= east else east + 360)
    else:
        wide, inside = True, (lambda a: False)
    chosen = [a for a in known if a["icao"] in always or (not wide and inside(a))]
    chosen.sort(key=lambda a: (a["icao"] not in always, not a.get("tower"), -a.get("runway_m", 0)))
    return [_vfr_airport(a) for a in chosen[:VFR_MAX_AIRPORTS]]


def _vfr_airport(a: dict) -> dict[str, Any]:
    icao, elev = a["icao"], float(a.get("elev_ft") or 0)
    zone = classes.zone_for(icao, towered=bool(a.get("tower")), approach=bool(a.get("approach")),
                            icao_region=region_for(icao).icao)

    def agl(ft: float) -> int:  # above the field, in feet MSL, rounded to the hundred as a chart prints it
        return round((elev + ft) / 100) * 100

    if zone.kind == "B":
        rings = [{"nm": 10, "floor": 0, "ceiling": 10000}, {"nm": 20, "floor": agl(3000), "ceiling": 10000},
                 {"nm": 30, "floor": agl(6000), "ceiling": 10000}]
    elif zone.kind == "C":
        rings = [{"nm": 5, "floor": 0, "ceiling": agl(4000)}, {"nm": 10, "floor": agl(1200), "ceiling": agl(4000)}]
    elif zone.kind:
        rings = [{"nm": zone.radius_nm, "floor": 0, "ceiling": agl(zone.ceiling_agl_ft)}]
    else:
        rings = []
    return {"icao": icao, "name": a.get("name", ""), "lat": a["lat"], "lon": a["lon"], "class": zone.kind,
            "label": zone.name, "towered": bool(a.get("tower")), "rings": rings}


def _ends(engine: Any, plan: Any) -> tuple[str | None, str | None]:
    if engine is not None:
        return engine.state.flight.origin, engine.state.flight.destination
    if plan is not None:
        return plan.origin or None, plan.destination or None
    return None, None


def _route_points(plan: Any, ends: list[Airport | None]) -> list[tuple[float, float]]:
    """Points along the planned route close enough together that no centre it crosses is missed."""
    points = [(f.lat, f.lon) for f in (plan.fixes if plan is not None else [])]
    if not points:
        points = [(a.lat, a.lon) for a in ends if a is not None]
    out: list[tuple[float, float]] = []
    for (lat1, lon1), (lat2, lon2) in pairwise(points):
        steps = max(1, int(math.hypot(lat2 - lat1, (lon2 - lon1) * math.cos(math.radians(lat1))) * 60 / ROUTE_SAMPLE_NM))
        out += [(lat1 + (lat2 - lat1) * i / steps, lon1 + (lon2 - lon1) * i / steps) for i in range(steps)]
    return out + points[-1:]


def _terminal(engine: Any, airspace: Airspace, found: Airport, role: str) -> Area | None:
    if engine is not None:
        return engine.terminal_area(found.icao, role)
    area = airspace.approach_for(found.icao, found.lat, found.lon, role=role)
    return area if area is not None and area.contains(found.lat, found.lon) else None


def _final(engine: Any, found: Airport, plan: Any) -> dict[str, Any] | None:
    """The stretch of final where approach clears the approach: JOIN_LATERAL_NM either side of the extended
    centreline, out to JOIN_FINAL_NM, for the runway the flight is expected to land on."""
    geo = engine.geometry(found.icao) if engine is not None else AirportGeometry(found)
    runway = engine.state.assignments.arrival_runway if engine is not None else None
    runway = runway or (plan.arr_runway if plan is not None and getattr(plan, "arr_runway", "") else None)
    end = geo.end(runway) if geo is not None and runway else None
    if end is None:
        return None
    ux, uy = unit(end.heading_true)
    x0, y0 = end.threshold
    near_m, out_m, side_m = 1852.0, JOIN_FINAL_NM * 1852.0, JOIN_LATERAL_NM * 1852.0  # as _joining_final tests it

    def at(back: float, across: float) -> tuple[float, float]:
        return x0 - ux * back + uy * across, y0 - uy * back - ux * across

    corners = [at(near_m, -side_m), at(near_m, side_m), at(out_m, side_m), at(out_m, -side_m)]
    ring = [list(geo.frame.to_latlon(x, y)) for x, y in corners]
    return {"icao": found.icao, "runway": end.ident, "ring": [[round(a, 5), round(b, 5)] for a, b in ring]}


def _gate(engine: Any) -> dict[str, Any] | None:
    """The stand ground assigned at the destination, once it has."""
    a = engine.state.assignments if engine is not None else None
    geo = engine.geometry(engine.state.flight.destination) if a is not None and a.gate_index is not None else None
    spot = next((s for s in geo.airport.parking if s.index == a.gate_index), None) if geo is not None else None
    if spot is None:
        return None
    return {"icao": geo.airport.icao, "name": a.gate, "lat": spot.lat, "lon": spot.lon}


def _area(area: Area, **flags: bool) -> dict[str, Any]:
    return {"id": area.id, "name": area.name, "kind": area.kind, "label": list(area.label),
            "rings": [[[lat, lon] for lat, lon in ring] for ring in area.rings], **flags}
