"""EXPERIMENTAL: LocalTC taking a hand in MSFS's Live Traffic. Off unless ``[traffic] control`` says otherwise.

What SimConnect allows, checked against the SDK before any of this was written:

- **Observing** the sim's traffic: yes (``RequestDataOnSimObjectType``): position, callsign, model, livery, the AI's
  origin, destination and state.
- **Removing** it: no. ``AIRemoveObject`` only takes objects the same client created; the sim's own traffic (Live
  Traffic, its AI) can't be taken out, hidden or handed over and back. Releasing an aircraft from its AI
  (``AIReleaseControl``) leaves it for the client to fly, with no way to give it back: that is rebuilding the traffic
  engine, and isn't done.
- **Creating** traffic: yes, with a model and livery (MSFS 2024's ``AICreate..._EX1``): parked, or flying a flight plan
  under the sim's own AI.

So there are two modes, and neither can make the sim's own traffic do anything:

- ``shadow``: every aircraft the sim has around is followed by a *shadow* here: who it is, what it's doing (parked,
  taxiing, climbing ...), and anything odd about it (a teleport, a callsign changing, two with the same callsign, one
  vanishing). The sim's traffic is untouched. ATC keeps working from the same traffic it always has.
- ``reinject``: the same, and when the sim **drops** an aircraft close by (Live Traffic despawns aircraft on the
  ground and in the air without warning), LocalTC puts the same aircraft back where it was, with its callsign: the
  same model (FSLTL's model of its type and airline instead of the sim's generic one, when FSLTL is installed),
  parked if it was parked at its stand, or flying on at its speed to its destination with the runway from LocalTC's
  own ATIS in its flight plan, so it lands where ATC is landing everyone. Never more than ``max_reinjected``.

What the sim lets back in, as tried in MSFS 2024 (RJTT, 2026-10-08): an aircraft parked at its stand; an arrival in
level flight at least ``MIN_ARRIVAL_NM`` out and ``MIN_ARRIVAL_ABOVE_FT`` above the field (closer in, the sim puts the
copy on the ground at the destination), filed from a real airport and started past it (``PLAN_POSITION``: the
waypoint index, then how far to the next). Not a departure (refused climbing; at 35 nm out it starts from the ground),
nor anything taxiing (it would be left parked on the taxiway). The sim's static parked aircraft (scenery: no flight,
made-up ids shared by many) aren't traffic, and aren't counted or put back. A copy is created with no speed: it's
given its own at once, and its airline and flight number (the model's own airline otherwise). The approach is the
sim's AI's own: it lands on the runway asked for, sometimes after a go-around (it descends late; a fix on the final
didn't change that).

The safeguards: an aircraft that vanishes is waited for (``GRACE_S``) in case it's back under a new object id (the
same callsign or registration close by); one seen twice is a duplicate and LocalTC's copy goes; a copy that never
shows up is given up (``FAILED_S``) and not tried again; one that teleports is noted, not counted as lost; a copy that
flies out of the area is removed; a reconnect, a replay, the end of the flight or turning it off takes every copy out
again (back to the sim's own traffic alone).

Pure: ``observe`` takes each bus event and returns the commands to send and the status to publish.
"""

