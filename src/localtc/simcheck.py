"""Checks against the live sim: what only MSFS can answer, run on the PC with the sim (``localtc debug ...``).

Each check talks to the sim through the same ``SimSource`` the app flies with, and writes what it saw to a JSON
report, so the result can be read without the sim (by a developer, or a Claude session on another machine):

- ``aircraft``: who the user aircraft is, the copilot profile it gets, and every MSFS 2024 input event it lists
  (what a profile's ``[actions.x] input = "NAME"`` can name), with the profile's own input names checked against it.
- ``hands``: the copilot's hands, one control at a time: the command the copilot would send for it (``crew.actions``,
  with the aircraft's profile), whether the sim then shows it (read back as the copilot reads it), and the control
  put back as it was. Parked only: on the ground, stopped. Never the gear, never the engines; the parking brake only
  with the engines off, the autopilot only when asked.
- ``traffic``: the sim's AI traffic as LocalTC sees it (snapshots, identities, installed models, FSLTL), and one
  aircraft created beside the user's, seen in the traffic, then removed. With an airport and runway, one flying in
  on a flight plan to that runway, watched to see whether the sim's AI flies it.

``FakeSim`` (tests/test_simcheck.py) stands in for the sim on a machine without one.
"""

import asyncio
import contextlib
import json
import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from msgspec.structs import replace

from localtc.crew import actions, profiles
from localtc.crew.commands import Command
from localtc.sim_api import (
    AircraftIdentity,
    AircraftInputEvents,
    AircraftSystems,
    AircraftVars,
    ClickSequence,
    WatchVars,
    AiObjectAssigned,
    AirportData,
    NearbyAirports,
    EnumerateModels,
    ModelList,
    OwnshipState,
    RemoveAiAircraft,
    RequestAirportData,
    SendSimEvent,
    SetInputEvent,
    SetAiVar,
    TurnKnob,
    NudgeVar,
    SetSimVar,
    SpawnAiAircraft,
    TrafficIdentity,
    TrafficControlStatus,
    TrafficSnapshot,
)

READBACK_S = 4.0  # how long a control has to show it moved (the systems come once a second)
SPAWN_REQUEST = 9001


@dataclass
class Step:
    """One thing tried: ``result`` pass (the sim showed it), fail (it didn't), sent (sent; the sim gives no way to
    read it back), skip (not tried, ``detail`` says why), info (something seen, nothing tried)."""

    name: str
    result: str
    detail: str = ""
    sent: list[str] = field(default_factory=list)
    before: Any = None
    after: Any = None
    restored: bool | None = None


@dataclass
class Report:
    check: str
    sim: str = ""
    aircraft: str = ""
    model: str = ""
    profile: str = ""
    started: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    input_events: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)

    def add(self, step: Step) -> Step:
        self.steps.append(step)
        return step

    @property
    def failed(self) -> list[Step]:
        return [s for s in self.steps if s.result == "fail"]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1, default=str)

    def text(self) -> str:
        head = [f"{self.check}: {self.aircraft or '(no aircraft)'} [{self.model}] profile {self.profile or '-'}  ({self.sim})"]
        marks = {"pass": "PASS", "fail": "FAIL", "sent": "SENT", "skip": "skip", "info": "info"}
        for s in self.steps:
            line = f"  {marks.get(s.result, s.result):<4}  {s.name}"
            if s.sent:
                line += f"  <- {'; '.join(s.sent)}"
            if s.detail:
                line += f"  ({s.detail})"
            if s.restored is False:
                line += "  NOT RESTORED"
            head.append(line)
        counts = {k: sum(1 for s in self.steps if s.result == k) for k in marks}
        head.append("  " + ", ".join(f"{n} {k}" for k, n in counts.items() if n))
        return "\n".join(head)


