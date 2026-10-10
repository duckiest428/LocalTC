"""LocalTC's traffic (localtc.traffic), without the sim or the network: the real flights flown in the sim with their
own airline's model, the gates filled for the hour and kept clear for the user, and the traffic answering to ATC."""

from localtc.sim_api import (
    AiLights,
    AiObjectAssigned,
    AiTrack,
    Airport,
    OwnshipState,
    ParkingSpot,
    RemoveAiAircraft,
    Runway,
    RunwayEnd,
    SetAiVar,
    SpawnAiAircraft,
    TrafficControlStatus,
    TrafficSnapshot,
    TrafficTarget,
)
from localtc.sim_bridge.motion import Track, advance
from localtc.traffic.feed import Flight, FlightBook, LiveFeed, parse
from localtc.traffic.manager import AtcLink, TrafficManager, TrafficSettings
from localtc.traffic.models import ModelPicker
from localtc.traffic.models import parse as parse_model

MODELS = [("FSLTL_FAIB_B738_UAL-United_NC", ""), ("FSLTL_A20N_ZZZZ", ""), ("FSLTL_B738_ZZZZ", ""),
          ("FSLTL_CRJ7_SKW_DAL", ""), ("FSLTL_B789_ZZZZ", ""), ("FSLTL_A321_OMS_SalamAir-STUB", ""),
          ("FSLTL_FSPXAI_B789_UAL-United_NC", ""), ("FSLTL A321 DAL Delta", ""), ("A320neo V2", ""),
          ("FSLTL_GA_C172_ZZZ", "")]
# A one-runway airport (09/27, 3,000 m, centred on 0,0) with four gates and a GA ramp.
RWY = Runway(lat=0.0, lon=0.0, elev_ft=100.0, heading_true=90.0, length_m=3000.0, width_m=45.0,
             primary=RunwayEnd(number=9), secondary=RunwayEnd(number=27))
GATES = tuple(ParkingSpot(index=i, name=f"GATE A {i + 1}", kind="gate_medium", lat=0.006, lon=-0.004 + i * 0.002,
                          heading_true=180.0, radius_m=22.0) for i in range(4))
RAMP = ParkingSpot(index=4, name="PARKING 1", kind="ramp_ga_small", lat=0.006, lon=0.01, heading_true=180.0, radius_m=8.0)
APT = Airport(icao="KTST", lat=0.0, lon=0.0, elev_ft=100.0, runways=(RWY,), parking=GATES + (RAMP,))
NOW = 1_000_000.0


def own(t=0.0, lat=0.02, lon=0.0, on_ground=True, gs=0.0, zulu_h=14.0, **kw) -> OwnshipState:
    return OwnshipState(t=t, lat=lat, lon=lon, alt_msl_ft=100.0, alt_indicated_ft=100.0, alt_agl_ft=0.0,
                        altimeter_inhg=29.92, altimeter_setting_inhg=29.92, hdg_true=90.0, hdg_mag=90.0, ias_kt=gs,
                        gs_kt=gs, vs_fpm=0.0, on_ground=on_ground, squawk="1200", xpdr_mode="alt", com1_mhz=118.0, com2_mhz=121.5,
                        zulu_s=zulu_h * 3600, **kw)


def flight(hex_="a1", callsign="UAL123", type_="B738", lat=0.05, lon=0.0, alt=3000.0, ground=False, gs=180.0,
           track=180.0, t=NOW, vs=-700.0) -> Flight:
    return Flight(hex=hex_, callsign=callsign, registration="N123UA", type=type_, category="A3", lat=lat, lon=lon,
                  alt_ft=None if ground else alt, on_ground=ground, gs_kt=gs, track=track, vs_fpm=0.0 if ground else vs,
                  squawk="", t=t)


def manager(atc: AtcLink | None = None, **settings) -> TrafficManager:
    m = TrafficManager(TrafficSettings(**{"parked": False, **settings}), atc, picker=ModelPicker(MODELS))
    m.on_airport(APT)
    m.on_own(own())
    return m


