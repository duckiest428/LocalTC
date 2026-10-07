"""EXPERIMENTAL: LocalTC taking a hand in MSFS's Live Traffic. Off unless ``[traffic] control`` says otherwise.

What SimConnect allows, checked against the SDK before any of this was written:

- **Observing** the sim's traffic: yes (``RequestDataOnSimObjectType``): position, callsign, model, livery, the AI's
  origin, destination and state.
- **Removing** it: no. ``AIRemoveObject`` only takes objects the same client created; the sim's own traffic (Live
  Traffic, its AI) can't be taken out, hidden or handed over and back. Releasing an aircraft from its AI
  (``AIReleaseControl``) leaves it for the client to fly, with no way to give it back: that is rebuilding the traffic
  engine, and isn't done.
- **Creating** traffic: yes, with a model and livery (MSFS 2024's ``AICreate..._EX1``): parked, or flying a flight plan
  under the sim's own AI and ATC.

So there are two modes, and neither can make the sim's own traffic do anything:

- ``shadow``: every aircraft the sim has around is followed by a *shadow* here: who it is, what it's doing (parked,
  taxiing, climbing ...), and anything odd about it (a teleport, a callsign changing, two with the same callsign, one
  vanishing). The sim's traffic is untouched. ATC keeps working from the same traffic it always has.
- ``reinject``: the same, and when the sim **drops** an aircraft close by (Live Traffic despawns aircraft on the
  ground and in the air without warning), LocalTC puts the same aircraft back where it was: the same model and
  livery (FSLTL's model of it instead, when FSLTL is installed), parked if it was parked, or flying on to its
  destination if it was flying there and the destination is one of this flight's airports, with the runway from
  LocalTC's own ATIS in its flight plan. Nothing that was taxiing, taking off or landing is put back (it couldn't be
  without jumping). Never more than ``max_reinjected``.

The safeguards: an aircraft that vanishes is waited for (``GRACE_S``) in case it's back under a new object id; one
seen twice is a duplicate and LocalTC's copy goes; a copy that never shows up is given up (``FAILED_S``) and not tried
again; one that teleports is noted, not counted as lost; a reconnect, a replay, the end of the flight or turning it
off takes every copy out again (back to the sim's own traffic alone).

Pure: ``observe`` takes each bus event and returns the commands to send and the status to publish.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from localtc.sim_api import (
    AiObjectAssigned,
    ConnectionStatus,
    ModelList,
    OwnshipState,
    RemoveAiAircraft,
    SimLifecycle,
    SpawnAiAircraft,
    TrafficControlEntry,
    TrafficControlStatus,
    TrafficIdentity,
    TrafficSnapshot,
    TrafficTarget,
)
from localtc.sim_api.geo import haversine_nm

log = logging.getLogger(__name__)

GRACE_S = 8.0  # gone this long (and not back under another id) before it's lost
FAILED_S = 15.0  # a copy that hasn't appeared by then has failed
STATUS_EVERY_S = 5.0
EDGE = 0.8  # vanishing beyond this share of the radius is just leaving the sim's bubble: not put back
DUPLICATE_NM = 3.0
PARKED_KT = 2.0
CRUISE_KT = 100.0
FIRST_REQUEST = 7000  # request ids for the aircraft LocalTC creates, from here up

NOTE = ("EXPERIMENTAL. SimConnect can't remove or hand back the sim's own traffic: it's shadowed, never moved; only "
        "aircraft the sim itself drops nearby are put back by LocalTC.")


@dataclass
class Shadow:
    object_id: int
    callsign: str
    tail: str = ""  # its registration (ATC ID): what the copy is given, the airline and flight number besides
    airline: str = ""
    flight_number: str = ""
    atc_model: str = ""
    title: str = ""
    livery: str = ""
    origin: str = ""
    destination: str = ""
    ai_state: str = ""
    lat: float = 0.0
    lon: float = 0.0
    alt_ft: float = 0.0
    hdg: float = 0.0
    gs_kt: float = 0.0
    on_ground: bool = True
    first_t: float = 0.0
    last_t: float = 0.0
    mode: str = "shadowed"  # shadowed, reinjected, lost, removed
    issues: list[str] = field(default_factory=list)

    @property
    def phase(self) -> str:
        if self.on_ground:
            return "parked" if self.gs_kt < PARKED_KT else "taxiing" if self.gs_kt < 40 else "rolling"
        return "cruise" if self.gs_kt >= 300 and self.alt_ft > 18000 else "flying"

    def note(self, issue: str) -> None:
        if issue not in self.issues:
            self.issues.append(issue)
            log.info("Traffic control: %s (%s): %s", self.callsign or self.object_id, self.object_id, issue)


@dataclass
class _Pending:
    request_id: int
    shadow: Shadow
    asked_t: float
    model: str


def fsltl_model(models: list[tuple[str, str]], atc_model: str, airline: str) -> tuple[str, str] | None:
    """FSLTL's model for this type and airline, when FSLTL is installed: (title, livery), or None."""
    if not atc_model:
        return None
    kind = atc_model.upper().replace("-", "")
    best, score = None, 0
    for title, livery in models:
        both = f"{title} {livery}".upper()
        if "FSLTL" not in both or kind not in both.replace("-", ""):
            continue
        s = 1 + (2 if airline and re.search(rf"\b{re.escape(airline.upper())}\b", both) else 0)
        if s > score:
            best, score = (title, livery), s
    return best


