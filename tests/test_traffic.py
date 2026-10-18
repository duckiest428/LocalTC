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
    m = TrafficManager(TrafficSettings(**{"parked": False, "live_delay_s": 0.0, "fill_gates": True, **settings}), atc,
                       picker=ModelPicker(MODELS))
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
    assert [c.text for c in of(SetAiVar, out) if c.text] == ["UAL123", "UAL", "123"]  # who it is, for ATC
    assert [(c.name, c.value) for c in of(SetAiVar, out) if not c.text] == [("GEAR HANDLE POSITION", 1.0)]  # its gear too (low, coming in)
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
                       or ["said"])


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
    for i in range(1, 140):  # nothing heard from it any more (the others still are: the feed is working)
        m.on_feed([flight(hex_="far", lat=0.6 + i * 0.001, lon=0.6, t=NOW + i)], "adsb.lol", NOW + i)
        out += m.tick(NOW + i)
    plane = m.planes["live:a1"]
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


def test_held_departures_go_one_at_a_time_the_way_the_user_went_with_one_hold_call():
    """Four held short for the user were all cleared at once, two of them the other way down the runway, eight calls in a
    row on tower."""
    atc = Atc()
    m = manager(atc.link())
    edge = (22.5 + 60) / 111_320
    m.on_feed([flight(hex_=f"h{i}", callsign=f"UAL{10 + i}", lat=-edge, lon=-0.004 + i * 0.004, ground=True, gs=10.0,
                      track=0.0) for i in range(3)], "adsb.lol", NOW)
    assign(m, m.tick(NOW) + m.tick(NOW + 0.5))
    atc.runway = ("KTST", "27", "takeoff")
    m.tick(NOW + 1)
    held = [p for p in m.planes.values() if p.mode == "hold"]
    assert len(held) == 3 and [k for _, k, _ in atc.said] == ["hold_short"]  # one call, not three
    atc.runway = None
    for i in range(200):
        m.tick(NOW + 2 + i)
    takeoffs = [(cs, rwy) for cs, k, rwy in atc.said if k == "takeoff"]
    assert len(takeoffs) == 3 and {rwy for _, rwy in takeoffs} == {"27"}
    released = sorted(p.local.started for p in m.planes.values() if p.mode == "departing")
    assert all(b - a >= 90 for a, b in zip(released, released[1:]))  # spaced as a tower spaces them


def test_one_taxiing_at_the_user_gives_way_and_goes_on_once_clear():
    m = manager()
    # The user stands still on a taxiway; one taxis straight at them from 150 m east.
    m.on_own(own(lat=0.003, lon=0.0, gs=0.0))
    m.on_feed([flight(lat=0.003, lon=150 / 111_320, ground=True, gs=15.0, track=270.0)], "adsb.lol", NOW)
    assign(m, m.tick(NOW))
    m.tick(NOW + 1)
    plane = next(iter(m.planes.values()))
    assert plane.mode == "give_way" and "gave way" in m.recent[-1]
    m.on_own(own(lat=0.003, lon=-400 / 111_320, gs=12.0))  # the user taxied on, well clear
    m.tick(NOW + 30)
    assert plane.mode == "live"


def test_a_new_position_is_eased_in_slowly_never_jumped_to():
    m = manager()
    m.on_feed([flight(lat=0.1, t=NOW)], "adsb.lol", NOW)
    assign(m, m.tick(NOW))
    # The next report puts it 200 m further on than it was carried to: eased over long enough to look natural.
    ahead = 180 * 1852 / 3600 * 6 + 200
    m.on_feed([flight(lat=0.1 - ahead / 111_320, t=NOW + 6)], "adsb.lol", NOW + 6)
    track = of(AiTrack, m.tick(NOW + 6))[-1]
    assert track.blend_s >= 15


