"""LocalTC's traffic at the gates and on the taxiways: the departures due put at gates their wings fit before they
push, the real one taking over once its transponder's on; an arrival the receivers lost landed, turned off and taxied
to a stand; a stand not swapped for the taxi line and back as the real positions wander; and what stands on a ramp
fitting the airport."""

import math
from dataclasses import replace
import random
from pathlib import Path

from localtc.airports import load_airport
from localtc.atc_core.airport.geometry import AirportGeometry
from localtc.atc_core.airport.taxi_route import TaxiGraph
from localtc.ops.schedules import Departure, icao_callsign, local_offset_s
from localtc.sim_api import AiObjectAssigned, AiTrack, OwnshipState, RemoveAiAircraft, SpawnAiAircraft
from localtc.sim_api.geo import advance, haversine_nm
from localtc.traffic.feed import Flight, Route
from localtc.traffic.ground import Follower, free_stand, taxi_in_path
from localtc.traffic.manager import BIZJET_TYPES, GA_TYPES, AtcLink, TrafficManager, TrafficSettings
from localtc.traffic.models import ModelPicker

RJTT = load_airport(Path(__file__).parent / "fixtures" / "airports_real" / "RJTT.json")
NOW = 2_000_000_000.0
MODELS = [("FSLTL_FAIB_B788_JAL-Japan_NC", ""), ("FSLTL_A35K_ZZZZ", ""), ("FSLTL_B789_ZZZZ", ""),
          ("FSLTL_A320_ZZZZ", ""), ("FSLTL_C56X_ZZZZ", ""), ("FSLTL_CL60_ZZZZ", ""), ("FSLTL_GA_C172_ZZZ", "")]


def own_at(lat=35.5494, lon=139.7798, ground=True) -> OwnshipState:
    return OwnshipState(t=0.0, lat=lat, lon=lon, alt_msl_ft=20.0, alt_indicated_ft=20.0, alt_agl_ft=0.0,
                        altimeter_inhg=29.92, altimeter_setting_inhg=29.92, hdg_true=0.0, hdg_mag=0.0, ias_kt=0.0,
                        gs_kt=0.0, vs_fpm=0.0, on_ground=ground, squawk="1200", xpdr_mode="alt", com1_mhz=118.0,
                        com2_mhz=121.5, zulu_s=10 * 3600.0)


def manager(calls: list | None = None) -> TrafficManager:
    atc = AtcLink(call=lambda **kw: (calls.append(kw) if calls is not None else None) or [])
    m = TrafficManager(TrafficSettings(parked=False, live_delay_s=0.0), atc, picker=ModelPicker(MODELS))
    m.on_airport(RJTT)
    m.on_own(own_at())
    return m


def test_a_departure_due_waits_at_a_gate_its_wings_fit_then_gives_way_to_the_real_one():
    calls: list = []
    m = manager(calls)
    m.scheduled["RJTT"] = [Departure("JAL5", "JAL", NOW + 30 * 60, destination="KJFK", type="B788", source="test"),
                           Departure("JAL7", "JAL", NOW + 3 * 3600, destination="KORD", type="B788", source="test")]
    out = m.tick(NOW)
    spawns = [c for c in out if isinstance(c, SpawnAiAircraft)]
    assert len(spawns) == 1 and spawns[0].tail == "JAL5"  # the one due within 45 minutes, not the one in three hours
    plane = m.planes["sched:JAL5"]
    spot = RJTT.parking[plane.spot[1]]
    assert spot.kind.startswith("gate") and spot.radius_m + 1 >= 60.1 / 2
    m.on_assigned(AiObjectAssigned(t=0.0, request_id=spawns[0].request_id, object_id=500), NOW)
    m.tick(NOW + 11 * 60)  # 19 minutes before it's due: delivery's clearance
    assert [c["kind"] for c in calls] == ["clearance"] and calls[0]["destination"] == "KJFK"
    real = Flight(hex="8401", callsign="JAL5", registration="", type="B788", category="A5", lat=spot.lat,
                  lon=spot.lon, alt_ft=None, on_ground=True, gs_kt=0.0, track=0.0, vs_fpm=0.0, squawk="", t=NOW + 12 * 60)
    m.on_feed([real], "test", NOW + 12 * 60)
    out = m.tick(NOW + 12 * 60)
    # The same aircraft on the same stand is the real one from here (taken away and made again, it was elsewhere).
    assert RemoveAiAircraft(object_id=500) not in out and "sched:JAL5" not in m.planes
    plane = m.planes["live:8401"]
    assert plane.object_id == 500 and RJTT.parking[plane.spot[1]] is spot and plane.mode == "parked"


