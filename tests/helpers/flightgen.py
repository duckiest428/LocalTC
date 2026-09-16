"""Deterministic synthetic flights: kinematic legs that emit OwnshipState ticks.

    b = FlightBuilder(kpae(), destination=kbfi())
    b.park(60); b.taxi_route(...); b.takeoff(...); ...
    events = b.events  # OwnshipState + AirportData + SimLifecycle, sorted by t

Not a flight model, just plausible positions, speeds and flags for phase
detection, dialogue and scenario tests.
"""

import math
from dataclasses import dataclass, replace

from localtc.atc_core.airport import AirportGeometry, RunwayEndGeometry, TaxiGraph, TaxiRoute
from localtc.sim_api import Airport, AirportData, AircraftIdentity, BusEvent, OwnshipState, SimLifecycle
from localtc.sim_api.geo import METERS_PER_NM, angle_diff, bearing_deg, haversine_nm, unit

KT_TO_MPS = METERS_PER_NM / 3600


@dataclass
class _State:
    lat: float
    lon: float
    alt_msl_ft: float
    hdg_true: float
    gs_kt: float = 0.0
    vs_fpm: float = 0.0
    on_ground: bool = True
    on_runway: bool = False
    flaps: int = 0
    parking_brake: bool = True
    engine: bool = True
    squawk: str = "1200"
    com1: float = 121.8


def move(lat: float, lon: float, heading: float, meters: float) -> tuple[float, float]:
    ux, uy = unit(heading)
    return (
        lat + uy * meters / 111_320.0,
        lon + ux * meters / (111_320.0 * math.cos(math.radians(lat))),
    )


