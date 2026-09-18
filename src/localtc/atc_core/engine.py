"""``AtcEngine``: the deterministic IFR dialogue.

Synchronous and driven purely by events: ``handle(event) -> list[BusEvent]``.
Given the same events, config and seed it produces the same transmissions,
which is what makes replay-based scenario tests possible. Replies are
scheduled a short, seeded delay after the pilot's transmission and go out on
the first event at or after their due time.
"""

import math
import random
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from localtc.atc_core.airport import AirportGeometry, TaxiGraph, select_runway
from localtc.atc_core.facilities import Facility, airport_facilities, center_facility
from localtc.atc_core.llm import LlmPhraser
from localtc.atc_core.phase import FlightPhase, PhaseThresholds, PhaseTracker
from localtc.atc_core.phraseology import TemplateLibrary, speech
from localtc.atc_core.readback import (
    ChainInterpreter,
    GrammarInterpreter,
    InterpretContext,
    Interpretation,
    Interpreter,
    PendingReadback,
    SayAgainInterpreter,
)
from localtc.atc_core.session import Clearance, Exchange, IssuedInstruction, SessionSnapshot, SessionState, snapshot
from localtc.atc_core.values import Approach, Callsign, Phrase, Wind, clean_sim_name
from localtc.sim_api import (
    AircraftIdentity,
    Airport,
    AirportData,
    AtcAlert,
    AtcTransmission,
    BusEvent,
    OwnshipState,
    PhaseChanged,
    PttPressed,
    PttReleased,
    RadioTuned,
    ReadbackEvaluated,
    SimLifecycle,
    Transcript,
)

P = FlightPhase
DEPARTURE_PHASES = {P.PARKED, P.TAXI_OUT, P.RUNWAY_HOLD, P.TAKEOFF, P.DEPARTURE, P.CRUISE}
RESERVED_SQUAWKS = {"1200", "1202", "1255", "1276", "1277", "2000", "4000", "0000"}
PTT_TIMEOUT_S = 30.0  # a PttPressed without a release can't silence ATC forever
MAX_READBACK_ATTEMPTS = 3  # then ATC repeats the instruction once more and stops asking

# Which controller handles each pilot request (None: whoever is tuned).
REQUEST_CONTROLLER = {
    "request_ifr_clearance": "clearance",
    "ready_to_taxi": "ground",
    "ready_for_departure": "tower",
    "report_final": "tower",
    "request_taxi_parking": "ground",
    "clear_of_runway": "ground",
}


@dataclass
class EngineConfig:
    destination: str | None = None
    cruise_ft: int | None = None
    callsign: str | None = None  # overrides the sim's ATC ID
    rules: str = "IFR"
    center_name: str = "Seattle"
    center_mhz: float = 125.1
    strict_callsign: bool = False
    seed: int = 0  # 0 = derived from the callsign
    thresholds: PhaseThresholds = field(default_factory=PhaseThresholds)
    response_delay_s: tuple[float, float] = (1.5, 3.0)
    min_gap_s: float = 8.0  # quiet time before an automatic ATC call


@dataclass
class _Scheduled:
    due: float
    instruction_id: str
    slots: dict[str, Any]
    facility: Facility
    clearance: str | None = None
    handoff_to: Facility | None = None
    expects_readback: bool = True
    on_issue: Callable[[], None] | None = None