def assign(m: TrafficManager, out: list, now: float = NOW) -> list:
    """The sim's answers to everything created: object ids 100 up."""
    more = []
    for i, c in enumerate([c for c in out if isinstance(c, SpawnAiAircraft)]):
        more += m.on_assigned(AiObjectAssigned(t=0.0, request_id=c.request_id, object_id=100 + c.request_id), now)
    return more


def of(kind, out):
    return [c for c in out if isinstance(c, kind)]


# --- models, feed, motion --------------------------------------------------------------------------------------------


def test_fsltl_names_are_read_and_the_airlines_own_livery_wins():
    assert parse_model("FSLTL_CRJ7_SKW_DAL").airlines == ("SKW", "DAL")
    assert parse_model("FSLTL A321 DAL Delta").type == "A321"
    assert parse_model("FSLTL_A321_OMS_SalamAir-STUB") is None
    p = ModelPicker(MODELS)
    assert p.pick("B738", "UAL") == ("FSLTL_FAIB_B738_UAL-United_NC", "")
    assert p.pick("B38M", "SWA") == ("FSLTL_B738_ZZZZ", "")  # no Southwest: the unpainted one, never another airline's
    assert p.pick("CRJ9", "SKW") == ("FSLTL_CRJ7_SKW_DAL", "")  # the family's
    assert p.pick("", "", "A3") == ("FSLTL_A20N_ZZZZ", "")  # unknown type: by its size
    assert ModelPicker([("A320neo V2", "")]).pick("A20N", "NKS") == ("A320neo V2", "")  # no FSLTL: the sim's own


def test_the_feed_reads_readsb_answers_and_falls_back():
    data = {"now": NOW * 1000, "ac": [
        {"hex": "abc123", "flight": "UAL123  ", "r": "N1", "t": "B738", "lat": 1.0, "lon": 2.0, "alt_baro": 3000,
         "gs": 200, "track": 90, "baro_rate": -640, "seen_pos": 1.5, "category": "A3"},
        {"hex": "def456", "flight": "", "r": "N2", "lat": 1.0, "lon": 2.0, "alt_baro": "ground", "gs": 0, "seen_pos": 0},
        {"hex": "old", "lat": 1, "lon": 2, "alt_baro": 1000, "seen_pos": 90},  # too old
        {"hex": "nopos", "alt_baro": 1000}]}
    got = parse(data, now=NOW)
    assert [f.hex for f in got] == ["abc123", "def456"]
    assert got[0].airline == "UAL" and got[0].t == NOW - 1.5 and got[0].vs_fpm == -640
    assert got[1].on_ground and got[1].alt_ft is None and got[1].airline == ""
    calls = []

    def fetch(url):
        calls.append(url)
        if "adsb.lol" in url:
            raise OSError("busy")
        return data

    feed = LiveFeed(fetch)
    assert len(feed.around(1.0, 2.0, 40)) == 2 and feed.source == "adsb.fi" and len(calls) == 2


def test_routes_and_types_are_asked_once_and_kept(tmp_path):
    asked = []

    def fetch(url):
        asked.append(url)
        if "callsign" in url:
            return {"response": {"flightroute": {"origin": {"icao_code": "KORD"}, "destination": {"icao_code": "KLAS"}}}}
        return {"response": {"aircraft": {"icao_type": "A321"}}}

    book = FlightBook(tmp_path / "f.json", fetch)
    flights = [flight(), flight(hex_="b2", callsign="N172SP", type_="")]
    book.look_up(flights)
    book.look_up(flights)
    assert book.route("UAL123").destination == "KLAS" and book.type_of("b2") == "A321" and len(asked) == 2
    book.save()
    assert FlightBook(tmp_path / "f.json", fetch).route("UAL123").origin == "KORD"


