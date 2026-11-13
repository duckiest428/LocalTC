"""The arrival sequence and the push, as a controller builds them: worked out here, deterministically, from the traffic
picture. The words are the templates' (or the model's, saying the same).

- **The landing order**: approach tells an arrival with others ahead to the same runway where it fits ("number three for
  runway 28R"), once, and again only if that changes by more than one.
- **The downwind stretched to the gap**: on the downwind with the one ahead too close to turn in behind (the wake
  spacing behind it, ``wake_miles``, and a mile's give), approach keeps it going ("extend downwind, I'll call your base")
  and turns it in once the gap's there, or once the downwind's as long as it can be.
- **Direct only when the sequence allows**: a direct towards the arrival with arrivals stacked ahead isn't given
  ("unable direct for sequencing, expect it in about four minutes"); approach gives it unasked once they've thinned.
- **Side-step**: on final to a runway with an aircraft still on it and a free parallel close beside it in visual
  weather, tower moves the landing over ("side-step to runway 28L, cleared to land runway 28L"); asked for, the same.
- **Below visual minimums**: a visual approach to expect, and the weather goes under it before the clearance: the
  instrument approach to that runway instead, and said why.
- **The push and the taxi congestion**: an aircraft taxiing behind the stand holds the push until it's by; a long line
  for the departure runway holds it at the gate for a while (``push_delay_min``) rather than in the line, and ground
  calls back when it's time.
"""

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from localtc.atc_core.airport.aircraft_size import wake_category, wake_miles
from localtc.atc_core.phase import FlightPhase as P
from localtc.atc_core.phraseology import speech
from localtc.atc_core.routing import Amendment
from localtc.atc_core.values import Approach, Phrase
from localtc.sim_api import OwnshipState, TrafficTarget
from localtc.sim_api.geo import bearing_deg, haversine_nm

if TYPE_CHECKING:
    from localtc.atc_core.facilities import Facility

SEQUENCE_FROM_NM = 40.0  # approach gives the landing order inside this
SEQUENCE_AHEAD_NM = 30.0  # counting the ones ahead inside this of the field
GAP_GIVE_NM = 1.0  # on top of the wake spacing: room for the speeds to settle
EXTEND_MAX_NM = 16.0  # a downwind isn't stretched past this from the field: turned in, and tower spaces it from there
DIRECT_HELD_AHEAD = 3  # this many arrivals ahead, in the arrival's area: a direct waits
DIRECT_AREA_NM = 60.0
DIRECT_OFFER_EVERY_S = 120.0
SIDESTEP_FROM_NM, SIDESTEP_TO_NM = 2.5, 8.0  # on final this far out: room to move across
PARALLEL_MAX_M = 1600.0  # parallels this far apart or closer: a side-step, not another approach
STOPPED_KT = 3.0  # an aircraft on the runway this slow ...
STOPPED_S = 40.0  # ... this long is stuck there (a landing rolls off; this one isn't going anywhere)
VISUAL_SM, VISUAL_CEILING_FT = 3.0, 1000.0  # the visual's minimums (FAA 7-4-2: 1,000 and 3)
BEHIND_PUSH_M = 140.0  # an aircraft taxiing this close behind the stand holds the push
PUSH_LOOK_S = 45.0
QUEUE_FREE = 8  # this many in line for the runway is normal; each one more holds the push this long
QUEUE_MIN_PER = 1.5
PUSH_DELAY_MAX_MIN = 12
PUSH_HELD_MAX_S = 180.0  # held for traffic behind this long at most, then pushed (it's moved on, or it's parked)


@dataclass
class PushHold:
    """A push held at the gate: why, until when (or until the traffic behind is by), and the tail it'll get."""

    reason: str  # "traffic" or "queue"
    since_t: float
    until_t: float
    tail: str | None
    target: int | None = None


def parallels(geo: Any, ident: str) -> list[Any]:
    """The other ends landing the same way beside ``ident`` (28R: 28L, 28C), close enough for a side-step."""
    end = geo.end(ident)
    if end is None:
        return []
    out = []
    ux, uy = math.sin(math.radians(end.heading_true)), math.cos(math.radians(end.heading_true))
    for other in geo.ends:
        if other.ident == ident or other.runway is end.runway:
            continue
        if abs(((other.heading_true - end.heading_true + 540) % 360) - 180) > 10:
            continue
        dx, dy = other.threshold[0] - end.threshold[0], other.threshold[1] - end.threshold[1]
        if abs(dx * uy - dy * ux) <= PARALLEL_MAX_M:
            out.append(other)
    return out


