"""Phase detection: transitions, dwell times, noise, and a full synthetic IFR flight."""

import pytest
from helpers.airports import kbfi, kpae
from helpers.flightgen import ifr_kpae_kbfi

from localtc.atc_core.airport import AirportGeometry, TaxiGraph, select_runway
from localtc.atc_core.phase import ContextBuilder, FlightPhase, PhaseDetector, PhaseThresholds, PhaseTracker
from localtc.sim_api import OwnshipState, SimLifecycle
from localtc.sim_api.geo import METERS_PER_NM

P = FlightPhase


def own(t: float, **kw) -> OwnshipState:
    fields = dict(
        lat=47.9, lon=-122.28, alt_msl_ft=606.0, alt_indicated_ft=606.0, alt_agl_ft=0.0, altimeter_inhg=29.92,
        hdg_mag=324.0, hdg_true=340.0, ias_kt=0.0, gs_kt=0.0, vs_fpm=0.0, on_ground=True, squawk="1200",
        xpdr_mode="alt", com1_mhz=121.8, com2_mhz=121.5, engine_running=True,
    )
    fields.update(kw)
    return OwnshipState(t=t, **fields)


def run(detector: PhaseDetector, ticks) -> list[tuple[float, str]]:
    builder = ContextBuilder()  # no airports: context comes from the ownship flags alone
    changes = []
    for tick in ticks:
        change = detector.update(tick, builder.build(tick))
        if change:
            changes.append((change.t, change.phase))
    return changes


def test_full_ifr_flight_phase_timeline():
    tracker = PhaseTracker(destination="KBFI", cruise_ft=5000)
    changes = [c for ev in ifr_kpae_kbfi(kpae(), kbfi()).sorted_events() if (c := tracker.handle(ev))]
    assert [c.phase for c in changes] == [
        "PARKED", "TAXI_OUT", "RUNWAY_HOLD", "TAKEOFF", "DEPARTURE", "CRUISE", "ARRIVAL", "APPROACH", "LANDING",
        "TAXI_IN", "PARKED",
    ]
    reasons = {c.phase: c.reason for c in changes}
    assert reasons["RUNWAY_HOLD"] == "holding short"
    assert reasons["LANDING"] == "short final runway 14R"
    assert all(b.t > a.t for a, b in zip(changes, changes[1:]))


def test_taxi_needs_dwell_and_ignores_single_tick_noise():
    det = PhaseDetector()
    ticks = [own(0), own(1, gs_kt=6), own(2), own(3), own(4, gs_kt=5), own(5, gs_kt=5), own(6, gs_kt=5)]
    assert run(det, ticks) == [(0, P.PARKED), (6, P.TAXI_OUT)]


def test_takeoff_without_airport_data_uses_sim_runway_flag():
    det = PhaseDetector()
    ticks = [own(0, gs_kt=10), own(1, gs_kt=35, on_runway=True), own(2, gs_kt=40, on_runway=True)]
    ticks += [own(3 + i, gs_kt=60, on_ground=False, alt_agl_ft=30 + 20 * i, vs_fpm=700) for i in range(4)]
    assert [p for _, p in run(det, ticks)] == [P.TAXI_OUT, P.TAKEOFF, P.DEPARTURE]


def test_rejected_takeoff_returns_to_runway_hold():
    det = PhaseDetector()
    ticks = [own(0, gs_kt=10), own(1, gs_kt=35, on_runway=True), own(2, gs_kt=45, on_runway=True)]
    ticks += [own(3, gs_kt=12, on_runway=True)]
    assert [p for _, p in run(det, ticks)] == [P.TAXI_OUT, P.TAKEOFF, P.RUNWAY_HOLD]


def test_go_around_from_landing():
    det = PhaseDetector()
    det.phase = P.LANDING
    det._min_agl_in_phase = 400
    ticks = [own(t, on_ground=False, alt_agl_ft=agl, vs_fpm=vs) for t, agl, vs in [
        (0, 250, -400), (1, 200, 0), (2, 230, 900), (3, 270, 900), (4, 320, 900), (5, 360, 900)]]
    assert run(det, ticks) == [(5, P.DEPARTURE)]