def test_a_departure_never_heard_goes_an_hour_after_its_time():
    m = manager()
    m.scheduled["RJTT"] = [Departure("JAL9", "JAL", NOW + 10 * 60, type="A35K")]
    out = m.tick(NOW)
    m.on_assigned(AiObjectAssigned(t=0.0, request_id=[c for c in out if isinstance(c, SpawnAiAircraft)][0].request_id,
                                   object_id=501), NOW)
    assert "sched:JAL9" in m.planes
    assert RemoveAiAircraft(object_id=501) in m.tick(NOW + 75 * 60) and "sched:JAL9" not in m.planes


def test_nothing_is_scheduled_while_flying():
    m = manager()
    m.on_own(own_at(ground=False))
    m.scheduled["RJTT"] = [Departure("JAL5", "JAL", NOW + 600, type="B788")]
    assert not [c for c in m.tick(NOW) if isinstance(c, SpawnAiAircraft)]


def test_schedules_by_their_codes_and_times():
    assert icao_callsign("BA7") == "BAW7" and icao_callsign("JL 005") == "JAL5" and icao_callsign("ZZ1") == ""
    # The first departure of the next hours, 09:05 local with 00:00Z now: nine hours ahead (Tokyo).
    assert local_offset_s([9 * 3600 + 300, 9 * 3600 + 900], 0.0, 139.8) == 9 * 3600
    assert local_offset_s([], 0.0, -0.46) == 0.0
    assert local_offset_s([15 * 3600.0], 0.0, -0.46) == 0.0  # a quiet field's afternoon flight: the longitude's


def test_an_arrival_lost_on_the_runway_turns_off_and_taxies_to_a_stand():
    geo = AirportGeometry(RJTT)
    graph = TaxiGraph(geo)
    runway = next(r for r in geo.runways if r.name == "22/04")
    end = next(e for e in runway.ends if e.ident == "04")
    # 600 m past the threshold of 04, rolling out.
    u = (math.sin(math.radians(end.heading_true)), math.cos(math.radians(end.heading_true)))
    lat, lon = geo.frame.to_latlon(end.threshold[0] + u[0] * 600, end.threshold[1] + u[1] * 600)
    spot = free_stand(geo, [], 60.1, [], seed="JAL5")
    points, exit_index = taxi_in_path(geo, graph, lat, lon, end.heading_true, spot)
    follower = Follower(points, gs_kt=60.0, exit_index=exit_index, spot=spot)
    for _ in range(3000):
        lat, lon, hdg, gs = follower.step(lat, lon)
        if follower.done:
            break
    stand = RJTT.parking[spot]
    assert follower.done and math.hypot(lat - stand.lat, lon - stand.lon) < 1e-6
    assert not runway.contains(geo.xy(*points[3]))  # off the runway by the third point