class FlightBuilder:
    def __init__(
        self,
        origin: Airport,
        *,
        destination: Airport | None = None,
        parking_index: int = 0,
        dt: float = 1.0,
        wind: tuple[float, float] = (330.0, 8.0),
        destination_wind: tuple[float, float] | None = None,
        callsign: str = "N172LT",
    ) -> None:
        self.origin = AirportGeometry(origin)
        self.destination = AirportGeometry(destination) if destination else None
        self.dt = dt
        self.t = 0.0
        self.wind, self.destination_wind = wind, destination_wind or wind
        spot = origin.parking[parking_index]
        self.s = _State(lat=spot.lat, lon=spot.lon, alt_msl_ft=origin.elev_ft, hdg_true=spot.heading_true)
        self.events: list[BusEvent] = [
            SimLifecycle(t=0.0, kind="flight_loaded", detail="synthetic.flt"),
            AircraftIdentity(t=0.0, title="Cessna Skyhawk G1000", atc_id=callsign, atc_type="Cessna", atc_model="C172"),
            AirportData(t=0.0, airport=origin),
        ]
        if destination is not None:
            self.events.append(AirportData(t=0.0, airport=destination))

    # --- output -------------------------------------------------------------------

    def _ground_elev(self) -> float:
        if self.destination is None:
            return self.origin.airport.elev_ft
        d_origin = self.origin.distance_nm(self.s.lat, self.s.lon)
        d_dest = self.destination.distance_nm(self.s.lat, self.s.lon)
        return (self.origin if d_origin <= d_dest else self.destination).airport.elev_ft

    def _wind(self) -> tuple[float, float]:
        if self.destination is None:
            return self.wind
        closer_to_dest = self.destination.distance_nm(self.s.lat, self.s.lon) < self.origin.distance_nm(self.s.lat, self.s.lon)
        return self.destination_wind if closer_to_dest else self.wind

    def tick(self) -> None:
        s = self.s
        agl = max(0.0, s.alt_msl_ft - self._ground_elev()) if not s.on_ground else 0.0
        wind_dir, wind_kt = self._wind()
        self.events.append(
            OwnshipState(
                t=round(self.t, 3), lat=round(s.lat, 7), lon=round(s.lon, 7),
                alt_msl_ft=round(s.alt_msl_ft, 1), alt_indicated_ft=round(s.alt_msl_ft, 1), alt_agl_ft=round(agl, 1),
                altimeter_inhg=29.92, hdg_mag=round((s.hdg_true - 16) % 360, 1), hdg_true=round(s.hdg_true % 360, 1),
                ias_kt=round(s.gs_kt, 1), gs_kt=round(s.gs_kt, 1), vs_fpm=round(s.vs_fpm), on_ground=s.on_ground,
                squawk=s.squawk, xpdr_mode="standby" if s.on_ground else "alt", com1_mhz=s.com1, com2_mhz=121.5,
                gear_down=True, flaps_index=s.flaps, parking_brake=s.parking_brake, engine_running=s.engine,
                on_runway=s.on_runway, wind_dir_true=wind_dir, wind_kt=wind_kt, magvar=16.0, altimeter_setting_inhg=29.92,
            )
        )
        self.t += self.dt

    def sorted_events(self) -> list[BusEvent]:
        return sorted(self.events, key=lambda e: e.t)

    # --- ground legs ----------------------------------------------------------------

    def park(self, seconds: float, **changes) -> "FlightBuilder":
        self.s = replace(self.s, gs_kt=0.0, vs_fpm=0.0, **changes)
        for _ in range(int(seconds / self.dt)):
            self.tick()
        return self

    def taxi_to(self, points: list[tuple[float, float]], speed_kt: float = 12.0, *, on_runway: bool = False) -> "FlightBuilder":
        self.s.parking_brake = False
        step = speed_kt * KT_TO_MPS * self.dt
        for lat, lon in points:
            while (dist := haversine_nm(self.s.lat, self.s.lon, lat, lon) * METERS_PER_NM) > 0.5:
                heading = bearing_deg(self.s.lat, self.s.lon, lat, lon)
                self.s.hdg_true = heading
                self.s.gs_kt = speed_kt
                self.s.on_runway = on_runway
                if dist <= step:
                    self.s.lat, self.s.lon = lat, lon
                else:
                    self.s.lat, self.s.lon = move(self.s.lat, self.s.lon, heading, step)
                self.tick()
        self.s.gs_kt = 0.0
        return self

    def taxi_route(self, geometry: AirportGeometry, route: TaxiRoute, speed_kt: float = 12.0) -> "FlightBuilder":
        graph = TaxiGraph(geometry)
        points = [geometry.frame.to_latlon(*graph.positions[node]) for node in route.nodes[1:]]
        return self.taxi_to(points, speed_kt)

    def line_up(self, end: RunwayEndGeometry, geometry: AirportGeometry) -> "FlightBuilder":
        ux, uy = unit(end.heading_true)
        spot = geometry.frame.to_latlon(end.threshold[0] + ux * 60, end.threshold[1] + uy * 60)
        self.taxi_to([spot], 6.0, on_runway=True)
        self.s.hdg_true = end.heading_true
        self.s.on_runway = True
        return self.park(5, on_runway=True, parking_brake=False)

    def takeoff(self, end: RunwayEndGeometry, *, rotate_kt: float = 60.0, accel: float = 3.0) -> "FlightBuilder":
        s = self.s
        s.hdg_true, s.on_runway, s.flaps = end.heading_true, True, 1
        while s.gs_kt < rotate_kt:
            s.gs_kt = min(rotate_kt, s.gs_kt + accel * self.dt)
            s.lat, s.lon = move(s.lat, s.lon, s.hdg_true, s.gs_kt * KT_TO_MPS * self.dt)
            self.tick()
        s.on_ground, s.on_runway = False, False
        self.climb_to(self._ground_elev() + 800, vs=700, gs=75)
        s.flaps = 0
        return self

    def rollout(self, *, exit_kt: float = 12.0, decel: float = 4.0) -> "FlightBuilder":
        s = self.s
        s.on_ground, s.on_runway, s.vs_fpm = True, True, 0.0
        s.alt_msl_ft = self._ground_elev()
        while s.gs_kt > exit_kt:
            s.gs_kt = max(exit_kt, s.gs_kt - decel * self.dt)
            s.lat, s.lon = move(s.lat, s.lon, s.hdg_true, s.gs_kt * KT_TO_MPS * self.dt)
            self.tick()
        return self

    def exit_and_park(self, geometry: AirportGeometry) -> "FlightBuilder":
        graph = TaxiGraph(geometry)
        # Exit via the hold-short point nearest the aircraft, then follow the graph to parking.
        hold, _ = geometry.nearest_hold_short(self.s.lat, self.s.lon)
        self.taxi_to([(hold.point.lat, hold.point.lon)], 10.0, on_runway=False)
        self.s.on_runway = False
        route = graph.parking_route(self.s.lat, self.s.lon)
        self.taxi_route(geometry, route)
        return self.park(30, parking_brake=True).park(10, parking_brake=True, engine=False)

    # --- air legs --------------------------------------------------------------------

    def climb_to(self, alt_msl: float, *, vs: float = 700, gs: float = 90, heading: float | None = None) -> "FlightBuilder":
        s = self.s
        s.on_ground, s.gs_kt = False, gs
        if heading is not None:
            s.hdg_true = heading
        while s.alt_msl_ft < alt_msl:
            s.vs_fpm = vs
            s.alt_msl_ft = min(alt_msl, s.alt_msl_ft + vs / 60 * self.dt)
            s.lat, s.lon = move(s.lat, s.lon, s.hdg_true, gs * KT_TO_MPS * self.dt)
            self.tick()
        s.vs_fpm = 0.0
        return self

    def fly_to(
        self,
        lat: float,
        lon: float,
        alt_msl: float,
        *,
        gs: float = 120,
        vs: float = 700,
        descend_within_nm: float | None = None,
        cruise_alt: float | None = None,
        stop_nm: float = 0.3,
    ) -> "FlightBuilder":
        """Fly direct; hold ``cruise_alt`` until within ``descend_within_nm`` of the target, then go to ``alt_msl``."""
        s = self.s
        s.on_ground, s.gs_kt = False, gs
        while haversine_nm(s.lat, s.lon, lat, lon) > stop_nm:
            remaining = haversine_nm(s.lat, s.lon, lat, lon)
            target_alt = alt_msl
            if cruise_alt is not None and descend_within_nm is not None and remaining > descend_within_nm:
                target_alt = cruise_alt
            error = target_alt - s.alt_msl_ft
            s.vs_fpm = max(-vs, min(vs, error * 60 / self.dt)) if abs(error) > 1 else 0.0
            s.alt_msl_ft += s.vs_fpm / 60 * self.dt
            desired = bearing_deg(s.lat, s.lon, lat, lon)
            turn = max(-3 * self.dt, min(3 * self.dt, ((desired - s.hdg_true + 180) % 360) - 180))  # standard rate
            s.hdg_true = (s.hdg_true + turn) % 360
            s.lat, s.lon = move(s.lat, s.lon, s.hdg_true, gs * KT_TO_MPS * self.dt)
            self.tick()
        return self

    def final_approach(self, end: RunwayEndGeometry, geometry: AirportGeometry, *, gs: float = 85) -> "FlightBuilder":
        """From the current position (roughly on the extended centerline) down a 3° path to touchdown."""
        s = self.s
        s.flaps = 2
        threshold = geometry.frame.to_latlon(*end.threshold)
        field = geometry.airport.elev_ft
        while True:
            dist_nm = haversine_nm(s.lat, s.lon, *threshold)
            past = angle_diff(bearing_deg(s.lat, s.lon, *threshold), end.heading_true) > 90
            glide_alt = field + 50 + dist_nm * 318 * (-1 if past else 1)
            if past and s.alt_msl_ft - field <= 5:
                break
            target = max(field, glide_alt)
            s.vs_fpm = max(-900, min(0, (target - s.alt_msl_ft) * 60 / self.dt))
            s.alt_msl_ft = max(field, s.alt_msl_ft + s.vs_fpm / 60 * self.dt)
            s.hdg_true = end.heading_true
            s.gs_kt = gs if dist_nm > 1 or not past else max(55, gs - 20)
            s.lat, s.lon = move(s.lat, s.lon, s.hdg_true, s.gs_kt * KT_TO_MPS * self.dt)
            s.on_runway = past or dist_nm < 0.02
            self.tick()
        return self.rollout()


