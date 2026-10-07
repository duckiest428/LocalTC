"""EXPERIMENTAL traffic control (localtc.traffic): the sim's traffic shadowed, never touched; what the sim drops nearby
put back by LocalTC, once, the same model (FSLTL's when installed), with every safeguard; nothing at all when off."""

import struct

import pytest

from localtc.sim_api import (
    AiObjectAssigned,
    ConnectionStatus,
    EnumerateModels,
    ModelList,
    OwnshipState,
    RemoveAiAircraft,
    SpawnAiAircraft,
    TrafficControlStatus,
    TrafficIdentity,
    TrafficSnapshot,
    TrafficTarget,
)
from localtc.traffic.control import GRACE_S, TrafficControl, flight_plan, fsltl_model


def own(t=0.0, lat=36.08, lon=-115.15):
    return OwnshipState(t=t, lat=lat, lon=lon, alt_msl_ft=2200, alt_indicated_ft=2200, alt_agl_ft=0, altimeter_inhg=29.92,
                        hdg_mag=260, hdg_true=272, ias_kt=0, gs_kt=0, vs_fpm=0, on_ground=True, squawk="1200",
                        xpdr_mode="alt", com1_mhz=118.0, com2_mhz=121.5)


def target(oid, *, lat=36.085, lon=-115.16, alt=2200.0, gs=0.0, ground=True, airline="Southwest", number="3721", hdg=90.0):
    return TrafficTarget(object_id=oid, atc_id="N8721Q", airline=airline, flight_number=number, atc_model="B738",
                         lat=lat, lon=lon, alt_ft=alt, hdg_true=hdg, gs_kt=gs, on_ground=ground)


def snap(t, *targets):
    return TrafficSnapshot(t=t, targets=tuple(targets))


def feed(tc, *events):
    out = []
    for ev in events:
        out += tc.observe(ev)
    return out


def commands(out, kind):
    return [o for o in out if isinstance(o, kind)]


def control(mode="reinject", **kw):
    tc = TrafficControl(mode, **kw)
    tc.observe(own())
    return tc


def test_off_does_nothing_at_all():
    tc = TrafficControl("off")
    assert feed(tc, own(), snap(1, target(1)), snap(20)) == []
    assert tc.start() == []


def test_shadow_follows_the_sims_traffic_and_never_touches_it():
    tc = control("shadow")
    out = feed(tc, snap(1, target(1, gs=12)), TrafficIdentity(t=2, object_id=1, title="Boeing 737-800", origin="KLAS",
                                                            destination="KLAX", state="taxi"))
    s = tc.shadows[1]
    assert s.mode == "shadowed" and s.title == "Boeing 737-800" and s.destination == "KLAX" and s.phase == "taxiing"
    out += feed(tc, snap(3), snap(3 + GRACE_S + 1))  # it vanished close by
    assert not commands(out, SpawnAiAircraft) and not commands(out, RemoveAiAircraft)  # shadow: nothing put back
    status = [o for o in out if isinstance(o, TrafficControlStatus)][-1]
    assert status.mode == "shadow" and status.lost == 1 and "EXPERIMENTAL" in status.note


def test_teleports_callsign_changes_and_duplicates_are_noted():
    tc = control("shadow")
    feed(tc, snap(1, target(1, ground=False, alt=9000, gs=250)))
    feed(tc, snap(2, target(1, lat=37.5, ground=False, alt=9000, gs=250)))  # 85 nm in a second
    assert any("teleported" in i for i in tc.shadows[1].issues)
    feed(tc, snap(3, target(1, lat=37.5, number="9999", ground=False, alt=9000, gs=250)))
    assert any("callsign changed" in i for i in tc.shadows[1].issues)
    feed(tc, snap(4, target(2, number="5"), target(3, number="5", lat=36.09)))
    assert any("two aircraft" in i for i in tc.shadows[2].issues)