def test_the_manager_rolls_a_lost_arrival_in_to_a_stand():
    m = manager()
    geo = AirportGeometry(RJTT)
    end = geo.end("04")
    u = (math.sin(math.radians(end.heading_true)), math.cos(math.radians(end.heading_true)))
    lat, lon = geo.frame.to_latlon(end.threshold[0] - u[0] * 3000, end.threshold[1] - u[1] * 3000)
    seen = Flight(hex="85", callsign="JAL501", registration="", type="B788", category="A5", lat=lat, lon=lon,
                  alt_ft=1000.0, on_ground=False, gs_kt=140.0, track=end.heading_true, vs_fpm=-700.0, squawk="", t=NOW)
    m.on_feed([seen], "test", NOW)
    out = m.tick(NOW)
    m.on_assigned(AiObjectAssigned(t=0.0, request_id=[c for c in out if isinstance(c, SpawnAiAircraft)][0].request_id,
                                   object_id=600), NOW)
    t = NOW
    other = Flight(hex="99", callsign="ANA1", registration="", type="B788", category="A5", lat=lat + 0.3, lon=lon,
                   alt_ft=9000.0, on_ground=False, gs_kt=250.0, track=0.0, vs_fpm=0.0, squawk="", t=NOW)
    for _ in range(1500):  # the receivers lose it (still hearing the others); LocalTC lands it, then taxies it in
        t += 1.0
        m.on_feed([replace(other, t=t, lat=other.lat + (t - NOW) * 0.001)], "test", t)
        m.tick(t)
        plane = m.planes.get("live:85")
        if plane is None or plane.mode == "parked":
            break
    assert plane is not None and plane.mode == "parked" and plane.spot is not None


def test_a_stand_isnt_swapped_for_the_taxi_line_as_the_positions_wander():
    m = manager()
    stand = next(p for p in RJTT.parking if p.kind.startswith("gate"))
    spot = ("RJTT", stand.index)
    from localtc.traffic.manager import Plane

    plane = Plane("live:9", 1, "FSLTL_A320_ZZZZ", "", callsign="JAL1", spot=spot, object_id=7)
    near = Flight(hex="9", callsign="JAL1", registration="", type="A320", category="A3", lat=stand.lat + 0.0001,
                  lon=stand.lon, alt_ft=None, on_ground=True, gs_kt=1.6, track=0.0, vs_fpm=0.0, squawk="", t=NOW)
    plane.flight = near
    assert m._on_spot(plane)  # 11 m off it at 1.6 kt: still on its stand
    plane.flight = Flight(**{**near.__dict__, "lat": stand.lat + 0.0004, "gs_kt": 3.0})
    assert not m._on_spot(plane) and plane.spot is None  # 45 m off, moving: pushed back


def test_a_hubs_ga_ramp_has_business_jets_not_cessnas():
    m = manager()
    m._gates_here = 120
    rng = random.Random(1)
    model = m._parked_model("ramp_ga_large", 15.0, {}, rng)
    assert any(t in model[0] for t in BIZJET_TYPES)
    m._gates_here = 2
    assert any(t in m._parked_model("ramp_ga_small", 8.0, {}, random.Random(1))[0] for t in GA_TYPES)


def test_a_large_ramp_at_an_airline_airport_is_a_remote_airliner_stand():
    """Haneda's scenery calls its 157 remote stands RAMP_GA_LARGE: they got light aircraft. An airliner of the stand's
    size, of the airport's airlines, now."""
    from collections import Counter

    from localtc.traffic.manager import TYPES_BY_SIZE

    m = manager()
    m._gates_here = 63
    model = m._parked_model("ramp_ga_large", 32.0, Counter({"JAL": 5, "ANA": 5}), random.Random(3))
    assert model is not None and any(t in model[0] for t in TYPES_BY_SIZE["heavy"]), model
    small = m._parked_model("ramp_ga_small", 8.0, Counter(), random.Random(3))
    assert small is None or not any(t in small[0] for t in ("C172", "DA62", "DA40", "PA28"))


def test_a_parked_airliner_is_always_in_an_airlines_colours():
    """No airline of the airport's installed in that size: another airline's colours, never FSLTL's white one."""
    from collections import Counter

    m = manager()
    m._gates_here = 63
    for seed in range(10):
        model = m._parked_model("gate_heavy", 32.0, Counter({"ANA": 5}), random.Random(seed))
        assert model is not None and "ZZZ" not in model[0], model