import logging
import math
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from localtc.sim_api import (
    AiObjectAssigned,
    ConnectionStatus,
    ModelList,
    NearbyAirports,
    OwnshipState,
    RemoveAiAircraft,
    SetAiVar,
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
LEFT_AREA = 1.2  # a copy this far beyond the radius has flown out of the area: removed
DUPLICATE_NM = 3.0
PARKED_KT = 2.0
CRUISE_KT = 100.0
FIRST_REQUEST = 7000  # request ids for the aircraft LocalTC creates, from here up
MIN_ARRIVAL_NM = 16.0  # closer in, an arrival is "landing" to the sim: its copy would be put on the ground
MIN_ARRIVAL_ABOVE_FT = 5000.0
BEHIND_NM = 30.0  # the plan's waypoint behind the aircraft (at least): the sim works out a climb from the departure
BEHIND_NM_PER_FT = 1 / 200  # airport, so a copy starts at its altitude only with room to have climbed to it
PLAN_POSITION = 2.0  # waypoint 2 of the plan (filed from 0, behind 1, here 2): created where it was
KT_TO_FPS = 1.6878
DEPARTURE_MARGIN_NM = 5.0
GROUND_STATES = ("TAXI", "TAKEOFF", "LANDING", "PUSHBACK", "LINEUP", "RUNWAY")  # AI states on the move, not parked

NOTE = ("EXPERIMENTAL. SimConnect can't remove or hand back the sim's own traffic: it's shadowed, never moved. What the "
        "sim drops nearby is put back by LocalTC where the sim allows it: aircraft parked at their stands, and arrivals "
        "16 nm or more out, which then land on LocalTC's runway.")


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
    moved: bool = False  # seen moving or flying: not parked at its stand, whatever its speed when it went
    mode: str = "shadowed"  # shadowed, reinjected, lost, removed
    issues: list[str] = field(default_factory=list)

    @property
    def phase(self) -> str:
        if self.on_ground:
            return "parked" if self.gs_kt < PARKED_KT else "taxiing" if self.gs_kt < 40 else "rolling"
        return "cruise" if self.gs_kt >= 300 and self.alt_ft > 18000 else "flying"

    @property
    def flight(self) -> bool:
        """A flight, not the sim's static scenery: an airline and a number, somewhere to go, or seen moving."""
        return bool((self.airline and self.flight_number) or self.origin or self.destination or self.moved)

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


# The sim's model names for a type, as the type FSLTL names its model by.
TYPE_SAME = {"A20N": "A320", "A21N": "A321", "A19N": "A319", "B38M": "B738", "B39M": "B739", "B37M": "B737",
             "A359": "A350", "A35K": "A350", "B789": "B787", "B788": "B787", "B78X": "B787", "B77W": "B777"}


def type_code(atc_model: str) -> str:
    """The ICAO type in the sim's model name: "A350", "ATCCOM.AC_MODEL B737.0.tts" -> "B737", "$$:ERJ" -> "ERJ"."""
    m = re.search(r"(?:AC_MODEL[ _]|\$\$:)?([A-Z][A-Z0-9]{2,3})(?:\.\d|$)", (atc_model or "").upper().strip())
    return m.group(1) if m else ""


def _family(code: str) -> str:
    return TYPE_SAME.get(code, code)[:3]


def fsltl_model(models: list[tuple[str, str]], atc_model: str, airline: str) -> tuple[str, str] | None:
    """FSLTL's model for this type and airline, when FSLTL is installed: (title, livery), or None. FSLTL names them
    "FSLTL_A359_JAL-Japan Airlines", "FSLTL_FAIB_B738_ASA-Alaska Airlines" (the type, then the airline's ICAO code);
    its "-STUB" ones are placeholders, never used. Only the airline's own: another's colours would be another flight."""
    code = type_code(atc_model)
    kind = _family(code)
    if not kind:
        return None
    best, score = None, 0
    for title, livery in models:
        both = f"{title} {livery}".upper()
        if "FSLTL" not in both or "STUB" in both:
            continue
        tokens = [t for t in re.split(r"[_\s]+", both) if t and t not in ("FSLTL", "FAIB")]
        types = [t for t in tokens if re.fullmatch(r"[A-Z][A-Z0-9]{2,3}", t) and re.search(r"\d", t)]
        if not types or _family(types[0]) != kind:
            continue
        # The airline's ICAO code as FSLTL writes it, in capitals ("JAL-Japan Airlines", "ANA old"): "Sky_Victor"
        # isn't Skymark's SKY.
        codes = {t.split("-")[0] for t in re.split(r"[_\s]+", f"{title} {livery}") if t.split("-")[0].isupper()}
        if airline and airline.upper() not in codes:
            continue  # another airline's colours would be a different flight: the sim's own model instead
        sc = 1 + (types[0] == code)
        if sc > score:
            best, score = (title, livery), sc
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
        self.airports: dict[str, tuple[float, float, float]] = {}  # the sim's airports around: a copy's plan's
        self.own: OwnshipState | None = None
        self._next_request = FIRST_REQUEST
        self._last_status = -1e9
        self._asked_models = False
        self.recent: deque[str] = deque(maxlen=6)  # what it did last, for the status line

    def _did(self, what: str) -> None:
        self.recent.appendleft(what)

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
        elif isinstance(ev, NearbyAirports):
            self.airports.update({a.icao.upper(): (a.lat, a.lon, a.elev_ft) for a in ev.airports})
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
                by_callsign.setdefault(s.callsign, []).append(x.object_id)
                if self.own is not None and haversine_nm(self.own.lat, self.own.lon, x.lat, x.lon) > self.radius_nm * LEFT_AREA:
                    log.info("Traffic control: %s flew out of the area; removing LocalTC's copy", s.callsign)
                    self._did(f"{s.callsign} (put back) flew out of the area: removed")
                    self.ours.pop(x.object_id).mode = "removed"
                    out.append(RemoveAiAircraft(object_id=x.object_id))
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
            s.moved = s.moved or x.gs_kt >= PARKED_KT or not x.on_ground
            if x.airline and x.flight_number:  # the sim's static aircraft share made-up ids ("ASXGSA"): not callsigns
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
        """The sim drops an aircraft and makes it again under a new id, sometimes renamed (its registration, then its
        flight): the same callsign or registration, close by, a moment later."""
        callsign = self._callsign(x)
        for oid, s in list(self.vanished.items()):
            same = s.callsign == callsign or (s.tail and s.tail == x.atc_id and s.flight)
            if same and haversine_nm(s.lat, s.lon, x.lat, x.lon) < 5 and t - s.last_t <= GRACE_S * 2:
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
                self._did(f"{callsign} is MSFS's again: LocalTC's copy removed")
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
            if self._near_edge(s) or not s.flight:
                continue  # left the sim's traffic bubble, or the sim's scenery gone with its tile: nothing odd
            self.lost += 1
            s.note("vanished")
            if self.mode == "reinject":
                back = self._reinject(s, t)
                out += back
                if not back:
                    why = next((i for i in reversed(s.issues) if i.startswith("not put back")), "")
                    self._did(f"{s.callsign} dropped by MSFS, {why}" if why else f"{s.callsign} dropped by MSFS")
            else:
                self._did(f"{s.callsign} dropped by MSFS")
        for p in list(self.pending):
            if t - p.asked_t > FAILED_S:
                self.pending.remove(p)
                self.failed += 1
                p.shadow.note("couldn't be put back (the sim never made it)")
                self._did(f"{p.shadow.callsign}: MSFS didn't make the copy")
        return out

    def _near_edge(self, s: Shadow) -> bool:
        if self.own is None:
            return True
        return haversine_nm(self.own.lat, self.own.lon, s.lat, s.lon) > self.radius_nm * EDGE

    def _model(self, s: Shadow) -> tuple[str, str]:
        """The model to put back: its own, unless that's the sim's generic one and FSLTL has its type and airline."""
        generic = not s.title or "PASSIVEAIRCRAFT" in s.title.upper().replace(" ", "") or "STUB" in s.title.upper()
        if generic and self.fsltl:
            found = fsltl_model(self.models, s.atc_model or s.title, airline_code(s.airline, s.tail or s.callsign))
            if found is not None:
                return found
        return s.title, s.livery

    def _reinject(self, s: Shadow, t: float) -> list[Any]:
        if not self.live:
            return []
        if len(self.ours) + len(self.pending) >= self.max_reinjected:
            s.note("not put back: as many put back as allowed")
            return []
        if s.callsign in self.reinjected_from:
            s.note("not put back: it was put back once already")
            return []
        title, livery = self._model(s)
        if not title:
            s.note("not put back: its model isn't known")
            return []
        request_id = self._next_request
        common = dict(request_id=request_id, title=title, livery=livery, tail=(s.tail or s.callsign.replace(" ", ""))[:12],
                      flight_number=int(s.flight_number) if s.flight_number.isdigit() else -1)
        if s.on_ground:
            if s.moved or s.gs_kt >= PARKED_KT or any(k in s.ai_state.upper() for k in GROUND_STATES):
                s.note("not put back: it was taxiing (it would be left parked where it stopped)")
                return []
            command = SpawnAiAircraft(kind="parked", lat=s.lat, lon=s.lon, alt_ft=s.alt_ft, heading=s.hdg, on_ground=True,
                                      **common)
        else:
            plan, why = self._plan(s)
            if plan is None:
                s.note(f"not put back: {why}")
                return []
            command = SpawnAiAircraft(kind="enroute", plan=plan, plan_position=PLAN_POSITION, lat=s.lat, lon=s.lon,
                                      alt_ft=s.alt_ft, heading=s.hdg, on_ground=False, airspeed_kt=s.gs_kt, **common)
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
        s = p.shadow
        s.mode = "reinjected"
        s.object_id = ev.object_id
        s.last_t = t
        self.ours[ev.object_id] = s
        where = f"flying to {s.destination}" + (f" {r}" if (r := self.runway_for(s.destination)) else "")             if not s.on_ground else "parked"
        self._did(f"{s.callsign} put back, {where}")
        out: list[Any] = []
        if not s.on_ground and s.gs_kt > 0:  # the sim starts it with no speed: on at its own at once
            out.append(SetAiVar(object_id=ev.object_id, name="VELOCITY BODY Z", unit="feet per second",
                                value=s.gs_kt * KT_TO_FPS))
        if s.airline and s.flight_number:  # its callsign for ATC, not the model's own airline
            out += [SetAiVar(object_id=ev.object_id, name="ATC AIRLINE", text=s.airline),
                    SetAiVar(object_id=ev.object_id, name="ATC FLIGHT NUMBER", text=s.flight_number)]
        return out

    def _where(self, icao: str) -> tuple[float, float, float] | None:
        if not icao:
            return None
        return self.airport_at(icao) or self.airports.get(icao.upper())

    def _plan(self, s: Shadow) -> tuple[str | None, str]:
        """A flight plan on from where it was to its destination, the runway LocalTC's ATIS has in use asked for in
        it; the path without its extension, as the sim takes it. (None, why) when the sim couldn't fly it."""
        if self.plan_dir is None:
            return None, "no flight plans here"
        if s.gs_kt < CRUISE_KT:
            return None, "too slow (taking off or landing)"
        dest = self._where(s.destination)
        if dest is None:
            if s.origin and self._where(s.origin) is not None:
                return None, "departing: the sim won't put a climbing aircraft back"
            return None, f"flying to {s.destination or 'somewhere'} LocalTC doesn't know"
        out_nm = haversine_nm(s.lat, s.lon, dest[0], dest[1])
        if out_nm < MIN_ARRIVAL_NM or s.alt_ft - dest[2] < MIN_ARRIVAL_ABOVE_FT:
            return None, f"too close in ({out_nm:.0f} nm, {s.alt_ft - dest[2]:,.0f} ft): the sim would put it on the ground"
        departure = self._departure(s, dest)
        if departure is None:
            return None, "no airport known to file it from"
        runway = self.runway_for(s.destination)
        name = re.sub(r"[^A-Za-z0-9]", "", s.callsign) or str(s.object_id)
        self.plan_dir.mkdir(parents=True, exist_ok=True)
        path = self.plan_dir / f"{name}.pln"
        path.write_text(flight_plan(s, dest, s.destination.upper(), runway, departure), encoding="utf-8")
        return str(path.with_suffix("")), ""

    def _departure(self, s: Shadow, dest: tuple[float, float, float]) -> tuple[str, float, float, float] | None:
        """The airport its plan is filed from: a real one (the sim's list has heliports and strips, "RJ26P", it won't
        file from), farther from the aircraft than its destination is: nearer its departure, the sim has it departing
        (created at -900 ft and climbing, filed from Kisarazu 13 nm away; at its altitude from Narita 25 nm away, with
        Haneda 22 nm ahead). Its origin when that's so, else the nearest such airport around."""
        to_dest = haversine_nm(s.lat, s.lon, dest[0], dest[1])
        dest_icao = s.destination.upper()
        found = [(s.origin.upper(), o)] if s.origin and (o := self._where(s.origin)) is not None else []
        found += sorted(((icao, a) for icao, a in self.airports.items() if re.fullmatch(r"[A-Z]{4}", icao)),
                        key=lambda x: haversine_nm(s.lat, s.lon, x[1][0], x[1][1]))
        for icao, a in found:
            if icao != dest_icao and haversine_nm(s.lat, s.lon, a[0], a[1]) >= to_dest + DEPARTURE_MARGIN_NM:
                return (icao, *a)
        return None

    # --- out --------------------------------------------------------------------------------------------------------

    def status(self, t: float) -> TrafficControlStatus:
        entries = [TrafficControlEntry(object_id=s.object_id, callsign=s.callsign, mode=s.mode, phase=s.phase,
                                       model=s.title, issues=tuple(s.issues))
                   for s in [*self.shadows.values(), *self.ours.values()] if s.flight or s.mode == "reinjected"]
        return TrafficControlStatus(t=t, mode=self.mode, shadowed=sum(1 for s in self.shadows.values() if s.flight),
                                    reinjected=len(self.ours), lost=self.lost, failed=self.failed, fsltl=self.fsltl,
                                    note=NOTE, entries=tuple(sorted(entries, key=lambda e: e.callsign))[:60],
                                    recent=tuple(self.recent))


def _dms(value: float, pos: str, neg: str) -> str:
    hemisphere = pos if value >= 0 else neg
    value = abs(value)
    d = int(value)
    m = int((value - d) * 60)
    sec = (value - d - m / 60) * 3600
    return f"{hemisphere}{d}° {m}' {sec:.2f}\""


def _offset(lat: float, lon: float, bearing: float, nm: float) -> tuple[float, float]:
    d, b = nm / 3440.065, math.radians(bearing)
    la, lo = math.radians(lat), math.radians(lon)
    la2 = math.asin(math.sin(la) * math.cos(d) + math.cos(la) * math.sin(d) * math.cos(b))
    lo2 = lo + math.atan2(math.sin(b) * math.sin(d) * math.cos(la), math.cos(d) - math.sin(la) * math.sin(la2))
    return math.degrees(la2), (math.degrees(lo2) + 540) % 360 - 180


def flight_plan(s: Shadow, dest: tuple[float, float, float], icao: str, runway: str | None,
                departure: tuple[str, float, float, float] | None = None) -> str:
    """An MSFS flight plan that has the aircraft in level flight where it was: filed from ``departure`` (a real
    airport: the sim refuses a plan from a point in the air, or to the airport it's from), on through a waypoint far
    enough behind it to have climbed to its altitude (from an airport 10 nm away, the copy started at 500 ft: the sim
    had it still climbing) and one where it is (``PLAN_POSITION``), to the airport, the runway (LocalTC's ATIS) asked
    for there."""
    lat, lon, elev = dest
    cruise = int(max(s.alt_ft, 3000) // 100 * 100)

    def at(la: float, lo: float, alt: float) -> str:
        return f"{_dms(la, 'N', 'S')},{_dms(lo, 'E', 'W')},+{max(alt, 0):09.2f}"

    def airport(ident: str, la: float, lo: float, alt: float, extra: str = "") -> str:
        return (f'    <ATCWaypoint id="{ident}"><ATCWaypointType>Airport</ATCWaypointType><WorldPosition>'
                f"{at(la, lo, alt)}</WorldPosition>{extra}<ICAO><ICAOIdent>{ident}</ICAOIdent></ICAO></ATCWaypoint>")

    def user(ident: str, la: float, lo: float, alt: float) -> str:
        return (f'    <ATCWaypoint id="{ident}"><ATCWaypointType>User</ATCWaypointType><WorldPosition>'
                f"{at(la, lo, alt)}</WorldPosition></ATCWaypoint>")

    number, designator = "", ""
    if runway:
        m = re.match(r"^0*(\d{1,2})([LRC]?)$", runway.upper())
        if m:
            number, designator = m.group(1), {"L": "LEFT", "R": "RIGHT", "C": "CENTER"}.get(m.group(2), "NONE")
    approach = (f"<RunwayNumberFP>{number}</RunwayNumberFP><RunwayDesignatorFP>{designator}</RunwayDesignatorFP>"
                if number else "")
    dep_id, dep_lat, dep_lon, dep_elev = departure or ("CUSTD", s.lat, s.lon, s.alt_ft)
    first = airport(dep_id, dep_lat, dep_lon, dep_elev) if departure else user(dep_id, dep_lat, dep_lon, dep_elev)
    back = _offset(s.lat, s.lon, (s.hdg + 180) % 360, max(BEHIND_NM, s.alt_ft * BEHIND_NM_PER_FT))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<SimBase.Document Type="AceXML" version="1,0">
  <Descr>AceXML Document</Descr>
  <FlightPlan.FlightPlan>
    <Title>{s.callsign} to {icao}</Title>
    <FPType>IFR</FPType>
    <RouteType>Direct</RouteType>
    <CruisingAlt>{cruise}</CruisingAlt>
    <DepartureID>{dep_id}</DepartureID>
    <DepartureLLA>{at(dep_lat, dep_lon, dep_elev)}</DepartureLLA>
    <DestinationID>{icao}</DestinationID>
    <DestinationLLA>{at(lat, lon, elev)}</DestinationLLA>
{first}
{user("BEHIND", *back, s.alt_ft)}
{user("HERE", s.lat, s.lon, s.alt_ft)}
{airport(icao, lat, lon, elev, approach)}
  </FlightPlan.FlightPlan>
</SimBase.Document>
"""