def test_the_feed_times_positions_by_the_sources_clock(monkeypatch):
    import localtc.traffic.feed as feed_module

    now = 1_791_600_000.0  # (a real clock: the sources send milliseconds)
    monkeypatch.setattr(feed_module.time, "time", lambda: now)
    data = {"now": (now - 4) * 1000, "ac": [{"hex": "abc", "lat": 1.0, "lon": 2.0, "alt_baro": 3000, "seen_pos": 1.0}]}
    assert parse(data)[0].t == now - 5  # kept 4 s by the source before it was sent: that much older
    assert parse({**data, "now": (now - 4000) * 1000})[0].t == now - 1  # a clock far off: this PC's


def test_the_gates_get_the_airlines_that_fly_there_and_a_few_models():
    from collections import Counter

    from localtc.traffic.airlines import weights
    from localtc.traffic.feed import Route

    assert max(weights("LPPT", Counter(), Counter()).items(), key=lambda kv: kv[1])[0] == "TAP"  # its hub
    assert set(weights("LFMN", Counter(), Counter())) == {"AFR"}  # nothing known: the country's
    live = weights("KLAX", Counter({"QFA": 10}), Counter())
    assert live["UAL"] > 0 and live["QFA"] > 0  # what flies there today, and its based airlines

    models = [(f"FSLTL_B738_{a}-X", "") for a in ("UAL", "DAL", "AAL", "SWA", "ASA", "JBU", "FFT", "NKS", "SCX", "QFA",
                                                  "BAW", "AFR")]
    gates = tuple(ParkingSpot(index=i, name=f"GATE B {i}", kind="gate_medium", lat=0.006 + (i // 10) * 0.001,
                              lon=-0.02 + (i % 10) * 0.002, heading_true=180.0, radius_m=22.0) for i in range(40))
    m = TrafficManager(TrafficSettings(max_parked=40, fill_gates=True), picker=ModelPicker(models),
                       routes=lambda cs: Route("KTST", "KXYZ") if cs.startswith("QFA") else None)
    m.on_airport(Airport(icao="KTST", lat=0.0, lon=0.0, elev_ft=100.0, runways=(RWY,), parking=gates))
    m.on_own(own(zulu_h=8.0))  # and a few live flights out of here
    m.on_feed([flight(hex_=f"q{i}", callsign=f"QFA{i + 1}", lat=0.3) for i in range(5)], "adsb.lol", NOW)
    m.tick(NOW)
    parked = [p for p in m.planes.values() if p.mode == "parked"]
    assert parked and len({p.title for p in parked}) <= 10  # drawn with a few models over and over
    assert any("QFA" in p.title for p in parked)  # the airline that flies here today


def test_tower_tells_the_real_flights_on_its_frequency():
    """Holding one short raised (a slot given as text), and the traffic stopped for the rest of the flight."""
    from pathlib import Path

    import msgspec

    from localtc.airports import load_airport_dir
    from localtc.atc_core.engine import AtcEngine, EngineConfig
    from localtc.replay import Recording
    from localtc.sim_api import AirportData, RadioChatter

    fixtures = Path(__file__).parent / "fixtures"
    engine = AtcEngine(EngineConfig(destination="KBFI", cruise_ft=5000, callsign="N172LT", seed=7, unscripted=False))
    for airport in load_airport_dir(fixtures / "airports"):
        engine.handle(AirportData(t=0.0, airport=airport))
    own_ = next(e for e in Recording(fixtures / "ifr_kpae_kbfi").events() if isinstance(e, OwnshipState))
    tower = engine.facility("tower")
    engine.handle(msgspec.structs.replace(own_, com1_mhz=tower.mhz))
    for i, kind in enumerate(("hold_short", "takeoff", "go_around")):  # each once the last has been said
        lines = engine.traffic_call("UAL531", kind, tower.airport, "16R", own_.t + 1 + 60 * i)
        assert [c.speaker for c in lines if isinstance(c, RadioChatter)][:1] == ["atc"], kind


def test_a_turn_is_rolled_into_and_out_of_with_the_nose_along_the_path():
    """Turning on the heading alone, wings level then a sudden bank, looked robotic."""
    track = Track(1, 0.0, 0.0, 5000.0, 0.0, 250.0, 0.0, 3.0, False, 2.0, 0.0, 0.0, 0.0)  # rate one, right
    banks, hdgs = [], []
    for i in range(1, 121):
        _, _, _, hdg, _, bank = track.at(i * 0.1)
        banks.append(bank)
        hdgs.append(hdg)
    assert banks[0] < 1.0  # not snapped to the bank ...
    assert all(b - a <= 5.0 * 0.1 + 1e-6 for a, b in zip(banks, banks[1:]))  # ... rolled in at a few degrees a second
    assert 15.0 < banks[-1] <= 25.0  # to the bank a rate one turn at 250 kt takes
    assert all(0 <= (b - a) % 360 < 1.0 for a, b in zip(hdgs, hdgs[1:]))  # the nose swinging steadily, the way it turns


def test_one_taxiing_onto_its_stand_stops_on_the_marker_not_in_the_terminal():
    """An arrival carried on past its last report taxied into LAX's terminal."""
    m = manager()
    gate = GATES[1]
    # 80 m south of gate A 2, taxiing north towards it at 8 kt.
    m.on_feed([flight(lat=gate.lat - 80 / 111_320, lon=gate.lon, ground=True, gs=8.0, track=0.0)], "adsb.lol", NOW)
    assign(m, m.tick(NOW))
    m.on_feed([flight(lat=gate.lat - 60 / 111_320, lon=gate.lon, ground=True, gs=6.0, track=0.0, t=NOW + 5)], "adsb.lol",
              NOW + 5)
    out = m.tick(NOW + 5)
    plane = next(iter(m.planes.values()))
    assert plane.spot == ("KTST", 1) and plane.mode == "parked"
    [track] = of(AiTrack, out)
    assert (track.lat, track.lon, track.gs_kt, track.hdg) == (gate.lat, gate.lon, 0.0, gate.heading_true)
    assert track.blend_s >= 60 / 3.0  # rolled onto it slowly
    # Never heard again (its transponder off at the gate): it stays there.
    for i in range(6, 120):
        m.tick(NOW + i)
    assert plane.object_id in m.by_object and plane.mode == "parked"


def test_on_the_ground_it_isnt_carried_on_far_past_its_last_report():
    lat0 = 0.003
    track = Track(1, lat0, 0.0, 100.0, 0.0, 15.0, 0.0, 0.0, True, 0.0, 0.0, 0.0, 0.0)
    lat, *_ = track.at(60.0)
    assert (lat - lat0) * 111_320 < 15 * 1852 / 3600 * 8 + 1  # 8 s at most, not a minute


def test_no_aircraft_are_parked_while_the_user_is_flying():
    """At FL390 they were parked at the fields below, and Paris's 36 came on final: only on the ground, slowed down."""
    m = manager(parked=True)
    m.on_own(own(zulu_h=3.0, on_ground=False, gs=250.0))
    assert not parked(m)
    m.on_own(own(zulu_h=3.0, on_ground=True, gs=90.0))  # the landing roll
    assert not parked(m)
    m.on_own(own(zulu_h=3.0, on_ground=True, gs=15.0))
    spawns = parked(m)
    assert spawns and len(spawns) <= 5  # one a second


# --- shown 30 s behind, the controllers' calls --------------------------------------------------------------------


def test_a_live_one_is_shown_thirty_seconds_behind_between_two_positions():
    m = manager(live_delay_s=30.0)
    m.on_feed([flight(lat=0.05, t=NOW)], "adsb.lol", NOW)
    assert not of(SpawnAiAircraft, m.tick(NOW + 10))  # not heard for 30 s yet: not made
    m.on_feed([flight(lat=0.04, t=NOW + 20)], "adsb.lol", NOW + 20)
    spawn = of(SpawnAiAircraft, m.tick(NOW + 40))[0]
    assert abs(spawn.lat - 0.045) < 1e-6  # half way between the two, where it was ten seconds after the first


def test_the_controllers_tell_a_real_departure_what_it_does_before_it_does_it():
    from localtc.traffic.feed import Route

    calls = []
    link = AtcLink(call=lambda **kw: calls.append((kw["kind"], kw["runway"])) or ["said"])
    m = TrafficManager(TrafficSettings(parked=False, live_delay_s=30.0), link, picker=ModelPicker(MODELS),
                       routes=lambda cs: Route("KTST", "RJTT", "Tokyo"))
    m.on_airport(APT)
    m.on_own(own(lat=0.03))
    gate = GATES[1]
    t = NOW
    for k in range(4):  # at its gate, transponder on
        m.on_feed([flight(lat=gate.lat, lon=gate.lon, ground=True, gs=0.0, t=t + k * 10)], "adsb.lol", t + k * 10)
    assign(m, m.tick(t + 31))
    for k in range(31, 40):
        m.tick(t + k)
    assert calls[:1] == [("clearance", "")]
    # It starts back: the push is called while it's still shown standing.
    m.on_feed([flight(lat=gate.lat - 0.0001, lon=gate.lon, ground=True, gs=3.0, track=0.0, t=t + 40)], "adsb.lol", t + 40)
    plane = next(iter(m.planes.values()))
    for k in range(41, 65):
        m.tick(t + k)
        if ("pushback", "") in calls:
            break
    assert ("pushback", "") in calls
    assert plane.flight.gs_kt < 1  # still shown at the gate: the call comes first


def test_one_on_the_users_runway_is_taken_away():
    atc = Atc()
    m = manager(atc.link())
    m.on_feed([flight(lat=0.0, lon=0.003, ground=True, gs=5.0, track=90.0)], "adsb.lol", NOW)
    assign(m, m.tick(NOW))
    atc.runway = ("KTST", "09", "takeoff")
    out = m.tick(NOW + 1)
    assert of(RemoveAiAircraft, out) and not m.planes


def test_an_arrival_stays_on_its_stand_three_quarters_of_an_hour():
    m = manager()
    gate = GATES[0]
    m.on_feed([flight(lat=gate.lat, lon=gate.lon, ground=True, gs=0.0)], "adsb.lol", NOW)
    assign(m, m.tick(NOW))
    for k in range(1, 200, 10):
        m.tick(NOW + k)  # its transponder off: no more reports
    plane = next(iter(m.planes.values()))
    assert plane.mode == "parked"
    assert not of(RemoveAiAircraft, m.tick(NOW + 40 * 60))
    assert of(RemoveAiAircraft, m.tick(NOW + 50 * 60))


def test_pushed_back_it_keeps_its_nose_and_goes_backwards():
    m = manager()
    m.on_feed([flight(lat=0.01, lon=0.0, ground=True, gs=5.0, track=180.0)], "adsb.lol", NOW)
    assign(m, m.tick(NOW))
    m.on_feed([flight(lat=0.0101, lon=0.0, ground=True, gs=3.0, track=0.0, t=NOW + 5)], "adsb.lol", NOW + 5)
    [track] = of(AiTrack, m.tick(NOW + 5))
    assert track.hdg == 180.0 and track.gs_kt < 0


def test_ground_and_delivery_talk_to_the_real_flights_and_hold_short_says_why():
    from pathlib import Path

    import msgspec

    from localtc.airports import load_airport_dir
    from localtc.atc_core.engine import AtcEngine, EngineConfig
    from localtc.replay import Recording
    from localtc.sim_api import AirportData, RadioChatter

    fixtures = Path(__file__).parent / "fixtures"
    engine = AtcEngine(EngineConfig(destination="KBFI", cruise_ft=5000, callsign="N172LT", seed=7, unscripted=False))
    for airport in load_airport_dir(fixtures / "airports"):
        engine.handle(AirportData(t=0.0, airport=airport))
    own_ = next(e for e in Recording(fixtures / "ifr_kpae_kbfi").events() if isinstance(e, OwnshipState))
    ground = engine.facility("ground")
    engine.handle(msgspec.structs.replace(own_, com1_mhz=ground.mhz))
    lines = engine.traffic_call("UAL531", "pushback", ground.airport, "", own_.t + 1)
    assert [c.speaker for c in lines] == ["atc", "pilot"] and "tail" in lines[0].text
    assert not engine.traffic_call("UAL531", "land", ground.airport, "16R", own_.t + 1)  # tower's, not ground's
    tower = engine.facility("tower")
    engine.handle(msgspec.structs.replace(own_, com1_mhz=tower.mhz, t=own_.t + 60))
    [atc, _] = engine.traffic_call("UAL532", "hold_short", tower.airport, "16R", own_.t + 61)
    assert "traffic departing" in atc.text  # the user's on the ground: departing, not landing


def test_the_chatter_leaves_localtcs_own_aircraft_to_the_traffic():
    from localtc.atc_core.engine import AtcEngine, EngineConfig
    from localtc.sim_api import TrafficTarget

    engine = AtcEngine(EngineConfig(destination="KBFI", callsign="N172LT"))
    target = TrafficTarget(object_id=42, atc_id="UAL1", lat=0.0, lon=0.0, alt_ft=100.0, hdg_true=0.0, gs_kt=0.0,
                           on_ground=True)
    engine.managed_traffic = {42}
    facility = type("F", (), {"controller": "ground"})()
    assert engine._doing(target, None, facility) is None


def test_a_taxiing_one_shown_ahead_of_the_real_one_waits_never_reverses():
    """The reports lag: one shown 40 m further down the taxiway than the next report says slows down and lets the real
    one catch up; eased back along the taxiway it drove backwards."""
    t = Track(1, 0.0, 0.0, 100.0, 90.0, 15.0, 0.0, 0.0, True, 0.0, 0.0, t0=0.0, blend_s=0.0)
    before = t.position(4.0)
    new = Track(1, before[0], before[1] - 40 / 111_320, 100.0, 90.0, 15.0, 0.0, 0.0, True, 0.0, 0.0, t0=4.0, blend_s=5.0)
    t.update(new, 4.0)
    east = [t.position(4.0 + i * 0.5)[1] * 111_320 for i in range(30)]
    assert all(b >= a - 0.01 for a, b in zip(east, east[1:]))  # never west, the way it came
    assert t.gs_kt < 15.0


def test_a_stale_record_resent_doesnt_hold_an_airliner_in_the_sky():
    """A source sending a moving aircraft's last position again with a new time: not a report (it held AAL1138 at
    7,000 ft for 11 minutes, then dived it to where the real one was)."""
    m = manager()
    m.on_feed([flight(t=NOW)], "adsb.fi", NOW)
    m.on_feed([flight(t=NOW + 5)], "adsb.fi", NOW + 5)
    assert m.flights["a1"].t == NOW
    m.on_feed([flight(t=NOW + 10, lat=0.049)], "adsb.fi", NOW + 10)
    assert m.flights["a1"].t == NOW + 10


def test_taking_off_between_two_reports_climbs_from_the_runway():
    m = manager()
    a = flight(t=NOW, ground=True, gs=140.0)
    b = flight(t=NOW + 10, alt=1200.0, gs=170.0, vs=2500.0)
    early, late = m._lift(a, b, 0.01, a), m._lift(a, b, 0.6, b)
    assert early.on_ground and not late.on_ground and late.alt_ft < 1000


def test_a_flight_taken_away_and_made_again_isnt_cleared_twice():
    m = manager()
    m.on_feed([flight(t=NOW)], "adsb.lol", NOW)
    m.tick(NOW)
    plane = next(iter(m.planes.values()))
    plane.said.add("taxi_out")
    m._remove(plane)
    m._create_live(flight(t=NOW + 5), None, NOW + 5)
    assert "taxi_out" in m.planes["live:a1"].said


def test_the_feed_down_isnt_an_arrival_out_of_sight():
    """No source answering: every flight goes quiet at once. None of them is "landed by LocalTC" for it."""
    m = manager()
    thr_lon = -1500 / 111_320
    m.on_feed([flight(lat=0.0, lon=thr_lon - 4 * 1852 / 111_320, alt=1400, track=90.0, gs=150)], "adsb.lol", NOW)
    assign(m, m.tick(NOW))
    for i in range(1, 40):
        m.on_feed(None, "", NOW + i)
        m.tick(NOW + i)
    assert all(p.mode != "landing" for p in m.planes.values())