def describe(cmd: Any) -> str:
    """A sim command as it reads in a report: "SendSimEvent LANDING_LIGHTS_SET 1"."""
    if isinstance(cmd, SendSimEvent):
        return f"event {cmd.name} {cmd.value}" + (f" #{cmd.index}" if cmd.index else "")
    if isinstance(cmd, SetInputEvent):
        return f"input {cmd.name} = {cmd.value:g}"
    if isinstance(cmd, NudgeVar):
        return f"encoder {cmd.name} by " + " then ".join(f"{d:+g}" for d in cmd.deltas)
    if isinstance(cmd, TurnKnob):
        return f"knob {cmd.name} to {cmd.target:g}"
    if isinstance(cmd, ClickSequence):
        return f"clicks {cmd.name} " + " ".join(map(str, cmd.codes))
    if isinstance(cmd, SetSimVar):
        return f"var {cmd.name} = {cmd.value:g}"
    return f"{type(cmd).__name__} {getattr(cmd, 'hz', '')}".strip()


class Probe:
    """The sim as the checks see it: everything it sends, kept up to date in the background."""

    def __init__(self, source: Any, *, readback_s: float = READBACK_S) -> None:
        self.source = source
        self.readback_s = readback_s
        self.cockpit = actions.Cockpit()
        self.identity: AircraftIdentity | None = None
        self.input_events: tuple[str, ...] | None = None
        self.snapshots: list[TrafficSnapshot] = []
        self.identities: dict[int, TrafficIdentity] = {}
        self.assigned: dict[int, int] = {}  # request id -> object id
        self.models: tuple[tuple[str, str], ...] | None = None
        self.airports: dict[str, Any] = {}
        self.nearby: list[Any] = []  # the sim's airports around (NearbyAirports)
        self.session: Any = None
        self._task: asyncio.Task | None = None
        self._profiles = profiles.load_all(None)
        self._raw_own: OwnshipState | None = None
        self._raw_systems: AircraftSystems | None = None

    async def __aenter__(self) -> "Probe":
        self.session = await self.source.start()
        self._task = asyncio.create_task(self._read())
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.source.stop()

    async def _read(self) -> None:
        async for ev in self.source.events():
            c = self.cockpit
            if isinstance(ev, OwnshipState):
                self._raw_own = ev
                c.own = actions.as_read(ev, c.profile, c.vars)
            elif isinstance(ev, AircraftSystems):
                if c.profile.altitude_index == 3:  # as the copilot reads it (crew/pm.py)
                    ev = replace(ev, ap_altitude_sel=ev.ap_altitude_sel_3)
                self._raw_systems = ev
                c.systems = actions.as_read(ev, c.profile, c.vars)
            elif isinstance(ev, AircraftVars):  # the aircraft's own switches (its profile's [reads])
                c.vars.update(ev.values)
                if self._raw_own is not None:
                    c.own = actions.as_read(self._raw_own, c.profile, c.vars)
                if self._raw_systems is not None:
                    c.systems = actions.as_read(self._raw_systems, c.profile, c.vars)
            elif isinstance(ev, AircraftIdentity):
                self.identity = ev
                c.profile = profiles.for_aircraft(self._profiles, ev.title, ev.atc_model)
                if c.profile.watch:
                    await self.source.send(WatchVars(names=c.profile.watch))
            elif isinstance(ev, AircraftInputEvents):
                self.input_events = ev.names
            elif isinstance(ev, TrafficSnapshot):
                self.snapshots.append(ev)
                del self.snapshots[:-50]
            elif isinstance(ev, TrafficIdentity):
                self.identities[ev.object_id] = ev
            elif isinstance(ev, AiObjectAssigned):
                self.assigned[ev.request_id] = ev.object_id
            elif isinstance(ev, ModelList):
                self.models = ev.models
            elif isinstance(ev, AirportData):
                self.airports[ev.airport.icao.upper()] = ev.airport
            elif isinstance(ev, NearbyAirports):
                self.nearby = list(ev.airports)

    async def until(self, ready: Callable[[], Any], timeout: float) -> bool:
        """Wait for ``ready()`` to be true, up to ``timeout`` seconds."""
        end = time.monotonic() + timeout
        while True:
            if ready():
                return True
            if time.monotonic() >= end:
                return False
            await asyncio.sleep(0.1)

    async def ready(self, timeout: float = 30.0) -> bool:
        """The aircraft, where it is and its switches: what every check needs first."""
        return await self.until(lambda: self.cockpit.own is not None and self.cockpit.systems is not None
                                and self.identity is not None, timeout)

    def fill(self, report: Report) -> None:
        report.sim = f"{getattr(self.session, 'sim_product', '')} {getattr(self.session, 'sim_version', '')}".strip()
        if self.identity is not None:
            report.aircraft, report.model = self.identity.title, self.identity.atc_model
        report.profile = self.cockpit.profile.name
        report.input_events = list(self.input_events or ())

    async def act(self, cmd: Command) -> tuple[str, list[str], str]:
        """Send what the copilot would for ``cmd`` and wait for the sim to show it: (result, what was sent, why)."""
        plan = actions.plan(cmd, self.cockpit)
        if isinstance(plan, str):
            return "skip", [], plan
        sent = [describe(w) for w in plan.writes]
        for w in plan.writes:
            await self.source.send(w)
        if plan.check(self.cockpit) is None and self.cockpit.systems is not None:
            await asyncio.sleep(self.readback_s / 2)  # nothing to read it back by: it goes as sent
            return ("sent" if plan.check(self.cockpit) is None else "pass" if plan.check(self.cockpit) else "fail"), sent, ""
        ok = await self.until(lambda: plan.check(self.cockpit) is True, max(self.readback_s, plan.check_s))
        return ("pass" if ok else "fail"), sent, "" if ok else "the sim didn't show it"