# --- the ground is LocalTC's: the live data only says when -------------------------------------------------------------


def real(hex_: str, callsign: str, lat: float, lon: float, *, gs: float = 0.0, track: float = 0.0, t: float = NOW,
         ground: bool = True, alt: float | None = None) -> Flight:
    return Flight(hex=hex_, callsign=callsign, registration="", type="A320", category="A3", lat=lat, lon=lon,
                  alt_ft=None if ground else alt, on_ground=ground, gs_kt=gs, track=track,
                  vs_fpm=0.0 if ground else -700.0, squawk="", t=t)


def made_at(out) -> list[tuple[float, float]]:
    return [(round(c.lat, 6), round(c.lon, 6)) for c in out if isinstance(c, SpawnAiAircraft)]


def test_a_real_one_on_the_ground_is_made_on_a_stand_never_where_its_reported():
    """O'Hare: a SkyWest made where it was reported, rolling on the user's runway, and tower stopped the user's takeoff
    for it; departures jumped about the apron. On the ground LocalTC picks: a stand, never the reported spot."""
    m = manager()
    m.routes = lambda cs: Route("RJTT", "RJCC") if cs == "ANA1" else None
    geo = AirportGeometry(RJTT)
    end = geo.end("34R")
    on_runway = geo.frame.to_latlon(*end.threshold)
    gates = [p for p in RJTT.parking if p.kind.startswith("gate")]
    feed = [real("01", "JAL9", *on_runway, gs=40.0, track=end.heading_true),  # rolling on 34R
            real("02", "ANA2", gates[0].lat + 0.0007, gates[0].lon),  # standing 80 m off a stand
            real("03", "ANA1", gates[5].lat - 0.001, gates[5].lon, gs=12.0)]  # taxiing out (bound for Sapporo)
    out = []
    for k in range(3):
        m.on_feed([replace(f, t=NOW + k) for f in feed], "test", NOW + k)
        out += m.tick(NOW + k)
    stands = {(round(p.lat, 6), round(p.lon, 6)) for p in RJTT.parking}
    assert "live:01" not in m.planes  # rolling on a runway: never made there
    assert len(made_at(out)) == 2 and set(made_at(out)) <= stands
    assert m.planes["live:03"].push_due and m.planes["live:02"].mode == "parked"


def test_a_departures_push_and_taxi_are_localtcs_and_it_goes_once_the_real_one_has():
    calls: list = []
    m = manager(calls)
    m.atc.runway_in_use = lambda icao: "34R"
    m.atc.call = lambda **kw: calls.append(kw) or ["said"]  # (said: the frequency was free)
    said: list = []
    m.atc.say = lambda cs, kind, icao, rwy: said.append((kind, rwy)) or ["said"]
    m.routes = lambda cs: Route("RJTT", "RJCC", "Sapporo")
    m.on_own(own_at(35.58, 139.80))  # the user off on the north side, out of the way
    gate = next(p for p in RJTT.parking if p.kind.startswith("gate"))
    t = NOW
    m.on_feed([real("05", "ANA5", gate.lat, gate.lon, t=t)], "test", t)
    for c in [c for c in m.tick(t) if isinstance(c, SpawnAiAircraft)]:
        m.on_assigned(AiObjectAssigned(t=0.0, request_id=c.request_id, object_id=700), t)
    plane = m.planes["live:05"]
    m.tick(t + 0.5)
    assert plane.mode == "parked" and [c["kind"] for c in calls] == ["clearance"]
    pushing, stages = [], set()
    for k in range(1, 2500):  # the real one pushes, taxies out, and is still on the ground
        t = NOW + k
        m.on_feed([real("05", "ANA5", gate.lat - 0.0002, gate.lon, gs=4.0, t=t)], "test", t)
        out = m.tick(t)
        if plane.mode == "taxi_out":
            stages.add(plane.local.stage)
            if plane.local.stage == "push":
                pushing += [c for c in out if isinstance(c, AiTrack)]
            if plane.local.stage == "hold_short":
                break
    assert {"push", "start", "taxi", "hold_short"} <= stages
    assert pushing and all(c.gs_kt < 0 for c in pushing)  # pushed: backwards, the nose the other way
    assert [c["kind"] for c in calls] == ["clearance", "pushback", "taxi_out"] and calls[-1]["runway"] == "34R"
    for k in range(5):  # at the hold short, the real one still on the ground: it waits
        t += 1
        m.on_feed([real("05", "ANA5", gate.lat, gate.lon, gs=4.0, t=t)], "test", t)
        m.tick(t)
    assert plane.local.stage == "hold_short"
    m.atc.user_runway = lambda: ("RJTT", "34R", "landing")  # the user cleared to land on it
    for k in range(5):
        t += 1
        m.on_feed([real("05", "ANA5", 35.50, 139.70, ground=False, alt=3000.0, track=340.0, t=t)], "test", t)
        m.tick(t)
    assert plane.local.stage == "hold_short"  # the real one's gone, but the user has the runway
    m.atc.user_runway = lambda: None
    for k in range(40):
        t += 1
        m.on_feed([real("05", "ANA5", 35.50, 139.70, ground=False, alt=3000.0, track=340.0, t=t)], "test", t)
        m.tick(t)
    assert plane.mode == "departing" and said[-1] == ("takeoff", "34R")


