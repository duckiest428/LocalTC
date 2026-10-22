"""LocalTC's traffic: the real flights around, flown in the sim with FSLTL's models, the ramps filled as busy as the
airport is at that hour, and the traffic answering to ATC.

Pure, like the ATC: it's told what happens (the user's aircraft, the live positions, the sim's answers and its
traffic, the airports) and says what to send (``SpawnAiAircraft``, ``AiTrack`` ...) and what to tell the app. The
service (service.py) does the waiting and the network.

- **Live flights** (adsb.lol and the like, feed.py): each one within ``radius_nm`` is created as a non-ATC aircraft
  with its own model (models.py) and callsign, held against the sim's physics and moved along its real track by the
  bridge (sim_bridge/motion.py), shown ``LIVE_DELAY_S`` behind the real one (between two positions known). One
  standing at a gate is put exactly on the sim's parking spot; an arrival on its stand stays there a while
  (``PARKED_STAY_S``), or until it's heard moving again for its next flight.
- **Parked aircraft** at the airport the user is on the ground at, on the sim's cargo and GA ramps (the gates have
  the real ones): as many as that airport has at that hour. The gate ATC gave the user (and wherever the user is
  parked) is kept clear: anything there is taken away.
- **ATC**: the controllers talk to the real flights as they do things (``_calls``: what each does next is known
  ``LIVE_DELAY_S`` ahead): delivery's clearance and ground's push at the gate, the taxi out, tower's line-up,
  takeoff and landing, the taxi in. Real flights don't know about the user. So when the user has the runway (cleared
  to land, lined up, taking off, rolling out), a real arrival on short final for it is sent around, a real departure
  about to go onto it is held short until the user's done, then lines up and goes (one at a time, spaced as a tower
  spaces them), and one already on it is taken away. On the ground, one taxiing towards the user gives way and waits
  until the user is clear.

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
from dataclasses import dataclass, field, replace
from typing import Any

from localtc.atc_core.airport.aircraft_size import wingspan_m
from localtc.atc_core.airport.geometry import AirportGeometry
from localtc.atc_core.airport.taxi_route import TaxiGraph
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
from localtc.sim_api.geo import advance, bearing_deg, haversine_nm, unit
from localtc.traffic import airlines as airline_data
from localtc.traffic.feed import Flight
from localtc.traffic.ground import Follower, free_stand, taxi_in_path
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
# Parked aircraft are put at the airport the user is on the ground at (the nearest within this), never while flying:
# at FL390 they were put at the fields below (Cork, Cherbourg), and Paris's 36 came while on final, each a stutter.
# Each live one is shown where it was this long ago: always between two positions known, never guessed ahead (the
# step from the taxiway into the air, and back, as smooth as the rest), and what it does next is known in time for
# the controller to tell it (``_calls``). A new one appears this long after it's first heard.
LIVE_DELAY_S = 30.0
HISTORY_S = 240.0
PARKED_STAY_S = 45 * 60.0  # an arrival on its stand stays this long (turning round), then goes
CALLS_GAP_S = 20.0  # the controllers' calls to the real flights at one airport this far apart at least
FINAL_CALL_NM = 6.0  # an arrival is cleared to land this far out
PARKED_AROUND_NM = 5.0
PARKED_SLOW_KT = 40.0  # ... once off the landing roll
PARKED_LEAVE_NM = 30.0  # and taken away once the user is this far from the airport again
PARKED_PER_TICK = 1  # made one a second: the sim loads each model's textures as it comes (all at once stutters)
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
GIVE_WAY_AGAIN_S = 45.0  # ... and isn't stopped again for this long after it goes on
GIVE_WAY_MAX_S = 180.0  # ... or after this long at most
LOCAL_TTL_S = 300.0  # a go-around or a released departure flown by LocalTC this long at most, then taken away
FEED_FRESH_S = 15.0  # the feed answered, with something new, this recently: it's working
HEARD_AGAIN_M = 1500.0  # one LocalTC is landing, heard again this close to where LocalTC has it: the real one again
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
PATH_MIN_M = 8.0  # on the ground, moved this far between two reports: its track is the way it went
GROUND_SNAP_M = 150.0  # on the ground, a correction bigger than this ...
GROUND_SNAP_NM = 1.0  # ... is made at once this far from the user (out of sight), ...
GROUND_BLEND_MAX_S = 60.0  # ... else eased out over this long
NATIVE_NOTE = ("MSFS's own traffic is on as well: set its air traffic to off (Options > General > Traffic) so only "
               "LocalTC's flies, or two of each will be about.")
NO_FEED_NOTE = "No live positions: no internet, or the free sources are busy. Trying again."
NO_SCHEDULE_NOTE = ("The departures due at the gates need a free OpenSky or AirLabs account (EFB > Data): without one "
                    "only the real aircraft there now are shown.")
PARKED_NOTE = ("MSFS's own parked aircraft are on: set them to off as well (Options > General > Traffic, parked or "
               "static aircraft) so LocalTC fills the gates with the airlines that fly here.")
NATIVE_PARKED = 6  # this many of the sim's own standing about: its parked aircraft are on
# How full an airport's gates are, by the hour (local): overnight nearly all aircraft are home; through the day
# a third or more are out flying.
BY_HOUR = (0.92, 0.93, 0.93, 0.92, 0.9, 0.85, 0.75, 0.62, 0.58, 0.6, 0.62, 0.62, 0.6, 0.58, 0.6, 0.6, 0.58, 0.58,
           0.6, 0.65, 0.72, 0.8, 0.86, 0.9)
GA_TYPES = ("C172", "PA28", "SR22", "C208", "TBM9", "C25C", "DA62")
# A big airport's GA ramp is business jets (no Cessnas at an international airport), a mid-size one's turboprops too.
BIZJET_TYPES = ("C56X", "CL60", "GLF5", "E55P", "C680", "GLEX", "FA7X", "C25A")
TURBOPROP_TYPES = ("C208", "PC12", "BE20", "TBM9", "C56X", "E55P")
CARGO_TYPES = ("B744", "B77L", "MD11", "B763", "A306")
CARGO_AIRLINES = ("FDX", "UPS", "DHK", "CLX", "GTI", "CKS", "ABX", "BOX", "CAO", "QTR", "ATN")
HUB_GATES, REGIONAL_GATES = 40, 10  # an airport with this many gates is a hub; under the other, a small field
# A large "GA" ramp spot at an airline airport is a remote airliner stand (Haneda's scenery calls its 157 remote stands
# RAMP_GA_LARGE): it gets an airliner of its size, not a light aircraft.
REMOTE_STAND_M = 18.0
# Scheduled departures (ops.schedules): each at a gate this long before it's due off it, gone this long after (no
# transponder by then: delayed past it, or cancelled), the controller's clearance this long before.
SCHEDULED_BEFORE_S = 45 * 60.0
SCHEDULED_LATE_S = 60 * 60.0
SCHEDULED_CLEARANCE_S = 20 * 60.0
SCHEDULED_MAX = 30
KEPT_OFF_S = 180.0  # a live flight taken off the user's runway isn't made again for this long (it's moved on by then)
SPOT_LEAVE_M = 20.0  # one on its stand leaves it (pushed back) only when the real one is this far off it and moving
OVERLAP_M = 30.0  # a live one standing this close to another of LocalTC's isn't made (two on one stand)
TAXI_TTL_S = 20 * 60.0  # one LocalTC taxies in itself, this long at most
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
    # Made-up aircraft only on the cargo and GA ramps: the gates have the real ones (at a gate with its transponder
    # on, or an arrival on its stand), not a full terminal of guesses at six in the morning.
    fill_gates: bool = False
    live_delay_s: float = 30.0  # LIVE_DELAY_S


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
    # The controllers telling a real flight what it's about to do (``TrafficManager._calls``): keyword arguments
    # callsign, kind, icao, runway, lat, lon, destination.
    call: Callable[..., list[Any]] = lambda **kw: []
    managed: Callable[[set[int]], None] = lambda ids: None  # the object ids that are LocalTC's, each second


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
    follow: Follower | None = None  # rolled out: off the runway and along the taxiways to a stand


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
    gave_way_t: float = -1e9  # when it went on again (not stopped again straight away)
    parked_t: float = 0.0  # when it stopped on its stand (an arrival there goes after PARKED_STAY_S)
    said: set[str] = field(default_factory=set)  # the calls the controllers have made to it
    airborne_seen: bool = False  # seen in the air: an arrival, once down
    left_stand: bool = False  # pushed back off a stand: a departure, never put on one again

    @property
    def live(self) -> bool:
        return self.key.startswith("live:")


def _light(title: str) -> bool:
    """A light aircraft by its title (a Cessna 152/172/182, a Diamond DA20/40/42/62, a Piper): never on a hub's ramp."""
    t = title.upper()
    return any(w in t for w in ("C152", "C172", "C182", "DA20", "DA40", "DA42", "DA62", "DIAMOND", "PIPER",
                                "PA28", "PA-28", "CUB", "SR22", "CIRRUS"))