# --- aircraft ------------------------------------------------------------------------------------------------------


async def check_aircraft(probe: Probe, *, wait_s: float = 20.0) -> Report:
    report = Report("aircraft")
    if not await probe.ready():
        report.add(Step("connected", "fail", "no aircraft data from the sim in 30 s: is a flight loaded?"))
        return report
    await probe.until(lambda: probe.input_events is not None, wait_s)
    probe.fill(report)
    events = set(probe.input_events or ())
    report.add(Step("input events", "info" if events else "fail",
                    f"{len(events)} listed" if events else "none listed (MSFS 2020, or the aircraft has none)"))
    for key, write in sorted(probe.cockpit.profile.actions.items()):
        if write.input:
            known = write.input.upper() in events
            report.add(Step(f"profile {key}: input {write.input}", "pass" if known else "fail",
                            "" if known else "this aircraft has no input event by that name"))
    s = probe.cockpit.systems
    report.add(Step("systems", "info", detail=", ".join(f"{k}={v}" for k, v in _fields(s).items()) if s else "none"))
    return report


def _fields(obj: Any) -> dict:
    import msgspec

    return {k: v for k, v in msgspec.structs.asdict(obj).items() if k != "t"} if obj is not None else {}


# --- hands -----------------------------------------------------------------------------------------------------------


def _light(c: actions.Cockpit, name: str) -> bool:
    return bool(getattr(c.systems, f"light_{name}"))