def test_taxiing_out_it_waits_for_the_user_in_its_way():
    m = manager()
    m.atc.runway_in_use = lambda icao: "34R"
    m.routes = lambda cs: Route("RJTT", "RJCC")
    m.on_own(own_at(35.58, 139.80))
    gate = next(p for p in RJTT.parking if p.kind.startswith("gate"))
    m.on_feed([real("06", "ANA6", gate.lat, gate.lon)], "test", NOW)
    for c in [c for c in m.tick(NOW) if isinstance(c, SpawnAiAircraft)]:
        m.on_assigned(AiObjectAssigned(t=0.0, request_id=c.request_id, object_id=701), NOW)
    plane = m.planes["live:06"]
    t = NOW
    while plane.mode != "taxi_out" or plane.local.stage != "taxi" or plane.local.follow.gs_kt < 10:
        t += 1
        m.on_feed([real("06", "ANA6", gate.lat - 0.0003, gate.lon, gs=4.0, t=t)], "test", t)
        m.tick(t)
        assert t < NOW + 600
    loc = plane.local
    ahead = advance(loc.lat, loc.lon, loc.hdg, 15.0, 0.0, 12.0)  # the user stopped 90 m ahead of it
    m.on_own(own_at(ahead[0], ahead[1]))
    speeds = []
    for k in range(1, 20):
        m.tick(t + k)
        speeds.append(loc.follow.gs_kt)
    # Braked to a stop, not stopped dead ("this plane in front of me stopping so suddenly"), and short of the user.
    assert speeds[0] > 0 and speeds[-1] == 0.0 and all(b <= a for a, b in zip(speeds, speeds[1:]))
    assert haversine_nm(loc.lat, loc.lon, ahead[0], ahead[1]) * 1852 > 30


