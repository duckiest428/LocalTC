"""LocalTC's traffic at the gates and on the taxiways: the departures due put at gates their wings fit before they
push, the real one taking over once its transponder's on; an arrival the receivers lost landed, turned off and taxied
to a stand; a stand not swapped for the taxi line and back as the real positions wander; and what stands on a ramp
fitting the airport."""

import math
import random
from pathlib import Path

from localtc.airports import load_airport
from localtc.atc_core.airport.geometry import AirportGeometry
from localtc.atc_core.airport.taxi_route import TaxiGraph
from localtc.ops.schedules import Departure, icao_callsign, local_offset_s
from localtc.sim_api import AiObjectAssigned, OwnshipState, RemoveAiAircraft, SpawnAiAircraft
from localtc.traffic.feed import Flight
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
    assert RemoveAiAircraft(object_id=500) in out and "sched:JAL5" not in m.planes


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
    for _ in range(1500):  # the receivers lose it; LocalTC lands it, then taxies it in
        t += 1.0
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
