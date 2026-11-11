"""The arrival sequence, side-steps, the landing after a departure, named visuals, the push held for traffic and the
queue, repeats in other words, and ride reports (atc_core/sequencing.py, rides.py, airport/charted_visuals.py)."""

import msgspec
from helpers.airports import kbfi
from test_phase import own
from test_traffic_sequencing import approaching, at_runway, said, tower_engine

from localtc.atc_core.airport import charted_visuals
from localtc.atc_core.airport.aircraft_size import departure_gap_s, wake_miles
from localtc.atc_core.engine import AtcEngine, EngineConfig, _Scheduled
from localtc.atc_core.phraseology.library import TemplateLibrary
from localtc.atc_core.rides import own_report, severity
from localtc.atc_core.sequencing import parallels
from localtc.atc_core.values import Approach, Callsign
from localtc.sim_api import AirportData, PilotReport, PilotReports, TrafficSnapshot, TrafficTarget


def kbfi_parallels():
    """KBFI with its 14L/32R beside 14R/32L (as the real one, 230 m apart), for the side-steps."""
    import math

    airport = kbfi()
    main = airport.runways[0]
    away = math.radians((main.heading_true - 90) % 360)  # 14L: left of 14R landing on 140
    lat = main.lat + 230 * math.cos(away) / 111320
    lon = main.lon + 230 * math.sin(away) / (111320 * math.cos(math.radians(main.lat)))
    left = msgspec.structs.replace(main, lat=lat, lon=lon, length_m=1100.0,
                                   primary=msgspec.structs.replace(main.primary, designator="L", ils_ident=""),
                                   secondary=msgspec.structs.replace(main.secondary, designator="R", ils_ident=""))
    return msgspec.structs.replace(airport, runways=(*airport.runways, left))


def parallel_engine() -> AtcEngine:
    engine = tower_engine()
    engine.handle(AirportData(t=0.5, airport=kbfi_parallels()))
    return engine


def on_runway(engine: AtcEngine, runway: str, along_m: float, *, ident: int = 1, heading_off: float = 0.0) -> TrafficTarget:
    geo = engine.geometry("KBFI")
    end = geo.end(runway)
    import math

    ux, uy = math.sin(math.radians(end.heading_true)), math.cos(math.radians(end.heading_true))
    lat, lon = geo.frame.to_latlon(end.threshold[0] + ux * along_m, end.threshold[1] + uy * along_m)
    return TrafficTarget(object_id=ident, atc_id="SWA123", atc_model="B737", lat=lat, lon=lon, alt_ft=25.0,
                         hdg_true=(end.heading_true + heading_off) % 360, gs_kt=0.0, on_ground=True)


def tuned_tower(engine: AtcEngine, distance_nm: float):
    first = approaching(engine, distance_nm=distance_nm)
    engine.handle(first)
    engine.state.comms.last_pilot_t = first.t + 1.0  # the pilot's checked in
    return msgspec.structs.replace(first, t=first.t + 31.0)


# --- wake and the gaps ------------------------------------------------------------------------------------------------

def test_wake_spacing_by_category():
    assert wake_miles("H", "M") == 5.0 and wake_miles("J", "L") == 8.0 and wake_miles("M", "M") == 3.0
    assert departure_gap_s("H", "M", 60.0) == 120.0 and departure_gap_s("M", "M", 60.0) == 60.0


def test_kbfi_has_parallels_for_a_sidestep():
    engine = parallel_engine()
    assert [e.ident for e in parallels(engine.geometry("KBFI"), "14R")] == ["14L"]


# --- side-step and the landing after a departure ----------------------------------------------------------------------

def test_an_occupied_runway_with_a_free_parallel_is_a_sidestep():
    engine = parallel_engine()
    engine.handle(TrafficSnapshot(t=1.0, targets=(on_runway(engine, "14R", 1200.0, heading_off=90.0),)))
    later = tuned_tower(engine, 5.0)
    first = engine.handle(later)
    assert "tower.sidestep_land" not in said(engine, first)  # stopped a moment: it may be on its way off
    out = engine.handle(msgspec.structs.replace(later, t=later.t + 45.0))
    assert "tower.sidestep_land" in said(engine, out), said(engine, out)