class AtcEngine:
    def __init__(
        self,
        config: EngineConfig | None = None,
        *,
        library: TemplateLibrary | None = None,
        interpreter: Interpreter | None = None,
        phraser: LlmPhraser | None = None,
    ) -> None:
        self.cfg = config or EngineConfig()
        self.library = library or TemplateLibrary.load()
        self.interpreter = interpreter or ChainInterpreter(GrammarInterpreter(), SayAgainInterpreter())
        self.phraser = phraser  # words replies that have no template; None: they get "unable"
        self.state = SessionState()
        flight = self.state.flight
        flight.rules = self.cfg.rules
        flight.destination = self.cfg.destination.upper() if self.cfg.destination else None
        flight.cruise_ft = self.cfg.cruise_ft
        if self.cfg.callsign:
            flight.callsign = Callsign(self.cfg.callsign.upper())
        self.tracker = PhaseTracker(destination=flight.destination, cruise_ft=flight.cruise_ft, thresholds=self.cfg.thresholds)
        self.facilities: list[Facility] = []
        self.airport_requests: list[str] = []  # airports the engine needs; the service fetches them
        self._requested: set[str] = set()
        self._scheduled: list[_Scheduled] = []
        self._rng: random.Random | None = None
        self._was_on_runway: bool | None = None  # None until the first tick
        self._ptt_since: float | None = None  # set while the pilot holds push-to-talk
        self._t = 0.0

    # --- public ---------------------------------------------------------------------------

    def handle(self, event: BusEvent) -> list[BusEvent]:
        self._t = max(self._t, event.t)
        out: list[BusEvent] = []
        if isinstance(event, AirportData):
            self.tracker.handle(event)
            self._rebuild_facilities()
        elif isinstance(event, AircraftIdentity):
            if not self.cfg.callsign and event.atc_id:
                self.state.flight.callsign = Callsign.from_sim(event.atc_id, event.airline, event.flight_number, event.atc_type)
            elif self.cfg.callsign and self.state.flight.callsign is not None:
                # Keep the type for abbreviated callsigns ("Boeing 38B") even when the ident is overridden.
                self.state.flight.callsign = replace(self.state.flight.callsign, type_name=clean_sim_name(event.atc_type))
            self.state.flight.aircraft_type = clean_sim_name(event.atc_model)
        elif isinstance(event, SimLifecycle):
            self.tracker.handle(event)
        elif isinstance(event, OwnshipState):
            out += self._on_ownship(event)
        elif isinstance(event, PttPressed):
            self._ptt_since = event.t
        elif isinstance(event, PttReleased):
            self._ptt_since = None
        elif isinstance(event, Transcript):
            self._ptt_since = None
            out += self._on_pilot(event)
        out += self._flush(event.t)
        return out

    def snapshot(self) -> SessionSnapshot:
        return snapshot(self.state, self._t)

    @property
    def idle(self) -> bool:
        """Nothing scheduled to say and the pilot isn't transmitting: a good moment for the pilot to call."""
        return not self._scheduled and not self._transmitting(self._t)

    def facility(self, controller: str) -> Facility | None:
        """The facility for a controller in the current flight context (origin before arrival, destination after)."""
        candidates = [f for f in self.facilities if f.controller == controller]
        if not candidates:
            return None
        arriving = self.state.phase is not None and P(self.state.phase) not in DEPARTURE_PHASES
        preferred = self.state.flight.destination if arriving else self.state.flight.origin
        return next((f for f in candidates if f.airport == preferred), candidates[0])

    def geometry(self, icao: str | None) -> AirportGeometry | None:
        return self.tracker.context_builder.airports.get(icao) if icao else None

    # --- telemetry ------------------------------------------------------------------------------

    def _on_ownship(self, own: OwnshipState) -> list[BusEvent]:
        st = self.state
        st.aircraft = own
        change = self.tracker.handle(own)
        ctx = self.tracker.context
        out: list[BusEvent] = []

        if st.flight.origin is None and own.on_ground and ctx.airport is not None:
            st.flight.origin = ctx.airport.icao
            self._rebuild_facilities()
        dest = st.flight.destination
        if dest and dest not in self.tracker.context_builder.airports and dest not in self._requested:
            self._requested.add(dest)
            self.airport_requests.append(dest)

        previous = (st.comms.tuned_mhz, st.comms.tuned)
        st.comms.tuned_mhz = own.com1_mhz
        st.comms.tuned = self._facility_for(own.com1_mhz)
        if (st.comms.tuned_mhz, st.comms.tuned) != previous:
            tuned = st.comms.tuned
            out.append(RadioTuned(t=own.t, radio=1, frequency_mhz=own.com1_mhz,
                                  controller=tuned.controller if tuned else None, station=tuned.station if tuned else None))

        if change is not None:
            st.phase, st.phase_since_t = change.phase, change.t
            st.phase_history.append((change.t, change.phase))
            out.append(change)
            out += self._on_phase_change(change, own)
        out += self._monitor(own)
        return out

    def _on_phase_change(self, change: PhaseChanged, own: OwnshipState) -> list[BusEvent]:
        st, t = self.state, change.t
        out: list[BusEvent] = []
        phase = P(change.phase)
        if phase is P.TAXI_OUT and change.previous == P.PARKED and "taxi" not in st.clearances and st.flight.origin:
            out.append(self._alert(t, "taxi_without_clearance", "moving without a taxi clearance"))
            ground = self.facility("ground")
            if ground is not None and st.comms.tuned is not None and st.comms.tuned.controller == "ground":
                self._schedule(t, "ground.hold_position", {}, ground, delay=False)
        elif phase is P.TAKEOFF and "takeoff" not in st.clearances:
            out.append(self._alert(t, "takeoff_without_clearance", "takeoff roll without a takeoff clearance"))
        elif phase is P.TAXI_IN and change.previous == P.LANDING and "landing" not in st.clearances:
            out.append(self._alert(t, "landed_without_clearance", "landed without a landing clearance"))
        return out

    def _monitor(self, own: OwnshipState) -> list[BusEvent]:
        """Automatic ATC calls and alerts that don't wait for the pilot."""
        st, ctx, t = self.state, self.tracker.context, own.t
        out: list[BusEvent] = []
        if own.squawk == "7700" and "squawk_7700" not in st.flags:
            st.flags.add("squawk_7700")
            out.append(self._alert(t, "emergency", "squawking 7700"))

        entered_runway = ctx.on_runway and self._was_on_runway is False  # starting on a runway isn't an incursion
        self._was_on_runway = ctx.on_runway
        if entered_runway and own.on_ground and st.phase in (P.TAXI_OUT, P.RUNWAY_HOLD):
            if not ({"takeoff", "line_up"} & st.clearances.keys()):
                where = f"runway {ctx.runway.name}" if ctx.runway else "a runway"
                out.append(self._alert(t, "runway_incursion", f"entered {where} without clearance"))

        if st.phase is None or not self._can_call(t, own):
            return out
        phase = P(st.phase)
        tuned = st.comms.tuned.controller if st.comms.tuned else None

        def once(flag: str) -> bool:
            if flag in st.flags:
                return False
            st.flags.add(flag)
            return True

        if phase is P.RUNWAY_HOLD and tuned == "ground" and "taxi" in st.clearances and once("handoff_tower"):
            if (tower := self.facility("tower")) is not None:
                self._handoff(t, "ground.handoff_tower", st.comms.tuned, tower)
        elif phase is P.DEPARTURE and own.alt_agl_ft > 500 and tuned == "tower" and once("handoff_departure"):
            if (departure := self.facility("departure") or self._center()) is not None:
                self._handoff(t, "tower.handoff_departure", st.comms.tuned, departure)
        elif phase is P.CRUISE and t - st.phase_since_t >= 30 and tuned == "departure" and once("handoff_center"):
            self._handoff(t, "departure.handoff_center", st.comms.tuned, self._center())
        elif phase in (P.ARRIVAL, P.APPROACH) and tuned in ("center", "departure"):
            if "descend" not in st.flags and (plan := self._arrival_plan(own)) is not None:
                st.flags.add("descend")
                self._schedule(
                    t, "center.descend", {"altitude": plan["arrival_alt"], "approach": plan["approach"]}, st.comms.tuned,
                    delay=False, on_issue=lambda: self._assign(altitude_ft=plan["arrival_alt"], approach=plan["approach"].display,
                                                              arrival_runway=plan["approach"].runway),
                )
            elif (
                ctx.destination_distance_nm is not None and ctx.destination_distance_nm <= 40
                and (approach := self.facility("approach")) is not None and once("handoff_approach")
            ):
                self._handoff(t, "center.handoff_approach", st.comms.tuned, approach)
        elif phase in (P.APPROACH, P.LANDING) and tuned == "approach" and "approach" not in st.clearances:
            near = ctx.final is not None and ctx.final.distance_nm <= 12
            close = ctx.destination_distance_nm is not None and ctx.destination_distance_nm <= 8
            if (near or close) and (plan := self._arrival_plan(own)) is not None and (tower := self.facility("tower")) is not None:
                self._schedule(
                    t, "approach.cleared", {"approach": plan["approach"], "station": tower.station, "frequency": tower.mhz},
                    st.comms.tuned, delay=False, clearance="approach", handoff_to=tower,
                    on_issue=lambda: self._assign(approach=plan["approach"].display, arrival_runway=plan["approach"].runway),
                )
        elif phase in (P.APPROACH, P.LANDING) and tuned == "tower" and "landing" not in st.clearances:
            if phase is P.LANDING or (ctx.final is not None and ctx.final.distance_nm <= 3):
                self._clear_to_land(t, own, st.comms.tuned, delay=False)
        elif phase is P.TAXI_IN and tuned == "tower" and once("exit_contact_ground"):
            if (ground := self.facility("ground")) is not None:
                self._handoff(t, "tower.exit_contact_ground", st.comms.tuned, ground)
        return out

    def _can_call(self, t: float, own: OwnshipState) -> bool:
        st = self.state
        if st.pending is not None or self._scheduled or self._transmitting(t):
            return False
        last = max(x for x in (st.comms.last_atc_t, st.comms.last_pilot_t, -math.inf) if x is not None)
        return t - last >= self.cfg.min_gap_s

    # --- pilot transmissions -----------------------------------------------------------------------

    def _on_pilot(self, ev: Transcript) -> list[BusEvent]:
        st, t = self.state, ev.t
        st.comms.last_pilot_t = t
        own = st.aircraft
        mhz = (own.com2_mhz if ev.radio == 2 else own.com1_mhz) if own else None
        facility = self._facility_for(mhz) if mhz is not None else None
        pending = st.pending if st.pending is not None and facility is not None and st.pending.controller == facility.controller else None
        if facility is None:
            st.exchanges.append(Exchange(t, "pilot", None, ev.text, None))
            where = f"{mhz:.3f}" if mhz is not None else "an unknown frequency"
            return [self._alert(t, "no_atc_on_frequency", f"no LocalTC controller on {where}; transmission not answered")]
        last_atc = next((e.text for e in reversed(st.exchanges) if e.speaker == "atc" and e.controller == facility.controller), None)
        interp = self.interpreter.interpret(ev.text, pending, InterpretContext(
            callsign=self._callsign(), phase=st.phase, strict_callsign=self.cfg.strict_callsign, t=t,
            station=facility.station, last_atc=last_atc,
        ))
        st.exchanges.append(Exchange(t, "pilot", facility.controller, ev.text, interp))
        out: list[BusEvent] = list(interp.exchanges)
        if interp.kind == "readback":
            return out + self._on_readback(interp, facility, t)
        if interp.intent == "emergency":
            return out + self._emergency(interp, facility, t)
        if interp.kind == "request":
            return out + self._on_request(interp, facility, t)
        if st.pending is not None and pending is not None:
            st.pending = replace(st.pending, attempts=st.pending.attempts + 1)
            if st.pending.attempts >= MAX_READBACK_ATTEMPTS:
                return out + self._give_up_readback(facility, t)
        self._schedule(t, "common.say_again", {}, facility)
        return out

    def _on_readback(self, interp: Interpretation, facility: Facility, t: float) -> list[BusEvent]:
        st = self.state
        pending = st.pending
        assert pending is not None
        issued = st.issued.get(pending.instruction_id)
        slots = issued.slots if issued else {}
        out: list[BusEvent] = [
            ReadbackEvaluated(
                t=t, instruction_id=pending.instruction_id, status=interp.status, missing=interp.missing,
                mismatched={k: _display(v) for k, v in interp.mismatched.items()},
            )
        ]
        for clearance in st.clearances.values():
            if clearance.instruction_id == pending.instruction_id and clearance.readback in ("pending", "incorrect", "incomplete"):
                clearance.readback = interp.status
        if interp.status == "correct":
            st.pending = None
            if self.library.get(pending.instruction_id).ack == "readback_correct":
                self._schedule(t, "common.readback_correct", {}, facility)
            return out
        if pending.attempts + 1 >= MAX_READBACK_ATTEMPTS:
            return out + self._give_up_readback(facility, t)
        # The follow-up only has to fix what was wrong or missing.
        st.pending = replace(
            pending, attempts=pending.attempts + 1, required=tuple([*interp.mismatched, *interp.missing]), optional=()
        )
        if interp.status == "incorrect":
            correction = self._fragments(list(interp.mismatched), slots)
            self._schedule(t, "common.negative", {"correction": correction}, facility, expects_readback=False)
        else:
            self._schedule(t, "common.read_back", {"missing": self._fragments(list(interp.missing), slots)}, facility,
                           expects_readback=False)
        return out

    def _give_up_readback(self, facility: Facility, t: float) -> list[BusEvent]:
        """Several tries and still no good readback: say the instruction once more, then stop asking.
        Without this, a readback the parser can't match turns into "say again" forever."""
        st = self.state
        pending = st.pending
        assert pending is not None
        st.pending = None
        issued = st.issued.get(pending.instruction_id)
        if issued is not None:
            self._schedule(t, pending.instruction_id, issued.slots, facility, expects_readback=False)
        return [self._alert(t, "readback_unresolved", f"{pending.instruction_id}: no correct readback after "
                                                       f"{MAX_READBACK_ATTEMPTS} tries")]

    def _on_request(self, interp: Interpretation, facility: Facility, t: float) -> list[BusEvent]:
        st = self.state
        intent = interp.intent
        own = st.aircraft
        if intent == "question":
            return self._answer(interp, facility, t)
        if intent == "request_altitude":
            self._altitude_request(interp, facility, t, own)
            return []
        if intent == "other":
            return self._decline(interp, facility, t)
        target = REQUEST_CONTROLLER.get(intent or "")
        if target is not None and facility.controller != target:
            target_facility = self.facility(target)
            if target_facility is not None and target_facility.matches(facility.mhz):
                facility = target_facility  # e.g. ground also works clearance delivery
            elif target_facility is not None:
                self._handoff(t, "common.contact", facility, target_facility, delay=True)
                return []
        if intent == "request_ifr_clearance":
            self._ifr_clearance(t, facility, interp)
        elif intent == "ready_to_taxi":
            if interp.values.get("atis"):
                self._assign(atis=interp.values["atis"])
            self._taxi_out(t, facility, own)
        elif intent == "ready_for_departure":
            runway = st.assignments.departure_runway or interp.values.get("runway") or self._departure_runway(own)
            if runway is None:
                self._schedule(t, "common.say_again", {}, facility)
                return []
            self._schedule(t, "tower.takeoff", {"runway": runway}, facility, clearance="takeoff",
                           on_issue=lambda: self._assign(departure_runway=runway))
        elif intent == "checkin":
            self._checkin(t, facility, own)
        elif intent == "report_final":
            self._clear_to_land(t, own, facility, delay=True, runway=interp.values.get("runway"))
        elif intent in ("request_taxi_parking", "clear_of_runway"):
            self._taxi_in(t, facility, own)
        elif intent == "say_again":
            last = st.last_issued
            if last is not None and last.facility.controller == facility.controller:
                self._schedule(t, last.instruction_id, last.slots, facility)
            else:
                self._schedule(t, "common.say_again", {}, facility)
        # acknowledge: nothing to say
        return []

    # --- non-routine: emergencies, questions, requests without a procedure --------------------------------

    def _emergency(self, interp: Interpretation, facility: Facility, t: float) -> list[BusEvent]:
        st = self.state
        out: list[BusEvent] = []
        if "emergency" not in st.flags:
            st.flags.add("emergency")
            out.append(self._alert(t, "emergency", interp.text))
        details = interp.values
        if details:
            parts = [details.get("emergency", ""), f"{details['souls']} souls" if "souls" in details else "",
                     f"{details['fuel']} of fuel" if "fuel" in details else ""]
            text = ", ".join(p for p in parts if p)
            self._schedule(t, "common.emergency_copied", {"message": Phrase(text, text)}, facility)
        else:
            self._schedule(t, "common.emergency", {}, facility)
        return out

    def _answer(self, interp: Interpretation, facility: Facility, t: float) -> list[BusEvent]:
        """Information the engine has (altimeter, wind, runway, ...) is answered from sim data; the rest is
        worded by the language model from the same facts, or "unable"."""
        st, own = self.state, self.state.aircraft
        topic = interp.values.get("topic", "other")
        if topic == "frequency" and st.comms.expected is not None and st.comms.expected != facility:
            self._handoff(t, "common.contact", facility, st.comms.expected, delay=True)
            return []
        parts: list[tuple[str, dict[str, Any]]] = []
        altimeter = round(own.altimeter_setting_inhg, 2) if own and 25 < own.altimeter_setting_inhg < 33 else None
        if topic in ("altimeter", "weather", "atis") and altimeter is not None:
            parts.append(("altimeter {altimeter}", {"altimeter": altimeter}))
        if topic in ("wind", "weather") and own is not None:
            parts.insert(0, ("wind {wind}", {"wind": self._wind(own)}))
        if topic == "atis" and st.assignments.atis:
            parts.insert(0, ("information {atis} is current", {"atis": st.assignments.atis}))
        if topic == "runway" and (runway := self._runway_in_use(own)) is not None:
            parts.append(("runway {runway}", {"runway": runway}))
        if topic == "squawk" and st.assignments.squawk:
            parts.append(("squawk {squawk}", {"squawk": st.assignments.squawk}))
        if topic == "altitude" and st.assignments.altitude_ft:
            parts.append(("maintain {altitude}", {"altitude": st.assignments.altitude_ft}))
        if parts:
            phrases = [Phrase(*self.library.fill(text, slots, context=f"answer {topic}")) for text, slots in parts]
            self._schedule(t, "common.info", {"message": sum(phrases[1:], phrases[0])}, facility)
            return []
        return self._phrase(interp, facility, t, "answer")

    def _decline(self, interp: Interpretation, facility: Facility, t: float) -> list[BusEvent]:
        return self._phrase(interp, facility, t, "decline")

    def _phrase(self, interp: Interpretation, facility: Facility, t: float, decision: str) -> list[BusEvent]:
        if self.phraser is None:
            self._schedule(t, "common.unable", {}, facility)
            return []
        callsign = self._callsign()
        callsigns = tuple({speech.callsign_display(callsign), speech.callsign_display(callsign.short), callsign.ident})
        message, exchanges = self.phraser.reply(
            pilot=interp.text, decision=decision, facts=self._facts(), callsigns=callsigns, t=t,
            trigger="question" if decision == "answer" else "unsupported_request",
        )
        if message is None:
            self._schedule(t, "common.unable", {}, facility)
        else:
            self._schedule(t, "common.info", {"message": message}, facility)
        return list(exchanges)

    def _altitude_request(self, interp: Interpretation, facility: Facility, t: float, own: OwnshipState | None) -> None:
        st = self.state
        wanted = interp.values.get("altitude")
        if facility.controller not in ("departure", "center", "approach") or own is None or own.on_ground:
            self._schedule(t, "common.unable", {}, facility)
            return
        if not isinstance(wanted, int):
            self._schedule(t, "common.say_altitude", {}, facility)
            return
        if not 1000 <= wanted <= 45000 or st.phase in (P.APPROACH, P.LANDING):
            self._schedule(t, "common.unable", {}, facility)
            return
        climbing = wanted > own.alt_indicated_ft

        def approve() -> None:
            self._assign(altitude_ft=wanted)
            if st.phase in (P.DEPARTURE, P.CRUISE) and climbing:
                st.flight.cruise_ft = wanted
                self._assign(cruise_ft=wanted)
                self.tracker.detector.cruise_ft = wanted  # cruise is wherever the pilot now levels off

        self._schedule(t, "common.climb" if climbing else "common.descend", {"altitude": wanted}, facility, on_issue=approve)

    def _facts(self) -> dict[str, str]:
        """What the phrasing model may use, in display form. Nothing here is an instruction to fly."""
        st, own = self.state, self.state.aircraft
        facts: dict[str, str] = {}
        dest = self.geometry(st.flight.destination)
        if dest is not None:
            facts["destination"] = speech.airport_name(dest.airport.name, dest.airport.icao)
        if own is not None:
            facts["wind"] = speech.wind_display(self._wind(own))
            if 25 < own.altimeter_setting_inhg < 33:
                facts["altimeter"] = f"{own.altimeter_setting_inhg:.2f}"
        if st.assignments.departure_runway:
            facts["departure runway"] = st.assignments.departure_runway
        if st.assignments.arrival_runway:
            facts["arrival runway"] = st.assignments.arrival_runway
        if st.phase:
            facts["phase"] = st.phase.lower().replace("_", " ")
        return facts

    def _runway_in_use(self, own: OwnshipState | None) -> str | None:
        st = self.state
        if st.phase is not None and P(st.phase) not in DEPARTURE_PHASES:
            if st.assignments.arrival_runway:
                return st.assignments.arrival_runway
            plan = self._arrival_plan(own) if own is not None else None
            return plan["approach"].runway if plan else None
        return st.assignments.departure_runway or self._departure_runway(own)

    @staticmethod
    def _wind(own: OwnshipState) -> Wind:
        magvar = ((own.hdg_true - own.hdg_mag + 180) % 360) - 180  # east positive; sim MAGVAR sign conventions vary
        return Wind(direction_mag=int(round((own.wind_dir_true - magvar) / 10) * 10) % 360 or 360, speed_kt=int(round(own.wind_kt)))

    # --- instructions ----------------------------------------------------------------------------------

    def _ifr_clearance(self, t: float, facility: Facility, interp: Interpretation) -> None:
        st = self.state
        dest = st.flight.destination
        if dest is None:
            self._schedule(t, "clearance.say_destination", {}, facility)
            return
        if interp.values.get("atis"):
            self._assign(atis=interp.values["atis"])
        cruise = st.flight.cruise_ft or 5000
        initial = min(cruise, 5000)
        departure = self.facility("departure") or self._center()
        dest_geo = self.geometry(dest)
        destination = speech.airport_name(dest_geo.airport.name, dest) if dest_geo else dest
        squawk = self._squawk()
        self._schedule(
            t, "clearance.ifr",
            {"destination": destination, "altitude": initial, "cruise": cruise, "frequency": departure.mhz, "squawk": squawk},
            facility, clearance="ifr",
            on_issue=lambda: self._assign(squawk=squawk, altitude_ft=initial, cruise_ft=cruise, departure_mhz=departure.mhz),
        )

    def _departure_runway(self, own: OwnshipState | None) -> str | None:
        geo = self.geometry(self.state.flight.origin)
        if geo is None or own is None:
            return None
        end = select_runway(geo, own.wind_dir_true, own.wind_kt)
        return end.ident if end else None

    def _taxi_out(self, t: float, facility: Facility, own: OwnshipState | None) -> None:
        geo = self.geometry(self.state.flight.origin)
        if geo is None or own is None:
            self._schedule(t, "common.roger", {}, facility)
            return
        end = select_runway(geo, own.wind_dir_true, own.wind_kt)
        route = TaxiGraph(geo).departure_route(own.lat, own.lon, end) if end else None
        if end is None:
            self._schedule(t, "common.roger", {}, facility)
            return
        if route is None or not route.taxiways:
            self._schedule(t, "ground.taxi_out_no_route", {"runway": end.ident}, facility, clearance="taxi",
                           on_issue=lambda: self._assign(departure_runway=end.ident))
            return
        slots: dict[str, Any] = {"runway": end.ident, "taxi_route": route.taxiways}
        instruction = "ground.taxi_out"
        if route.crossings:
            instruction = "ground.taxi_out_hold_short"
            slots["hold_short"] = route.crossings[0].split("/")[0]
        self._schedule(t, instruction, slots, facility, clearance="taxi",
                       on_issue=lambda: self._assign(departure_runway=end.ident, taxi_route=route.taxiways))

    def _checkin(self, t: float, facility: Facility, own: OwnshipState | None) -> None:
        st = self.state
        if facility.controller == "departure":
            cruise = st.assignments.cruise_ft or st.flight.cruise_ft
            if cruise:
                self._schedule(t, "departure.radar_contact", {"station": facility.station, "altitude": cruise}, facility,
                               on_issue=lambda: self._assign(altitude_ft=cruise))
                return
        elif facility.controller == "center":
            self._schedule(t, "center.checkin", {"station": facility.station}, facility)
            return
        elif facility.controller == "approach" and "approach" not in st.clearances and own is not None:
            plan = self._arrival_plan(own)
            if plan is not None:
                self._schedule(
                    t, "approach.descend", {"station": facility.station, "altitude": plan["approach_alt"], "approach": plan["approach"]},
                    facility, on_issue=lambda: self._assign(altitude_ft=plan["approach_alt"], approach=plan["approach"].display,
                                                            arrival_runway=plan["approach"].runway),
                )
                return
        self._schedule(t, "common.roger", {}, facility)

    def _clear_to_land(self, t: float, own: OwnshipState | None, facility: Facility, *, delay: bool, runway: str | None = None) -> None:
        st, ctx = self.state, self.tracker.context
        runway = runway or st.assignments.arrival_runway or (ctx.final.end.ident if ctx.final else None)
        if runway is None or own is None:
            self._schedule(t, "common.say_again", {}, facility)
            return
        self._schedule(t, "tower.land", {"runway": runway, "wind": self._wind(own)}, facility, delay=delay, clearance="landing",
                       on_issue=lambda: self._assign(arrival_runway=runway))

    def _taxi_in(self, t: float, facility: Facility, own: OwnshipState | None) -> None:
        ctx = self.tracker.context
        geo = ctx.airport
        route = TaxiGraph(geo).parking_route(own.lat, own.lon) if geo is not None and own is not None else None
        if route is not None and route.taxiways:
            self._schedule(t, "ground.taxi_in", {"taxi_route": route.taxiways}, facility, clearance="taxi_in",
                           on_issue=lambda: self._assign(taxi_route=route.taxiways))
        else:
            self._schedule(t, "ground.taxi_in_no_route", {}, facility, clearance="taxi_in")

    def _handoff(self, t: float, instruction_id: str, from_facility: Facility, to_facility: Facility, *, delay: bool = False) -> None:
        self._schedule(t, instruction_id, {"station": to_facility.station, "frequency": to_facility.mhz}, from_facility,
                       delay=delay, handoff_to=to_facility)

    def _arrival_plan(self, own: OwnshipState) -> dict[str, Any] | None:
        geo = self.geometry(self.state.flight.destination)
        if geo is None:
            return None
        # Once an arrival runway is assigned, stick with it; later wind samples shouldn't flip the plan.
        assigned = self.state.assignments.arrival_runway
        end = geo.end(assigned) if assigned else select_runway(geo, own.wind_dir_true, own.wind_kt)
        if end is None:
            return None
        elev = geo.airport.elev_ft
        cruise = self.state.flight.cruise_ft or 10000
        return {
            "approach": Approach("ILS" if end.has_ils else "RNAV", end.ident),
            "arrival_alt": int(min(cruise, max(3000, math.ceil((elev + 2500) / 1000) * 1000))),
            "approach_alt": int(math.ceil((elev + 2000) / 100) * 100),
        }

    # --- scheduling & issuing ------------------------------------------------------------------------------

    def _schedule(
        self,
        t: float,
        instruction_id: str,
        slots: dict[str, Any],
        facility: Facility,
        *,
        delay: bool = True,
        clearance: str | None = None,
        handoff_to: Facility | None = None,
        expects_readback: bool = True,
        on_issue: Callable[[], None] | None = None,
    ) -> None:
        due = t + (self._random().uniform(*self.cfg.response_delay_s) if delay else 0.0)
        self._scheduled.append(_Scheduled(due, instruction_id, slots, facility, clearance, handoff_to, expects_readback, on_issue))

    def _transmitting(self, t: float) -> bool:
        """True while the pilot holds push-to-talk.

        The COM TRANSMIT simvar means "this radio is selected to transmit on", which is true for the
        whole flight, so it says nothing about whether the mic is keyed.
        """
        if self._ptt_since is None:
            return False
        if t - self._ptt_since > PTT_TIMEOUT_S:  # a missed release must not mute ATC forever
            self._ptt_since = None
            return False
        return True

    def _flush(self, t: float) -> list[BusEvent]:
        if self._transmitting(t):
            return []  # never step on the pilot
        out: list[BusEvent] = []
        due = sorted((s for s in self._scheduled if s.due <= t), key=lambda s: s.due)
        for item in due:
            self._scheduled.remove(item)
            out.append(self._issue(item, t))
        return out

    def _issue(self, item: _Scheduled, t: float) -> AtcTransmission:
        st = self.state
        facility = item.facility
        callsign = self._callsign()
        if facility.controller in st.comms.contacted and not callsign.is_airline:
            callsign = callsign.short
        slots = {**item.slots, "callsign": callsign}
        rendered = self.library.render(item.instruction_id, slots, rng=self._random(), controller=facility.controller)
        st.comms.contacted.add(facility.controller)
        st.comms.last_atc_t = t
        st.exchanges.append(Exchange(t, "atc", facility.controller, rendered.text))
        issued = IssuedInstruction(item.instruction_id, slots, facility, t)
        st.issued[item.instruction_id] = issued
        if item.expects_readback and item.instruction_id not in ("common.say_again", "common.roger", "common.readback_correct"):
            st.last_issued = issued
        if item.expects_readback and (rendered.required or rendered.optional):
            st.pending = PendingReadback(
                item.instruction_id, facility.controller, rendered.expected, rendered.required, rendered.optional, issued_t=t
            )
        if item.clearance:
            st.clearances[item.clearance] = Clearance(
                kind=item.clearance, instruction_id=item.instruction_id, controller=facility.controller, issued_t=t,
                readback="pending" if rendered.required else "none",
            )
        if item.handoff_to is not None:
            st.comms.expected = item.handoff_to
        if item.on_issue is not None:
            item.on_issue()
        return AtcTransmission(
            t=t, station=facility.station, frequency_mhz=facility.mhz, text=rendered.text, controller=facility.controller,
            instruction_id=item.instruction_id, spoken=rendered.spoken,
        )

    # --- helpers ------------------------------------------------------------------------------------------------

    def _rebuild_facilities(self) -> None:
        airports = self.tracker.context_builder.airports
        facilities: list[Facility] = []
        origin, dest = self.state.flight.origin, self.state.flight.destination
        if origin and origin in airports:
            facilities += airport_facilities(airports[origin].airport, role="departure")
        if dest and dest in airports and dest != origin:
            facilities += airport_facilities(airports[dest].airport, role="arrival")
        known: list[Airport] = [g.airport for g in airports.values()]
        facilities.append(center_facility(known, self.cfg.center_name, self.cfg.center_mhz))
        self.facilities = facilities

    def _center(self) -> Facility:
        return next(f for f in self.facilities if f.controller == "center") if self.facilities else center_facility(
            [], self.cfg.center_name, self.cfg.center_mhz
        )

    def _facility_for(self, mhz: float | None) -> Facility | None:
        if mhz is None:
            return None
        matches = [f for f in self.facilities if f.matches(mhz)]
        if len(matches) <= 1:
            return matches[0] if matches else None
        expected = self.state.comms.expected
        if expected is not None and expected in matches:
            return expected
        arriving = self.state.phase is not None and P(self.state.phase) not in DEPARTURE_PHASES
        preferred_airport = self.state.flight.destination if arriving else self.state.flight.origin
        ranked = sorted(matches, key=lambda f: (f.airport != preferred_airport, f.controller == ("departure" if arriving else "approach")))
        return ranked[0]

    def _callsign(self) -> Callsign:
        return self.state.flight.callsign or Callsign("UNKNOWN")

    def _random(self) -> random.Random:
        if self._rng is None:
            seed = self.cfg.seed or zlib.crc32(self._callsign().ident.encode())
            self._rng = random.Random(seed)
        return self._rng

    def _squawk(self) -> str:
        rng = random.Random(self.cfg.seed or zlib.crc32(("squawk" + self._callsign().ident).encode()))
        while True:
            code = str(rng.randint(1, 6)) + "".join(rng.choice("01234567") for _ in range(3))
            if code not in RESERVED_SQUAWKS:
                return code

    def _assign(self, **values: Any) -> None:
        for key, value in values.items():
            setattr(self.state.assignments, key, value)

    def _alert(self, t: float, kind: str, detail: str) -> AtcAlert:
        alert = AtcAlert(t=t, kind=kind, detail=detail)
        self.state.alerts.append(alert)
        return alert

    def _fragments(self, elements: list[str], slots: dict[str, Any]) -> Phrase:
        parts = []
        for element in elements:
            try:
                parts.append(self.library.fragment(element, slots))
            except Exception:
                parts.append(Phrase(element.replace("_", " "), element.replace("_", " ")))
        return sum(parts[1:], parts[0]) if parts else Phrase("", "")


def _display(value: Any) -> str:
    if isinstance(value, float):
        return speech.frequency_display(value)
    if isinstance(value, tuple):
        return ", ".join(value)
    if isinstance(value, Approach):
        return value.display
    return str(value)