def hands_plan(c: actions.Cockpit, *, autopilot: bool = False) -> list[tuple[str, Command, Command | None, str]]:
    """What to try on this aircraft as it is now: (name, the command, the one that puts it back, why it's skipped)."""
    own, sys_ = c.own, c.systems
    out: list[tuple[str, Command, Command | None, str]] = []
    for name in ("landing", "taxi", "nav", "beacon", "strobe", "logo"):
        now = _light(c, name)
        out.append((f"{name} light {'off' if now else 'on'}", Command("light", "off" if now else "on", name),
                    Command("light", "on" if now else "off", name), ""))
    flaps_now = own.flaps_index if own else 0
    target = "1" if flaps_now == 0 else "up"
    back = c.profile.detent_name(flaps_now, c.flap_positions) if c.flap_positions else str(flaps_now)
    out.append((f"flaps {target}", Command("flaps", target), Command("flaps", back if flaps_now else "up"), ""))
    armed = bool(sys_ and sys_.spoilers_armed)
    out.append((f"spoilers {'disarm' if armed else 'arm'}", Command("spoilers", "disarm" if armed else "arm"),
                Command("spoilers", "arm" if armed else "disarm"), ""))
    hdg = round(sys_.ap_heading_sel) % 360 if sys_ else 0
    out.append(("heading bug", Command("heading", str((hdg + 40) % 360 or 360)), Command("heading", str(hdg or 360)), ""))
    alt = int(round((sys_.ap_altitude_sel if sys_ else 0) / 100) * 100)
    out.append(("autopilot altitude", Command("altitude", str(12000 if alt != 12000 else 14000)),
                Command("altitude", str(alt)) if alt > 0 else None, ""))
    spd = int(round(sys_.ap_speed_sel)) if sys_ else 0
    out.append(("autopilot speed", Command("speed", "250" if spd != 250 else "240"),
                Command("speed", str(spd)) if spd > 0 else None, ""))
    vs = int(round((sys_.ap_vs_sel if sys_ else 0) / 100) * 100)
    out.append(("vertical speed", Command("vs", "1500" if vs != 1500 else "1200"), Command("vs", str(vs)), ""))
    squawk = own.squawk if own else "1200"
    out.append(("squawk", Command("squawk", "4521" if squawk != "4521" else "4522"), Command("squawk", squawk), ""))
    stby = sys_.com1_standby_mhz if sys_ else 0.0
    out.append(("COM1 standby", Command("com_standby", "121.900" if abs(stby - 121.9) > 0.001 else "122.800"),
                Command("com_standby", f"{stby:.3f}") if stby else None, ""))
    com1 = own.com1_mhz if own else 0.0
    out.append(("COM1 active", Command("com_active", "122.950" if abs(com1 - 122.95) > 0.001 else "123.000"),
                Command("com_active", f"{com1:.3f}") if com1 else None, ""))
    baro = own.altimeter_setting_inhg if own and own.altimeter_setting_inhg else 29.92
    out.append(("altimeter (first officer's)", Command("altimeter", "30.12" if abs(baro - 30.12) > 0.005 else "30.02"),
                Command("altimeter", f"{baro:.2f}"), ""))
    engines_off = sys_ is not None and sys_.engines_running == 0
    brake = bool(own and own.parking_brake)
    out.append((f"parking brake {'release' if brake else 'set'}", Command("parking_brake", "off" if brake else "on"),
                Command("parking_brake", "on" if brake else "off"),
                "the engines are running" if not engines_off else
                "the sim doesn't show this aircraft's brake: not touched" if "parking_brake" in c.profile.unread else ""))
    ap = bool(sys_ and sys_.ap_master)
    out.append((f"autopilot {'off' if ap else 'on'}", Command("autopilot", "off" if ap else "on"),
                Command("autopilot", "on" if ap else "off"), "" if autopilot else "only with --autopilot"))
    if c.profile.autobrake:
        out.append(("autobrake (read only)", Command("check"), None,
                    f"switch at {sys_.autobrake if sys_ else '?'}: {c.profile.autobrake_name(sys_.autobrake) if sys_ else '?'}"))
    return out