class TrafficManager:
    def __init__(self, settings: TrafficSettings | None = None, atc: AtcLink | None = None, *,
                 picker: ModelPicker | None = None, routes: Callable[[str], Any] | None = None,
                 types: Callable[[str], str] | None = None, seed: int = 0) -> None:
        self.s = settings or TrafficSettings()
        self.unscheduled = False  # the departures at the gates asked for, with no schedule to take them from
        self._kept_off: dict[str, float] = {}  # live flights taken off the user's runway: not made again until then
        self.atc = atc or AtcLink()
        self.picker = picker or ModelPicker()
        self.routes = routes or (lambda callsign: None)
        self.types = types or (lambda hex_: "")
        self.seed = seed
        self.on = True
        self.own: OwnshipState | None = None
        self.flights: dict[str, Flight] = {}  # by hex: the newest position of each
        self.history: dict[str, list[Flight]] = {}  # by hex: its positions over the last few minutes, oldest first
        self._calls_t: dict[str, float] = {}  # by airport: the last call to one of the real flights there
        # The calls made to each real flight, kept when it's taken away and made again (out of the nearest few, back
        # in): N711UW was cleared to taxi twice, the same long route four minutes apart.
        self._said: dict[str, set[str]] = {}
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
        # The departures due from an airport (ops.schedules, fetched by the service): by airport.
        self.scheduled: dict[str, list] = {}
        self._graphs: dict[str, TaxiGraph] = {}
        self._gates_here = 0  # the gates at the airport being filled (what kind of airport it is)

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
            if old is not None and stuck(old, f):
                continue  # the same spot again, moving: a stale record re-sent (held AAL1138 at 7,000 ft 11 minutes)
            if old is None or f.t > old.t:
                self.flights[hex_] = f
                seen = self.history.setdefault(hex_, [])
                seen.append(f)
                while len(seen) > 2 and now - seen[1].t > HISTORY_S:
                    seen.pop(0)
        for hex_ in [h for h, f in self.flights.items() if now - f.t > LOST_S * 2]:
            del self.flights[hex_]
            self.history.pop(hex_, None)
            self._said.pop(f"live:{hex_}", None)

    def _shown(self, hex_: str, now: float) -> Flight | None:
        """Where a live one is shown now: where it was ``LIVE_DELAY_S`` ago, between the two positions either side
        (its time set to ``now``: nothing to carry on); past the newest (no word for a while), carried on from that.
        None until it's been heard that long."""
        seen = self.history.get(hex_)
        at = now - self.s.live_delay_s
        if not seen or at < seen[0].t:
            return None
        for a, b in zip(seen, seen[1:]):
            if a.t <= at <= b.t:
                k = (at - a.t) / (b.t - a.t) if b.t > a.t else 1.0
                return self._lift(a, b, k, _on_path(a, b, _between(a, b, k, now)))
        last = seen[-1]
        carried = replace(last, t=now - (at - last.t))
        return _on_path(seen[-2], last, carried) if len(seen) > 1 else carried

    def _lift(self, a: Flight, b: Flight, k: float, mid: Flight) -> Flight:
        """Between a report on the ground and one in the air (or the other way): the altitude eased from the field's
        to the reported one, off the ground once above it. Halfway it was on the runway, then 600 ft up at once."""
        if a.on_ground == b.on_ground:
            return mid
        if a.on_ground:  # taking off
            ground = self._pressure_alt(self._elev(a.lat, a.lon))
            alt = ground + ((b.alt_ft if b.alt_ft is not None else ground) - ground) * k
        else:  # landing
            ground = self._pressure_alt(self._elev(b.lat, b.lon))
            alt = (a.alt_ft if a.alt_ft is not None else ground) + (ground - (a.alt_ft if a.alt_ft is not None else ground)) * k
        airborne = alt > ground + 20
        return replace(mid, alt_ft=None if not airborne else alt, on_ground=not airborne)

    def _pressure_alt(self, true_ft: float) -> float:
        """The sim's altitude as a pressure altitude (``_true_alt`` the other way)."""
        return true_ft - (self._true_alt(true_ft) - true_ft)

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
        out += self._scheduled(now)
        out += self._clear_reserved()
        self.atc.managed({p.object_id for p in self.planes.values() if p.object_id is not None})
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
            else NO_FEED_NOTE if not self.feed_ok else NO_SCHEDULE_NOTE if self.unscheduled and self.own is not None \
            and self.own.on_ground else ""
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
            if plane.mode == "landing" and f is not None and f.t > plane.used_t + 1 and plane.local is not None \
                    and haversine_nm(f.lat, f.lon, plane.local.lat, plane.local.lon) * M_PER_NM < HEARD_AGAIN_M:
                # Heard again where LocalTC has it: the real one from here. Somewhere else (the reports late), LocalTC
                # lands it: swapping back and forth was a jump each time.
                plane.mode, plane.local, plane.flight = "live", None, f
                out += self._track(plane, now, blend_s=8.0)
                continue
            if not plane.live or plane.mode in ("go_around", "departing", "hold", "landing", "give_way"):
                continue
            # Only one close enough to be flown down here (one further off was landed, taken away for the distance and
            # made again from the next report, every 2 s).
            if f is not None and plane.mode == "live" and not f.on_ground and now - f.t > QUIET_S and self._feed_fresh(now) \
                    and plane.object_id is not None and self._dist_nm(f.lat, f.lon) <= min(15.0, self.s.radius_nm):
                out += self._landing(plane, now)
                if plane.mode == "landing":
                    continue
            if plane.mode == "parked":  # on its stand (an arrival, or one switched off at its gate): there a while
                if f is not None and f.t > plane.parked_t + 1 and f.gs_kt >= 1 and (shown := self._shown(f.hex, now)) \
                        is not None and shown.gs_kt >= 1:
                    plane.mode, plane.flight, plane.left_stand = "live", shown, True  # moving again: pushed back for its next flight
                    continue
                lat, lon = self._spot_latlon(plane.spot) if plane.spot is not None else \
                    (plane.sent[0], plane.sent[1]) if plane.sent is not None else (self.own.lat, self.own.lon)
                if now - plane.parked_t > PARKED_STAY_S or self._dist_nm(lat, lon) > PARKED_LEAVE_NM:
                    out += self._remove(plane)
                continue
            age = now - f.t if f is not None else 1e9
            far = f is not None and self._dist_nm(f.lat, f.lon) > self.s.radius_nm * LEAVE
            if f is not None and age > STALE_S and f.on_ground and f.gs_kt < 3:
                plane.mode, plane.parked_t = "parked", now  # its transponder off at the gate: parked there
                out += self._track(plane, now)
                continue
            if far or age > LOST_S or (key not in keep and age > STALE_S):
                out += self._remove(plane)
        # A scheduled departure's stand-in at the gate gives way to the real one once its transponder's on.
        heard = {f.callsign for f in self.flights.values() if f.callsign}
        for plane in [p for p in self.planes.values() if p.key.startswith("sched:") and p.callsign in heard]:
            self._did(f"{plane.callsign}'s transponder is on: the real one from here")
            out += self._remove(plane)
        # New ones.
        made = 0
        for d, f in wanted[: self.s.max_live]:
            key = f"live:{f.hex}"
            plane = self.planes.get(key)
            shown = self._shown(f.hex, now)
            if plane is not None:
                if plane.mode == "live" and shown is not None and plane.object_id is not None:
                    plane.flight = shown
                    plane.airborne_seen = plane.airborne_seen or not shown.on_ground
                    if plane.spot is None and self._arriving(plane, shown) and (spot := self._stand_ahead(shown)) is not None:
                        out += self._onto_stand(plane, spot, now)
                    else:
                        out += self._track(plane, now)
                    out += self._calls(plane, f, now)
                continue
            if made >= LIVE_PER_TICK or now - f.t > STALE_S or shown is None or self._kept_off.get(key, 0.0) > now:
                continue  # (one taken off the user's runway was made again from its next report, every 2 s: tower
                # saw it there each time and stopped the user's takeoff roll)
            f = shown
            if not f.on_ground and now - self._first_t > FIRST_LOT_S and d < POP_IN_NM:
                continue  # would appear out of nowhere in front of the user: it comes in from further out instead
            if f.on_ground and d * M_PER_NM < CLEAR_OF_USER_M and own.on_ground:
                continue  # where the user is (the sim's gate and the real one the same)
            spot = self._spot_for(f) if f.on_ground and f.gs_kt < 3 else None
            if spot is not None and self._reserved_at(*self._spot_latlon(spot)):
                continue  # the user's gate: not loaded there
            if f.on_ground and f.gs_kt < 3 and spot is None and self._overlaps(f.lat, f.lon):
                continue  # standing where another of LocalTC's is (two BAWs on one stand): not made on top of it
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
                      spot=spot, created_t=now, said=self._said.setdefault(key, set()))
        self._add(plane)
        lat, lon, alt, hdg = self._where(plane, now)
        gs = 0.0 if spot is not None and f.gs_kt < 3 else f.gs_kt
        # The bridge holds it here from the moment the sim makes it, carried on at its speed (sim_bridge, spawned).
        plane.sent = (lat, lon, alt, hdg, gs, 0.0, 0.0, now)
        out.append(SpawnAiAircraft(request_id=plane.request_id, kind="parked", title=plane.title, livery=plane.livery,
                                   tail=(f.registration or f.callsign)[:9], lat=lat, lon=lon, alt_ft=alt, heading=hdg,
                                   on_ground=f.on_ground, airspeed_kt=gs))
        return out

    def _feed_fresh(self, now: float) -> bool:
        """The sources answering, and other flights heard just now: one gone quiet is out of their sight, not the
        feed down (an hour of adsb.lol timing out had every arrival "landed by LocalTC", then heard again, then ...)."""
        newest = max((f.t for f in self.flights.values()), default=-1e9)
        return self.feed_ok and now - self.feed_t < FEED_FRESH_S and now - newest < FEED_FRESH_S

    def _where(self, plane: Plane, now: float) -> tuple[float, float, float, float]:
        """Where a live one is now: on its spot, or carried on from its last position to now."""
        if self._on_spot(plane):
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
        if alt - self._elev(lat, lon) < 50 and err > GROUND_SNAP_M:
            # On the ground, a long way off where it's shown: out of the user's sight it's put there; in sight it
            # gets there slowly, never sliding across the apron at three times its speed.
            return 0.5 if self._dist_nm(lat, lon) > GROUND_SNAP_NM else GROUND_BLEND_MAX_S
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
        standing = plane.mode == "parked" or (plane.mode == "live" and self._on_spot(plane))
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
        if not airborne and gs < 8 and plane.sent is not None \
                and abs(((f.track - plane.sent[3]) + 180) % 360 - 180) > 120:
            # Going the way its tail points (pushed back): the nose stays where it was, the speed backwards (it was
            # turned round to face the way it was being pushed).
            hdg, gs = (f.track + 180) % 360, -gs
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
        was = plane.lights
        plane.lights = state
        gear, landing, taxi, beacon, strobe, nav, logo = state
        out: list[Any] = [AiLights(object_id=plane.object_id, gear_down=gear, landing=landing, taxi=taxi, beacon=beacon,
                                   strobe=strobe, nav=nav, logo=logo, engines=beacon)]
        if not was or was[0] != gear:
            # The gear handle too: the key event alone left arrivals landing with their wheels up.
            out.append(SetAiVar(object_id=plane.object_id, name="GEAR HANDLE POSITION", unit="Bool", value=float(gear)))
        return out

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

    def _arriving(self, plane: Plane, f: Flight) -> bool:
        """Taxiing in (a stand ahead is where it's going), not out: seen landing, or bound here, and never pushed back
        off a stand. Departures taxiing out slowly past free stands were put on one ("on its stand"), let go as they
        moved on, put on the next: a 787 jumping about O'Hare's apron, into the user's path and back."""
        if plane.airborne_seen:
            return True
        if plane.left_stand:
            return False
        route = self.routes(plane.callsign)
        return route is None or route.destination == self._near_airport(f.lat, f.lon)

    def _onto_stand(self, plane: Plane, spot: tuple[str, int], now: float) -> list[Any]:
        """Turned onto its stand and stopped on the marker, facing the terminal as the stand has it: the real one
        about to switch its transponder off (and the receivers losing it among the buildings) was carried on, through
        the terminal."""
        out: list[Any] = []
        for other in [p for p in self.planes.values() if p.spot == spot and p is not plane]:
            out += self._remove(other)  # one parked there gives way to the real one coming in
        plane.spot, plane.mode, plane.parked_t = spot, "parked", now
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
        """The airport the user is on the ground at, slowed down, if it has gates: none while flying."""
        own = self.own
        if not own.on_ground or own.gs_kt > PARKED_SLOW_KT:
            return []
        near = sorted((haversine_nm(own.lat, own.lon, a.lat, a.lon), icao) for icao, a in self.airports.items()
                      if any(p.kind.startswith(("gate", "ramp")) for p in a.parking))
        return [near[0][1]] if near and near[0][0] <= PARKED_AROUND_NM else []

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
        self._gates_here = sum(1 for p in airport.parking if p.kind.startswith("gate"))
        taken = [self._spot_latlon(p.spot) for p in self.planes.values() if p.spot is not None and p.spot[0] == icao]
        spots = []
        for i, p in enumerate(airport.parking):
            if not p.kind.startswith(("gate", "ramp") if self.s.fill_gates else ("ramp",)):
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
                  f"parked at {icao}'s {'gates' if self.s.fill_gates else 'ramps'} ({round(share * 100)}% full this {what})")

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
        gates = self._gates_here
        if kind.startswith("ramp_ga") and gates >= REGIONAL_GATES and radius_m >= REMOTE_STAND_M:
            kind = "remote_stand"  # an airliner's stand by its size (below), the airport's airlines
        elif kind.startswith("ramp_ga"):
            # What stands on a GA ramp there: business jets at a hub (no Cessnas at Heathrow), turboprops and jets
            # at a mid-size airport, light aircraft only at a small field. None installed of those: left empty, never
            # a light aircraft in their place.
            types = BIZJET_TYPES if gates >= HUB_GATES else TURBOPROP_TYPES if gates >= REGIONAL_GATES else GA_TYPES
            return self._reuse(used, ("ramp_ga", ""), rng, lambda: next(
                (m for t in rng.sample(types, k=len(types)) if (m := self.picker.pick(t)) is not None
                 and (types is GA_TYPES or not _light(m[0]))), None))
        if kind.startswith("ramp_cargo"):
            def cargo() -> tuple[str, str] | None:  # a cargo airline's colours, else any airline's: never unpainted
                for t in rng.sample(CARGO_TYPES, k=len(CARGO_TYPES)):
                    for airline in rng.sample(CARGO_AIRLINES, k=3):
                        if (m := self.picker.pick(t, airline)) is not None and _liveried(m):
                            return m
                return next((m for t in CARGO_TYPES if (m := self.picker.painted(t, rng)) is not None), None)
            return self._reuse(used, ("ramp_cargo", ""), rng, cargo)
        size = "heavy" if "heavy" in kind or radius_m >= 30 else "small" if "small" in kind or radius_m < 18 else "medium"
        names = list(airlines)
        weights = [airlines[n] for n in names]
        airline = rng.choices(names, weights)[0] if names else ""

        def pick() -> tuple[str, str] | None:
            for name in [airline] + [n for n in rng.choices(names, weights, k=3) if n != airline] if names else [""]:
                for kind_code in rng.sample(TYPES_BY_SIZE[size], k=len(TYPES_BY_SIZE[size])):
                    model = self.picker.pick(kind_code, name)
                    if model is not None and _liveried(model):
                        return model
            # None of the airlines here installed in that size: another airline's colours, never a white one.
            return next((m for t in rng.sample(TYPES_BY_SIZE[size], k=len(TYPES_BY_SIZE[size]))
                         if (m := self.picker.painted(t, rng)) is not None), None)

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
                xy = geo.xy(lat, lon)
                if runway.contains(xy, 5.0):
                    # On the user's runway, slow (the real one lining up or crossing as the user was cleared): it can't
                    # be stopped there, and the user's roll was called off for it. Taken away; it comes back from the
                    # live data once it's gone.
                    self._did(f"{plane.callsign} taken off your runway {end.ident}")
                    self._kept_off[plane.key] = now + KEPT_OFF_S
                    out += self._remove(plane)
                elif 0 < runway.distance_to(xy) <= RUNWAY_NEAR_M and self._heading_onto(runway, xy, hdg, f.gs_kt):
                    # Short of it and heading onto it (to line up or to cross): held there.
                    out += self._hold(plane, geo, end, now)
        for plane in [p for p in self.planes.values() if p.mode == "hold" and p.local is not None]:
            if runway.contains(geo.xy(plane.local.lat, plane.local.lon), 5.0):
                self._did(f"{plane.callsign} taken off your runway {end.ident}")
                self._kept_off[plane.key] = now + KEPT_OFF_S
                out += self._remove(plane)
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
                    plane.mode, plane.local, plane.gave_way_t = "live", None, now
                    out += self._track(plane, now)
                continue
            f = plane.flight
            if plane.mode != "live" or not f.on_ground or f.gs_kt < 3 or not own.on_ground:
                continue
            if now - plane.gave_way_t < GIVE_WAY_AGAIN_S:
                continue  # just went on: stopping again at once was a stop-start every 2 s in front of the user
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
        elif loc.follow is None:
            loc.gs_kt = max(20.0, loc.gs_kt - 4.0)  # braking to taxi speed
            if loc.gs_kt <= 60.0:
                loc.follow = self._way_in(plane, geo, loc)

    def _local(self, now: float) -> list[Any]:
        """The flights LocalTC is flying itself, a second on (go-arounds, released departures, quiet arrivals)."""
        out: list[Any] = []
        for plane in list(self.planes.values()):
            loc = plane.local
            if plane.mode not in ("go_around", "departing", "landing") or loc is None or plane.object_id is None:
                continue
            if plane.mode == "landing":
                if loc.follow is not None:
                    out += self._taxi_in(plane, loc, now)
                    continue
                if now - loc.started > LOCAL_TTL_S / 2:
                    out += self._remove(plane)  # never heard again, and nowhere to taxi it: gone
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

    def _graph(self, icao: str) -> TaxiGraph | None:
        geo = self._geo(icao)
        if geo is None:
            return None
        if icao not in self._graphs:
            self._graphs[icao] = TaxiGraph(geo)
        return self._graphs[icao]

    def _taken(self, icao: str) -> list[tuple[float, float]]:
        """Where aircraft stand at ``icao``: LocalTC's on their stands, the live ones standing, the user's gate."""
        out = [self._spot_latlon(p.spot) for p in self.planes.values() if p.spot is not None and p.spot[0] == icao]
        out += [(f.lat, f.lon) for f in self.flights.values() if f.on_ground and f.gs_kt < 3]
        out += list(self.atc.reserved()) + list(self.native_spots)
        if self.own is not None and self.own.on_ground:
            out.append((self.own.lat, self.own.lon))
        return out

    def _airline_near(self, airline: str, icao: str) -> list[tuple[float, float]]:
        """Where the airline's own aircraft stand at ``icao`` (its terminal)."""
        if not airline:
            return []
        out = [self._spot_latlon(p.spot) for p in self.planes.values()
               if p.spot is not None and p.spot[0] == icao and (p.callsign or "").startswith(airline)]
        out += [(f.lat, f.lon) for f in self.flights.values() if f.on_ground and f.gs_kt < 3 and f.airline == airline]
        return out

    def _way_in(self, plane: Plane, geo: AirportGeometry, loc: Local) -> Follower | None:
        """Rolled out with no word from the real one: the way off the runway and in to a free stand its wings fit."""
        graph = self._graph(geo.icao)
        f = plane.flight
        span = wingspan_m(f.type or self.types(f.hex)) if f is not None else 0.0
        airline = f.airline if f is not None else ""
        spot = free_stand(geo, self._taken(geo.icao), span, self._airline_near(airline, geo.icao),
                          seed=f"{plane.callsign}{geo.icao}")
        if graph is None or spot is None:
            return None
        got = taxi_in_path(geo, graph, loc.lat, loc.lon, loc.hdg, spot)
        if got is None:
            return None
        points, exit_index = got
        self._did(f"{plane.callsign or 'An arrival'} taxied in to {geo.airport.parking[spot].name.title()} by LocalTC")
        plane.spot = (geo.icao, spot)  # kept for it: nobody else is put there meanwhile
        return Follower(points, gs_kt=loc.gs_kt, exit_index=exit_index, spot=spot)

    def _taxi_in(self, plane: Plane, loc: Local, now: float) -> list[Any]:
        """A second along the way in; on the stand, parked there (and staying, as an arrival does)."""
        if now - loc.started > TAXI_TTL_S:
            return self._remove(plane)
        follow = loc.follow
        loc.lat, loc.lon, loc.hdg, loc.gs_kt = follow.step(loc.lat, loc.lon)
        loc.on_ground, loc.vs_fpm = True, 0.0
        loc.alt_ft = self._elev(loc.lat, loc.lon)
        if follow.done:
            plane.mode, plane.local, plane.parked_t = "parked", None, now
            return self._track(plane, now, blend_s=6.0)
        out = [self._send(plane, AiTrack(object_id=plane.object_id, lat=loc.lat, lon=loc.lon, alt_ft=loc.alt_ft,
                                         hdg=loc.hdg, gs_kt=loc.gs_kt, on_ground=True, blend_s=1.5), now)]
        return out + self._lights(plane, on_ground=True, gs=loc.gs_kt, agl=0.0, lat=loc.lat, lon=loc.lon)

    def _on_spot(self, plane: Plane) -> bool:
        """Standing on its stand: until the real one is moving and well off it (its pushback). The receivers' positions
        at a gate wander a few metres and a knot or two: it was put from the stand to the taxi line and back, over
        and over."""
        if plane.spot is None:
            return False
        f = plane.flight
        if f is None or f.gs_kt < 1:
            return True
        lat, lon = self._spot_latlon(plane.spot)
        if f.gs_kt < 2 or haversine_nm(f.lat, f.lon, lat, lon) * M_PER_NM < SPOT_LEAVE_M:
            return True
        plane.spot, plane.left_stand = None, True  # pushed back: off its stand for good
        return False

    def _overlaps(self, lat: float, lon: float) -> bool:
        for p in self.planes.values():
            if p.spot is not None:
                a, b = self._spot_latlon(p.spot)
            elif p.sent is not None:
                a, b = p.sent[0], p.sent[1]
            else:
                continue
            if haversine_nm(lat, lon, a, b) * M_PER_NM < OVERLAP_M:
                return True
        return False

    # --- the scheduled departures ------------------------------------------------------------------------------

    def _scheduled(self, now: float) -> list[Any]:
        """At the airport the user's on the ground at: each departure due within ``SCHEDULED_BEFORE_S`` at a gate
        (one a second), its clearance given before it's due, and taken away once the real one's transponder is on
        or it's well past its time."""
        out: list[Any] = []
        here = set(self._parked_airports())
        for plane in [p for p in self.planes.values() if p.key.startswith("sched:")]:
            due = plane.parked_t  # (its time off the gate)
            icao = plane.spot[0] if plane.spot else ""
            if icao not in here and icao not in self.parked_at or now > due + SCHEDULED_LATE_S:
                out += self._remove(plane)
                continue
            if plane.object_id is not None and "clearance" not in plane.said and now >= due - SCHEDULED_CLEARANCE_S \
                    and self.s.atc_control and now - self._calls_t.get(icao, -1e9) >= CALLS_GAP_S:
                plane.said.add("clearance")
                self._calls_t[icao] = now
                out += self.atc.call(callsign=plane.callsign, kind="clearance", icao=icao, runway="",
                                     destination=plane.note or "")
        made = 0
        heard = {f.callsign for f in self.flights.values() if f.callsign}
        for icao in here:
            geo = self._geo(icao)
            if geo is None:
                continue
            count = sum(1 for p in self.planes.values() if p.key.startswith("sched:"))
            for dep in sorted(self.scheduled.get(icao, ()), key=lambda d: d.off_t):
                if made >= 1 or count >= SCHEDULED_MAX:
                    break
                key = f"sched:{dep.callsign}"
                if key in self.planes or dep.callsign in heard or not dep.off_t - SCHEDULED_BEFORE_S <= now <= dep.off_t:
                    continue
                kind = dep.type or (self.types(dep.hex) if dep.hex else "") or "A320"
                model = self.picker.pick(kind, dep.airline)
                spot = free_stand(geo, self._taken(icao), wingspan_m(kind), self._airline_near(dep.airline, icao),
                                  seed=dep.callsign)
                if model is None or spot is None:
                    continue
                p = geo.airport.parking[spot]
                plane = Plane(key, self._request(), model[0], model[1], callsign=dep.callsign, spot=(icao, spot),
                              mode="parked", created_t=now, note=dep.destination)
                plane.parked_t = dep.off_t
                self._add(plane)
                made += 1
                count += 1
                self._did(f"{dep.callsign} at {p.name.title()}, due off at {_hhmm(dep.off_t)}Z ({dep.source})")
                out.append(SpawnAiAircraft(request_id=plane.request_id, kind="parked", title=plane.title,
                                           livery=plane.livery, tail=dep.callsign[:9], lat=p.lat, lon=p.lon,
                                           alt_ft=geo.airport.elev_ft, heading=p.heading_true, on_ground=True))
        return out

    # --- the controllers' calls to the real flights ------------------------------------------------------------

    def _calls(self, plane: Plane, real: Flight, now: float) -> list[Any]:
        """What the controllers tell a real flight, just before it does it (it's shown ``LIVE_DELAY_S`` behind the
        real one, whose newest position already shows what it does next): delivery its clearance and ground its
        push at the gate, its taxi out; tower its line-up, takeoff and landing; ground its taxi in. Each once, and
        spaced at an airport (``CALLS_GAP_S``)."""
        shown = plane.flight
        if not plane.callsign or shown is None or not self.s.atc_control:
            return []
        icao = self._near_airport(shown.lat, shown.lon, 3.0 if shown.on_ground else FINAL_NM)
        geo = self._geo(icao) if icao else None
        if geo is None or now - self._calls_t.get(icao, -1e9) < CALLS_GAP_S:
            return []
        call = next((c for c in self._next_calls(plane, geo, shown, real) if c[0] not in plane.said), None)
        if call is None:
            return []
        kind, runway, extra = call
        out = self.atc.call(callsign=plane.callsign, kind=kind, icao=geo.icao, runway=runway, **extra)
        if out:  # not said (the frequency busy, the pilot owing a readback): tried again on the next tick
            plane.said.add(kind)
            self._calls_t[icao] = now
        return out

    def _next_calls(self, plane: Plane, geo: AirportGeometry, shown: Flight, real: Flight) -> list[tuple[str, str, dict]]:
        """The calls that fit what the flight is doing now and about to do, in the order they're made."""
        out: list[tuple[str, str, dict]] = []
        route = self.routes(plane.callsign)
        departing = not plane.airborne_seen and (route is None or route.origin == geo.icao)
        if not shown.on_ground:
            if real.on_ground or shown.vs_fpm > 300:
                return out
            final = geo.final_approach(shown.lat, shown.lon, shown.track, max_distance_nm=FINAL_CALL_NM)
            if final is not None and (shown.alt_ft or 0) - geo.airport.elev_ft < 3000:
                out.append(("land", final.end.ident, {}))
            return out
        on_runway = geo.runway_at(real.lat, real.lon, 10.0) if real.on_ground else None
        if plane.airborne_seen:
            if shown.gs_kt < 40 and geo.runway_at(shown.lat, shown.lon, 10.0) is None:
                out.append(("taxi_in", "", {"lat": shown.lat, "lon": shown.lon}))
            return out
        if not departing:
            return out
        at_gate = shown.gs_kt < 1 and (plane.spot is not None or self._spot_for(shown) is not None)
        if at_gate and real.gs_kt < 1 and route is not None and route.destination:
            out.append(("clearance", "", {"destination": route.destination_name or route.destination}))
        if at_gate and real.gs_kt >= 1:
            out.append(("pushback", "", {}))
        if real.gs_kt >= 5 and on_runway is None and not (real.on_ground is False):
            out.append(("taxi_out", "", {"lat": real.lat, "lon": real.lon}))
        if on_runway is not None or not real.on_ground:
            end = (on_runway.end_for_heading(real.track, 60) if on_runway is not None else None)
            if end is not None and real.gs_kt < 30:
                out.append(("lineup", end.ident, {}))
            if end is not None or not real.on_ground:
                ident = end.ident if end is not None else ""
                if ident:
                    out.append(("takeoff", ident, {}))
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