def airline_code(target_airline: str, callsign: str) -> str:
    """The airline's ICAO code from its callsign ("AAL123" -> "AAL"), or its name as the sim has it."""
    m = re.match(r"^([A-Z]{3})\d", callsign or "")
    return m.group(1) if m else target_airline


class TrafficControl:
    def __init__(self, mode: str = "off", *, radius_nm: float = 25.0, max_reinjected: int = 8, live: bool = True,
                 runway_for: Callable[[str], str | None] = lambda icao: None,
                 airport_at: Callable[[str], tuple[float, float, float] | None] = lambda icao: None,
                 plan_dir: Path | None = None) -> None:
        self.mode = mode if mode in ("off", "shadow", "reinject") else "off"
        self.radius_nm = radius_nm
        self.max_reinjected = max_reinjected
        self.live = live  # a replay never injects anything
        self.runway_for = runway_for  # the runway LocalTC's ATIS has in use at an airport, for a copy's flight plan
        self.airport_at = airport_at  # (lat, lon, elevation ft) of an airport LocalTC knows
        self.plan_dir = plan_dir
        self.shadows: dict[int, Shadow] = {}
        self.vanished: dict[int, Shadow] = {}  # gone, waiting out the grace
        self.pending: list[_Pending] = []
        self.ours: dict[int, Shadow] = {}  # object id: LocalTC's copy
        self.reinjected_from: set[str] = set()  # callsigns put back once: never twice
        self.failed: int = 0
        self.lost: int = 0
        self.models: list[tuple[str, str]] = []
        self.own: OwnshipState | None = None
        self._next_request = FIRST_REQUEST
        self._last_status = -1e9
        self._asked_models = False

    @property
    def fsltl(self) -> bool:
        return any("FSLTL" in f"{t} {l}".upper() for t, l in self.models)

    # --- in -------------------------------------------------------------------------------------------------------

    def observe(self, ev: Any) -> list[Any]:
        if self.mode == "off":
            return []
        out: list[Any] = []
        t = ev.t
        if isinstance(ev, OwnshipState):
            self.own = ev
        elif isinstance(ev, TrafficSnapshot):
            out += self._snapshot(ev, t)
        elif isinstance(ev, TrafficIdentity):
            s = self.shadows.get(ev.object_id) or self.vanished.get(ev.object_id)
            if s is not None:
                if s.title and ev.title and s.title != ev.title:
                    s.note("its model changed")
                s.title, s.livery = ev.title or s.title, ev.livery or s.livery
                s.origin, s.destination, s.ai_state = ev.origin or s.origin, ev.destination or s.destination, ev.state
        elif isinstance(ev, ModelList):
            self.models = list(ev.models)
            log.info("Traffic control: %d models installed%s", len(self.models), ", FSLTL among them" if self.fsltl else "")
        elif isinstance(ev, AiObjectAssigned):
            out += self._assigned(ev, t)
        elif isinstance(ev, ConnectionStatus) and not ev.connected:
            self._reset("the sim disconnected: its copies went with it")
        elif isinstance(ev, SimLifecycle) and ev.kind in ("sim_stop", "flight_loaded", "crashed"):
            out += self.release(f"the sim's {ev.kind.replace('_', ' ')}")
        out += self._expire(t)
        if t - self._last_status >= STATUS_EVERY_S:
            self._last_status = t
            out.append(self.status(t))
        return out

    def start(self) -> list[Any]:
        """At the start of a session: ask what's installed (for FSLTL) when it may put aircraft back."""
        from localtc.sim_api import EnumerateModels

        if self.mode == "reinject" and self.live and not self._asked_models:
            self._asked_models = True
            return [EnumerateModels()]
        return []

    def set_mode(self, mode: str, t: float = 0.0) -> list[Any]:
        """Changed mid-flight: out of reinject, every copy goes (the sim's traffic alone again)."""
        out = self.release("traffic control changed") if mode != "reinject" else []
        self.mode = mode if mode in ("off", "shadow", "reinject") else "off"
        if self.mode == "off":
            self.shadows.clear()
            self.vanished.clear()
        out += self.start()
        out.append(self.status(t))
        return out

    def release(self, why: str) -> list[Any]:
        """Every copy LocalTC put in, out again."""
        out = [RemoveAiAircraft(object_id=oid) for oid in self.ours]
        if self.ours:
            log.info("Traffic control: removing %d reinjected aircraft (%s)", len(self.ours), why)
        for s in self.ours.values():
            s.mode = "removed"
        self.ours.clear()
        self.pending.clear()
        return out

    def _reset(self, why: str) -> None:
        log.info("Traffic control: %s", why)
        self.ours.clear()
        self.pending.clear()
        self.shadows.clear()
        self.vanished.clear()

    # --- the sim's traffic ------------------------------------------------------------------------------------------

    def _snapshot(self, ev: TrafficSnapshot, t: float) -> list[Any]:
        out: list[Any] = []
        seen: set[int] = set()
        by_callsign: dict[str, list[int]] = {}
        for x in ev.targets:
            seen.add(x.object_id)
            if x.object_id in self.ours:  # LocalTC's own copy
                s = self.ours[x.object_id]
                s.lat, s.lon, s.alt_ft, s.gs_kt, s.on_ground, s.last_t = x.lat, x.lon, x.alt_ft, x.gs_kt, x.on_ground, t
                by_callsign.setdefault(self._callsign(x), []).append(x.object_id)
                continue
            if self._is_pending_copy(x, t):
                continue
            s = self.shadows.get(x.object_id)
            callsign = self._callsign(x)
            if s is None:
                back = self._came_back(x, t)  # vanished a moment ago, here again under a new id
                s = back or Shadow(object_id=x.object_id, callsign=callsign, first_t=t)
                s.object_id = x.object_id
                if back is not None:
                    s.note("came back under a new object id")
                self.shadows[x.object_id] = s
            else:
                if s.callsign and callsign and callsign != s.callsign:
                    s.note(f"callsign changed from {s.callsign} to {callsign}")
                dt = max(t - s.last_t, 0.1)
                jump = haversine_nm(s.lat, s.lon, x.lat, x.lon)
                if s.last_t and jump > max(2.0, (max(s.gs_kt, x.gs_kt) + 50) / 3600 * dt * 3):
                    s.note(f"teleported {jump:.1f} nm")
            s.callsign = callsign or s.callsign
            s.airline, s.flight_number, s.atc_model, s.tail = x.airline, x.flight_number, x.atc_model, x.atc_id or s.tail
            s.lat, s.lon, s.alt_ft, s.hdg, s.gs_kt, s.on_ground, s.last_t = x.lat, x.lon, x.alt_ft, x.hdg_true, x.gs_kt, x.on_ground, t
            by_callsign.setdefault(s.callsign, []).append(x.object_id)
        for oid in [oid for oid in self.shadows if oid not in seen]:
            s = self.shadows.pop(oid)
            self.vanished[oid] = s
        for callsign, ids in by_callsign.items():
            if callsign and len(ids) > 1:
                out += self._duplicate(callsign, ids)
        return out

    def _callsign(self, x: TrafficTarget) -> str:
        if x.airline and x.flight_number:
            return f"{x.airline} {x.flight_number}"
        return x.atc_id

    def _came_back(self, x: TrafficTarget, t: float) -> Shadow | None:
        callsign = self._callsign(x)
        for oid, s in list(self.vanished.items()):
            if s.callsign == callsign and haversine_nm(s.lat, s.lon, x.lat, x.lon) < 5 and t - s.last_t <= GRACE_S * 2:
                del self.vanished[oid]
                return s
        return None

    def _is_pending_copy(self, x: TrafficTarget, t: float) -> bool:
        """A copy LocalTC asked for, seen before the sim said which object it is: not a new aircraft of the sim's."""
        callsign = self._callsign(x)
        return any(p.shadow.callsign == callsign and haversine_nm(p.shadow.lat, p.shadow.lon, x.lat, x.lon) < 2
                   for p in self.pending)

    def _duplicate(self, callsign: str, ids: list[int]) -> list[Any]:
        mine = [oid for oid in ids if oid in self.ours]
        others = [oid for oid in ids if oid not in self.ours]
        for oid in others:
            if oid in self.shadows:
                self.shadows[oid].note("two aircraft with this callsign")
        if mine and others:  # the sim's own is back: LocalTC's copy goes
            out = []
            for oid in mine:
                log.info("Traffic control: %s is the sim's again; removing LocalTC's copy", callsign)
                self.ours.pop(oid).mode = "removed"
                out.append(RemoveAiAircraft(object_id=oid))
            return out
        return []

    # --- putting one back ------------------------------------------------------------------------------------------

    def _expire(self, t: float) -> list[Any]:
        out: list[Any] = []
        for oid, s in list(self.vanished.items()):
            if t - s.last_t < GRACE_S:
                continue
            del self.vanished[oid]
            s.mode = "lost"
            if self._near_edge(s):
                continue  # left the sim's traffic bubble: nothing odd
            self.lost += 1
            s.note("vanished")
            if self.mode == "reinject":
                out += self._reinject(s, t)
        for p in list(self.pending):
            if t - p.asked_t > FAILED_S:
                self.pending.remove(p)
                self.failed += 1
                p.shadow.note("couldn't be put back (the sim never made it)")
        return out

    def _near_edge(self, s: Shadow) -> bool:
        if self.own is None:
            return True
        return haversine_nm(self.own.lat, self.own.lon, s.lat, s.lon) > self.radius_nm * EDGE

    def _reinject(self, s: Shadow, t: float) -> list[Any]:
        if not self.live:
            return []
        if len(self.ours) + len(self.pending) >= self.max_reinjected:
            s.note("not put back: as many put back as allowed")
            return []
        if s.callsign in self.reinjected_from:
            s.note("not put back: it was put back once already")
            return []
        model = fsltl_model(self.models, s.atc_model, airline_code(s.airline, s.callsign)) if self.fsltl else None
        title, livery = model or (s.title, s.livery)
        if not title:
            s.note("not put back: its model isn't known")
            return []
        request_id = self._next_request
        common = dict(request_id=request_id, title=title, livery=livery, tail=(s.tail or s.callsign.replace(" ", ""))[:12],
                      flight_number=int(s.flight_number) if s.flight_number.isdigit() else -1)
        if s.on_ground and s.gs_kt < PARKED_KT:
            command = SpawnAiAircraft(kind="parked", lat=s.lat, lon=s.lon, alt_ft=s.alt_ft, heading=s.hdg, on_ground=True,
                                      **common)
        elif not s.on_ground and s.gs_kt >= CRUISE_KT and (plan := self._plan(s)) is not None:
            command = SpawnAiAircraft(kind="enroute", plan=plan, plan_position=0.05, lat=s.lat, lon=s.lon, alt_ft=s.alt_ft,
                                      heading=s.hdg, on_ground=False, airspeed_kt=s.gs_kt, **common)
        else:
            s.note("not put back: it was " + ("moving on the ground" if s.on_ground else "flying somewhere LocalTC "
                                              "doesn't know, or too slow (taking off or landing)"))
            return []
        self._next_request += 1
        self.pending.append(_Pending(request_id, s, t, title))
        self.reinjected_from.add(s.callsign)
        log.info("Traffic control: putting %s back (%s, %s%s)", s.callsign, command.kind, title,
                 f", {livery}" if livery else "")
        return [command]

    def _assigned(self, ev: AiObjectAssigned, t: float) -> list[Any]:
        p = next((x for x in self.pending if x.request_id == ev.request_id), None)
        if p is None:
            return []
        self.pending.remove(p)
        p.shadow.mode = "reinjected"
        p.shadow.object_id = ev.object_id
        p.shadow.last_t = t
        self.ours[ev.object_id] = p.shadow
        return []

    def _plan(self, s: Shadow) -> str | None:
        """A flight plan from where it was to its destination (one of LocalTC's airports), the runway LocalTC's ATIS
        has in use asked for in it; the path without its extension, as the sim takes it. None: can't be flown."""
        if self.plan_dir is None or not s.destination:
            return None
        dest = self.airport_at(s.destination)
        if dest is None:
            return None
        runway = self.runway_for(s.destination)
        name = re.sub(r"[^A-Za-z0-9]", "", s.callsign) or str(s.object_id)
        self.plan_dir.mkdir(parents=True, exist_ok=True)
        path = self.plan_dir / f"{name}.pln"
        path.write_text(flight_plan(s, dest, s.destination, runway), encoding="utf-8")
        return str(path.with_suffix(""))

    # --- out --------------------------------------------------------------------------------------------------------

    def status(self, t: float) -> TrafficControlStatus:
        entries = [TrafficControlEntry(object_id=s.object_id, callsign=s.callsign, mode=s.mode, phase=s.phase,
                                       model=s.title, issues=tuple(s.issues))
                   for s in [*self.shadows.values(), *self.ours.values()]]
        return TrafficControlStatus(t=t, mode=self.mode, shadowed=len(self.shadows), reinjected=len(self.ours),
                                    lost=self.lost, failed=self.failed, fsltl=self.fsltl, note=NOTE,
                                    entries=tuple(sorted(entries, key=lambda e: e.callsign))[:60])