def _state(c: actions.Cockpit, cmd: Command) -> Any:
    """What the sim shows for the control ``cmd`` moves, for the report."""
    own, s = c.own, c.systems
    if own is None or s is None:
        return None
    return {
        "light": lambda: _light(c, cmd.target), "flaps": lambda: own.flaps_index, "spoilers": lambda: s.spoilers_armed,
        "heading": lambda: s.ap_heading_sel, "altitude": lambda: s.ap_altitude_sel, "speed": lambda: s.ap_speed_sel,
        "vs": lambda: s.ap_vs_sel, "squawk": lambda: own.squawk, "com_standby": lambda: s.com1_standby_mhz,
        "com_active": lambda: own.com1_mhz, "altimeter": lambda: own.altimeter_setting_inhg,
        "parking_brake": lambda: own.parking_brake, "autopilot": lambda: s.ap_master,
    }.get(cmd.action, lambda: None)()


async def check_hands(probe: Probe, *, autopilot: bool = False, only: tuple[str, ...] = ()) -> Report:
    report = Report("hands")
    if not await probe.ready():
        report.add(Step("connected", "fail", "no aircraft data from the sim in 30 s: is a flight loaded?"))
        return report
    await probe.until(lambda: probe.input_events is not None, 5.0)
    probe.fill(report)
    own = probe.cockpit.own
    if not own.on_ground or own.gs_kt > 1.0:
        report.add(Step("parked", "fail", "only parked: on the ground and stopped"))
        return report
    for name, cmd, back, skip in hands_plan(probe.cockpit, autopilot=autopilot):
        if only and not any(o.lower() in name.lower() for o in only):
            continue
        if cmd.action == "check":
            report.add(Step(name, "info", skip))
            continue
        if not skip and cmd.action in probe.cockpit.profile.cannot:
            skip = "the profile leaves it to the pilot (cannot)"
        if not skip and cmd.action in actions.PILOT_SIDE:
            skip = "the pilot's side: the copilot leaves it to the pilot"
        if skip:
            report.add(Step(name, "skip", skip))
            continue
        before = _state(probe.cockpit, cmd)
        result, sent, why = await probe.act(cmd)
        step = report.add(Step(name, result, why, sent, before, _state(probe.cockpit, cmd)))
        if back is not None and result in ("pass", "fail", "sent"):
            back_result, back_sent, _ = await probe.act(back)
            step.restored = back_result in ("pass", "sent")
            step.sent += [f"(back) {s}" for s in back_sent]
    return report


# --- traffic ---------------------------------------------------------------------------------------------------------


def offset(lat: float, lon: float, bearing_deg: float, metres: float) -> tuple[float, float]:
    """The point ``metres`` from ``lat, lon`` towards ``bearing_deg`` (true)."""
    d = metres / 6_371_000
    b, la, lo = math.radians(bearing_deg), math.radians(lat), math.radians(lon)
    la2 = math.asin(math.sin(la) * math.cos(d) + math.cos(la) * math.sin(d) * math.cos(b))
    lo2 = lo + math.atan2(math.sin(b) * math.sin(d) * math.cos(la), math.cos(d) - math.sin(la) * math.sin(la2))
    return math.degrees(la2), (math.degrees(lo2) + 540) % 360 - 180


def _seen(probe: Probe, object_id: int) -> bool:
    return bool(probe.snapshots) and any(t.object_id == object_id for t in probe.snapshots[-1].targets)