def ifr_kpae_kbfi(kpae_airport: Airport, kbfi_airport: Airport, *, dt: float = 1.0) -> FlightBuilder:
    """Paine Field 34L departure, north 18 nm, direct to a 10 nm final for Boeing Field 14R, land, park."""
    # Calm at Paine (ILS runway 34L in use), southeasterly at Boeing Field (runway 14R).
    b = FlightBuilder(kpae_airport, destination=kbfi_airport, dt=dt, wind=(150, 3), destination_wind=(150, 8))
    origin, dest = b.origin, b.destination
    dep_end, arr_end = origin.end("34L"), dest.end("14R")
    b.park(90)  # clearance delivery
    route = TaxiGraph(origin).departure_route(b.s.lat, b.s.lon, dep_end)
    b.taxi_route(origin, route)
    b.park(40, parking_brake=False)  # holding short, run-up
    b.line_up(dep_end, origin)
    b.takeoff(dep_end)
    turn_lat, turn_lon = move(origin.airport.lat, origin.airport.lon, 340, 18 * METERS_PER_NM)
    b.fly_to(turn_lat, turn_lon, 5000, gs=110)
    b.park(0)
    iaf = dest.frame.to_latlon(
        arr_end.threshold[0] - unit(arr_end.heading_true)[0] * 10 * METERS_PER_NM,
        arr_end.threshold[1] - unit(arr_end.heading_true)[1] * 10 * METERS_PER_NM,
    )
    b.fly_to(*iaf, 3200, gs=140, vs=600, cruise_alt=5000, descend_within_nm=22)
    b.final_approach(arr_end, dest)
    b.exit_and_park(dest)
    return b
