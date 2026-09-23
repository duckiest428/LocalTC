"""VFR: what ATC does differently for a flight under visual flight rules.

The engine runs the same phases, taxi routing, runway checks, traffic calls and airspace for a VFR flight;
this is the part that isn't the IFR flow:

- **Departure.** Out of Class B (or an ICAO control zone), clearance delivery gives a VFR departure
  clearance: "cleared out of the Class Bravo airspace northbound, maintain VFR at or below 4,500...". Tower
  clears the takeoff with the way out ("northbound departure approved"), and once the aircraft is clear
  of the field, "frequency change approved", or a handoff to departure for flight following.
- **Flight following.** Asked for (or by calling departure, centre or approach), ATC assigns a code,
  identifies the aircraft ("radar contact, 12 miles south of Paine Field, altimeter 30.01"), hands it from
  sector to sector, and near the destination terminates the service and sends it to tower.
- **Class B.** Entering it without "cleared into the Class Bravo airspace" is an alert; asking for it
  gets the clearance.
- **Arrival.** Calling tower inbound: "enter left downwind runway 34, report midfield" (the side the aircraft
  is already on), or straight-in when lined up. Reporting in the pattern gets the landing clearance;
  asking for the option or a touch and go gets that, and after the touch and go, closed traffic.

With ICAO phraseology the same calls are worded for control zones and circuits (templates/icao/vfr.toml).
"""

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from localtc.atc_core.airport import classes
from localtc.atc_core.phase import FlightPhase as P
from localtc.atc_core.phraseology import speech
from localtc.atc_core.region import region_for
from localtc.atc_core.values import Phrase
from localtc.sim_api import AtcAlert, OwnshipState
from localtc.sim_api.geo import unit

if TYPE_CHECKING:
    from localtc.atc_core.facilities import Facility
    from localtc.atc_core.readback import Interpretation

CLEAR_OF_FIELD_NM = 2.0  # a departure this far out, and above FREQUENCY_CHANGE_AGL_FT, is told to go
FREQUENCY_CHANGE_AGL_FT = 1000
APPROACH_HANDOFF_NM = 25.0  # flight following into Class B/C: approach takes over by here
TERMINATE_NM = 15.0  # and radar service ends here, with a handoff to tower
PATTERN_FINAL_NM = 2.0
STRAIGHT_IN_NM = 12.0
STRAIGHT_IN_OFFSET_NM = 1.5
STRAIGHT_IN_HEADING_DEG = 30.0
VFR_LIMIT_FT = 4500  # "at or below" when nothing better is known
AIRBORNE_CONTROLLERS = ("departure", "center", "approach")


@dataclass
class VfrState:
    direction: str | None = None  # "northbound", "straight-out", or "closed-traffic" for pattern work
    following: bool = False  # flight following asked for (or implied by calling a radar controller)
    identified: bool = False  # "radar contact" given
    want_class_b: bool = False  # asked for Class B before being identified: cleared right after
    class_b_cleared: bool = False
    option: str | None = None  # "touch_and_go", "option" or "full_stop"
    pattern_side: str = "left"
    circuits: int = 0
    b_alerted: set[str] = field(default_factory=set)