def test_a_parked_aircraft_the_sim_drops_is_put_back_once_with_its_model():
    tc = control()
    feed(tc, snap(1, target(1)), TrafficIdentity(t=1.5, object_id=1, title="Boeing 737-800 Asobo", livery="Southwest Heart"))
    out = feed(tc, snap(2), snap(2 + GRACE_S + 0.5))
    [spawn] = commands(out, SpawnAiAircraft)
    assert spawn.kind == "parked" and spawn.title == "Boeing 737-800 Asobo" and spawn.livery == "Southwest Heart"
    assert spawn.lat == pytest.approx(36.085) and spawn.on_ground and spawn.tail == "N8721Q" and spawn.flight_number == 3721
    feed(tc, AiObjectAssigned(t=12, request_id=spawn.request_id, object_id=500))
    assert tc.ours[500].mode == "reinjected"
    out = feed(tc, snap(13, target(500)), snap(14), snap(14 + GRACE_S + 1))  # our copy isn't the sim's, nor lost
    assert not commands(out, SpawnAiAircraft) and 500 not in tc.shadows


def test_it_waits_in_case_the_aircraft_is_back_under_a_new_id():
    tc = control()
    feed(tc, snap(1, target(1)), TrafficIdentity(t=1.5, object_id=1, title="737"))
    out = feed(tc, snap(2), snap(4, target(77)), snap(4 + GRACE_S + 1, target(77)))
    assert not commands(out, SpawnAiAircraft)
    assert tc.shadows[77].callsign == "Southwest 3721" and any("new object id" in i for i in tc.shadows[77].issues)


def test_leaving_the_sims_bubble_is_not_vanishing():
    tc = control(radius_nm=25)
    feed(tc, snap(1, target(1, lat=36.45)), TrafficIdentity(t=1.5, object_id=1, title="737"))  # 22 nm out
    out = feed(tc, snap(2), snap(2 + GRACE_S + 1))
    assert not commands(out, SpawnAiAircraft) and tc.lost == 0


def test_nothing_taxiing_or_landing_is_put_back():
    tc = control()
    feed(tc, snap(1, target(1, gs=15)), TrafficIdentity(t=1.5, object_id=1, title="737"))
    out = feed(tc, snap(2), snap(2 + GRACE_S + 1))
    assert not commands(out, SpawnAiAircraft) and tc.lost == 1  # lost, noted, and left alone


def test_a_flying_one_goes_on_to_its_destination_with_localtcs_runway(tmp_path):
    tc = control(runway_for=lambda icao: "24R" if icao == "KLAX" else None,
                 airport_at=lambda icao: (33.94, -118.40, 125.0) if icao == "KLAX" else None, plan_dir=tmp_path)
    feed(tc, snap(1, target(1, ground=False, alt=11000, gs=280)),
         TrafficIdentity(t=1.5, object_id=1, title="737", destination="KLAX"))
    out = feed(tc, snap(2), snap(2 + GRACE_S + 1))
    [spawn] = commands(out, SpawnAiAircraft)
    assert spawn.kind == "enroute" and spawn.plan.endswith("Southwest3721")
    plan = (tmp_path / "Southwest3721.pln").read_text()
    assert "<DestinationID>KLAX</DestinationID>" in plan and "<RunwayNumberFP>24</RunwayNumberFP>" in plan
    assert "<RunwayDesignatorFP>RIGHT</RunwayDesignatorFP>" in plan


def test_a_flying_one_to_somewhere_localtc_doesnt_know_isnt_put_back(tmp_path):
    tc = control(plan_dir=tmp_path)
    feed(tc, snap(1, target(1, ground=False, alt=11000, gs=280)),
         TrafficIdentity(t=1.5, object_id=1, title="737", destination="KSFO"))
    assert not commands(feed(tc, snap(2), snap(2 + GRACE_S + 1)), SpawnAiAircraft)


def test_fsltl_models_are_used_when_installed():
    models = [("Boeing 737-800 Asobo", "Southwest"), ("FSLTL_B738_SWA", "FSLTL Southwest"), ("FSLTL_B738_AAL", "")]
    assert fsltl_model(models, "B738", "SWA") == ("FSLTL_B738_SWA", "FSLTL Southwest")
    assert fsltl_model(models, "A20N", "SWA") is None
    tc = control()
    assert tc.start() == [EnumerateModels()]
    feed(tc, ModelList(t=0.5, models=tuple(models)))
    feed(tc, snap(1, target(1, airline="SWA")), TrafficIdentity(t=1.5, object_id=1, title="Boeing 737-800 Asobo"))
    [spawn] = commands(feed(tc, snap(2), snap(2 + GRACE_S + 1)), SpawnAiAircraft)
    assert spawn.title.startswith("FSLTL_B738")