def test_a_departure_starting_its_roll_gets_the_landing_clearance_with_it():
    engine = parallel_engine()
    engine.handle(TrafficSnapshot(t=1.0, targets=(on_runway(engine, "14R", 50.0),)))
    tick = tuned_tower(engine, 4.5)
    ids = [i for dt in (0.0, 15.0, 30.0) for i in said(engine, engine.handle(msgspec.structs.replace(tick, t=tick.t + dt)))]
    assert "tower.land_after_departure" in ids and "tower.sidestep_land" not in ids, ids
    assert engine.conditions.active("after_departure") is not None


# --- named visuals ----------------------------------------------------------------------------------------------------

def test_the_faa_named_visuals():
    procedures, airports = charted_visuals.count()
    assert procedures >= 40 and airports >= 26
    assert charted_visuals.charted_for("KDCA", "19") == ["River"]
    assert charted_visuals.choose("KSFO", "28R", "UAL1", ceiling_ft=8000, visibility_sm=10, in_cloud=False).charted in ("Quiet Bridge", "Tipp Toe")
    assert charted_visuals.choose("KDCA", "19", "x", ceiling_ft=1500, visibility_sm=10, in_cloud=False) is None  # too low for it
    asked = charted_visuals.requested("KLGA", None, "request the Park Visual")
    assert asked is not None and asked.runway == "31"


def test_a_named_visual_is_said_by_name():
    lib = TemplateLibrary.load()
    river = Approach("VISUAL", "19", charted="River")
    assert Approach.parse(river.display) == river
    r = lib.render("common.expect_approach", {"callsign": Callsign.named("DAL123"), "approach": river}, controller="approach")
    assert "expect River Visual runway 19 approach" in r.text and "river visual runway one niner" in r.spoken


# --- repeats -----------------------------------------------------------------------------------------------------------

def test_a_repeat_is_said_in_other_words():
    engine = tower_engine()
    engine.handle(approaching(engine, distance_nm=5.0))
    tower = engine.facility("tower")
    slots = {"runway": "14R", "wind": engine._surface_wind(engine.state.aircraft, "KBFI")}
    first = engine._issue(_Scheduled(0.0, "tower.land", slots, tower), 600.0)
    again = engine._issue(_Scheduled(0.0, "tower.land", slots, tower, repeat=True), 610.0)
    assert "I say again" in again.text and again.text.split("I say again, ")[1] != first.text.split(", ", 1)[1]


# --- the push ---------------------------------------------------------------------------------------------------------

def parked_engine():
    engine = AtcEngine(EngineConfig(callsign="N172LT", destination="KBFI", seed=3))
    engine.handle(AirportData(t=0.0, airport=kbfi()))
    engine.state.flight.origin = "KBFI"
    engine.state.phase = "PARKED"
    return engine


def test_traffic_taxiing_behind_holds_the_push_until_it_is_by():
    engine = parked_engine()
    me = own(100.0, lat=47.5300, lon=-122.3020, hdg_true=90.0, hdg_mag=90.0, on_ground=True, com1_mhz=121.9)
    engine.handle(me)
    # 80 m behind the tail (west), taxiing north across it.
    behind = TrafficTarget(object_id=9, atc_id="ASA55", atc_model="A320", lat=47.5300, lon=-122.30307, alt_ft=21.0,
                           hdg_true=0.0, gs_kt=12.0, on_ground=True)
    engine.handle(TrafficSnapshot(t=100.0, targets=(behind,)))
    ground = engine.facility("ground")
    engine._pushback(100.0, ground, me)
    assert [s.instruction_id for s in engine._scheduled][-1] == "ground.push_hold_traffic"
    assert engine.push_hold is not None and "pushback" not in engine.state.clearances
    engine._scheduled.clear()
    engine.state.comms.tuned = ground
    engine.handle(TrafficSnapshot(t=160.0, targets=()))
    assert engine._push_watch(msgspec.structs.replace(me, t=160.0))
    assert engine.push_hold is None and engine._scheduled[-1].instruction_id.startswith("ground.pushback")


