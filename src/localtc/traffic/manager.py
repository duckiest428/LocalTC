"""LocalTC's traffic: the real flights around, flown in the sim with FSLTL's models, the gates filled as busy as the
airport is at that hour, and the traffic answering to ATC.

Pure, like the ATC: it's told what happens (the user's aircraft, the live positions, the sim's answers and its
traffic, the airports) and says what to send (``SpawnAiAircraft``, ``AiTrack`` ...) and what to tell the app. The
service (service.py) does the waiting and the network.

- **Live flights** (adsb.lol and the like, feed.py): each one within ``radius_nm`` is created as a non-ATC aircraft
  with its own model (models.py) and callsign, held against the sim's physics and moved along its real track by the
  bridge (sim_bridge/motion.py). One standing at a gate is put exactly on the sim's parking spot.
- **Parked aircraft** at the airport the user is at or going to, on the sim's own parking spots: as many as that
  airport has at that hour (night: nearly all gates full; mid-afternoon at a hub: most; a small field: fewer), the
  airlines that fly there, the size each spot takes. A live flight coming to life at a spot replaces the one parked
  there; the gate ATC gave the user (and wherever the user is parked) is kept clear: anything there is taken away.
- **ATC**: real flights don't know about the user. So when the user has the runway (cleared to land, lined up,
  taking off, rolling out), a real arrival on short final for it is sent around, and a real departure about to go
  onto it is held short until the user's done, then lines up and goes (one at a time, spaced as a tower spaces them).
  On the ground, one taxiing towards the user gives way and waits until the user is clear. Tower says so on its
  frequency, now and then (never a string of calls at once).

Each is moved smoothly: a new live position is never jumped to. The difference from where it was being shown is
eased out slowly enough that it never looks more than a little faster or slower than it is (``_blend``), and one
just made carries on from where it was made (the bridge holds it there the moment the sim makes it).
"""

import logging
import math
import random
import zlib
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from localtc.atc_core.airport.geometry import AirportGeometry
from localtc.sim_api import (
    AiLights,
    AiObjectAssigned,
    AiTrack,
    Airport,
    OwnshipState,
    RemoveAiAircraft,
    SetAiVar,
    SpawnAiAircraft,
    TrafficControlStatus,
    TrafficSnapshot,
)
from localtc.sim_api.geo import advance, haversine_nm, unit
from localtc.traffic import airlines as airline_data
from localtc.traffic.feed import Flight
from localtc.traffic.models import ModelPicker

log = logging.getLogger(__name__)

FIRST_REQUEST = 7000  # request ids for the aircraft LocalTC creates, from here up
KT_TO_FPM = 101.27
M_PER_NM = 1852.0
LEAVE = 1.15  # beyond the radius by this much: taken away (a little past, so one on the edge doesn't flicker)
STALE_S = 40.0  # no position for this long: lost (or, standing at a gate, parked there for good)
LOST_S = 75.0  # carried on this long without a position at most
POP_IN_NM = 6.0  # a new airborne flight isn't created closer than this to the user once the first lot is in
FIRST_LOT_S = 30.0  # the first lot: everything around when the traffic comes on, made over this long
SNAP_M = 45.0  # a live aircraft standing this close to a parking spot is put on it
CLEAR_OF_USER_M = 70.0
RESERVED_M = 35.0
PARKED_AROUND_NM = 12.0  # the airports parked aircraft are put at: within this of the user ...
PARKED_INBOUND_NM = 45.0  # ... or the destination, once this close
PARKED_LEAVE_NM = 30.0  # and taken away once the user is this far from the airport again
PARKED_PER_TICK = 2  # made a couple a second: the sim loads each model's textures as it comes (all at once stutters)
LIVE_PER_TICK = 2
PARKED_MODELS = 10  # an airport's parked aircraft drawn with this many models at most (each one more is memory)
STATUS_EVERY_S = 5.0
SHORT_FINAL_NM = 4.0
SHORT_FINAL_AGL = 2000.0
RUNWAY_NEAR_M = 120.0
HELD_RELEASE_S = 20.0  # the user off the runway this long: the held departure goes
DEPARTURE_GAP_S = 90.0  # then the next one this long after (a tower's spacing on one runway)
CALL_GAP_S = 60.0  # tower tells one held short this often at most (the departures go spaced, a go-around is always said)
GIVE_WAY_M = 90.0  # one taxiing that would come this close to the user on the ground stops and gives way ...
GIVE_WAY_AHEAD_S = 20.0  # ... looking this far ahead
GIVE_WAY_CLEAR_M = 150.0  # ... and goes on once the user is this far away (or behind it)
GIVE_WAY_MAX_S = 180.0  # ... or after this long at most
LOCAL_TTL_S = 300.0  # a go-around or a released departure flown by LocalTC this long at most, then taken away
QUIET_S = 20.0  # an arrival on final not heard from this long (low, out of the receivers' sight): LocalTC lands it
FINAL_NM = 15.0
GLIDE = math.tan(math.radians(3.0))
EXTRAPOLATE_S = 60.0  # carried on past its last position this long at most (as the bridge does) ...
EXTRAPOLATE_GROUND_S = 8.0  # ... on the ground only a few seconds (one carried on past its last report taxied into
# LAX's terminal)
STAND_NM = 150 / 1852  # one taxiing in this close to a free stand it's heading for is put on it ...
STAND_SLOW_KT = 20.0  # ... at taxi speed or less
TAXI_IN_MPS = 2.5  # and rolls onto the stand's marker at a walking pace, stopping there
CATCH_UP = 0.1  # a correction eased out over long enough that it's never more than this share of its speed ...
BLEND_MIN_S, BLEND_MAX_S = 3.0, 20.0  # ... within these
NATIVE_NOTE = ("MSFS's own traffic is on as well: set its air traffic to off (Options > General > Traffic) so only "
               "LocalTC's flies, or two of each will be about.")
NO_FEED_NOTE = "No live positions: no internet, or the free sources are busy. Trying again."
PARKED_NOTE = ("MSFS's own parked aircraft are on: set them to off as well (Options > General > Traffic, parked or "
               "static aircraft) so LocalTC fills the gates with the airlines that fly here.")
NATIVE_PARKED = 6  # this many of the sim's own standing about: its parked aircraft are on
# How full an airport's gates are, by the hour (local): overnight nearly all aircraft are home; through the day
# a third or more are out flying.
BY_HOUR = (0.92, 0.93, 0.93, 0.92, 0.9, 0.85, 0.75, 0.62, 0.58, 0.6, 0.62, 0.62, 0.6, 0.58, 0.6, 0.6, 0.58, 0.58,
           0.6, 0.65, 0.72, 0.8, 0.86, 0.9)