def test_an_arrival_low_on_final_is_landed_and_taxied_in_by_localtc():
    m = manager()
    geo = AirportGeometry(RJTT)
    end = geo.end("04")
    u = (math.sin(math.radians(end.heading_true)), math.cos(math.radians(end.heading_true)))

    def on_final(nm: float, t: float) -> Flight:
        lat, lon = geo.frame.to_latlon(end.threshold[0] - u[0] * nm * 1852, end.threshold[1] - u[1] * nm * 1852)
        return real("07", "JAL7", lat, lon, ground=False, alt=30 + nm * 318, track=end.heading_true, t=t)

    t = NOW
    m.on_feed([on_final(6.0, t)], "test", t)
    for c in [c for c in m.tick(t) if isinstance(c, SpawnAiAircraft)]:
        m.on_assigned(AiObjectAssigned(t=0.0, request_id=c.request_id, object_id=702), t)
    plane = m.planes["live:07"]
    for k in range(1, 200):
        t = NOW + k
        m.on_feed([on_final(max(0.0, 6.0 - k * 140 / 3600), t)], "test", t)
        m.tick(t)
        if plane.mode == "landing":
            break
    assert plane.mode == "landing" and plane.local.owned  # low on final: LocalTC's from here
    for k in range(1, 1500):  # the real one heard again rolling out and taxiing: LocalTC's own way all the same
        t += 1
        m.on_feed([real("07", "JAL7", 35.548, 139.79, gs=15.0, t=t)], "test", t)
        m.tick(t)
        if plane.mode == "parked":
            break
    assert plane.mode == "parked" and plane.spot is not None
    assert RJTT.parking[plane.spot[1]].kind.startswith("gate")


def test_taxiing_out_it_lines_up_behind_the_one_ahead():
    """ "The aircraft ahead of me are all bundled up together, they should be lined up instead": each of LocalTC's
    departures gave way to the user only, so they all ran onto the same hold short. Now one waits behind another."""
    from localtc.traffic.manager import QUEUE_GAP_M, QUEUE_M, Local, Plane

    m = manager()
    m.atc.runway_in_use = lambda icao: "34R"
    m.routes = lambda cs: Route("RJTT", "RJCC")
    m.on_own(own_at(35.58, 139.80))
    gate = next(p for p in RJTT.parking if p.kind.startswith("gate"))
    m.on_feed([real("07", "ANA7", gate.lat, gate.lon)], "test", NOW)
    for c in [c for c in m.tick(NOW) if isinstance(c, SpawnAiAircraft)]:
        m.on_assigned(AiObjectAssigned(t=0.0, request_id=c.request_id, object_id=702), NOW)
    plane = m.planes["live:07"]
    t = NOW
    while plane.mode != "taxi_out" or plane.local.stage != "taxi" or plane.local.follow.gs_kt < 10:
        t += 1
        m.on_feed([real("07", "ANA7", gate.lat - 0.0003, gate.lon, gs=4.0, t=t)], "test", t)
        m.tick(t)
        assert t < NOW + 600
    loc = plane.local
    lat, lon, _ = advance(loc.lat, loc.lon, loc.hdg, (QUEUE_M - 5) / 0.5144, 0.0, 1.0)  # stopped just ahead of it
    ahead = Plane("live:99", 9, "FSLTL_A320_ZZZZ", "", callsign="ANA9", object_id=703, mode="taxi_out",
                  local=Local(lat, lon, 20.0, loc.hdg, 0.0, 0.0, True, started=t, runway=("RJTT", "34R"),
                              stage="hold_short"))
    m.planes[ahead.key] = ahead
    m._released_t = t + 1e6  # held there (another just went)
    for k in range(1, 20):
        m.on_feed([real("07", "ANA7", gate.lat, gate.lon, gs=4.0, t=t + k)], "test", t + k)
        m.tick(t + k)
    gap = haversine_nm(loc.lat, loc.lon, lat, lon) * 1852
    assert loc.follow.gs_kt == 0.0 and QUEUE_GAP_M - 10 <= gap < QUEUE_M  # stopped in line, a gap behind it
    before = (loc.lat, loc.lon)
    t += 20
    ahead.local.hdg = (loc.hdg + 180) % 360  # one coming the other way isn't one to queue behind
    for k in range(2):
        m.on_feed([real("07", "ANA7", gate.lat, gate.lon, gs=4.0, t=t + k)], "test", t + k)
        m.tick(t + k)
    assert (loc.lat, loc.lon) != before