def test_a_long_line_at_the_runway_holds_the_push_at_the_gate():
    engine = parked_engine()
    me = own(100.0, lat=47.5300, lon=-122.3020, hdg_true=90.0, hdg_mag=90.0, on_ground=True, com1_mhz=121.9)
    engine.handle(me)
    end = engine._runway_end("KBFI", me)
    geo = engine.geometry("KBFI")
    lat, lon = geo.frame.to_latlon(*end.threshold)
    line = tuple(TrafficTarget(object_id=20 + i, atc_id=f"N{i}", atc_model="C172", lat=lat + 0.0003 * i, lon=lon, alt_ft=21.0,
                               hdg_true=0.0, gs_kt=0.0, on_ground=True) for i in range(12))
    engine.handle(TrafficSnapshot(t=100.0, targets=line))
    assert engine.push_delay_min(me) >= 4


# --- rides -------------------------------------------------------------------------------------------------------------

def test_the_pilots_own_report_is_read():
    me = own(1.0, alt_indicated_ft=35000, on_ground=False)
    r = own_report("moderate chop at flight level 350, tops at 330", me)
    assert r is not None and r.turbulence == "MOD" and r.alt_ft == 35000 and r.tops_ft == 33000
    assert own_report("we're with you at 350", me) is None
    assert severity("LGT-MOD") == 3


def test_ride_reports_and_a_smoother_level():
    engine = tower_engine()
    me = own(1000.0, lat=47.0, lon=-121.0, hdg_true=0.0, hdg_mag=0.0, alt_msl_ft=35000, alt_indicated_ft=35000,
             on_ground=False, gs_kt=450, ias_kt=280)
    engine.handle(me)
    engine.handle(PilotReports(t=1000.0, area="KSEA", reports=(
        PilotReport(lat=47.5, lon=-121.0, alt_ft=35000, turbulence="MOD", aircraft="A321", age_min=20.0, raw="a"),
        PilotReport(lat=47.6, lon=-121.0, alt_ft=37000, turbulence="NEG", aircraft="B738", age_min=10.0, raw="b"),
    )))
    message = engine._ride_message(me, "any ride reports?")
    assert "moderate turbulence reported at FL350" in message.display and "smooth reported at FL370" in message.display, message


# --- the cabin's sights, the sim's panel ------------------------------------------------------------------------------

def test_a_sight_out_of_the_window():
    from localtc.crew import sights

    seen: set[str] = set()
    s = sights.sight(46.55, -122.16, 0.0, local_hour=12, in_cloud=False, seen=seen)
    assert s is not None and s.name == "Mount Rainier" and s.side == "right"
    assert "right side" in sights.wording(s, night=False)
    assert sights.sight(46.55, -122.16, 0.0, local_hour=23, in_cloud=False, seen=seen) is None  # a mountain in the dark
    assert sights.sight(46.55, -122.16, 0.0, local_hour=12, in_cloud=True, seen=seen) is None
    night = sights.sight(41.68, -88.03, 0.0, local_hour=22, in_cloud=False, seen=seen)
    assert night is not None and night.city and "lights" in sights.wording(night, night=True)


def test_the_sims_panel_reads_the_radio_log(tmp_path, monkeypatch):
    import asyncio

    from localtc.config import Config
    from localtc.ui.controller import AppController

    monkeypatch.setenv("LOCALTC_SETTINGS", str(tmp_path / "settings.toml"))
    controller = AppController(Config(), plan_path=tmp_path / "flightplan.json", cache=None)
    controller.radio.append({"kind": "atc", "t": 10.0, "station": "Boeing Tower", "text": "cleared to land"})
    controller.radio.append({"kind": "pilot", "t": 12.0, "text": "cleared to land"})
    first = asyncio.run(controller.api_panel({}))
    assert [x["t"] for x in first["radio"]] == [10.0, 12.0] and first["language"]
    assert asyncio.run(controller.api_panel({"after": "10.0"}))["radio"][0]["t"] == 12.0
    from pathlib import Path

    static = Path(__file__).resolve().parents[1] / "src" / "localtc" / "ui" / "static"
    assert "/api/panel" in (static / "panel.js").read_text(encoding="utf-8")


def test_the_panel_isnt_installed_before_its_built(tmp_path):
    from localtc import simpkg

    (tmp_path / "Community").mkdir()
    if not (simpkg.PANEL / "manifest.json").exists():
        assert simpkg.install_panel([tmp_path]) == []
    else:
        assert simpkg.install_panel([tmp_path]) == [tmp_path / "Community" / "localtc-panel"]
