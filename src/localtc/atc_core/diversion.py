"""Emergencies: the nearest suitable airport, and vectors to it.

With an emergency declared far from the destination, ATC looks for somewhere closer to land:

- **Candidates** are the airports the engine has the layout of: the origin, the destination, and the ones
  around the aircraft. The sim lists those (``NearbyAirports``, every minute); the moment an emergency is
  declared, the engine asks for their layouts, nearest first.
- **Suitable** means a hard runway long enough for the aircraft, from its weight (``runway_needed_ft``):
  2,500 ft for a light single, 8,500 ft for a heavy.
- **Best** is the nearest, with an ILS and a tower each counting as a few miles closer. At nearly the same
  distance, the airport that can bring the aircraft in on instruments, with the fire trucks on the field,
  wins.

A few seconds after the emergency, once the layouts are in, ATC offers it: "you are number one. The
nearest suitable airport is Yuma, 38 miles southeast, runway 21R, 13,300 feet, ILS. Say intentions." The
destination still gets "cleared direct" when it's close (``KEEP_DESTINATION_NM``) or nothing suitable is
much closer (``CLOSER_BY_NM``).

The pilot asks for it ("request vectors to the nearest suitable airport"), or names another ("divert to
Blythe"). The diversion airport becomes the destination, and ATC turns the aircraft towards it with a
descent at the pilot's discretion. A new heading follows whenever the aircraft is well off the way there,
until approach clears the approach and tower the landing, as for any arrival (sooner, in an emergency).
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from localtc.atc_core.airport import AirportGeometry
from localtc.atc_core.airport.classes import bearing_words
from localtc.atc_core.phraseology import speech
from localtc.sim_api import NearbyAirport, NearbyAirports, OwnshipState
from localtc.sim_api.geo import bearing_deg, haversine_nm

if TYPE_CHECKING:
    from localtc.atc_core.airport.geometry import RunwayEndGeometry
    from localtc.atc_core.facilities import Facility
    from localtc.atc_core.readback import Interpretation
    from localtc.atc_core.values import Phrase

FETCH_AT_ONCE = 8  # airports around the aircraft whose layouts are asked for at a time
RANGE_NM = 150.0  # how far ATC looks for a diversion
KEEP_DESTINATION_NM = 40.0  # the destination this close is where the flight goes
CLOSER_BY_NM = 20.0  # somewhere else is offered only when it's at least this much closer than the destination
ILS_BONUS_NM, TOWER_BONUS_NM = 8.0, 5.0  # an ILS, and a tower, each count as this much closer
OFFER_WAIT_S = 15.0  # waiting this long at most for the layouts before saying what is known
VECTOR_UNTIL_NM = 12.0  # inside this, approach's vectors onto the final, or tower, take over
REVECTOR_DEG = 20.0  # this far off the way to the airport, a new heading
REVECTOR_GAP_S = 60.0
FINAL_GATE_NM = 40.0  # inside this, vectors aim for the final of the runway to expect, not the field
DESCENT_FT_PER_NM = 318  # a three degree path down
RADAR = ("departure", "center", "approach")

# MSFS runway SURFACE codes that are no place for an emergency landing: grass, water, snow, ice, dirt, sand, ...
SOFT_SURFACES = {1, 2, 3, 5, 6, 7, 8, 9, 11, 12, 13, 14, 20, 21, 22, 24}
M_TO_FT = 3.28084


def runway_needed_ft(gross_weight_lb: float | None) -> int:
    """The shortest runway worth sending the aircraft to, from its weight (unknown: a mid-size aircraft)."""
    if not gross_weight_lb:
        return 5000
    for heaviest, needed in ((6000, 2500), (12500, 3500), (41000, 5000), (150000, 6000), (300000, 7500)):
        if gross_weight_lb <= heaviest:
            return needed
    return 8500


@dataclass(frozen=True)
class Diversion:
    icao: str
    name: str  # as ATC says it: "Yuma", "Palm Springs"
    distance_nm: float
    bearing: str  # where it is from the aircraft: "southeast"
    runway: str  # the runway end to expect
    runway_ft: int
    ils: bool
    towered: bool
    score: float  # distance, less the bonuses: lower is better


def _end_score(end: "RunwayEndGeometry", wind_dir_true: float, wind_kt: float) -> tuple[float, int, float]:
    headwind = wind_kt * math.cos(math.radians(wind_dir_true - end.heading_true)) if wind_kt >= 5 else 0.0
    return round(headwind), int(end.has_ils), end.runway.runway.length_m


def diversion_at(geo: AirportGeometry, lat: float, lon: float, need_ft: int, wind: tuple[float, float] = (0.0, 0.0)
                 ) -> Diversion | None:
    """``geo`` as a diversion from (lat, lon), or None when no runway there is hard and long enough."""
    usable = [e for e in geo.ends if e.runway.runway.surface not in SOFT_SURFACES
              and e.runway.runway.length_m * M_TO_FT >= need_ft]
    if not usable:
        return None
    end = max(usable, key=lambda e: _end_score(e, *wind))
    airport = geo.airport
    ils = any(e.has_ils for e in usable) or any(a.kind == "ils" for a in airport.approaches)
    towered = bool(airport.frequencies_of("tower"))
    distance = haversine_nm(lat, lon, airport.lat, airport.lon)
    return Diversion(icao=airport.icao, name=speech.airport_name(airport.name, airport.icao), distance_nm=distance,
                     bearing=bearing_words(lat, lon, airport.lat, airport.lon), runway=end.ident,
                     runway_ft=round(end.runway.runway.length_m * M_TO_FT), ils=ils, towered=towered,
                     score=distance - (ILS_BONUS_NM if ils else 0.0) - (TOWER_BONUS_NM if towered else 0.0))


def diversions(airports: Iterable[AirportGeometry], lat: float, lon: float, need_ft: int,
               wind: tuple[float, float] = (0.0, 0.0), *, within_nm: float = RANGE_NM) -> list[Diversion]:
    """The suitable airports within range, best first."""
    found = [d for geo in airports if (d := diversion_at(geo, lat, lon, need_ft, wind)) is not None
             and d.distance_nm <= within_nm]
    return sorted(found, key=lambda d: (d.score, d.distance_nm))


@dataclass
class DiversionState:
    nearby: tuple[NearbyAirport, ...] = ()  # the sim's latest list of the airports around
    fetching: set[str] = field(default_factory=set)  # layouts asked for, for a diversion
    offer_due_t: float | None = None  # the offer waits for the layouts until then
    offered: str | None = None  # the airport ATC suggested
    asked: bool = False  # the pilot asked for a diversion along with the emergency
    asked_fix: str | None = None  # ... to this airport (None: the nearest suitable)
    target: str | None = None  # the airport the flight is diverting to
    vector_t: float = -math.inf  # when ATC last gave a heading towards it


class DiversionMixin:
    """The engine's part of it. Mixed into ``AtcEngine``: ``self`` is the engine."""

    diversion: DiversionState

    # --- the airports around ------------------------------------------------------------------------------

    def _on_nearby(self: Any, event: NearbyAirports) -> None:
        self.diversion.nearby = event.airports
        if "emergency" in self.state.flags and self.diversion.target is None:
            self._fetch_diversions()  # the aircraft has moved on: the next ones around

    def _fetch_diversions(self: Any) -> None:
        """Ask for the layouts of the nearest airports not known yet, a few at a time."""
        own = self.state.aircraft
        if own is None:
            return
        known = self.tracker.context_builder.airports
        around = sorted((a for a in self.diversion.nearby if a.icao and a.icao not in known and a.icao not in self._requested),
                        key=lambda a: haversine_nm(own.lat, own.lon, a.lat, a.lon))
        for airport in around[:FETCH_AT_ONCE]:
            if haversine_nm(own.lat, own.lon, airport.lat, airport.lon) > RANGE_NM:
                break
            self._requested.add(airport.icao)
            self.airport_requests.append(airport.icao)
            self.diversion.fetching.add(airport.icao)

    def _diversions_arriving(self: Any) -> bool:
        return bool(self.diversion.fetching - set(self.tracker.context_builder.airports))

    def _diversion_options(self: Any, own: OwnshipState, need_ft: int | None = None) -> list[Diversion]:
        need = runway_needed_ft(own.gross_weight_lb) if need_ft is None else need_ft
        wind = (own.wind_dir_true, own.wind_kt) if own.alt_agl_ft < 3000 else (0.0, 0.0)
        return diversions(self.tracker.context_builder.airports.values(), own.lat, own.lon, need, wind)

    def _diversion_offer(self: Any, own: OwnshipState) -> Diversion | None:
        """Somewhere better than the destination to land, or None: the destination it is."""
        options = self._diversion_options(own)
        if not options:
            return None
        best, dest = options[0], self.state.flight.destination
        here = next((o for o in options if o.icao == dest), None)
        if best.icao == dest or (here is not None and (here.distance_nm <= KEEP_DESTINATION_NM
                                                       or here.distance_nm - best.distance_nm < CLOSER_BY_NM)):
            return None
        return best

    # --- an emergency: priority, and where to go -------------------------------------------------------------

    def _emergency_started(self: Any, t: float) -> None:
        """Just declared: start fetching the airports around, so the offer has something to go on."""
        self._fetch_diversions()
        self.diversion.offer_due_t = t + OFFER_WAIT_S

    def _priority_ready(self: Any, t: float) -> bool:
        due = self.diversion.offer_due_t
        return due is None or t >= due or not self._diversions_arriving()

    def _emergency_priority(self: Any, t: float, own: OwnshipState, facility: "Facility") -> None:
        """Number one, and either direct to the destination or the nearest suitable airport, to say intentions."""
        st = self.state
        st.flags.add("emergency_priority")
        st.flags.discard("emergency_priority_due")
        offer = self._diversion_offer(own)
        if offer is None:
            destination = self._airport_name(st.flight.destination)
            # Told, not asked: a crew dealing with an emergency is not chased for a readback.
            self._schedule(t, "common.emergency_priority", {"destination": destination}, facility, delay=True,
                           expects_readback=False)
            return
        self.diversion.offered = offer.icao
        self._schedule(t, "common.emergency_nearest", {"message": self._describe(offer)}, facility, delay=True,
                       expects_readback=False)

    def _describe(self: Any, d: Diversion) -> "Phrase":
        parts: list[tuple[str, dict[str, Any]]] = [
            ("{destination}, {distance} miles {bearing}", {"destination": d.name, "distance": round(d.distance_nm),
                                                           "bearing": d.bearing}),
            ("runway {runway}, {length} feet", {"runway": d.runway, "length": d.runway_ft}),
        ]
        if d.ils:
            parts.append(("ILS available", {}))
        return self._phrases(parts)

    # --- the pilot's intentions ---------------------------------------------------------------------------

    def _divert_asked(self: Any, t: float, own: OwnshipState, facility: "Facility") -> None:
        """The diversion asked for with the emergency call, now that the airports around are known."""
        from localtc.atc_core.readback import Interpretation

        self.diversion.asked = False
        fix = self.diversion.asked_fix
        self._divert(Interpretation(kind="request", intent="request_diversion", text="",
                                    values={"fix": fix} if fix else {}), facility, t, own)

    def _divert(self: Any, interp: "Interpretation", facility: "Facility", t: float, own: OwnshipState | None) -> None:
        """ "Request vectors to the nearest suitable airport", "divert to Yuma": vectors there, and it's the
        destination from now on."""
        if not self._airborne_controller(facility, own):
            self._schedule(t, "common.unable", {}, facility)
            return
        wanted = interp.values.get("fix")
        options = self._diversion_options(own)
        if wanted:
            target = self._named_diversion(str(wanted), own)
        elif self.diversion.offered is not None:
            target = next((o for o in options if o.icao == self.diversion.offered), None) or (options[0] if options else None)
        else:
            target = options[0] if options else None
        if target is None:
            if options:  # an airport ATC doesn't know: say what is there
                self._schedule(t, "common.nearest", {"message": self._describe(options[0])}, facility, expects_readback=False)
                self.diversion.offered = options[0].icao
            else:
                self._schedule(t, "common.unable", {}, facility)
            return
        self._divert_to(target, t, own, facility)

    def _named_diversion(self: Any, wanted: str, own: OwnshipState) -> Diversion | None:
        """The known airport the pilot named, by its code or a word of its name. The pilot's choice goes, even
        where ATC wouldn't have picked it: only a runway is needed."""
        wanted_words = {w.lower() for w in wanted.split() if len(w) > 2}
        for icao, geo in self.tracker.context_builder.airports.items():
            words = {w.lower() for w in speech.airport_name(geo.airport.name, icao).split() if len(w) > 3}
            if wanted.replace(" ", "").upper() == icao or wanted_words & words:
                return diversion_at(geo, own.lat, own.lon, 0)
        return None

    def _divert_to(self: Any, target: Diversion, t: float, own: OwnshipState, facility: "Facility") -> None:
        st, d = self.state, self.diversion
        self._new_destination(target.icao)
        st.flags.add("emergency_priority")  # the diversion is the priority: no "cleared direct" to the old one
        st.flags.discard("emergency_priority_due")
        d.target, d.offered, d.vector_t = target.icao, None, t
        geo = self.geometry(target.icao)
        plan = self._arrival_plan(own)
        runway = plan["approach"].runway if plan else target.runway
        heading = self._diversion_heading(own, geo, runway)
        altitude = self._diversion_altitude(own, geo)
        turn = self._turn_towards(own.hdg_mag, heading) or ("right" if ((heading - own.hdg_mag) % 360) < 180 else "left")
        template = "common.divert" if own.alt_indicated_ft > altitude + 300 else "common.divert_level"
        slots = {"turn": turn, "heading": heading, "destination": target.name, "distance": round(target.distance_nm),
                 "altitude": altitude, "runway": runway}
        self._schedule(t, template, slots, facility,
                       on_issue=lambda: self._assign(heading=heading, altitude_ft=altitude, arrival_runway=runway),
                       note=self._altimeter_note(target.icao))
        st.flags.add("descend")  # the diversion clearance is the descent

    def _new_destination(self: Any, icao: str) -> None:
        """Somewhere else to land: the arrival flow starts over for it (a return, or a diversion)."""
        st = self.state
        st.flight.destination = icao
        self.tracker.context_builder.destination = icao
        self._assign(arrival_runway=None, approach=None, arrival_atis=None)
        self._approach_kind = None
        self._vector_leg = None
        for flag in ("descend", "handoff_approach", "handoff_center"):
            st.flags.discard(flag)
        for kind in ("approach", "landing"):
            st.clearances.pop(kind, None)
        self._rebuild_facilities()

    def _diversion_heading(self: Any, own: OwnshipState, geo: AirportGeometry | None, runway: str | None) -> int:
        """A magnetic heading, in tens: for the final of the runway once close and low enough to make it, for the
        field before that (high above it, the crew needs the room to get down however they choose)."""
        if geo is not None and runway and (distance := geo.distance_nm(own.lat, own.lon)) <= FINAL_GATE_NM \
                and own.alt_indicated_ft - geo.airport.elev_ft <= 1.5 * distance * DESCENT_FT_PER_NM + 3000 \
                and (heading := self._vector_heading(own, runway)) is not None:
            return heading
        lat, lon = (geo.airport.lat, geo.airport.lon) if geo is not None else (own.lat, own.lon)
        true = bearing_deg(own.lat, own.lon, lat, lon)
        magvar = ((own.hdg_true - own.hdg_mag + 180) % 360) - 180
        return int(round(((true - magvar) % 360) / 10) * 10) % 360 or 360

    def _diversion_altitude(self: Any, own: OwnshipState, geo: AirportGeometry | None) -> int:
        """Down towards a three degree path to the field, never below 3,000 ft above it (2,000 close in), and
        never a climb: level already low enough, the altitude being flown."""
        flying = int(round(own.alt_indicated_ft / 100.0) * 100)
        if geo is None:
            return flying
        distance = geo.distance_nm(own.lat, own.lon)
        field_ft = geo.airport.elev_ft
        path = field_ft + distance * DESCENT_FT_PER_NM
        floor = field_ft + (3000 if distance > 15 else 2000)
        wanted = int(math.ceil(max(path, floor) / 1000.0) * 1000)
        return wanted if wanted < flying - 300 else flying

    # --- keeping it pointed there -----------------------------------------------------------------------------

    def _diversion_vectors(self: Any, own: OwnshipState) -> bool:
        """A new heading when the aircraft is well off the way to the diversion airport."""
        st, d, t = self.state, self.diversion, own.t
        tuned = st.comms.tuned
        if d.target is None or st.flight.destination != d.target or tuned is None or tuned.controller not in RADAR:
            return False
        if "approach" in st.clearances or st.pending is not None or t - d.vector_t < REVECTOR_GAP_S:
            return False
        geo = self.geometry(d.target)
        if geo is None or (distance := geo.distance_nm(own.lat, own.lon)) <= VECTOR_UNTIL_NM:
            return False
        heading = self._diversion_heading(own, geo, st.assignments.arrival_runway)
        off = abs(((heading - own.hdg_mag + 540) % 360) - 180)
        turn = self._turn_towards(own.hdg_mag, heading)
        if off < REVECTOR_DEG or turn is None:
            return False
        d.vector_t = t
        self._schedule(t, "common.divert_vector", {"turn": turn, "heading": heading, "destination": self._airport_name(d.target),
                                                   "distance": round(distance)}, tuned, delay=False,
                       on_issue=lambda: self._assign(heading=heading))
        return True