async def check_traffic(probe: Probe, *, spawn: bool = True, title: str = "", live: bool = False,
                        watch_s: float = 120.0) -> Report:
    report = Report("traffic")
    if not await probe.ready():
        report.add(Step("connected", "fail", "no aircraft data from the sim in 30 s: is a flight loaded?"))
        return report
    probe.fill(report)
    got = await probe.until(lambda: len(probe.snapshots) >= 2, 15.0)
    n = len(probe.snapshots[-1].targets) if probe.snapshots else 0
    report.add(Step("traffic snapshots", "pass" if got else "fail", f"{n} aircraft around (the sim's AI and others)"))
    await probe.until(lambda: probe.identities, 15.0)
    states = sorted({i.state for i in probe.identities.values() if i.state})
    report.add(Step("traffic identity", "pass" if probe.identities else "fail" if n else "skip",
                    f"{len(probe.identities)} identified; AI states {', '.join(states) or '-'}" if probe.identities
                    else "no identities (title, livery, destination)" if n else "no traffic to identify"))
    await probe.source.send(EnumerateModels())
    await probe.until(lambda: probe.models is not None, 20.0)
    models = probe.models or ()
    fsltl = sum(1 for t, _ in models if t.upper().startswith("FSLTL"))
    report.add(Step("installed models", "pass" if models else "fail", f"{len(models)} models, {fsltl} FSLTL"))
    own = probe.cockpit.own
    model = title or (probe.identity.title if probe.identity else "")
    if spawn and model:
        lat, lon = offset(own.lat, own.lon, (own.hdg_true + 90) % 360, 120)  # beside the user's aircraft
        await probe.source.send(SpawnAiAircraft(request_id=SPAWN_REQUEST, kind="parked", title=model, tail="LTC01",
                                                lat=lat, lon=lon, alt_ft=own.alt_msl_ft, heading=own.hdg_true, on_ground=True))
        ok = await probe.until(lambda: SPAWN_REQUEST in probe.assigned, 15.0)
        step = report.add(Step("create a parked aircraft", "pass" if ok else "fail",
                               f"{model}, 120 m to the right" + ("" if ok else ": no object id from the sim in 15 s")))
        if ok:
            oid = probe.assigned[SPAWN_REQUEST]
            seen = await probe.until(lambda: _seen(probe, oid), 15.0)
            report.add(Step("... seen in the traffic", "pass" if seen else "fail", f"object {oid}"))
            await probe.source.send(RemoveAiAircraft(object_id=oid))
            gone = await probe.until(lambda: not _seen(probe, oid), 15.0)
            step.restored = gone
            report.add(Step("... removed", "pass" if gone else "fail", f"object {oid}"))
    elif spawn:
        report.add(Step("create a parked aircraft", "skip", "no model to create (the user aircraft's title unknown)"))
    if live:
        await _live(probe, report, watch_s)
    return report