def test_motion_carries_on_and_eases_a_correction_without_a_jump():
    lat, lon, hdg = advance(0.0, 0.0, 90.0, 360.0, 0.0, 10.0)  # 360 kt east for 10 s: 1 nm
    assert abs(lon * 111_320 - 1852) < 5 and abs(hdg - 90) < 1e-9
    _, _, hdg = advance(0.0, 0.0, 90.0, 200.0, 3.0, 10.0)
    assert abs(hdg - 120.0) < 1e-6
    t = Track(1, 0.0, 0.0, 3000.0, 90.0, 200.0, 0.0, 0.0, False, 0.0, 0.0, t0=0.0, blend_s=0.0)
    before = t.at(4.0)
    # The real one turns out to be 300 m north: shown where it was, then eased over to it.
    new = Track(1, before[0] + 300 / 111_320, before[1], 3000.0, 90.0, 200.0, 0.0, 0.0, False, 0.0, 0.0, t0=4.0, blend_s=5.0)
    t.update(new, 4.0)
    assert abs(t.at(4.0)[0] - before[0]) * 111_320 < 1
    steps = [t.at(4.0 + i * 0.1)[0] * 111_320 for i in range(60)]
    assert max(b - a for a, b in zip(steps, steps[1:])) < 20  # never more than 20 m north in a tenth of a second
    assert abs(steps[-1] - (before[0] * 111_320 + 300)) < 2


# --- the live flights ------------------------------------------------------------------------------------------------


def test_a_real_flight_is_flown_in_the_sim_with_its_model_and_callsign():
    m = manager()
    m.on_feed([flight(alt=2000.0)], "adsb.lol", NOW)
    out = m.tick(NOW)
    spawn = of(SpawnAiAircraft, out)[0]
    assert spawn.title == "FSLTL_FAIB_B738_UAL-United_NC" and not spawn.on_ground and spawn.kind == "parked"
    out = assign(m, out)
    assert [c.text for c in of(SetAiVar, out)] == ["UAL123", "UAL", "123"]  # who it is, for ATC
    track = of(AiTrack, out)[0]
    assert not track.on_ground and track.gs_kt == 180 and abs(track.hdg - 180) < 1e-9
    assert of(AiLights, out)[0].gear_down and of(AiLights, out)[0].landing  # low, on the way in
    # Five seconds on, the next position: moved on from there.
    m.on_feed([flight(lat=0.046, alt=1900.0, t=NOW + 5)], "adsb.lol", NOW + 5)
    out = m.tick(NOW + 5)
    assert of(AiTrack, out) and abs(of(AiTrack, out)[0].lat - 0.046) < 1e-6


def test_one_out_of_the_area_or_lost_is_taken_away():
    m = manager(radius_nm=10)
    m.on_feed([flight()], "adsb.lol", NOW)
    assign(m, m.tick(NOW))
    m.on_feed([flight(lat=0.5, t=NOW + 5)], "adsb.lol", NOW + 5)  # 30 nm away
    assert of(RemoveAiAircraft, m.tick(NOW + 5))


def test_one_at_a_gate_stands_on_the_sims_spot_and_its_transponder_off_leaves_it_parked():
    m = manager()
    m.on_feed([flight(lat=0.00605, lon=-0.00402, ground=True, gs=0.0)], "adsb.lol", NOW)
    out = m.tick(NOW)
    spawn = of(SpawnAiAircraft, out)[0]
    assert (spawn.lat, spawn.lon, spawn.heading) == (GATES[0].lat, GATES[0].lon, 180.0)
    assign(m, out)
    out = m.tick(NOW + 60.0)  # nothing heard from it for a minute: switched off at its gate
    assert not of(RemoveAiAircraft, out)
    plane = next(iter(m.planes.values()))
    assert plane.mode == "parked"


# --- parked aircraft -------------------------------------------------------------------------------------------------


def parked(m: TrafficManager, now: float = NOW) -> list[SpawnAiAircraft]:
    out = []
    for i in range(5):
        out += m.tick(now + i)
    return of(SpawnAiAircraft, out)


