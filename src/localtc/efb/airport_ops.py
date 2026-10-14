"""Airport Ops: the ramp's side of the flight, between ATC, GSX, the cabin and the turnaround.

- **Gates**: the gate map's request for a gate goes through ATC's gate assignment (``AtcEngine.request_gate``):
  approved when it's free and fits, and ground then sends the arrival there; refused with the reason.
- **The turnaround**: cleaning, catering, fuel, boarding and baggage (deboarding after landing), each waiting, in
  progress or done. From GSX when it's running (its own published L:vars), the fuel from the sim's fuel quantity
  going up; without GSX, the services aren't made up: they're "not tracked" (the fuel still is).
- **De-icing**: at or below 3°C with moisture (precipitation, fog, in cloud) at the gate before the push: required,
  with a place in the queue and the wait (GSX's own de-icing state when it's running).
- **Ramp closures and parking restrictions**: the airport's NOTAMs that close a taxiway or a stand, and a stand too
  small for the aircraft.
- **Notes to Ops**: maintenance and dispatch notes from the pilot, acknowledged and kept with the flight.

Everything it says is a message from "Airport Ops" in the EFB (never on the ATC frequency), with its time.
"""

import time
from dataclasses import dataclass, field

# GSX's published L:vars (its manual's "GSX Lvars"): 1 available, 2 not available, 3 bypassed, 4 requested,
# 5 being performed, 6 completed.
GSX_VARS = {"boarding": "L:FSDT_GSX_BOARDING_STATE", "deboarding": "L:FSDT_GSX_DEBOARDING_STATE",
            "catering": "L:FSDT_GSX_CATERING_STATE", "fuel": "L:FSDT_GSX_REFUELING_STATE",
            "pushback": "L:FSDT_GSX_DEPARTURE_STATE", "deicing": "L:FSDT_GSX_DEICING_STATE",
            "cleaning": "L:FSDT_GSX_CLEANING_STATE"}
GSX_STATE = {1: "waiting", 2: "not available", 3: "skipped", 4: "requested", 5: "in progress", 6: "done"}
DEPARTURE_SERVICES = ("cleaning", "catering", "fuel", "boarding")
ARRIVAL_SERVICES = ("deboarding",)
DEICE_C = 3.0
DEICE_VIS_M = 1600.0
DEICE_MIN_PER_AIRCRAFT = 6
FUEL_RISE_LB = 50.0  # the fuel up this much while parked: fuelling
FUEL_STEADY_S = 90.0  # ... and steady this long after: done
MESSAGES_KEEP = 80


@dataclass
class Service:
    state: str = "not tracked"
    source: str = ""
    since: float | None = None

    def to_json(self) -> dict:
        return {"state": self.state, "source": self.source, "since": self.since}


@dataclass
class OpsState:
    icao: str = ""
    role: str = ""  # "departure" (at the gate before the push) or "arrival"
    services: dict[str, Service] = field(default_factory=dict)
    deice: dict = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)
    closures: list[str] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)