async def _live(probe: Probe, report: Report, watch_s: float) -> None:
    """LocalTC's traffic for ``watch_s`` seconds: the real flights around fetched, created in the sim, followed, the
    gates around filled; then all taken away. Each one is checked against where its real one is."""
    from localtc.sim_api import AiObjectAssigned
    from localtc.traffic.feed import FlightBook, LiveFeed
    from localtc.traffic.manager import TrafficManager, TrafficSettings
    from localtc.traffic.models import ModelPicker

    own = probe.cockpit.own
    feed, book = LiveFeed(), FlightBook(None)
    flights = await asyncio.to_thread(feed.around, own.lat, own.lon, 50.0)
    if not flights:
        report.add(Step("live flights", "fail", "no source answered (adsb.lol, adsb.fi): no internet?"))
        return
    report.add(Step("live flights", "pass", f"{len(flights)} within 50 nm from {feed.source}, "
                                           f"{sum(1 for f in flights if f.on_ground)} on the ground"))
    await probe.until(lambda: probe.airports, 30.0)
    m = TrafficManager(TrafficSettings(radius_nm=30.0, max_live=25, max_parked=40), picker=ModelPicker(list(probe.models or ())),
                       types=book.type_of)
    for airport in probe.airports.values():
        m.on_airport(airport)
    m.on_own(own)
    m.on_feed(flights, feed.source, time.time())
    seen: set[int] = set()
    errors: list[float] = []
    jumps: list[float] = []
    last_pos: dict[int, tuple[float, float, float]] = {}
    end_at, next_feed = time.monotonic() + watch_s, time.monotonic() + 5
    looked: set[int] = set()
    while time.monotonic() < end_at:
        m.on_own(probe.cockpit.own)
        if probe.snapshots:
            m.on_snapshot(probe.snapshots[-1])
        for request_id, object_id in list(probe.assigned.items()):
            if request_id in m.by_request:
                for cmd in m.on_assigned(AiObjectAssigned(t=0.0, request_id=request_id, object_id=object_id), time.time()):
                    await probe.source.send(cmd)
        for cmd in m.tick(time.time()):
            if not isinstance(cmd, TrafficControlStatus):
                await probe.source.send(cmd)
        if time.monotonic() >= next_feed:
            next_feed = time.monotonic() + 5
            got = await asyncio.to_thread(feed.around, probe.cockpit.own.lat, probe.cockpit.own.lon, 50.0)
            m.on_feed(got, feed.source, time.time())
            if got:
                await asyncio.to_thread(book.look_up, got)
        latest = probe.snapshots[-1] if probe.snapshots else None
        if latest is None or id(latest) in looked:
            await asyncio.sleep(0.5)
            continue
        looked.add(id(latest))
        # When the sim's traffic was read (its clock), on this one's: the real ones compared at that moment.
        now = time.time() - (probe.source.clock.now() - latest.t)
        for target in latest.targets:
            plane = m.by_object.get(target.object_id)
            if plane is None:
                continue
            seen.add(target.object_id)
            o = probe.cockpit.own
            if _nm(target.lat, target.lon, o.lat, o.lon) > 10:
                continue  # (written to the sim twice a second or less out there: nobody sees it)
            if plane.mode == "live" and plane.flight is not None and not plane.flight.on_ground and plane.flight.t <= now:
                lat, lon, _alt, _hdg = m._where(plane, now)
                errors.append(_nm(target.lat, target.lon, lat, lon) * 1852)
            prev = last_pos.get(target.object_id)
            if prev is not None and target.gs_kt < 400:
                moved = _nm(prev[0], prev[1], target.lat, target.lon) * 1852
                expected = target.gs_kt * 0.5144 * (now - prev[2])
                jumps.append(moved - expected)
            last_pos[target.object_id] = (target.lat, target.lon, now)
        await asyncio.sleep(0.5)
    live = sum(1 for p in m.planes.values() if p.live and p.object_id is not None)
    parked = sum(1 for p in m.planes.values() if not p.live and p.object_id is not None)
    report.add(Step("created", "pass" if live + parked else "fail",
                    f"{live} real flights, {parked} parked at {', '.join(sorted(m.parked_at)) or 'no airport'} "
                    f"(models: {'FSLTL' if m.picker.fsltl else 'the sim own'})"))
    report.add(Step("seen in the sim's traffic", "pass" if seen else "fail", f"{len(seen)} of LocalTC's"))
    if errors:
        errors.sort()
        median = errors[len(errors) // 2]
        report.add(Step("following the real ones", "pass" if median < 400 else "fail",
                        f"within 10 nm: median {median:.0f} m from where the real one is, 90% within "
                        f"{errors[int(len(errors) * 0.9)]:.0f} m ({len(errors)} looks)"))
    if jumps:
        worst = max(jumps)
        report.add(Step("no jumps", "pass" if worst < 300 else "fail",
                        f"within 10 nm, the most one moved more than its speed accounts for between two looks: {worst:.0f} m"))
    removed = m.set_on(False)
    for cmd in removed:
        await probe.source.send(cmd)
    gone = await probe.until(lambda: not any(t.object_id in seen for t in (probe.snapshots[-1].targets
                                                                           if probe.snapshots else ())), 20.0)
    report.add(Step("all taken away", "pass" if gone else "fail", f"{len(removed)} removed"))
    for line in m.recent:
        report.add(Step("note", "info", line))


def _nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(a))


def write(report: Report, out: Path | None) -> Path:
    """The report as JSON (``out``, else ``simcheck-<check>-<time>.json`` here)."""
    path = out or Path(f"simcheck-{report.check}-{time.strftime('%Y%m%d-%H%M%S')}.json")
    path.write_text(report.to_json(), encoding="utf-8")
    return path