def test_teleport_reclassifies():
    det = PhaseDetector()
    far = own(1, lat=48.5, on_ground=False, alt_agl_ft=5000, vs_fpm=0)
    assert run(det, [own(0), far]) == [(0, P.PARKED), (1, P.CRUISE)]


def test_flight_loaded_resets_phase():
    tracker = PhaseTracker()
    tracker.handle(own(0))
    tracker.handle(SimLifecycle(t=1, kind="flight_loaded"))
    change = tracker.handle(own(2, gs_kt=20))
    assert change is not None and change.phase == "TAXI_OUT" and change.reason == "initial state"


def test_thresholds_are_configurable():
    det = PhaseDetector(PhaseThresholds(taxi_start_s=0))
    assert run(det, [own(0), own(1, gs_kt=5)]) == [(0, P.PARKED), (1, P.TAXI_OUT)]


# --- geometry & routing ------------------------------------------------------------


def test_runway_ends_hold_shorts_and_selection():
    geo = AirportGeometry(kpae())
    assert [(e.ident, e.heading_true) for e in geo.ends] == [("16R", 160.0), ("34L", 340.0)]
    assert {h.point.index: h.end.ident for h in geo.hold_shorts} == {3: "34L", 6: "16R", 8: "34L"}
    assert select_runway(geo, 330, 10).ident == "34L"
    assert select_runway(geo, 150, 10).ident == "16R"
    assert select_runway(geo, 150, 2).ident == "34L"  # calm: the ILS end


def test_runway_polygon_and_final_approach():
    geo = AirportGeometry(kbfi())
    center = geo.frame.to_latlon(*geo.runways[0].center)
    assert geo.runway_at(*center) is not None
    end = geo.end("14R")
    ux, uy = 0.6427876, -0.7660444  # unit vector for 140°
    lat, lon = geo.frame.to_latlon(end.threshold[0] - ux * 5 * METERS_PER_NM, end.threshold[1] - uy * 5 * METERS_PER_NM)
    final = geo.final_approach(lat, lon, 142)
    assert final.end.ident == "14R" and final.distance_nm == pytest.approx(5, abs=0.01)
    assert geo.final_approach(lat, lon, 320) is None  # wrong direction
    assert geo.final_approach(*center, 140) is None  # past the threshold


def test_taxi_routes():
    geo = AirportGeometry(kpae())
    graph = TaxiGraph(geo)
    spot = kpae().parking[0]
    route = graph.departure_route(spot.lat, spot.lon, geo.end("34L"))
    assert (route.via, route.hold_short, route.crossings) == ("C, A, A1", "34L", ())
    north = graph.departure_route(spot.lat, spot.lon, geo.end("16R"))
    assert north.via == "C, A, A3"
    mid = geo.frame.to_latlon(*geo.runways[0].center)
    back = graph.parking_route(*mid)
    assert back.taxiways == ("A2", "C") and back.nodes[-1][0] == "parking"


def test_committed_ifr_fixture_matches_generator():
    """Regenerate with `python tools/make_ifr_fixture.py` when the generator changes."""
    from pathlib import Path

    from localtc.airports import load_airport
    from localtc.replay import Recording

    fixtures = Path(__file__).parent / "fixtures"
    assert list(Recording(fixtures / "ifr_kpae_kbfi").events()) == ifr_kpae_kbfi(kpae(), kbfi()).sorted_events()
    assert load_airport(fixtures / "airports" / "KPAE.json") == kpae()
    assert load_airport(fixtures / "airports" / "KBFI.json") == kbfi()


def test_cli_phases(capsys, tmp_path):
    from pathlib import Path

    from localtc.cli import main

    fixture = Path(__file__).parent / "fixtures" / "ifr_kpae_kbfi"
    config = tmp_path / "empty.toml"
    config.write_text("", encoding="utf-8")
    assert main(["phases", str(fixture), "--destination", "KBFI", "--cruise-ft", "5000", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "ARRIVAL -> APPROACH" in out and "TAXI_IN -> PARKED" in out