def number_phrase(number: int, runway: str) -> Phrase:
    return Phrase(f"number {number} for runway {runway}", f"number {speech.number_words(number)} for runway {speech.runway(runway)}")


class SequenceMixin:
    """The engine's side (``AtcEngine``)."""

    push_hold: PushHold | None
    _seq: dict[str, Any]

    # --- who's ahead to the same runway ----------------------------------------------------------------------------

    def _landing_order(self: Any, own: OwnshipState, runway: str) -> list[tuple[float, TrafficTarget]]:
        """The arrivals ahead of this one to ``runway``: (their miles to the field, them), nearest first. On its final
        already, or nearer the field and lower, coming this way."""
        geo = self.geometry(self.state.flight.destination)
        end = geo.end(runway) if geo is not None else None
        if end is None:
            return []
        mine = geo.distance_nm(own.lat, own.lon)
        ahead = []
        for target in self._traffic.values():
            if target.on_ground or target.gs_kt < 60 or self._climbing(target):
                continue
            d = geo.distance_nm(target.lat, target.lon)
            if d >= mine or d > SEQUENCE_AHEAD_NM or target.alt_ft - geo.airport.elev_ft > 15000:
                continue
            final = geo.final_approach(target.lat, target.lon, target.hdg_true)
            if final is not None and final.end.ident != end.ident and final.distance_nm < 12:
                continue  # on another runway's final
            towards = abs(((bearing_deg(target.lat, target.lon, geo.airport.lat, geo.airport.lon) - target.hdg_true + 540) % 360) - 180)
            if (final is not None and final.end.ident == end.ident) or towards < 110 or d < 10:
                ahead.append((final.distance_nm if final is not None and final.end.ident == end.ident else d, target))
        return sorted(ahead, key=lambda x: x[0])

    def _seq_watch(self: Any, own: OwnshipState, facility: "Facility | None") -> bool:
        """One of these, when it's due. True when ATC spoke."""
        st = self.state
        if facility is None or own.on_ground or st.pending is not None or self._going_around or "emergency" in st.flags:
            return False
        return self._weather_fallback(own, facility) or self._sidestep(own, facility) \
            or self._approach_number(own, facility) or self._direct_offer(own, facility)

    def _approach_number(self: Any, own: OwnshipState, facility: "Facility") -> bool:
        st = self.state
        if not self._works_approach(own) or "approach" in st.clearances or self._holding():
            return False
        distance = self.tracker.context.destination_distance_nm
        plan = self._arrival_plan(own)
        if distance is None or distance > SEQUENCE_FROM_NM or plan is None:
            return False
        runway = plan["approach"].landing_runway
        number = len(self._landing_order(own, runway)) + 1
        said = self._seq.get("number")
        if number < 2 or (said is not None and abs(number - said) <= 1):
            return False
        self._seq["number"] = number
        self._schedule(own.t, "approach.sequence", {"message": number_phrase(number, runway)}, facility, delay=False,
                       expects_readback=False)
        return True

    # --- the downwind stretched to the gap ------------------------------------------------------------------------

    def _gap_short(self: Any, own: OwnshipState, runway: str, track_nm: float) -> tuple[TrafficTarget, float] | None:
        """Turning in now (``track_nm`` still to fly) would put this one inside the spacing behind the one ahead to
        ``runway``: (it, the miles wanted), or None. Past ``EXTEND_MAX_NM`` it's turned in anyway."""
        geo = self.geometry(self.state.flight.destination)
        if geo is None or geo.distance_nm(own.lat, own.lon) > EXTEND_MAX_NM:
            return None
        order = self._landing_order(own, runway)
        if not order:
            return None
        miles, target = order[-1]  # the last one ahead: the one to follow
        mine = wake_category(self.state.flight.aircraft_type or self._aircraft_title) or "M"
        wanted = wake_miles(self._wake_of(target) or "M", mine) + GAP_GIVE_NM
        # Both flying on at their speeds: what counts is the gap at the threshold, so this one's slower or faster
        # approach is in it as time (a heavy at 150 kt with a jet at 210 behind closes up).
        if target.gs_kt > 40 and own.gs_kt > 40:
            closing = max(0.6, min(1.4, own.gs_kt / target.gs_kt))
            track_nm = track_nm / closing
        return (target, wanted) if track_nm - miles < wanted else None

    def _extend_for_gap(self: Any, own: OwnshipState, runway: str, leg: Any) -> bool:
        """Called by ``_vector`` when the next leg would turn in: True keeps it on the downwind."""
        if leg.leg not in ("base", "intercept") or self._vector_leg != "downwind":
            return False
        short = self._gap_short(own, runway, leg.track_nm)
        if short is None:
            if self._seq.get("extended"):
                self._seq["extended"] = False  # the gap's there: the turn in comes now
            return False
        self._seq["extended"] = self._seq.get("extended") or own.t
        self._seq["extend_target"] = short[0].object_id
        return True

    def _extend_said(self: Any, own: OwnshipState, facility: "Facility", runway: str) -> bool:
        """The downwind's being stretched and the pilot hasn't been told: told once."""
        if not self._seq.get("extended") or self._seq.get("extend_told"):
            return False
        self._seq["extend_told"] = True
        target = self._traffic.get(self._seq.get("extend_target"))
        number = len(self._landing_order(own, runway)) + 1
        kind = self._aircraft(target) if target is not None else None
        message = number_phrase(max(number, 2), runway)
        if kind is not None:
            article = "an" if kind.display[:1].upper() in "AEIOU" else "a"
            message = message + Phrase(f"traffic to follow is {article} {kind.display} on final", f"traffic to follow is {article} {kind.spoken} on final")
        self.amendments.append(Amendment("extended", own.t, reason=message.display))
        self._schedule(own.t, "approach.extend_downwind", {"message": message}, facility, delay=False, expects_readback=False)
        return True

    # --- directs ----------------------------------------------------------------------------------------------------

    def _direct_held(self: Any, own: OwnshipState, fix: str) -> int | None:
        """Minutes until a direct ``fix`` fits the sequence, or None: it fits now. Only towards the arrival, with
        arrivals stacked ahead; a direct along the way in cruise is the centre's to give whatever."""
        geo = self.geometry(self.state.flight.destination)
        if geo is None or geo.distance_nm(own.lat, own.lon) > DIRECT_AREA_NM:
            return None
        ahead = self._arrivals_ahead(own)
        if len(ahead) < DIRECT_HELD_AHEAD:
            return None
        # Each one more than the arrival rate's share holds it about two minutes (a runway lands 25-30 an hour).
        return max(2, min(15, 2 * (len(ahead) - DIRECT_HELD_AHEAD + 1)))

    def _direct_offer(self: Any, own: OwnshipState, facility: "Facility") -> bool:
        """A direct refused for sequencing, given unasked once the sequence allows it."""
        held = self._seq.get("direct_held")
        if held is None or own.t - self._seq.get("direct_t", -math.inf) < DIRECT_OFFER_EVERY_S:
            return False
        self._seq["direct_t"] = own.t
        fix, asked_t = held
        if own.t - asked_t > 20 * 60 or "approach" in self.state.clearances:
            self._seq["direct_held"] = None
            return False
        if self._direct_held(own, fix) is not None or not self.validate(own, fix=fix).ok:
            return False
        self._seq["direct_held"] = None
        self._schedule(own.t, "common.direct_now", {"fix": fix}, facility, delay=False)
        return True

    # --- side-step --------------------------------------------------------------------------------------------------

    def _visual_weather(self: Any, *, aboard: bool = True) -> bool:
        """The destination's weather good enough for a visual; ``aboard``: and the aircraft out of cloud now."""
        weather = self.weather.surface(self.state.flight.destination or "", self.tracker.context_builder.airports)
        own = self.state.aircraft
        if aboard and own is not None and own.in_cloud:
            return False
        if weather is None:
            return True
        return (weather.visibility_sm is None or weather.visibility_sm >= VISUAL_SM) \
            and (weather.ceiling_ft is None or weather.ceiling_ft >= VISUAL_CEILING_FT)

    def _free_parallel(self: Any, own: OwnshipState, runway: str) -> str | None:
        geo = self.geometry(self.state.flight.destination)
        if geo is None:
            return None
        for other in parallels(geo, runway):
            if not self._closed(self.state.flight.destination, other.ident) and self._traffic_on_runway(own, other.ident) is None \
                    and not any(t.on_ground is False and (f := geo.final_approach(t.lat, t.lon, t.hdg_true)) is not None
                                and f.end.ident == other.ident and f.distance_nm < 6 for t in self._traffic.values()):
                return other.ident
        return None

    def _sidestep(self: Any, own: OwnshipState, facility: "Facility") -> bool:
        """Tower: on final to a runway with somebody on it, a free parallel beside it, visual: moved across."""
        st, ctx = self.state, self.tracker.context
        if facility.controller != "tower" or self._seq.get("sidestep") or ctx.final is None:
            return False
        if not SIDESTEP_FROM_NM <= ctx.final.distance_nm <= SIDESTEP_TO_NM or P(st.phase) not in (P.APPROACH, P.LANDING):
            return False
        runway = self._landing_runway(own)
        blocking = self._traffic_on_runway(own, runway) if runway is not None else None
        still = self._seq.setdefault("still", {})
        if blocking is None or blocking.gs_kt >= STOPPED_KT:
            still.clear()  # nothing there, or rolling: a landing on its way off ("continue, traffic vacating")
            return False
        since = still.setdefault(blocking.object_id, own.t)
        if own.t - since < STOPPED_S or self._departing_ahead(own, runway, blocking) or not self._visual_weather():
            return False  # stopped only a moment, or a departure about to go: the landing clearance comes with it
        other = self._free_parallel(own, runway)
        if other is None:
            return False
        self._seq["sidestep"] = other
        self._do_sidestep(own, facility, other, asked=False)
        return True

    def _do_sidestep(self: Any, own: OwnshipState, facility: "Facility", runway: str, *, asked: bool) -> None:
        st = self.state
        st.clearances.pop("landing", None)
        self._scheduled = [s for s in self._scheduled if s.instruction_id not in ("tower.land", "tower.continue_traffic")]
        self.amendments.append(Amendment("sidestep", own.t, fix=runway, reason="asked" if asked else "the runway's occupied"))
        if facility.controller == "tower":
            self._schedule(own.t, "tower.sidestep_land", {"runway": runway, "wind": self._surface_wind(own, st.flight.destination)},
                           facility, delay=asked, clearance="landing", on_issue=lambda: self._assign(arrival_runway=runway))
        else:
            self._schedule(own.t, "approach.sidestep", {"runway": runway}, facility, delay=asked,
                           on_issue=lambda: self._assign(arrival_runway=runway))

    def _sidestep_request(self: Any, own: OwnshipState | None, facility: "Facility", runway: str | None, t: float) -> bool:
        """ "Request side-step to 28L" on the approach: True when it's handled here (approved or why not)."""
        st = self.state
        if own is None or own.on_ground or not runway or "approach" not in st.clearances:
            return False
        assigned = st.assignments.arrival_runway
        geo = self.geometry(st.flight.destination)
        if geo is None or not assigned or runway == assigned or runway not in {e.ident for e in parallels(geo, assigned)}:
            return False
        if not self._visual_weather() or self._closed(st.flight.destination, runway) or self._free_parallel(own, assigned) != runway:
            self._schedule(t, "common.unable", {}, facility)
            return True
        self._do_sidestep(own, facility, runway, asked=True)
        return True

    # --- the weather under the visual's minimums ------------------------------------------------------------------

    def _weather_fallback(self: Any, own: OwnshipState, facility: "Facility") -> bool:
        st = self.state
        given = Approach.parse(str(st.assignments.approach or ""))
        if given is None or given.kind != "VISUAL" or "approach" in st.clearances or not self._works_approach(own):
            return False
        distance = self.tracker.context.destination_distance_nm
        # (The field's weather, not a cloud the aircraft passes through: Tarama's "gone below visual minimums" was a
        # cloud at 3,000 feet.)
        if distance is None or distance > 60 or self._visual_weather(aboard=False):
            return False
        geo = self.geometry(st.flight.destination)
        end = geo.end(given.runway) if geo is not None else None
        if end is None or self._seq.get("fallback") == given.runway:
            return False
        self._seq["fallback"] = given.runway
        self._approach_kind = None
        self.state.assignments.approach = None
        instrument = self._approach_for(end.ident, has_ils=end.has_ils)
        if instrument.kind == "VISUAL":
            return False  # nothing else to fly there: the visual stays, and tower and the pilot decide
        self._approach_kind = instrument
        self._visual_vectors = False
        self._schedule(own.t, "approach.weather_change", {"approach": instrument}, facility, delay=False,
                       on_issue=lambda: self._assign(approach=instrument.display, arrival_runway=instrument.landing_runway))
        return True

    # --- the push ---------------------------------------------------------------------------------------------------

    def _taxiing_behind(self: Any, own: OwnshipState) -> TrafficTarget | None:
        """Traffic taxiing behind the stand, coming past it within the next ``PUSH_LOOK_S``."""
        behind = (own.hdg_true + 180) % 360
        for target in self._traffic.values():
            if not target.on_ground or not 3 <= target.gs_kt <= 30:
                continue
            d_m = haversine_nm(own.lat, own.lon, target.lat, target.lon) * 1852
            if d_m > BEHIND_PUSH_M + target.gs_kt * 0.5144 * PUSH_LOOK_S:
                continue
            where = abs(((bearing_deg(own.lat, own.lon, target.lat, target.lon) - behind + 540) % 360) - 180)
            if where > 75:
                continue  # beside or ahead of the nose: not where the tail's going
            # Closing on the line behind the tail, or already on it.
            to_us = bearing_deg(target.lat, target.lon, own.lat, own.lon)
            if d_m <= BEHIND_PUSH_M or abs(((to_us - target.hdg_true + 540) % 360) - 180) < 80:
                return target
        return None

    def _departure_queue(self: Any, own: OwnshipState) -> int:
        """How many are taxiing out or waiting for the departure runway now."""
        geo = self.geometry(self.state.flight.origin)
        end = self._runway_end(self.state.flight.origin, own) if geo is not None else None
        if geo is None or end is None:
            return 0
        count = 0
        for target in self._traffic.values():
            if not target.on_ground or geo.distance_nm(target.lat, target.lon) > 3.0:
                continue
            on = geo.runway_at(target.lat, target.lon)
            if on is not None and target.gs_kt > 40:
                continue  # rolling: gone
            x, y = geo.xy(target.lat, target.lon)
            far = math.hypot(x - end.threshold[0], y - end.threshold[1])
            # Waiting at the end, or taxiing towards it (not one taxiing in from a landing, going the other way).
            towards = math.degrees(math.atan2(end.threshold[0] - x, end.threshold[1] - y)) % 360
            moving_out = 3 <= target.gs_kt <= 30 and abs(((towards - target.hdg_true + 540) % 360) - 180) < 60
            if (far < 600 and target.gs_kt < 3) or (moving_out and far < 2500):
                count += 1
        return count

    def push_delay_min(self: Any, own: OwnshipState) -> int:
        queue = self._departure_queue(own)
        if queue <= QUEUE_FREE:
            return 0
        return int(min(PUSH_DELAY_MAX_MIN, math.ceil((queue - QUEUE_FREE) * QUEUE_MIN_PER)))

    def _push_or_hold(self: Any, t: float, facility: "Facility", own: OwnshipState | None, tail: str | None) -> bool:
        """Before the push is approved: held for traffic behind, or for the line at the runway. True when held."""
        if own is None or not self.cfg.unscripted or "emergency" in self.state.flags:
            return False
        held = self.push_hold
        if held is not None and t - held.since_t < 30:
            return False  # asked again straight away: given (the pilot can see it's clear)
        behind = self._taxiing_behind(own)
        if behind is not None:
            self.push_hold = PushHold("traffic", t, t + PUSH_HELD_MAX_S, tail, behind.object_id)
            kind = self._aircraft(behind)
            article = "an" if kind is not None and kind.display[:1].upper() in "AEIOU" else "a"
            what = Phrase(f"{article} {kind.display} taxiing behind you", f"{article} {kind.spoken} taxiing behind you") if kind is not None \
                else Phrase("traffic taxiing behind you", "traffic taxiing behind you")
            self._schedule(t, "ground.push_hold_traffic", {"message": what}, facility)
            return True
        minutes = self.push_delay_min(own)
        if minutes and held is None:
            self.push_hold = PushHold("queue", t, t + minutes * 60, tail)
            self._schedule(t, "ground.push_hold_queue", {"minutes": minutes}, facility)
            return True
        return False

    def _push_watch(self: Any, own: OwnshipState) -> bool:
        """A held push, approved once what it waited on is done."""
        held = self.push_hold
        st = self.state
        if held is None or "pushback" in st.clearances or P(st.phase) is not P.PARKED:
            if held is not None and P(st.phase) is not P.PARKED:
                self.push_hold = None
            return False
        facility = st.comms.tuned
        if facility is None or facility.controller not in ("ground", "clearance", "ramp"):
            return False
        t = own.t
        if held.reason == "traffic":
            target = self._traffic.get(held.target)
            gone = target is None or haversine_nm(own.lat, own.lon, target.lat, target.lon) * 1852 > BEHIND_PUSH_M * 1.5 \
                or self._taxiing_behind(own) is None
            if not gone and t < held.until_t:
                return False
        elif t < held.until_t:
            return False
        self.push_hold = None
        self._pushback(t, facility, own, tail=held.tail, released=True)
        return True