def test_the_gates_fill_by_the_hour_and_the_users_gate_stays_clear():
    night = manager(parked=True)
    night.on_own(own(zulu_h=3.0))  # 03:00 here (longitude 0): everyone's home
    day = manager(parked=True)
    day.on_own(own(zulu_h=15.0))
    assert night.occupancy(APT, 0) > day.occupancy(APT, 0)
    gate = GATES[2]
    m = manager(AtcLink(reserved=lambda: [(gate.lat, gate.lon)]), parked=True)
    m.on_own(own(zulu_h=3.0))
    spawns = parked(m)
    assert spawns and all((s.lat, s.lon) != (gate.lat, gate.lon) for s in spawns)
    assert all("GA_C172" in s.title for s in spawns if (s.lat, s.lon) == (RAMP.lat, RAMP.lon))  # GA on the GA ramp


def test_a_parked_one_on_the_gate_atc_gives_the_user_is_taken_away():
    reserved: list = []
    m = manager(AtcLink(reserved=lambda: reserved), parked=True)
    m.on_own(own(zulu_h=3.0))
    out = []
    for i in range(5):
        tick = m.tick(NOW + i)
        out += tick + assign(m, tick, NOW + i)
    taken = next(p for p in m.planes.values() if p.object_id is not None)
    reserved.append(m._spot_latlon(taken.spot))  # ATC gives the user that gate
    out = m.tick(NOW + 10)
    assert RemoveAiAircraft(object_id=taken.object_id) in out and taken.key not in m.planes


def test_a_real_departure_coming_to_life_at_a_gate_replaces_the_one_parked_there():
    m = manager(parked=True)
    m.on_own(own(zulu_h=3.0))
    for i in range(5):
        tick = m.tick(NOW + i)
        assign(m, tick, NOW + i)
    at = next(p for p in m.planes.values() if not p.live and p.object_id is not None)
    lat, lon = m._spot_latlon(at.spot)
    m.on_feed([flight(lat=lat + 0.00005, lon=lon, ground=True, gs=0.0, t=NOW + 6)], "adsb.lol", NOW + 6)
    out = m.tick(NOW + 6)
    assert RemoveAiAircraft(object_id=at.object_id) in out and of(SpawnAiAircraft, out)


# --- ATC -------------------------------------------------------------------------------------------------------------


class Atc:
    def __init__(self):
        self.runway = None
        self.said = []

    def link(self) -> AtcLink:
        return AtcLink(user_runway=lambda: self.runway, say=lambda cs, kind, icao, rwy: self.said.append((cs, kind, rwy))
                       or [])


def test_an_arrival_on_short_final_goes_around_when_the_user_is_on_the_runway():
    atc = Atc()
    m = manager(atc.link())
    # 2 nm out on final for 09 (west of the threshold, heading east), 700 ft up.
    thr_lon = -1500 / 111_320
    m.on_feed([flight(lat=0.0, lon=thr_lon - 2 * 1852 / 111_320, alt=700, track=90.0)], "adsb.lol", NOW)
    assign(m, m.tick(NOW))
    atc.runway = ("KTST", "09", "on")  # the user lined up on 09
    m.tick(NOW + 1)
    plane = next(iter(m.planes.values()))
    assert plane.mode == "go_around" and atc.said == [("UAL123", "go_around", "09")]
    out = []
    for i in range(30):
        out += m.tick(NOW + 2 + i)
    tracks = of(AiTrack, out)
    assert tracks[-1].alt_ft > tracks[0].alt_ft + 500 and not tracks[-1].on_ground  # climbing away