class AirportOps:
    def __init__(self, engine=None, clock=time.time) -> None:
        self.engine = engine
        self.clock = clock
        self.s = OpsState()
        self.gsx: dict[str, float] = {}
        self._fuel: tuple[float, float] | None = None  # (lb, when) the fuel was last seen steady
        self._fuel_rising_t: float | None = None
        self._told: set[str] = set()
        self.listeners: list = []

    # --- what it's told ---------------------------------------------------------------------------------------

    def observe(self, ev) -> None:
        from localtc.sim_api import AircraftVars, FlightArrived, NoticesReport, OwnshipState, PhaseChanged

        if isinstance(ev, AircraftVars):
            self._gsx({k: v for k, v in ev.values.items()} if hasattr(ev, "values") else {})
        elif isinstance(ev, OwnshipState):
            self._ownship(ev)
        elif isinstance(ev, NoticesReport):
            self._closures(ev)
        elif isinstance(ev, PhaseChanged):
            if ev.phase == "PUSHBACK" and self.s.role == "departure":
                left = [k for k in DEPARTURE_SERVICES if self.s.services.get(k) and self.s.services[k].state == "in progress"]
                if left:
                    self._say(f"Pushing back with {', '.join(left)} still in progress.", "warning", key=f"push-left:{','.join(left)}")
                self.s.deice.pop("position", None)
        elif isinstance(ev, FlightArrived):
            self._arrived(ev)

    def _gsx(self, values: dict) -> None:
        changed = False
        for service, var in GSX_VARS.items():
            raw = values.get(var) if var in values else values.get(var.removeprefix("L:"))
            if raw is None:
                continue
            self.gsx[service] = float(raw)
            state = GSX_STATE.get(int(raw), "")
            if not state or service == "pushback":
                continue
            svc = self.s.services.setdefault(service, Service())
            if svc.state != state or svc.source != "GSX":
                svc.state, svc.source, svc.since = state, "GSX", self.clock()
                changed = True
                if state in ("in progress", "done"):
                    self._say(f"{service.capitalize()} {state}.", "service", key=f"gsx:{service}:{state}")
        if changed:
            self._changed()

    def _ownship(self, own) -> None:
        engine = self.engine
        st = engine.state if engine is not None else None
        icao = (st.flight.origin if st is not None and not getattr(engine, "_landed", False) else
                st.flight.destination if st is not None else "") or ""
        role = "arrival" if engine is not None and getattr(engine, "_landed", False) else "departure"
        if own.on_ground and icao and (icao, role) != (self.s.icao, self.s.role):
            self.s.icao, self.s.role = icao, role
            names = DEPARTURE_SERVICES if role == "departure" else ARRIVAL_SERVICES
            self.s.services = {k: self.s.services.get(k, Service()) for k in names}
            self._welcome()
        parked = own.on_ground and own.gs_kt < 1.0 and not own.engine_running
        if parked and role == "departure" and own.fuel_lb is not None:
            self._fuel_watch(own)
        if parked and role == "departure":
            self._deice(own)

    def _fuel_watch(self, own) -> None:
        if self.s.services.get("fuel") and self.s.services["fuel"].source == "GSX":
            return
        t = own.t
        if self._fuel is None:
            self._fuel = (own.fuel_lb, t)
            return
        base, since = self._fuel
        svc = self.s.services.setdefault("fuel", Service())
        if own.fuel_lb > base + FUEL_RISE_LB:
            if svc.state != "in progress":
                svc.state, svc.source, svc.since = "in progress", "the sim's fuel", self.clock()
                self._say("Fuelling in progress.", "service", key="fuel:start")
                self._changed()
            self._fuel = (own.fuel_lb, t)
            self._fuel_rising_t = t
        elif self._fuel_rising_t is not None and t - self._fuel_rising_t > FUEL_STEADY_S and svc.state == "in progress":
            svc.state, svc.since = "done", self.clock()
            self._say(f"Fuelling complete, {int(round(own.fuel_lb, -1)):,} lb on board.", "service", key="fuel:done")
            self._changed()

    def _deice(self, own) -> None:
        if own.temperature_c is None:
            return
        wet = (own.precip & 12) != 0 or (own.visibility_m is not None and own.visibility_m < DEICE_VIS_M) or own.in_cloud
        required = own.temperature_c <= DEICE_C and wet
        if required and not self.s.deice.get("required"):
            ahead = self._departures_waiting()
            self.s.deice = {"required": True, "position": ahead + 1, "wait_min": (ahead + 1) * DEICE_MIN_PER_AIRCRAFT,
                            "why": f"{own.temperature_c:.0f}°C with " + ("snow" if own.precip & 8 else "rain" if own.precip & 4
                                                                         else "fog" if not own.in_cloud else "cloud"),
                            "state": "queued"}
            self._say(f"De-icing required ({self.s.deice['why']}): you're number {ahead + 1} for the pad, about "
                      f"{self.s.deice['wait_min']} minutes. Call us when ready.", "deice", key="deice")
            self._changed()
        gsx = self.gsx.get("deicing")
        if gsx is not None and self.s.deice.get("required"):
            state = GSX_STATE.get(int(gsx), self.s.deice.get("state"))
            if state != self.s.deice.get("state"):
                self.s.deice["state"] = state
                self._changed()

    def _departures_waiting(self) -> int:
        engine = self.engine
        if engine is None:
            return 0
        moving = [t for t in engine._traffic.values() if t.on_ground and 2.0 < t.gs_kt < 30.0]
        return min(6, len(moving) // 2)

    def _closures(self, ev) -> None:
        closed = sorted({f"{n.kind.replace('_', ' ')}: {n.subject}" for n in ev.notices
                         if n.kind in ("taxiway_closed", "runway_closed", "construction")})
        if closed != self.s.closures:
            self.s.closures = closed
            for c in closed:
                self._say(f"{ev.icao} {c} (NOTAM).", "closure", key=f"closure:{ev.icao}:{c}")
            self._changed()

    def _arrived(self, ev) -> None:
        self._say("Welcome in. Chocks on; deboarding as soon as the doors are open.", "arrival", key="arrived")

    def _welcome(self) -> None:
        engine = self.engine
        if engine is None:
            return
        st = engine.state
        if self.s.role == "departure":
            gate = st.assignments.departure_gate
            self._say(f"Good day from {self.s.icao} Ops" + (f", {gate}" if gate else "") + ". Tell us when you need anything.",
                      "welcome", key=f"welcome:{self.s.icao}:dep")
        else:
            gate = st.assignments.gate
            self._say(f"Welcome to {self.s.icao}" + (f": {gate} is yours" if gate else "") + ".", "welcome",
                      key=f"welcome:{self.s.icao}:arr")

    # --- what's asked of it ---------------------------------------------------------------------------------

    def request_gate(self, label: str) -> dict:
        engine = self.engine
        if engine is None:
            return {"ok": False, "why": "No flight running."}
        icao = engine.state.flight.destination
        ok, why = engine.request_gate(icao, label)
        req = {"gate": label, "icao": icao, "t": self.clock(), "state": "approved" if ok else "refused", "why": why}
        self.s.requests.append(req)
        self._say(why if ok else f"Unable {label}: {why}", "gate", key=f"gate:{label}:{self.clock():.0f}")
        self._changed()
        return {"ok": ok, "why": why}

    def note(self, text: str, kind: str = "maintenance") -> dict:
        text = str(text or "").strip()[:500]
        if not text:
            raise ValueError("an empty note")
        item = {"t": self.clock(), "kind": kind if kind in ("maintenance", "dispatch", "other") else "other", "text": text}
        self.s.notes.append(item)
        self._say(f"Ops copies your {item['kind']} note.", "ack", key=f"note:{item['t']:.0f}")
        self._changed()
        return item

    # --- out ----------------------------------------------------------------------------------------------

    def _say(self, text: str, kind: str, *, key: str) -> None:
        if key in self._told:
            return
        self._told.add(key)
        self.s.messages.append({"t": self.clock(), "kind": kind, "text": text, "from": "Airport Ops"})
        self.s.messages = self.s.messages[-MESSAGES_KEEP:]
        self._changed()

    def _changed(self) -> None:
        for listener in list(self.listeners):
            try:
                listener(self.to_json())
            except Exception:  # noqa: BLE001
                pass

    def to_json(self) -> dict:
        s = self.s
        return {"icao": s.icao, "role": s.role, "services": {k: v.to_json() for k, v in s.services.items()},
                "gsx": bool(self.gsx), "deice": s.deice, "messages": s.messages[-30:], "requests": s.requests[-10:],
                "closures": s.closures, "notes": s.notes[-20:]}