def _parked_at_gate(hex_: str, callsign: str, route: Route):
    m = manager()
    m.atc.runway_in_use = lambda icao: "34R"
    m.routes = lambda cs: route
    m.on_own(own_at(35.58, 139.80))
    gate = next(p for p in RJTT.parking if p.kind.startswith("gate"))
    m.on_feed([real(hex_, callsign, gate.lat, gate.lon)], "test", NOW)
    for c in [c for c in m.tick(NOW) if isinstance(c, SpawnAiAircraft)]:
        m.on_assigned(AiObjectAssigned(t=0.0, request_id=c.request_id, object_id=710), NOW)
    return m, gate, m.planes[f"live:{hex_}"]


def test_a_position_wandering_at_the_gate_is_not_a_pushback():
    m, gate, plane = _parked_at_gate("08", "ANA8", Route("RJTT", "RJCC"))
    for k in range(1, 120):  # the receivers' few metres and a knot or two at a gate
        m.on_feed([real("08", "ANA8", gate.lat + 0.00004 * (k % 2), gate.lon, gs=3.0, t=NOW + k)], "test", NOW + k)
        m.tick(NOW + k)
    assert plane.mode == "parked"


def test_an_arrival_still_moving_in_isnt_pushed_back():
    m, gate, plane = _parked_at_gate("09", "ANA9", Route("RJCC", "RJTT"))  # it came in here
    for k in range(1, 120):
        m.on_feed([real("09", "ANA9", gate.lat - 0.000005 * k, gate.lon, gs=5.0, t=NOW + k)], "test", NOW + k)
        m.tick(NOW + k)
    assert plane.mode == "parked"


def test_a_turn_is_taken_slower_than_the_straight():
    from localtc.traffic.ground import Follower, _toward

    a = (47.45, -122.30)
    b = _toward(a, 0, 400)
    follower = Follower([a, b, _toward(b, 90, 300), _toward(_toward(b, 90, 300), 90, 10)], gs_kt=15.0, exit_index=-1)
    lat, lon, speeds = *a, []
    while not follower.done and len(speeds) < 300:
        lat, lon, _, gs = follower.step(lat, lon)
        speeds.append(gs)
    assert max(speeds[:20]) >= 14 and min(speeds[20:60]) <= 12  # slowed for the corner, in good time


def test_at_the_holding_point_it_goes_in_its_turn_when_the_real_one_is_slow():
    """Each waited there for its real one to take off: one whose real one was slow held the line behind it (the user
    ten minutes in it). It waits a while, then goes, and the real one isn't made again on the runway after it."""
    from localtc.traffic.manager import HOLD_SHORT_MAX_S, Local, Plane

    m = manager()
    geo = AirportGeometry(RJTT)
    end = geo.end("34R")
    lat, lon = geo.frame.to_latlon(*end.threshold)
    gate = next(p for p in RJTT.parking if p.kind.startswith("gate"))
    plane = Plane("live:08", 8, "FSLTL_A320_ZZZZ", "", callsign="ANA8", object_id=704, mode="taxi_out",
                  local=Local(lat, lon, 20.0, end.heading_true, 0.0, 0.0, True, started=NOW, runway=("RJTT", "34R"),
                              stage="hold_short", stage_t=NOW), runway=("RJTT", "34R"))
    m.planes[plane.key] = plane
    m._released_t = 0.0
    m.on_feed([real("08", "ANA8", gate.lat, gate.lon, t=NOW)], "test", NOW)  # the real one still at its gate
    m.tick(NOW + 5)
    assert plane.mode == "taxi_out"  # waits for it a while
    t = NOW + HOLD_SHORT_MAX_S + 1
    m.on_feed([real("08", "ANA8", gate.lat, gate.lon, t=t)], "test", t)
    m.tick(t)
    assert plane.mode == "departing" and m._kept_off.get("live:08", 0.0) > t