def _hhmm(t: float) -> str:
    return f"{int(t // 3600 % 24):02d}{int(t // 60 % 60):02d}"


def _liveried(model: tuple[str, str]) -> bool:
    """In an airline's colours: not FSLTL's unpainted ("ZZZZ") one."""
    return "ZZZ" not in f"{model[0]} {model[1]}".upper()


def stuck(old: Flight, new: Flight) -> bool:
    """A report of a moving aircraft at exactly the place of the last one: a source re-sending a stale record."""
    return new.t > old.t and new.lat == old.lat and new.lon == old.lon and new.gs_kt >= 5 \
        and (not new.on_ground or new.gs_kt >= 5)


def _on_path(a: Flight, b: Flight, f: Flight) -> Flight:
    """On the ground, its track the way it really went from one report to the next: at taxi speed the reported track
    is often off by 50 degrees or more, and it slid along sideways with its nose that way."""
    if not (a.on_ground and b.on_ground) or haversine_nm(a.lat, a.lon, b.lat, b.lon) * M_PER_NM < PATH_MIN_M:
        return f
    return replace(f, track=bearing_deg(a.lat, a.lon, b.lat, b.lon))


def _between(a: Flight, b: Flight, k: float, now: float) -> Flight:
    """The flight ``k`` of the way from report ``a`` to report ``b``, timed ``now``."""
    k = max(0.0, min(1.0, k))
    track = (a.track + (((b.track - a.track) + 180) % 360 - 180) * k) % 360
    alt = a.alt_ft + (b.alt_ft - a.alt_ft) * k if a.alt_ft is not None and b.alt_ft is not None else \
        (b.alt_ft if k >= 0.5 else a.alt_ft)
    return replace(b, lat=a.lat + (b.lat - a.lat) * k, lon=a.lon + (b.lon - a.lon) * k, alt_ft=alt, track=track,
                   gs_kt=a.gs_kt + (b.gs_kt - a.gs_kt) * k, vs_fpm=a.vs_fpm + (b.vs_fpm - a.vs_fpm) * k,
                   on_ground=a.on_ground if k < 0.5 else b.on_ground, t=now)