def test_a_departure_about_to_go_onto_the_runway_holds_short_then_goes_after_the_user():
    atc = Atc()
    m = manager(atc.link())
    # Taxiing north towards the runway's edge, 60 m short of it.
    edge = (22.5 + 60) / 111_320
    m.on_feed([flight(lat=-edge, lon=0.0, ground=True, gs=10.0, track=0.0)], "adsb.lol", NOW)
    assign(m, m.tick(NOW))
    atc.runway = ("KTST", "27", "landing")  # the user cleared to land
    m.tick(NOW + 1)
    plane = next(iter(m.planes.values()))
    assert plane.mode == "hold" and atc.said == [("UAL123", "hold_short", "27")]
    atc.runway = None  # the user's off the runway
    for i in range(25):
        m.tick(NOW + 2 + i)
    assert plane.mode == "departing" and atc.said[-1][1] == "takeoff"
    out = []
    for i in range(80):
        out += m.tick(NOW + 30 + i)
    assert any(not c.on_ground for c in of(AiTrack, out))  # off the ground and away


def test_an_arrival_gone_quiet_on_final_is_landed_and_handed_back_when_heard_again():
    m = manager()
    thr_lon = -1500 / 111_320
    m.on_feed([flight(lat=0.0, lon=thr_lon - 4 * 1852 / 111_320, alt=1400, track=90.0, gs=150)], "adsb.lol", NOW)
    assign(m, m.tick(NOW))
    out = []
    for i in range(1, 140):
        out += m.tick(NOW + i)  # nothing heard any more
    plane = next(iter(m.planes.values()))
    assert plane.mode == "landing" and plane.local.on_ground  # down on 09, not hanging in the air
    assert not of(RemoveAiAircraft, out)
    m.on_feed([flight(lat=0.0, lon=0.005, ground=True, gs=20, track=90.0, t=NOW + 141)], "adsb.lol", NOW + 141)
    m.tick(NOW + 141)
    assert plane.mode == "live"


# --- on and off ------------------------------------------------------------------------------------------------------


def test_switched_off_everything_of_localtcs_goes_and_the_sims_own_traffic_is_noticed():
    m = manager(parked=True)
    m.on_own(own(zulu_h=3.0))
    m.on_feed([flight()], "adsb.lol", NOW)
    for i in range(5):
        assign(m, m.tick(NOW + i), NOW + i)
    ours = {p.object_id for p in m.planes.values() if p.object_id is not None}
    m.on_snapshot(TrafficSnapshot(t=0.0, targets=tuple(
        TrafficTarget(object_id=9000 + i, lat=0.1, lon=0.1, alt_ft=5000, hdg_true=0, gs_kt=250, on_ground=False)
        for i in range(4))))
    status = m.status(NOW)
    assert isinstance(status, TrafficControlStatus) and status.native == 4 and "MSFS's own traffic" in status.note
    out = m.set_on(False)
    assert {c.object_id for c in of(RemoveAiAircraft, out)} == ours and not m.planes
    assert m.tick(NOW + 10) == []


def test_a_busy_source_is_left_alone_and_the_sources_take_turns():
    import urllib.error

    asked = []
    clock = [0.0]

    def fetch(url):
        asked.append(url.split("/")[2])
        if "adsb.lol" in url:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        return {"ac": []}

    feed = LiveFeed(fetch, clock=lambda: clock[0])
    for _ in range(4):
        assert feed.around(1.0, 2.0, 40) == []
    assert asked == ["api.adsb.lol", "opendata.adsb.fi", "opendata.adsb.fi", "opendata.adsb.fi", "opendata.adsb.fi"]
    clock[0] = 200.0  # long after: asked again in its turn
    feed.around(1.0, 2.0, 40)
    feed.around(1.0, 2.0, 40)
    assert "api.adsb.lol" in asked[5:]


def test_the_sims_own_parked_aircraft_are_noticed():
    m = manager()
    m.on_snapshot(TrafficSnapshot(t=0.0, targets=tuple(
        TrafficTarget(object_id=9000 + i, lat=0.006, lon=0.0, alt_ft=100, hdg_true=0, gs_kt=0, on_ground=True)
        for i in range(8))))
    assert "parked aircraft" in m.status(NOW).note