def test_safeguards_cap_retry_failure_and_duplicates():
    tc = control(max_reinjected=1)
    feed(tc, snap(1, target(1), target(2, number="1")), TrafficIdentity(t=1.5, object_id=1, title="737"),
         TrafficIdentity(t=1.5, object_id=2, title="737"))
    out = feed(tc, snap(2), snap(2 + GRACE_S + 1))
    assert len(commands(out, SpawnAiAircraft)) == 1  # the cap
    out = feed(tc, snap(40))  # no answer from the sim: failed, not retried
    assert tc.failed == 1 and not tc.pending
    # The sim's own aircraft back with the same callsign as LocalTC's copy: the copy goes.
    tc2 = control()
    feed(tc2, snap(1, target(1)), TrafficIdentity(t=1.5, object_id=1, title="737"))
    [spawn] = commands(feed(tc2, snap(2), snap(2 + GRACE_S + 1)), SpawnAiAircraft)
    feed(tc2, AiObjectAssigned(t=12, request_id=spawn.request_id, object_id=500))
    out = feed(tc2, snap(30, target(500), target(9)))
    assert RemoveAiAircraft(object_id=500) in out and not tc2.ours


def test_turning_it_off_a_disconnect_and_a_replay():
    tc = control()
    feed(tc, snap(1, target(1)), TrafficIdentity(t=1.5, object_id=1, title="737"))
    [spawn] = commands(feed(tc, snap(2), snap(2 + GRACE_S + 1)), SpawnAiAircraft)
    feed(tc, AiObjectAssigned(t=12, request_id=spawn.request_id, object_id=500))
    assert RemoveAiAircraft(object_id=500) in tc.set_mode("shadow", 13)  # back to the sim's traffic alone
    tc.mode = "reinject"
    tc.ours[501] = tc.shadows.get(1) or next(iter(tc.vanished.values()), None) or spawn  # anything
    feed(tc, ConnectionStatus(t=14, connected=False))
    assert not tc.ours and not tc.shadows
    replay = control(live=False)
    assert replay.start() == []
    feed(replay, snap(1, target(1)), TrafficIdentity(t=1.5, object_id=1, title="737"))
    assert not commands(feed(replay, snap(2), snap(2 + GRACE_S + 1)), SpawnAiAircraft)


def test_the_flight_plan_is_one_the_sim_reads():
    from localtc.traffic.control import Shadow

    s = Shadow(object_id=1, callsign="DAL 2543", lat=40.5, lon=-73.5, alt_ft=9000)
    plan = flight_plan(s, (40.64, -73.78, 13.0), "KJFK", "04L")
    assert plan.startswith("<?xml") and "<FlightPlan.FlightPlan>" in plan and "N40° 30' 0.00\"" in plan
    assert "<RunwayNumberFP>4</RunwayNumberFP><RunwayDesignatorFP>LEFT</RunwayDesignatorFP>" in plan


def test_the_bridge_reads_the_sims_answers():
    from localtc.sim_bridge.protocol import FACILITIES_LIST_OFFSET, AssignedObject, ModelLivery, parse_message

    assert parse_message(struct.pack("<IIIII", 20, 6, 12, 7001, 555)) == AssignedObject(request_id=7001, object_id=555)
    pairs = [("FSLTL_B738_SWA", "FSLTL Southwest"), ("Airbus A320neo", "Frontier")]
    payload = b"".join(t.encode().ljust(256, b"\0") + v.encode().ljust(256, b"\0") for t, v in pairs)
    raw = struct.pack("<IIIIIII", FACILITIES_LIST_OFFSET + len(payload), 6, 38, 11, len(pairs), 0, 1) + payload
    msg = parse_message(raw)
    assert isinstance(msg, ModelLivery) and msg.models == tuple(pairs)


def test_with_it_off_the_bridge_asks_nothing_new():
    from localtc.config import Config

    cfg = Config()
    assert cfg.traffic.control == "off" and cfg.live.traffic_identity is False