def _dms(value: float, pos: str, neg: str) -> str:
    hemisphere = pos if value >= 0 else neg
    value = abs(value)
    d = int(value)
    m = int((value - d) * 60)
    sec = (value - d - m / 60) * 3600
    return f"{hemisphere}{d}° {m}' {sec:.2f}\""


def flight_plan(s: Shadow, dest: tuple[float, float, float], icao: str, runway: str | None) -> str:
    """A minimal MSFS flight plan: from the aircraft's position to the airport, at its altitude, the destination
    runway (LocalTC's ATIS) asked for."""
    lat, lon, elev = dest
    cruise = int(max(s.alt_ft, 3000) // 100 * 100)
    here = f"{_dms(s.lat, 'N', 'S')},{_dms(s.lon, 'E', 'W')},+{max(s.alt_ft, 0):09.2f}"
    there = f"{_dms(lat, 'N', 'S')},{_dms(lon, 'E', 'W')},+{max(elev, 0):09.2f}"
    number, designator = "", ""
    if runway:
        m = re.match(r"^0*(\d{1,2})([LRC]?)$", runway.upper())
        if m:
            number, designator = m.group(1), {"L": "LEFT", "R": "RIGHT", "C": "CENTER"}.get(m.group(2), "NONE")
    approach = (f"<RunwayNumberFP>{number}</RunwayNumberFP><RunwayDesignatorFP>{designator}</RunwayDesignatorFP>"
                if number else "")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<SimBase.Document Type="AceXML" version="1,0">
  <Descr>AceXML Document</Descr>
  <FlightPlan.FlightPlan>
    <Title>{s.callsign} to {icao}</Title>
    <FPType>IFR</FPType>
    <RouteType>Direct</RouteType>
    <CruisingAlt>{cruise}</CruisingAlt>
    <DepartureID>CUSTD</DepartureID>
    <DepartureLLA>{here}</DepartureLLA>
    <DestinationID>{icao}</DestinationID>
    <DestinationLLA>{there}</DestinationLLA>
    <ATCWaypoint id="CUSTD"><ATCWaypointType>User</ATCWaypointType><WorldPosition>{here}</WorldPosition></ATCWaypoint>
    <ATCWaypoint id="{icao}"><ATCWaypointType>Airport</ATCWaypointType><WorldPosition>{there}</WorldPosition>{approach}<ICAO><ICAOIdent>{icao}</ICAOIdent></ICAO></ATCWaypoint>
  </FlightPlan.FlightPlan>
</SimBase.Document>
"""