class VfrMixin:
    """VFR handling for AtcEngine. Everything here uses the engine's own state and helpers."""

    state: Any
    cfg: Any
    tracker: Any
    vfr: VfrState

    @property
    def _vfr(self) -> bool:
        return (self.state.flight.rules or "IFR").upper() == "VFR"

    @property
    def _vfr_in_pattern(self) -> bool:
        """Working the pattern: closed traffic asked for, a touch and go or the option, or a circuit flown."""
        v = self.vfr
        return bool(v.circuits) or v.direction == "closed-traffic" or v.option in ("touch_and_go", "option")

    # --- the airspace ------------------------------------------------------------------------------------

    def _zone(self, icao: str | None) -> classes.Zone:
        if not icao:
            return classes.NONE
        towered = any(f.airport == icao and f.controller == "tower" for f in self.facilities)
        approach = any(f.airport == icao and f.controller in ("approach", "departure") for f in self.facilities)
        return classes.zone_for(icao, towered=towered, approach=approach, icao_region=region_for(icao).icao)

    def _vfr_class_b_watch(self, own: OwnshipState) -> AtcAlert | None:
        """A VFR aircraft inside Class B without a clearance into it: one alert per Class B."""
        if not self._vfr or own.on_ground or self.vfr.class_b_cleared:
            return None
        for icao, geo in self.tracker.context_builder.airports.items():
            if icao not in classes.CLASS_B or icao in self.vfr.b_alerted:
                continue
            departing_through = icao == self.state.flight.origin and "vfr_departure" in self.state.clearances
            if departing_through:
                continue  # "cleared out of the Bravo": the clearance covers the climb out
            if classes.inside_class_b(classes.zone_for(icao, towered=True, approach=True, icao_region=False),
                                      geo, own.lat, own.lon, own.alt_msl_ft):
                self.vfr.b_alerted.add(icao)
                return self._alert(own.t, "class_b_entry", f"entered the {icao} Class Bravo without a clearance")
        return None

    # --- the pilot's calls -------------------------------------------------------------------------------

    def _vfr_request(self, interp: "Interpretation", facility: "Facility", t: float, own: OwnshipState | None) -> bool:
        """A VFR flight's call, if it's one VFR handles differently. True when answered here."""
        values, intent = interp.values, interp.intent
        if values.get("direction"):
            self.vfr.direction = values["direction"]
        if values.get("flight_following"):
            self.vfr.following = True
        if values.get("option"):
            self.vfr.option = values["option"]
        airborne = own is not None and not own.on_ground
        if intent == "request_ifr_clearance":
            if airborne:
                self._vfr_class_b(t, facility, own)
            else:
                self._vfr_departure_clearance(t, facility)
            return True
        if intent == "request_class_b":
            if airborne:
                self._vfr_class_b(t, facility, own)
                return True
            return False
        if intent == "request_flight_following":
            if airborne and facility.controller in AIRBORNE_CONTROLLERS:
                self._vfr_follow(t, facility, own)
            else:
                self._schedule(t, "common.roger", {}, facility, expects_readback=False)
            return True
        if intent == "checkin" and airborne and facility.controller in AIRBORNE_CONTROLLERS and values.get("class_b"):
            self.vfr.want_class_b = True
            return False  # the check-in itself goes on to _vfr_checkin
        if intent == "report_final" and airborne:
            if facility.controller in AIRBORNE_CONTROLLERS and self.vfr.following:
                self._vfr_terminate(t, facility)  # "field in sight" to approach: over to tower
                return True
            if facility.controller == "tower" and not self._vfr_close_final():
                self._vfr_enter_pattern(t, facility, own)
                return True
            return False  # on final: the landing clearance, as for anyone
        if intent == "position_report" and airborne and facility.controller == "tower":
            if "landing" not in self.state.clearances:
                self._clear_to_land(t, own, facility, delay=True, runway=self.state.assignments.arrival_runway)
            else:
                self._schedule(t, "common.roger", {}, facility, expects_readback=False)
            return True
        if intent == "going_around" and airborne and facility.controller == "tower":
            self._vfr_touch_and_go(t)  # VFR goes around into the pattern: closed traffic, no missed approach
            return True
        if intent == "request_option":
            if "landing" in self.state.clearances and facility.controller == "tower" and airborne:
                self.state.clearances.pop("landing", None)
                self._clear_to_land(t, own, facility, delay=True, runway=self.state.assignments.arrival_runway)
            else:
                self._schedule(t, "common.roger", {}, facility, expects_readback=False)
            return True
        return False

    def _vfr_checkin(self, t: float, facility: "Facility", own: OwnshipState | None) -> bool:
        """Calling a radar controller in flight is asking for flight following; calling tower inbound, for the
        pattern."""
        if own is None or own.on_ground:
            return False
        if facility.controller in AIRBORNE_CONTROLLERS:
            if self.vfr.identified:
                self._schedule(t, "common.roger", {}, facility, expects_readback=False)
            else:
                self._vfr_follow(t, facility, own)
            return True
        if facility.controller == "tower" and P(self.state.phase) in (P.ARRIVAL, P.APPROACH, P.CRUISE):
            self._vfr_enter_pattern(t, facility, own)
            return True
        return False

    # --- ATC's side --------------------------------------------------------------------------------------

    def _vfr_departure_clearance(self, t: float, facility: "Facility") -> None:
        st = self.state
        zone = self._zone(st.flight.origin)
        if zone.kind not in ("B", "CTR"):
            words = "VFR departure, no clearance required, contact ground when ready to taxi"
            self._schedule(t, "common.info", {"message": Phrase(words, words)}, facility, expects_readback=False)
            return
        cruise = st.flight.cruise_ft or VFR_LIMIT_FT
        limit = int(min(cruise, VFR_LIMIT_FT) // 500 * 500) or VFR_LIMIT_FT
        departure = self.facility("departure") or self._center()
        squawk = st.assignments.squawk or self._squawk()
        self._schedule(
            t, "vfr.class_b_departure",
            {"direction": self.vfr.direction or "on course", "altitude": limit, "frequency": departure.mhz, "squawk": squawk},
            facility, clearance="vfr_departure",
            on_issue=lambda: self._assign(squawk=squawk, altitude_ft=limit, departure_mhz=departure.mhz),
        )
        self.vfr.following = True  # out of Class B the flight is on departure's radar

    def _vfr_follow(self, t: float, facility: "Facility", own: OwnshipState) -> None:
        """Flight following: a code first, then "radar contact" once the transponder shows it."""
        self.vfr.following = True
        squawk = self.state.assignments.squawk or self._squawk()
        if own.squawk == squawk:
            self._vfr_radar_contact(t, facility, own)
            return
        self._schedule(t, "vfr.squawk", {"squawk": squawk}, facility, on_issue=lambda: self._assign(squawk=squawk))

    def _vfr_radar_contact(self, t: float, facility: "Facility", own: OwnshipState) -> None:
        self.vfr.identified = True
        parts = [self._vfr_position(own)]
        near = self._vfr_nearest(own)
        if near is not None and (note := self._altimeter_note(near)) is not None:
            parts.append(note)
        parts.append(Phrase("maintain VFR", "maintain VFR"))
        message = sum(parts[1:], parts[0])
        self._schedule(t, "vfr.radar_contact", {"station": facility.station, "message": message}, facility,
                       expects_readback=False)
        if self.vfr.want_class_b:
            self._vfr_class_b(t, facility, own)

    def _vfr_class_b(self, t: float, facility: "Facility", own: OwnshipState) -> None:
        if facility.controller not in AIRBORNE_CONTROLLERS:
            self._schedule(t, "common.unable", {}, facility)
            return
        if not self.vfr.identified:
            self.vfr.want_class_b = True
            self._vfr_follow(t, facility, own)
            return
        self.vfr.want_class_b = False
        wanted = self.state.flight.cruise_ft or math.ceil(own.alt_indicated_ft / 500) * 500  # where it's going
        limit = int(min(max(wanted, 2500), 10000))
        self.vfr.class_b_cleared = True
        self._schedule(t, "vfr.cleared_class_b", {"altitude": limit}, facility, clearance="class_b",
                       on_issue=lambda: self._assign(altitude_ft=limit))

    def _vfr_terminate(self, t: float, facility: "Facility") -> None:
        """Radar service ends near the destination: a squawk of 1200 and over to tower."""
        self.vfr.following = self.vfr.identified = False
        tower = self._destination_facility("tower")
        self._assign(squawk=None)
        if tower is not None:
            self._schedule(t, "vfr.service_terminated", {"station": tower.station, "frequency": tower.mhz}, facility,
                           handoff_to=tower)
        else:
            words = "radar service terminated, squawk VFR, frequency change approved"
            self._schedule(t, "common.info", {"message": Phrase(words, words)}, facility, expects_readback=False)

    def _vfr_enter_pattern(self, t: float, facility: "Facility", own: OwnshipState) -> None:
        """Into the pattern from where the aircraft is: straight-in when lined up, else the downwind on its side."""
        st = self.state
        geo = self.geometry(st.flight.destination) or self.tracker.context.airport
        runway = st.assignments.arrival_runway or (
            (self._landing_runway(own) or self._runway_in_use(own)) if geo is not None else None)
        end = geo.end(runway) if geo is not None and runway else None
        if end is None:
            self._schedule(t, "common.roger", {}, facility, expects_readback=False)
            return
        x, y = geo.xy(own.lat, own.lon)
        ux, uy = unit(end.heading_true)
        dx, dy = x - end.threshold[0], y - end.threshold[1]
        along, across = (dx * ux + dy * uy) / 1852.0, (dx * uy - dy * ux) / 1852.0
        heading_off = abs((own.hdg_true - end.heading_true + 180) % 360 - 180)
        if -STRAIGHT_IN_NM <= along < 0 and abs(across) <= STRAIGHT_IN_OFFSET_NM and heading_off <= STRAIGHT_IN_HEADING_DEG:
            self._schedule(t, "vfr.straight_in", {"station": facility.station, "runway": end.ident}, facility,
                           on_issue=lambda: self._assign(arrival_runway=end.ident),
                           note=self._atis_note(st.flight.destination, st.assignments.arrival_atis))
            return
        side = "right" if across > 0 else "left"
        self.vfr.pattern_side = side
        self._schedule(t, "vfr.enter_downwind", {"station": facility.station, "turn": side, "runway": end.ident}, facility,
                       on_issue=lambda: self._assign(arrival_runway=end.ident),
                       note=self._atis_note(st.flight.destination, st.assignments.arrival_atis))

    def _vfr_close_final(self) -> bool:
        final = self.tracker.context.final
        return final is not None and final.distance_nm <= 4.0

    def _vfr_takeoff(self, runway: str) -> tuple[str, dict[str, Any]]:
        """The takeoff clearance: with the way out, or into closed traffic for pattern work."""
        if self.vfr.direction == "closed-traffic":
            self._assign(arrival_runway=runway)  # back to the same runway
            return "vfr.takeoff_closed", {"runway": runway, "turn": self.vfr.pattern_side}
        return "vfr.takeoff", {"runway": runway, "direction": self.vfr.direction or "straight-out"}

    def _vfr_landing(self) -> str:
        """The landing clearance's template: to land, the option, or a touch and go."""
        return {"touch_and_go": "vfr.touch_and_go", "option": "vfr.option"}.get(self.vfr.option or "", "tower.land")

    def _vfr_touch_and_go(self, t: float) -> None:
        """Back in the air off a touch and go (or a go-around in the pattern): the next circuit needs a new
        clearance, and tower says which way."""
        st = self.state
        st.clearances.pop("landing", None)
        self.vfr.circuits += 1
        runway = st.assignments.arrival_runway
        tower = st.comms.tuned
        if runway and tower is not None and tower.controller == "tower":
            self._schedule(t, "vfr.closed_traffic", {"turn": self.vfr.pattern_side, "runway": runway}, tower, delay=False)

    def _vfr_monitor(self, t: float, own: OwnshipState, phase: P, tuned: str | None, once: Any) -> bool:
        """ATC's own VFR calls. True when this step belongs to VFR (the IFR flow is skipped); False to let the
        shared calls run: landing clearance and taxi-in at the destination."""
        st = self.state
        if tuned == "tower" and self._vfr_in_pattern and phase in (P.CRUISE, P.ARRIVAL, P.APPROACH) and not own.on_ground:
            # Around the pattern the landing clearance waits for the pilot's report ("midfield downwind"); a
            # pilot who never reports gets it on a two-mile final for the runway being used.
            final, runway = self.tracker.context.final, st.assignments.arrival_runway
            if "landing" not in st.clearances and final is not None and runway and final.end.ident == runway \
                    and final.distance_nm <= PATTERN_FINAL_NM and once(f"pattern_final_{self.vfr.circuits}"):
                self._clear_to_land(t, own, st.comms.tuned, delay=False, runway=runway)
            return True
        if tuned == "tower" and phase in (P.ARRIVAL, P.APPROACH, P.LANDING, P.TAXI_IN):
            return False
        if self.vfr.following and not self.vfr.identified and tuned in AIRBORNE_CONTROLLERS \
                and st.assignments.squawk and own.squawk == st.assignments.squawk and st.pending is None:
            self._vfr_radar_contact(t, st.comms.tuned, own)
            return True
        if phase is P.DEPARTURE and tuned == "tower" and not own.on_ground:
            if self.vfr.circuits or self.vfr.option in ("touch_and_go", "option") \
                    or self.vfr.direction == "closed-traffic":
                return True  # in the pattern: tower keeps it
            origin = self.geometry(st.flight.origin)
            clear = origin is None or origin.distance_nm(own.lat, own.lon) >= CLEAR_OF_FIELD_NM
            if own.alt_agl_ft >= FREQUENCY_CHANGE_AGL_FT and clear and once("vfr_departed"):
                departure = self.facility("departure") or self._center()
                if self.vfr.following and departure is not None:
                    self._handoff(t, "tower.handoff_departure", st.comms.tuned, departure)
                else:
                    self._schedule(t, "vfr.frequency_change", {}, st.comms.tuned, delay=False, expects_readback=False)
            return True
        if not (self.vfr.following and self.vfr.identified) or tuned not in AIRBORNE_CONTROLLERS:
            return True
        dest = self.geometry(st.flight.destination)
        to_go = dest.distance_nm(own.lat, own.lon) if dest is not None else math.inf
        zone = self._zone(st.flight.destination)
        approach = self._destination_facility("approach")
        if tuned == "departure" and self._leaving_departure(own, phase) and once("handoff_center"):
            center = self._center()
            self._sector, self._sector_since = center, t
            self._handoff(t, "departure.handoff_center", st.comms.tuned, center)
        elif tuned == "center" and to_go > APPROACH_HANDOFF_NM and (crossing := self._sector_crossing(own)) is not None:
            self._sector, self._sector_since = crossing, t
            self._handoff(t, "center.handoff_center", st.comms.tuned, crossing)
        elif tuned in ("center", "departure") and to_go <= APPROACH_HANDOFF_NM and zone.kind in ("B", "C", "CTR") \
                and approach is not None and st.comms.tuned != approach and once("handoff_approach"):
            self._handoff(t, "center.handoff_approach", st.comms.tuned, approach)
        elif to_go <= TERMINATE_NM and self._destination_facility("tower") is not None and once("vfr_terminated"):
            self._vfr_terminate(t, st.comms.tuned)
        return True

    # --- helpers -----------------------------------------------------------------------------------------

    def _destination_facility(self, controller: str) -> "Facility | None":
        dest = self.state.flight.destination
        return next((f for f in self.facilities if f.airport == dest and f.controller == controller), None)

    def _vfr_nearest(self, own: OwnshipState) -> str | None:
        airports = self.tracker.context_builder.airports
        if not airports:
            return None
        return min(airports, key=lambda icao: airports[icao].distance_nm(own.lat, own.lon))

    def _vfr_position(self, own: OwnshipState) -> Phrase:
        """ "12 miles south of Paine Field", from the nearest airport LocalTC knows."""
        icao = self._vfr_nearest(own)
        if icao is None:
            return Phrase("", "")
        geo = self.tracker.context_builder.airports[icao]
        place = self._airport_name(icao)
        miles = round(geo.distance_nm(own.lat, own.lon))
        if miles < 2:
            return Phrase(f"over {place}", f"over {place}")
        where = classes.bearing_words(geo.airport.lat, geo.airport.lon, own.lat, own.lon)
        spoken_miles = speech.number_words(miles) if miles < 100 else speech.digits(str(miles))
        return Phrase(f"{miles} miles {where} of {place}", f"{spoken_miles} miles {where} of {place}")