def test_one_hanging_in_the_air_is_taken_away():
    """Reported flying at 4,100 ft south of Seattle, in the same place report after report: it stood there eight
    minutes."""
    from localtc.traffic.manager import FROZEN_AIR_S

    m = manager()
    m.on_feed([real("09", "ANA9", 35.40, 139.80, gs=250.0, ground=False, alt=4000.0, track=340.0, t=NOW)], "test", NOW)
    for c in [c for c in m.tick(NOW) if isinstance(c, SpawnAiAircraft)]:
        m.on_assigned(AiObjectAssigned(t=0.0, request_id=c.request_id, object_id=705), NOW)
    out = []
    for dt in range(5, int(FROZEN_AIR_S) + 15, 5):
        m.on_feed([real("09", "ANA9", 35.40, 139.80, gs=250.0, ground=False, alt=4000.0, track=340.0, t=NOW + dt)], "test", NOW + dt)
        out += m.tick(NOW + dt)
    assert "live:09" not in m.planes and any(isinstance(c, RemoveAiAircraft) for c in out)


def test_an_arrival_reported_moving_is_never_taken_away_for_standing_still():
    """Hundreds of real arrivals were taken away "standing still in the air": judged by what LocalTC had sent, not
    by where they were reported."""
    from localtc.traffic.manager import FROZEN_AIR_S

    m = manager()
    m.on_feed([real("10", "ANA10", 35.40, 139.80, gs=250.0, ground=False, alt=4000.0, track=340.0, t=NOW)], "test", NOW)
    for c in [c for c in m.tick(NOW) if isinstance(c, SpawnAiAircraft)]:
        m.on_assigned(AiObjectAssigned(t=0.0, request_id=c.request_id, object_id=706), NOW)
    for dt in range(5, int(FROZEN_AIR_S) * 3, 5):
        lat = 35.40 + dt * 0.0006  # about 250 kt north
        m.on_feed([real("10", "ANA10", lat, 139.80, gs=250.0, ground=False, alt=4000.0, track=0.0, t=NOW + dt)], "test", NOW + dt)
        m.tick(NOW + dt)
    assert "live:10" in m.planes


def test_a_line_goes_round_one_that_never_moves():
    """A Delta stood across the taxiway at the end of O'Hare's line held the whole line behind it."""
    from localtc.traffic.manager import QUEUE_M, QUEUE_STUCK_S, Local, Plane

    m = manager()
    m.atc.runway_in_use = lambda icao: "34R"
    m.routes = lambda cs: Route("RJTT", "RJCC")
    m.on_own(own_at(35.58, 139.80))
    gate = next(p for p in RJTT.parking if p.kind.startswith("gate"))
    m.on_feed([real("07", "ANA7", gate.lat, gate.lon)], "test", NOW)
    for c in [c for c in m.tick(NOW) if isinstance(c, SpawnAiAircraft)]:
        m.on_assigned(AiObjectAssigned(t=0.0, request_id=c.request_id, object_id=702), NOW)
    plane = m.planes["live:07"]
    t = NOW
    while plane.mode != "taxi_out" or plane.local.stage != "taxi" or plane.local.follow.gs_kt < 10:
        t += 1
        m.on_feed([real("07", "ANA7", gate.lat - 0.0003, gate.lon, gs=4.0, t=t)], "test", t)
        m.tick(t)
        assert t < NOW + 600
    loc = plane.local
    lat, lon, _ = advance(loc.lat, loc.lon, loc.hdg, (QUEUE_M - 5) / 0.5144, 0.0, 1.0)
    ahead = Plane("live:99", 9, "FSLTL_A320_ZZZZ", "", callsign="DAL9", object_id=703, mode="taxi_out",
                  local=Local(lat, lon, 20.0, loc.hdg, 0.0, 0.0, True, started=t, runway=("RJTT", "34R"),
                              stage="hold_short"))
    m.planes[ahead.key] = ahead
    m._released_t = t + 1e6
    for k in range(1, int(QUEUE_STUCK_S) + 20):
        m.on_feed([real("07", "ANA7", gate.lat, gate.lon, gs=4.0, t=t + k)], "test", t + k)
        m.tick(t + k)
        if k == 30:
            assert loc.follow.gs_kt == 0.0  # in line behind it
    assert loc.follow.gs_kt > 0.0  # stood there two minutes: gone round