GA_TYPES = ("C172", "PA28", "SR22", "C208", "TBM9", "C25C", "DA62")
CARGO_TYPES = ("B744", "B77L", "MD11", "B763", "A306")
TYPES_BY_SIZE = {"heavy": ("B789", "B77W", "A359", "A333", "B763", "B788", "B772"),
                 "medium": ("A320", "B738", "A321", "A20N", "B739", "A319", "B38M", "A21N", "BCS3", "E190"),
                 "small": ("CRJ9", "E75L", "CRJ7", "E170", "DH8D", "AT76", "CRJ2")}


@dataclass(frozen=True)
class TrafficSettings:
    radius_nm: float = 40.0
    max_live: int = 30
    parked: bool = True
    max_parked: int = 40
    atc_control: bool = True


@dataclass
class AtcLink:
    """What the traffic needs from ATC (the app wires it to the engine; a test gives its own). ``user_runway``: the
    runway the user has right now, (airport, runway end, "landing" | "takeoff" | "on"), or None; ``reserved``: the
    positions no aircraft of LocalTC's may stand on (the gate ATC gave the user, where the user is parked); ``say``:
    ATC telling a real flight (callsign as said, "go_around" | "hold_short" | "takeoff", airport, runway): it speaks
    on the tower frequency when the user is on it."""

    user_runway: Callable[[], tuple[str, str, str] | None] = lambda: None
    reserved: Callable[[], list[tuple[float, float]]] = lambda: []
    say: Callable[[str, str, str, str], list[Any]] = lambda callsign, kind, icao, runway: []
    destination: Callable[[], str] = lambda: ""


@dataclass
class Local:
    """A flight LocalTC flies itself for a while (a go-around, a held departure going): where it is and how it's
    moving, carried on a second at a time."""

    lat: float
    lon: float
    alt_ft: float
    hdg: float
    gs_kt: float
    vs_fpm: float
    on_ground: bool
    target_alt: float = 0.0
    target_kt: float = 0.0
    accel: float = 0.0  # knots a second
    rotate_kt: float = 0.0  # lifts off at this speed
    started: float = 0.0
    runway: tuple[str, str] | None = None  # (airport, runway end) it's landing on


@dataclass
class Plane:
    key: str  # "live:<hex>" or "parked:<icao>:<spot>"
    request_id: int
    title: str
    livery: str
    callsign: str = ""
    object_id: int | None = None
    flight: Flight | None = None
    used_t: float = 0.0  # the live position last sent
    spot: tuple[str, int] | None = None  # (airport, parking index) it stands on
    mode: str = "live"  # live, parked, hold, go_around, departing
    local: Local | None = None
    turn_dps: float = 0.0
    last_track: tuple[float, float] | None = None  # (track, when): the turn worked out from the next one
    lights: tuple = ()
    created_t: float = 0.0
    note: str = ""  # what ATC did with it, said once
    sent: tuple[float, ...] | None = None  # (lat, lon, alt, hdg, gs, vs, turn, when): the last state the bridge has
    runway: tuple[str, str] | None = None  # (airport, runway end) it was held short of
    give_way_t: float = 0.0  # when it stopped for the user

    @property
    def live(self) -> bool:
        return self.key.startswith("live:")


class TrafficManager:
    def __init__(self, settings: TrafficSettings | None = None, atc: AtcLink | None = None, *,
                 picker: ModelPicker | None = None, routes: Callable[[str], Any] | None = None,
                 types: Callable[[str], str] | None = None, seed: int = 0) -> None:
        self.s = settings or TrafficSettings()
        self.atc = atc or AtcLink()
        self.picker = picker or ModelPicker()
        self.routes = routes or (lambda callsign: None)
        self.types = types or (lambda hex_: "")
        self.seed = seed
        self.on = True
        self.own: OwnshipState | None = None
        self.flights: dict[str, Flight] = {}  # by hex: the newest position of each
        self.feed_t = 0.0
        self.source = ""
        self.feed_ok = True
        self.planes: dict[str, Plane] = {}
        self.by_request: dict[int, Plane] = {}
        self.by_object: dict[int, Plane] = {}
        self.airports: dict[str, Airport] = {}
        self._geometry: dict[str, AirportGeometry] = {}
        self.parked_at: set[str] = set()  # airports filled with parked aircraft
        self.native = 0
        self.native_spots: list[tuple[float, float]] = []  # the sim's own aircraft standing about
        self.recent: deque[str] = deque(maxlen=6)
        self._next_request = FIRST_REQUEST
        self._status_t = -1e9
        self._first_t: float | None = None
        self._user_runway: tuple[str, str, str] | None = None
        self._user_runway_t = 0.0  # when the user last had a runway
        self._released_t = -1e9  # the last held departure sent on its way
        self._call_t = -1e9  # the last time tower told one to hold short

    # --- what happens ------------------------------------------------------------------------------------------

    def on_own(self, own: OwnshipState) -> None:
        self.own = own

    def on_feed(self, flights: list[Flight] | None, source: str, now: float) -> None:
        """A new set of live positions (None: no source answered)."""
        self.feed_ok = flights is not None
        if flights is None:
            return
        self.source = source
        self.feed_t = now
        fresh = {f.hex: f for f in flights if f.hex}
        for hex_, f in fresh.items():
            old = self.flights.get(hex_)
            if old is None or f.t > old.t:
                self.flights[hex_] = f
        for hex_ in [h for h, f in self.flights.items() if now - f.t > LOST_S * 2]:
            del self.flights[hex_]

    def on_airport(self, airport: Airport) -> None:
        self.airports[airport.icao.upper()] = airport
        self._geometry.pop(airport.icao.upper(), None)

    def on_models(self, models: list[tuple[str, str]]) -> None:
        self.picker = ModelPicker(list(models))

    def on_assigned(self, ev: AiObjectAssigned, now: float) -> list[Any]:
        plane = self.by_request.pop(ev.request_id, None)
        if plane is None:
            return []
        if self.planes.get(plane.key) is not plane:  # taken away while it was being made
            return [RemoveAiAircraft(object_id=ev.object_id)]
        plane.object_id = ev.object_id
        self.by_object[ev.object_id] = plane
        out: list[Any] = []
        if plane.callsign:
            # Who it is, for ATC (and the map): the airline's code and number, or a registration.
            out += [SetAiVar(object_id=ev.object_id, name="ATC ID", text=plane.callsign[:9])]
            if plane.flight is not None and plane.flight.airline:
                out += [SetAiVar(object_id=ev.object_id, name="ATC AIRLINE", text=plane.flight.airline),
                        SetAiVar(object_id=ev.object_id, name="ATC FLIGHT NUMBER", text=plane.callsign[3:])]
        out += self._track(plane, now)  # from where it was made (the bridge holds it there): eased, not jumped
        return out

    def on_snapshot(self, snap: TrafficSnapshot) -> None:
        """The sim's traffic: anything not LocalTC's is the sim's own (it should be off while this is on)."""
        others = [t for t in snap.targets if t.object_id not in self.by_object]
        self.native = sum(1 for t in others if t.gs_kt > 1.0 or not t.on_ground)
        self.native_spots = [(t.lat, t.lon) for t in others if t.on_ground]

    def set_on(self, on: bool) -> list[Any]:
        """Switched on or off from the app: off takes every aircraft of LocalTC's away at once."""
        self.on = on
        if on:
            return []
        out = [RemoveAiAircraft(object_id=p.object_id) for p in self.planes.values() if p.object_id is not None]
        self.planes.clear()
        self.by_object.clear()
        self.by_request.clear()
        self.parked_at.clear()
        return out

    # --- every second ------------------------------------------------------------------------------------------

    def tick(self, now: float) -> list[Any]:
        if not self.on or self.own is None:
            return []
        out: list[Any] = []
        out += self._live(now)
        if self.s.parked:
            out += self._parked(now)
        out += self._clear_reserved()
        if self.s.atc_control:
            out += self._atc(now)
        out += self._local(now)
        if now - self._status_t >= STATUS_EVERY_S:
            self._status_t = now
            out.append(self.status(now))
        return out

    def status(self, now: float) -> TrafficControlStatus:
        live = sum(1 for p in self.planes.values() if p.live and p.object_id is not None and p.mode != "parked")
        parked = sum(1 for p in self.planes.values() if p.object_id is not None and p.mode == "parked")
        note = NATIVE_NOTE if self.native > 2 else PARKED_NOTE if len(self.native_spots) >= NATIVE_PARKED \
            else NO_FEED_NOTE if not self.feed_ok else ""
        return TrafficControlStatus(t=self.own.t if self.own else 0.0, mode="on" if self.on else "off", source=self.source,
                                    live=live, parked=parked, native=self.native, fsltl=self.picker.fsltl, note=note,
                                    recent=tuple(reversed(self.recent)))

    # --- the live flights --------------------------------------------------------------------------------------

    def _true_alt(self, pressure_ft: float) -> float:
        """A pressure altitude (what ADS-B sends) as the sim's altitude: corrected by the difference at the user's."""
        o = self.own
        if o is None or not o.altimeter_setting_inhg:
            return pressure_ft
        pressure_own = o.alt_indicated_ft - (o.altimeter_setting_inhg - 29.92) * 1000.0
        return pressure_ft + (o.alt_msl_ft - pressure_own)

    def _dist_nm(self, lat: float, lon: float) -> float:
        return haversine_nm(self.own.lat, self.own.lon, lat, lon)

    def _live(self, now: float) -> list[Any]:
        out: list[Any] = []
        own = self.own
        if self._first_t is None and self.flights:
            self._first_t = now
        wanted: list[tuple[float, Flight]] = []
        for f in self.flights.values():
            d = self._dist_nm(f.lat, f.lon)
            if d <= self.s.radius_nm:
                wanted.append((d, f))
        wanted.sort(key=lambda x: x[0])
        keep = {f"live:{f.hex}" for _, f in wanted[: self.s.max_live]}
        # Gone: out of the area, lost, or one too many.
        for key, plane in list(self.planes.items()):
            f = self.flights.get(key[5:]) if plane.live else None
            if plane.mode == "landing" and f is not None and f.t > plane.used_t + 1:
                plane.mode, plane.local, plane.flight = "live", None, f  # heard again: the real one from here
                out += self._track(plane, now, blend_s=8.0)
                continue
            if not plane.live or plane.mode in ("go_around", "departing", "hold", "landing", "give_way"):
                continue
            if f is not None and plane.mode == "live" and not f.on_ground and now - f.t > QUIET_S \
                    and plane.object_id is not None and (landing := self._landing(plane, now)):
                out += landing
                continue
            if plane.mode == "parked":  # a real one that's switched off at its gate: there until the user's gone
                if f is None or self._dist_nm(f.lat, f.lon) > PARKED_LEAVE_NM:
                    out += self._remove(plane)
                continue
            age = now - f.t if f is not None else 1e9
            far = f is not None and self._dist_nm(f.lat, f.lon) > self.s.radius_nm * LEAVE
            if f is not None and age > STALE_S and f.on_ground and f.gs_kt < 3:
                plane.mode = "parked"  # its transponder off at the gate: parked there
                out += self._track(plane, now)
                continue
            if far or age > LOST_S or (key not in keep and age > STALE_S):
                out += self._remove(plane)
        # New ones.
        made = 0
        for d, f in wanted[: self.s.max_live]:
            key = f"live:{f.hex}"
            plane = self.planes.get(key)
            if plane is not None:
                if plane.mode == "live" and f.t > plane.used_t and plane.object_id is not None:
                    plane.flight = f
                    if plane.spot is None and (spot := self._stand_ahead(f)) is not None:
                        out += self._onto_stand(plane, spot, now)
                    else:
                        out += self._track(plane, now)
                continue
            if made >= LIVE_PER_TICK or now - f.t > STALE_S:
                continue
            if not f.on_ground and now - self._first_t > FIRST_LOT_S and d < POP_IN_NM:
                continue  # would appear out of nowhere in front of the user: it comes in from further out instead
            if f.on_ground and d * M_PER_NM < CLEAR_OF_USER_M and own.on_ground:
                continue  # where the user is (the sim's gate and the real one the same)
            spot = self._spot_for(f) if f.on_ground and f.gs_kt < 3 else None
            if spot is not None and self._reserved_at(*self._spot_latlon(spot)):
                continue  # the user's gate: not loaded there
            made += 1
            out += self._create_live(f, spot, now)
        return out

    def _create_live(self, f: Flight, spot: tuple[str, int] | None, now: float) -> list[Any]:
        kind = f.type or self.types(f.hex)
        model = self.picker.pick(kind, f.airline, f.category)
        if model is None:
            return []
        out: list[Any] = []
        if spot is not None:
            for other in [p for p in self.planes.values() if p.spot == spot]:
                out += self._remove(other)  # the one parked there gives way to the real one
        key = f"live:{f.hex}"
        plane = Plane(key, self._request(), model[0], model[1], callsign=f.callsign or f.registration, flight=f,
                      spot=spot, created_t=now)
        self._add(plane)
        lat, lon, alt, hdg = self._where(plane, now)
        gs = 0.0 if spot is not None and f.gs_kt < 3 else f.gs_kt
        # The bridge holds it here from the moment the sim makes it, carried on at its speed (sim_bridge, spawned).
        plane.sent = (lat, lon, alt, hdg, gs, 0.0, 0.0, now)
        out.append(SpawnAiAircraft(request_id=plane.request_id, kind="parked", title=plane.title, livery=plane.livery,
                                   tail=(f.registration or f.callsign)[:9], lat=lat, lon=lon, alt_ft=alt, heading=hdg,
                                   on_ground=f.on_ground, airspeed_kt=gs))
        return out

    def _where(self, plane: Plane, now: float) -> tuple[float, float, float, float]:
        """Where a live one is now: on its spot, or carried on from its last position to now."""
        if plane.spot is not None and (plane.flight is None or plane.flight.gs_kt < 3):
            lat, lon = self._spot_latlon(plane.spot)
            return lat, lon, self._elev(lat, lon), self._spot_heading(plane.spot)
        f = plane.flight
        dt = min(max(0.0, now - f.t), EXTRAPOLATE_GROUND_S if f.on_ground else EXTRAPOLATE_S)
        u = unit(f.track)
        d = f.gs_kt * dt / 3600.0 * M_PER_NM
        lat = f.lat + u[1] * d / 111_320.0
        lon = f.lon + u[0] * d / (111_320.0 * math.cos(math.radians(f.lat)))
        alt = self._elev(lat, lon) if f.on_ground or f.alt_ft is None else self._true_alt(f.alt_ft + f.vs_fpm * dt / 60)
        return lat, lon, alt, f.track

    def _blend(self, plane: Plane, lat: float, lon: float, alt: float, gs_kt: float, now: float) -> float:
        """How long the difference between where the bridge is showing it and where it is now takes to ease out:
        long enough that it never looks more than ``CATCH_UP`` faster or slower than it's going."""
        s = plane.sent
        if s is None:
            return 0.0
        dt = min(max(0.0, now - s[7]), EXTRAPOLATE_S)
        plat, plon, _ = advance(s[0], s[1], s[3], s[4], s[6], dt)
        palt = s[2] + s[5] * dt / 60.0
        err = math.hypot(haversine_nm(lat, lon, plat, plon) * M_PER_NM, (alt - palt) * 0.3048 * 3)
        speed = max(gs_kt, s[4], 15.0) * M_PER_NM / 3600.0
        return max(BLEND_MIN_S, min(BLEND_MAX_S, 1.5 * err / (CATCH_UP * speed)))

    def _send(self, plane: Plane, track: AiTrack, now: float) -> AiTrack:
        plane.sent = (track.lat, track.lon, track.alt_ft, track.hdg, track.gs_kt, track.vs_fpm, track.turn_dps, now)
        return track

    def _track(self, plane: Plane, now: float, blend_s: float | None = None) -> list[Any]:
        """Where one is going now, to the bridge (and its lights when they change). ``blend_s``: how long the
        difference from where it's shown takes to ease out (by default: as long as it takes to look natural)."""
        if plane.object_id is None:
            return []
        f = plane.flight
        standing = plane.mode == "parked" or (plane.mode == "live" and plane.spot is not None
                                              and (f is None or f.gs_kt < 3))
        if standing:
            if plane.spot is not None:
                lat, lon = self._spot_latlon(plane.spot)
                hdg = self._spot_heading(plane.spot)
            elif f is not None:
                lat, lon, hdg = f.lat, f.lon, f.track
            else:
                return []
            alt = self._elev(lat, lon)
            blend = blend_s if blend_s is not None else self._blend(plane, lat, lon, alt, 5.0, now)
            out: list[Any] = [self._send(plane, AiTrack(object_id=plane.object_id, lat=lat, lon=lon, alt_ft=alt, hdg=hdg,
                                                        on_ground=True, blend_s=blend), now)]
            return out + self._lights(plane, on_ground=True, gs=0.0, agl=0.0, parked=plane.mode == "parked")
        if f is None:
            return []
        lat, lon, alt, hdg = self._where(plane, now)
        turn = 0.0
        if plane.last_track is not None and f.t > plane.last_track[1]:
            turn = max(-3.5, min(3.5, ((f.track - plane.last_track[0] + 180) % 360 - 180) / (f.t - plane.last_track[1])))
        plane.last_track = (f.track, f.t)
        plane.turn_dps = turn if not f.on_ground else 0.0
        plane.used_t = f.t
        gs, airborne = f.gs_kt, not f.on_ground
        pitch = bank = 0.0
        if airborne:
            climb = math.degrees(math.atan2(f.vs_fpm / 196.85, max(gs, 60) * 0.5144))
            pitch = max(-4.0, min(16.0, climb + (3.0 if f.vs_fpm > -300 else 1.5)))
            bank = max(-30.0, min(30.0, math.degrees(math.atan(gs * 0.5144 * math.radians(plane.turn_dps) / 9.81))))
        blend = blend_s if blend_s is not None else self._blend(plane, lat, lon, alt, gs, now)
        out = [self._send(plane, AiTrack(object_id=plane.object_id, lat=lat, lon=lon, alt_ft=alt, hdg=hdg, gs_kt=gs,
                                         vs_fpm=f.vs_fpm if airborne else 0.0, turn_dps=plane.turn_dps,
                                         on_ground=not airborne, pitch=pitch, bank=bank, blend_s=blend), now)]
        agl = alt - self._elev(lat, lon) if airborne else 0.0
        return out + self._lights(plane, on_ground=not airborne, gs=gs, agl=agl, lat=lat, lon=lon,
                                  climbing=f.vs_fpm > 300)

    def _lights(self, plane: Plane, *, on_ground: bool, gs: float, agl: float, parked: bool = False,
                lat: float = 0.0, lon: float = 0.0, climbing: bool = False) -> list[Any]:
        """Gear and lights as a crew has them: parked dark, taxiing with the taxi light, the landing lights and
        strobes on the runway and below 10,000 ft, the gear down on the ground and low on the way in."""
        if parked:
            state = (True, False, False, False, False, self._night(), False)
        else:
            on_runway = on_ground and self._on_a_runway(lat, lon)
            gear = on_ground or agl < (1200 if climbing else 2500)
            low = agl < 10_000
            state = (gear, (on_runway and gs > 30) or (not on_ground and low), on_ground and gs > 3, True,
                     on_runway or not on_ground, True, self._night())
        if state == plane.lights or plane.object_id is None:
            return []
        plane.lights = state
        gear, landing, taxi, beacon, strobe, nav, logo = state
        return [AiLights(object_id=plane.object_id, gear_down=gear, landing=landing, taxi=taxi, beacon=beacon,
                         strobe=strobe, nav=nav, logo=logo)]

    # --- airports ----------------------------------------------------------------------------------------------

    def _geo(self, icao: str) -> AirportGeometry | None:
        if icao not in self._geometry and icao in self.airports:
            self._geometry[icao] = AirportGeometry(self.airports[icao])
        return self._geometry.get(icao)

    def _near_airport(self, lat: float, lon: float, within_nm: float = 5.0) -> str | None:
        best = min(((haversine_nm(lat, lon, a.lat, a.lon), icao) for icao, a in self.airports.items()), default=None)
        return best[1] if best is not None and best[0] <= within_nm else None

    def _elev(self, lat: float, lon: float) -> float:
        icao = self._near_airport(lat, lon, 25.0)
        return self.airports[icao].elev_ft if icao else 0.0

    def _on_a_runway(self, lat: float, lon: float) -> bool:
        icao = self._near_airport(lat, lon)
        geo = self._geo(icao) if icao else None
        return geo is not None and geo.runway_at(lat, lon, 10.0) is not None

    def _spot_latlon(self, spot: tuple[str, int]) -> tuple[float, float]:
        p = self.airports[spot[0]].parking[spot[1]]
        return p.lat, p.lon

    def _spot_heading(self, spot: tuple[str, int]) -> float:
        return self.airports[spot[0]].parking[spot[1]].heading_true

    def _stand_ahead(self, f: Flight) -> tuple[str, int] | None:
        """The free stand one taxiing in is about to turn onto: close, ahead of it (or right by it), its size."""
        if not f.on_ground or f.gs_kt > STAND_SLOW_KT:
            return None
        icao = self._near_airport(f.lat, f.lon)
        if icao is None:
            return None
        taken = {p.spot for p in self.planes.values() if p.spot is not None}
        best = None
        for i, p in enumerate(self.airports[icao].parking):
            if not p.kind.startswith(("gate", "ramp")) or (icao, i) in taken:
                continue
            d = haversine_nm(f.lat, f.lon, p.lat, p.lon)
            if d > STAND_NM:
                continue
            u = unit(f.track)
            dx = (p.lon - f.lon) * 111_320.0 * math.cos(math.radians(f.lat))
            dy = (p.lat - f.lat) * 111_320.0
            ahead = dx * u[0] + dy * u[1]
            if ahead < 0 and d * M_PER_NM > 40:
                continue  # behind it: one it's taxiing past
            if self._reserved_at(p.lat, p.lon):
                continue
            if best is None or d < best[0]:
                best = (d, (icao, i))
        return best[1] if best is not None else None

    def _onto_stand(self, plane: Plane, spot: tuple[str, int], now: float) -> list[Any]:
        """Turned onto its stand and stopped on the marker, facing the terminal as the stand has it: the real one
        about to switch its transponder off (and the receivers losing it among the buildings) was carried on, through
        the terminal."""
        out: list[Any] = []
        for other in [p for p in self.planes.values() if p.spot == spot and p is not plane]:
            out += self._remove(other)  # one parked there gives way to the real one coming in
        plane.spot, plane.mode = spot, "parked"
        lat, lon = self._spot_latlon(spot)
        f = plane.flight
        metres = haversine_nm(f.lat, f.lon, lat, lon) * M_PER_NM if f is not None else 0.0
        self._did(f"{plane.callsign or 'An arrival'} on its stand")
        blend = max(BLEND_MIN_S, metres / TAXI_IN_MPS)
        out.append(self._send(plane, AiTrack(object_id=plane.object_id, lat=lat, lon=lon, alt_ft=self._elev(lat, lon),
                                             hdg=self._spot_heading(spot), on_ground=True, blend_s=blend), now))
        return out + self._lights(plane, on_ground=True, gs=0.0, agl=0.0, parked=True)

    def _spot_for(self, f: Flight) -> tuple[str, int] | None:
        """The parking spot a live aircraft standing still is on (the sim's spot within a few metres of the real)."""
        icao = self._near_airport(f.lat, f.lon)
        if icao is None:
            return None
        geo = self._geo(icao)
        xy = geo.xy(f.lat, f.lon)
        best = min(((math.dist(xy, geo.xy(p.lat, p.lon)), i) for i, p in enumerate(geo.airport.parking)), default=None)
        if best is None or best[0] > SNAP_M:
            return None
        taken = {p.spot for p in self.planes.values() if p.live and p.spot is not None}
        return None if (icao, best[1]) in taken else (icao, best[1])

    def _reserved_at(self, lat: float, lon: float) -> bool:
        return any(haversine_nm(lat, lon, a, b) * M_PER_NM < RESERVED_M for a, b in self.atc.reserved())

    def _clear_reserved(self) -> list[Any]:
        """The gate ATC gave the user (and where the user stands) kept clear: anything of LocalTC's there goes."""
        out: list[Any] = []
        reserved = self.atc.reserved()
        if not reserved:
            return out
        for plane in list(self.planes.values()):
            if plane.spot is None or plane.object_id is None:
                continue
            lat, lon = self._spot_latlon(plane.spot)
            if any(haversine_nm(lat, lon, a, b) * M_PER_NM < RESERVED_M for a, b in reserved):
                self._did(f"{plane.callsign or 'An aircraft'} moved off the gate you were given")
                out += self._remove(plane)
        return out

    # --- parked aircraft ---------------------------------------------------------------------------------------

    def _night(self) -> bool:
        hour = self._local_hour(self.own.lon) if self.own is not None else 12.0
        return hour < 6.5 or hour > 19.5

    def _local_hour(self, lon: float) -> float:
        zulu = self.own.zulu_s if self.own is not None and self.own.zulu_s is not None else 12 * 3600.0
        return (zulu / 3600.0 + lon / 15.0) % 24

    def occupancy(self, airport: Airport, live_here: int) -> float:
        """The share of an airport's gates with an aircraft at them now: by the hour, and how big and busy it is
        (a hub keeps more aircraft on the ground than a field few airlines fly to; the live flights there now show
        how busy it is today)."""
        hour = self._local_hour(airport.lon)
        base = BY_HOUR[int(hour) % 24]
        gates = sum(1 for p in airport.parking if p.kind.startswith("gate"))
        size = 1.0 if gates >= 60 else 0.85 if gates >= 20 else 0.6 if gates else 0.4
        busy = min(1.1, 0.85 + 0.25 * (live_here / max(gates, 1)) * 4) if gates else 1.0
        return max(0.1, min(0.95, base * size * busy))

    def _parked_airports(self) -> list[str]:
        own = self.own
        out = [icao for icao, a in self.airports.items() if haversine_nm(own.lat, own.lon, a.lat, a.lon) <= PARKED_AROUND_NM]
        dest = (self.atc.destination() or "").upper()
        if dest and dest in self.airports and dest not in out:
            a = self.airports[dest]
            if haversine_nm(own.lat, own.lon, a.lat, a.lon) <= PARKED_INBOUND_NM:
                out.append(dest)
        return [icao for icao in out if any(p.kind.startswith(("gate", "ramp")) for p in self.airports[icao].parking)]

    def _parked(self, now: float) -> list[Any]:
        out: list[Any] = []
        here = self._parked_airports()
        # An airport left behind: its parked aircraft go.
        for icao in list(self.parked_at):
            a = self.airports.get(icao)
            if a is None or (icao not in here and haversine_nm(self.own.lat, self.own.lon, a.lat, a.lon) > PARKED_LEAVE_NM):
                self.parked_at.discard(icao)
                for plane in [p for p in self.planes.values() if not p.live and p.spot and p.spot[0] == icao]:
                    out += self._remove(plane)
        made = 0
        for icao in here:
            airport = self.airports[icao]
            mine = [p for p in self.planes.values() if not p.live and p.spot and p.spot[0] == icao]
            if icao not in self.parked_at:
                self.parked_at.add(icao)
                self._plan_parked(icao, now)
            for plane in mine:
                if plane.object_id is None and plane.request_id not in self.by_request and made < PARKED_PER_TICK:
                    made += 1
                    out += self._create_parked(plane, airport)
        return out

    def _plan_parked(self, icao: str, now: float) -> None:
        """Which spots get an aircraft (and which), worked out once per visit: the same every time for the same hour."""
        airport = self.airports[icao]
        live_here = sum(1 for f in self.flights.values() if haversine_nm(f.lat, f.lon, airport.lat, airport.lon) < 8)
        share = self.occupancy(airport, live_here)
        hour = int(self._local_hour(airport.lon))
        rng = random.Random(zlib.crc32(f"{icao}:{hour}:{self.seed}".encode()))
        airlines = self._airlines_at(icao)
        taken = [self._spot_latlon(p.spot) for p in self.planes.values() if p.spot is not None and p.spot[0] == icao]
        spots = []
        for i, p in enumerate(airport.parking):
            if not p.kind.startswith(("gate", "ramp")):
                continue
            if any(haversine_nm(p.lat, p.lon, a, b) * M_PER_NM < max(p.radius_m, 15) for a, b in taken + self.native_spots):
                continue  # a real one is there, or the sim's own
            if self.own is not None and haversine_nm(p.lat, p.lon, self.own.lat, self.own.lon) * M_PER_NM < CLEAR_OF_USER_M:
                continue
            if self._reserved_at(p.lat, p.lon):
                continue
            spots.append(i)
        rng.shuffle(spots)
        count = min(int(round(len(spots) * share)), self.s.max_parked - sum(1 for p in self.planes.values() if not p.live))
        # Nearest the user first (what's seen from the gate), and drawn with a few models over and over.
        if self.own is not None:
            spots.sort(key=lambda i: haversine_nm(self.own.lat, self.own.lon, airport.parking[i].lat, airport.parking[i].lon))
        used: dict[tuple[str, str], list[tuple[str, str]]] = {}  # (size, airline) -> the models drawn with
        for i in spots[:max(0, count)]:
            spot = airport.parking[i]
            model = self._parked_model(spot.kind, spot.radius_m, airlines, rng, used)
            if model is None:
                continue
            plane = Plane(f"parked:{icao}:{i}", 0, model[0], model[1], spot=(icao, i), mode="parked", created_t=now)
            self.planes[plane.key] = plane
        what = "night" if self._night() else "day"
        self._did(f"{sum(1 for p in self.planes.values() if not p.live and p.spot and p.spot[0] == icao)} aircraft "
                  f"parked at {icao}'s gates ({round(share * 100)}% full this {what})")

    def _airlines_at(self, icao: str) -> Counter:
        """The airlines whose aircraft stand at ``icao``'s gates (airlines.py): the live flights to and from it (their
        routes), the ones close by, its own based airlines, else its country's."""
        airport = self.airports[icao]
        flown: Counter = Counter()
        near: Counter = Counter()
        for f in self.flights.values():
            if not f.airline:
                continue
            route = self.routes(f.callsign)
            if route is not None and icao in (route.origin, route.destination):
                flown[f.airline] += 1
            elif route is None and haversine_nm(f.lat, f.lon, airport.lat, airport.lon) < 15:
                near[f.airline] += 1
        return airline_data.weights(icao, flown, near)

    def _parked_model(self, kind: str, radius_m: float, airlines: Counter, rng: random.Random,
                      used: dict[tuple[str, str], list[tuple[str, str]]] | None = None) -> tuple[str, str] | None:
        """A model for a parking spot of that kind and size: an airline's that flies here (by its share), one of the
        models already drawn there when the airport has its few (``PARKED_MODELS``)."""
        used = {} if used is None else used
        for prefix, types in (("ramp_ga", GA_TYPES), ("ramp_cargo", CARGO_TYPES)):
            if kind.startswith(prefix):
                return self._reuse(used, (prefix, ""), rng, lambda: next(
                    (m for t in rng.sample(types, k=len(types)) if (m := self.picker.pick(t)) is not None), None))
        size = "heavy" if "heavy" in kind or radius_m >= 30 else "small" if "small" in kind or radius_m < 18 else "medium"
        names = list(airlines)
        weights = [airlines[n] for n in names]
        airline = rng.choices(names, weights)[0] if names else ""

        def pick() -> tuple[str, str] | None:
            for name in [airline] + [n for n in rng.choices(names, weights, k=3) if n != airline] if names else [""]:
                for kind_code in rng.sample(TYPES_BY_SIZE[size], k=len(TYPES_BY_SIZE[size])):
                    model = self.picker.pick(kind_code, name)
                    if model is not None and (not name or "ZZZ" not in model[0].upper()):
                        return model
            return self.picker.pick(TYPES_BY_SIZE[size][0])

        return self._reuse(used, (size, airline), rng, pick)

    @staticmethod
    def _reuse(used: dict[tuple[str, str], list[tuple[str, str]]], key: tuple[str, str], rng: random.Random,
               pick: Callable[[], tuple[str, str] | None]) -> tuple[str, str] | None:
        """A new model while the airport has few, else one already there (of that airline and size if it has one)."""
        models = {m for ms in used.values() for m in ms}
        if key in used and (len(models) >= PARKED_MODELS or len(used[key]) >= 2):
            return rng.choice(used[key])
        if len(models) >= PARKED_MODELS:
            same_size = [m for k, ms in used.items() if k[0] == key[0] for m in ms]
            if same_size:
                return rng.choice(same_size)
        model = pick()
        if model is not None:
            used.setdefault(key, []).append(model)
        return model

    def _create_parked(self, plane: Plane, airport: Airport) -> list[Any]:
        plane.request_id = self._request()
        self.by_request[plane.request_id] = plane
        p = airport.parking[plane.spot[1]]
        return [SpawnAiAircraft(request_id=plane.request_id, kind="parked", title=plane.title, livery=plane.livery,
                                lat=p.lat, lon=p.lon, alt_ft=airport.elev_ft, heading=p.heading_true, on_ground=True)]

    # --- ATC ---------------------------------------------------------------------------------------------------

    def _atc(self, now: float) -> list[Any]:
        """The user has the runway: real flights about to use it are sent around or held short."""
        out: list[Any] = []
        user = self.atc.user_runway()
        if user is not None:
            self._user_runway, self._user_runway_t = user, now
        out += self._give_way(now)
        held = sorted((p for p in self.planes.values() if p.mode == "hold"), key=lambda p: p.local.started if p.local else 0)
        if user is None:
            # One at a time: the first held goes once the user's been off the runway a while, the next spaced after.
            if held and now - self._user_runway_t >= HELD_RELEASE_S and now - self._released_t >= DEPARTURE_GAP_S:
                self._released_t = now
                out += self._release(held[0], now)
            return out
        icao, end_ident, kind = user
        geo = self._geo(icao)
        end = geo.end(end_ident) if geo is not None else None
        if end is None:
            return out
        runway = end.runway
        for plane in list(self.planes.values()):
            if plane.mode not in ("live", "landing") or plane.flight is None or plane.object_id is None \
                    or not plane.callsign:
                continue
            loc = plane.local
            lat, lon, alt, hdg = (loc.lat, loc.lon, loc.alt_ft, loc.hdg) if plane.mode == "landing" and loc is not None \
                else self._where(plane, now)
            f = plane.flight
            if plane.mode == "landing" and loc is not None and loc.on_ground:
                continue
            if not f.on_ground:
                final = geo.final_approach(lat, lon, hdg, max_distance_nm=SHORT_FINAL_NM)
                if final is not None and final.end.runway is runway and alt - geo.airport.elev_ft < SHORT_FINAL_AGL \
                        and kind in ("takeoff", "on"):
                    out += self._go_around(plane, geo, final.end, now)
            elif f.gs_kt < 40:
                # Short of it and heading onto it (to line up or to cross): held there. One already on it goes on
                # (held there it would block the user's own clearance).
                xy = geo.xy(lat, lon)
                if 0 < runway.distance_to(xy) <= RUNWAY_NEAR_M and self._heading_onto(runway, xy, hdg, f.gs_kt):
                    out += self._hold(plane, geo, end, now)
        return out

    @staticmethod
    def _heading_onto(runway, xy: tuple[float, float], hdg: float, gs: float) -> bool:
        """Moving towards the runway (or stopped right by it): about to go onto it."""
        if gs < 2:
            return runway.distance_to(xy) < 60
        u = unit(hdg)
        ahead = (xy[0] + u[0] * 40, xy[1] + u[1] * 40)
        return runway.distance_to(ahead) < runway.distance_to(xy) or runway.contains(ahead, 5)

    def _say(self, callsign: str, kind: str, icao: str, runway: str, now: float) -> list[Any]:
        """Tower telling a real flight: each held short only if it hasn't just told another (two or three held at
        once was a string of calls); the departures go spaced anyway, and a go-around is always said."""
        if kind == "hold_short":
            if now - self._call_t < CALL_GAP_S:
                return []
            self._call_t = now
        return self.atc.say(callsign, kind, icao, runway)

    def _hold(self, plane: Plane, geo: AirportGeometry, end, now: float) -> list[Any]:
        lat, lon, alt, hdg = self._where(plane, now)
        plane.mode = "hold"
        plane.runway = (geo.icao, end.ident)
        plane.local = Local(lat, lon, alt, hdg, 0.0, 0.0, True, started=now)
        self._did(f"{plane.callsign} held short of {end.ident} for you")
        # Stopping where it is (taxiing at a few knots: a few metres on, not back to where it was).
        out = [self._send(plane, AiTrack(object_id=plane.object_id, lat=lat, lon=lon, alt_ft=alt, hdg=hdg, on_ground=True,
                                         blend_s=4.0), now)]
        return out + self._say(plane.callsign, "hold_short", geo.icao, end.ident, now)

    def _give_way(self, now: float) -> list[Any]:
        """On the ground, a real flight taxiing at the user stops and waits (the real one doesn't know the user is
        there); it goes on once the user is clear, catching up with the real one slowly."""
        out: list[Any] = []
        own = self.own
        for plane in list(self.planes.values()):
            if plane.object_id is None or plane.flight is None:
                continue
            if plane.mode == "give_way":
                lat, lon = plane.local.lat, plane.local.lon
                away = haversine_nm(lat, lon, own.lat, own.lon) * M_PER_NM
                behind = self._closing(lat, lon, plane.local.hdg, own) is None
                if away > GIVE_WAY_CLEAR_M or (behind and away > GIVE_WAY_M) or now - plane.give_way_t > GIVE_WAY_MAX_S \
                        or not own.on_ground:
                    plane.mode, plane.local = "live", None
                    out += self._track(plane, now)
                continue
            f = plane.flight
            if plane.mode != "live" or not f.on_ground or f.gs_kt < 3 or not own.on_ground:
                continue
            lat, lon, alt, hdg = self._where(plane, now)
            closest = self._closing(lat, lon, hdg, own, f.gs_kt)
            if closest is not None and closest < GIVE_WAY_M:
                plane.mode, plane.give_way_t = "give_way", now
                plane.local = Local(lat, lon, alt, hdg, 0.0, 0.0, True, started=now)
                self._did(f"{plane.callsign or 'An aircraft'} gave way to you")
                out.append(self._send(plane, AiTrack(object_id=plane.object_id, lat=lat, lon=lon, alt_ft=alt, hdg=hdg,
                                                     on_ground=True, blend_s=5.0), now))
        return out

    @staticmethod
    def _closing(lat: float, lon: float, hdg: float, own: OwnshipState, gs_kt: float = 15.0) -> float | None:
        """How close one at ``lat, lon`` heading ``hdg`` comes to the user over the next ``GIVE_WAY_AHEAD_S`` (both
        moving on as they are), or None when it's moving away."""
        best, moving_closer = math.inf, False
        start = haversine_nm(lat, lon, own.lat, own.lon) * M_PER_NM
        for k in range(1, int(GIVE_WAY_AHEAD_S) + 1, 2):
            a = advance(lat, lon, hdg, gs_kt, 0.0, k)
            b = advance(own.lat, own.lon, own.hdg_true, own.gs_kt, 0.0, k)
            d = haversine_nm(a[0], a[1], b[0], b[1]) * M_PER_NM
            moving_closer = moving_closer or d < start - 5
            best = min(best, d)
        return best if moving_closer else None

    def _release(self, plane: Plane, now: float) -> list[Any]:
        """The user's done with the runway: the held one lines up on it and takes off (LocalTC flying it: the real
        one's long gone)."""
        user = self._user_runway
        icao = plane.runway[0] if plane.runway else user[0] if user else ""
        geo = self._geo(icao) if icao else None
        local = plane.local
        if geo is None or local is None:
            return self._remove(plane)
        xy = geo.xy(local.lat, local.lon)
        # The way it was held for (the way the user went: the runway in use), from where it waited.
        end = geo.end(plane.runway[1]) if plane.runway else None
        runway = end.runway if end is not None else min(geo.runways, key=lambda r: r.distance_to(xy))
        if end is None:
            end = min(runway.ends, key=lambda e: math.dist(e.threshold, xy))
        along, _ = runway.along_across(xy)
        u = unit(runway.ends[0].heading_true)
        on_line = (runway.center[0] + u[0] * along, runway.center[1] + u[1] * along)
        lat, lon = geo.frame.to_latlon(*on_line)
        plane.mode = "departing"
        plane.local = Local(lat, lon, geo.airport.elev_ft, end.heading_true, 0.0, 0.0, True,
                            target_alt=geo.airport.elev_ft + 5000, target_kt=250.0, accel=2.5, rotate_kt=150.0,
                            started=now)
        self._did(f"{plane.callsign} cleared for takeoff {end.ident} after you")
        out = [self._send(plane, AiTrack(object_id=plane.object_id, lat=lat, lon=lon, alt_ft=geo.airport.elev_ft,
                                         hdg=end.heading_true, on_ground=True, blend_s=12.0), now)]
        return out + self._say(plane.callsign, "takeoff", geo.icao, end.ident, now)

    def _go_around(self, plane: Plane, geo: AirportGeometry, end, now: float) -> list[Any]:
        loc = plane.local
        lat, lon, alt, hdg = (loc.lat, loc.lon, loc.alt_ft, loc.hdg) if plane.mode == "landing" and loc is not None \
            else self._where(plane, now)
        f = plane.flight
        plane.mode = "go_around"
        plane.local = Local(lat, lon, alt, end.heading_true, max(f.gs_kt, 140.0), 1800.0, False,
                            target_alt=geo.airport.elev_ft + 3000, target_kt=200.0, accel=1.0, started=now)
        self._did(f"{plane.callsign} sent around: you had {end.ident}")
        return self._say(plane.callsign, "go_around", geo.icao, end.ident, now)

    def _landing(self, plane: Plane, now: float) -> list[Any]:
        """A real arrival gone quiet on final (low, out of the receivers' sight): LocalTC flies it down the glide path,
        lands it and slows it on the runway, until the real one is heard again."""
        lat, lon, alt, hdg = self._where(plane, now)
        icao = self._near_airport(lat, lon, FINAL_NM + 3)
        geo = self._geo(icao) if icao else None
        final = geo.final_approach(lat, lon, hdg, max_distance_nm=FINAL_NM) if geo is not None else None
        if final is None:
            return []
        plane.mode = "landing"
        plane.local = Local(lat, lon, alt, final.end.heading_true, plane.flight.gs_kt, 0.0, False, target_kt=140.0,
                            started=now, runway=(geo.icao, final.end.ident))
        self._did(f"{plane.callsign or 'An arrival'} out of the receivers' sight on final {final.end.ident}: "
                  "LocalTC lands it")
        return []

    def _fly_landing(self, plane: Plane, loc: Local) -> None:
        """A second down the glide path to the threshold, the flare, the rollout to taxi speed."""
        icao, ident = loc.runway
        geo = self._geo(icao)
        end = geo.end(ident) if geo is not None else None
        if end is None:
            return
        xy, u = geo.xy(loc.lat, loc.lon), unit(end.heading_true)
        dx, dy = xy[0] - end.threshold[0], xy[1] - end.threshold[1]
        before = -(dx * u[0] + dy * u[1])
        right = dx * u[1] - dy * u[0]  # metres right of the centreline: steered back onto it
        loc.hdg = (end.heading_true - max(-15.0, min(15.0, right / 40.0))) % 360
        elev = geo.airport.elev_ft
        if not loc.on_ground:
            path = elev + 50 + max(0.0, before) / 0.3048 * GLIDE  # 50 ft over the threshold, 3 degrees
            loc.vs_fpm = max(-1500.0, min(0.0, (path - loc.alt_ft) * 6 - loc.gs_kt * KT_TO_FPM * GLIDE))
            loc.gs_kt = max(loc.target_kt, loc.gs_kt - 1.0)
            if before < -250 or loc.alt_ft <= elev + 5:
                loc.on_ground, loc.vs_fpm, loc.alt_ft = True, 0.0, elev
        else:
            loc.gs_kt = max(20.0, loc.gs_kt - 4.0)  # braking to taxi speed, then rolling to the end

    def _local(self, now: float) -> list[Any]:
        """The flights LocalTC is flying itself, a second on (go-arounds, released departures, quiet arrivals)."""
        out: list[Any] = []
        for plane in list(self.planes.values()):
            loc = plane.local
            if plane.mode not in ("go_around", "departing", "landing") or loc is None or plane.object_id is None:
                continue
            if plane.mode == "landing":
                if now - loc.started > LOCAL_TTL_S / 2:
                    out += self._remove(plane)  # never heard again (parked out of sight): gone
                    continue
                self._fly_landing(plane, loc)
            if now - loc.started > LOCAL_TTL_S or self._dist_nm(loc.lat, loc.lon) > min(15.0, self.s.radius_nm):
                out += self._remove(plane)
                continue
            dt = 1.0
            if plane.mode == "landing":
                if not loc.on_ground:
                    loc.alt_ft += loc.vs_fpm * dt / 60
            else:  # climbing away: a go-around, or a departure off the runway
                loc.gs_kt = min(loc.target_kt, loc.gs_kt + loc.accel * dt)
                if loc.on_ground and loc.rotate_kt and loc.gs_kt >= loc.rotate_kt:
                    loc.on_ground, loc.vs_fpm = False, 2000.0
                if not loc.on_ground:
                    if loc.alt_ft >= loc.target_alt:
                        loc.vs_fpm = 0.0
                    loc.alt_ft = min(loc.target_alt, loc.alt_ft + loc.vs_fpm * dt / 60)
            u = unit(loc.hdg)
            d = loc.gs_kt * dt / 3600.0 * M_PER_NM
            loc.lat += u[1] * d / 111_320.0
            loc.lon += u[0] * d / (111_320.0 * math.cos(math.radians(loc.lat)))
            pitch = 0.0 if loc.on_ground else (12.0 if loc.vs_fpm > 0 else 2.5)
            if plane.mode == "landing":
                plane.used_t = plane.flight.t if plane.flight is not None else plane.used_t
            out.append(self._send(plane, AiTrack(object_id=plane.object_id, lat=loc.lat, lon=loc.lon, alt_ft=loc.alt_ft,
                                                 hdg=loc.hdg, gs_kt=loc.gs_kt, vs_fpm=loc.vs_fpm, on_ground=loc.on_ground,
                                                 pitch=pitch, blend_s=1.5), now))
            agl = 0.0 if loc.on_ground else max(0.0, loc.alt_ft - self._elev(loc.lat, loc.lon))
            out += self._lights(plane, on_ground=loc.on_ground, gs=loc.gs_kt, agl=agl, lat=loc.lat, lon=loc.lon,
                                climbing=loc.vs_fpm > 300)
        return out

    # --- bookkeeping -------------------------------------------------------------------------------------------

    def _request(self) -> int:
        self._next_request += 1
        return self._next_request

    def _add(self, plane: Plane) -> None:
        self.planes[plane.key] = plane
        self.by_request[plane.request_id] = plane

    def _remove(self, plane: Plane) -> list[Any]:
        self.planes.pop(plane.key, None)
        self.by_request.pop(plane.request_id, None)
        if plane.object_id is not None:
            self.by_object.pop(plane.object_id, None)
            return [RemoveAiAircraft(object_id=plane.object_id)]
        return []

    def _did(self, text: str) -> None:
        log.info("Traffic: %s", text)
        self.recent.append(text)


__all__ = ["AtcLink", "TrafficManager", "TrafficSettings"]
