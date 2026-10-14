"""The EFB: fuel and weights, the scratchpad, Airport Ops, the achievements, ChartFox, and the phone's commands."""

import asyncio
import json
import math
from pathlib import Path

import pytest

from localtc.efb import perf
from localtc.efb.achievements import Achievements, FlightFacts, facts_from_recording, met_by
from localtc.efb.airport_ops import AirportOps
from localtc.efb.scratchpad import Scratchpad, kind_of, picked
from localtc.ops.chartfox import ChartFox, ChartFoxError, for_phase, grouped, mercator, position_on
from localtc.ops.fetch import Cache
from localtc.ops.keys import DataKeys

ROOT = Path(__file__).parents[1]


# --- fuel and weights --------------------------------------------------------------------------------------------


def test_a_fuel_plan_for_the_type_and_the_weights_against_its_limits():
    a320 = perf.aircraft("A320")
    fuel = perf.fuel_plan(a320, 500.0, alternate_nm=100.0)
    # 500 nm at 450 kt plus the climb and descent: about 1.3 h at 2,500 kg/h.
    assert 3000 < fuel.trip_kg < 3600 and fuel.contingency_kg == pytest.approx(fuel.trip_kg * 0.05)
    assert fuel.final_reserve_kg == pytest.approx(0.5 * 2500 * 0.8)
    assert fuel.block_kg == pytest.approx(fuel.trip_kg + fuel.contingency_kg + fuel.alternate_kg + fuel.final_reserve_kg
                                          + fuel.taxi_kg)
    w = perf.weights(a320, pax=150, cargo_kg=2000, block_kg=fuel.block_kg, trip_kg=fuel.trip_kg, taxi_kg=fuel.taxi_kg)
    assert w["zfw"]["kg"] == round(42600 + 150 * 84 + 2000) and w["ok"]
    heavy = perf.weights(a320, pax=180, cargo_kg=9000, block_kg=18000, trip_kg=3000, taxi_kg=100)
    assert not heavy["zfw"]["ok"] and not heavy["ok"]


def test_the_type_from_a_title_and_none_made_up():
    assert perf.aircraft("A330-300 (RR)").name == "Airbus A330-300"
    assert perf.aircraft("B37M").name == "Boeing 737 MAX 8"
    assert perf.aircraft("XYZ1") is None


def test_simbriefs_own_figures_are_used_as_they_are():
    from localtc.config import PlanPerf

    fuel = perf.from_simbrief(PlanPerf(block_fuel_lb=22046.2, takeoff_fuel_lb=21605.3, landing_fuel_lb=11023.1,
                                       reserve_fuel_lb=4409.2))
    assert fuel.source == "SimBrief" and round(fuel.block_kg) == 10000 and round(fuel.trip_kg) == 4800
    assert perf.from_simbrief(PlanPerf()) is None


def test_alternates_near_the_destination_with_a_long_enough_runway():
    index = [{"icao": "KONT", "name": "Ontario", "lat": 34.056, "lon": -117.601, "longest_ft": 12000},
             {"icao": "KSNA", "name": "John Wayne", "lat": 33.676, "lon": -117.868, "longest_ft": 5700},
             {"icao": "KBUR", "name": "Burbank", "lat": 34.2, "lon": -118.359, "longest_ft": 6886},
             {"icao": "KLAX", "name": "Los Angeles", "lat": 33.942, "lon": -118.408, "longest_ft": 12091}]
    got = perf.alternates((33.942, -118.408), index, min_runway_ft=6000, exclude="KLAX")
    assert [a["icao"] for a in got] == ["KBUR", "KONT"]


# --- the scratchpad -----------------------------------------------------------------------------------------------


def test_a_note_picks_out_a_clearance_but_isnt_taken_for_one(tmp_path):
    pad = Scratchpad(tmp_path / "pad.json", clock=lambda: 100.0)
    note = pad.add("CLR TO KLAX SEA9 RWY 16L CLIMB 5000 DEP 119.2 SQ 4521", flight="KSEA-KLAX", phase="PARKED")
    assert note["kind"] == "clearance"
    assert note["picked"] == {"squawk": "4521", "altitude": "5000", "frequency": "119.2", "runway": "16L",
                              "procedure": "SEA9", "destination": "KLAX"}
    assert pad.checked(note, {"squawk": "4521", "altitude": "4000"}) == {
        "squawk": "same", "altitude": "differs", "frequency": "not given", "runway": "not given",
        "procedure": "not given", "destination": "not given"}
    pad.add("Pinned: crew meal at TOC", flight="OTHER", pinned=True)
    assert len(pad.for_flight("KSEA-KLAX")) == 2  # the pinned one too
    again = Scratchpad(tmp_path / "pad.json")
    assert [n["id"] for n in again.notes] == [n["id"] for n in pad.notes]  # kept on disk
    pad.edit(note["id"], text="SQ 1234")
    assert pad.notes[0]["picked"] == {"squawk": "1234"}
    pad.delete(note["id"])
    with pytest.raises(KeyError):
        pad.delete(note["id"])
    with pytest.raises(ValueError):
        pad.add("   ")


def test_quick_note_kinds():
    assert kind_of("4521") == "squawk" and kind_of("124.35") == "frequency"
    assert kind_of("information Kilo, 1013") == "atis" and kind_of("wind 270/12 QNH 1009") == "weather"
    assert picked("FL350") == {"altitude": "FL350"}


# --- Airport Ops --------------------------------------------------------------------------------------------------


class _Engine:
    def __init__(self) -> None:
        from types import SimpleNamespace

        self.state = SimpleNamespace(flight=SimpleNamespace(origin="KSEA", destination="KLAX"),
                                     assignments=SimpleNamespace(departure_gate="Gate A5", gate=None))
        self._landed = False
        self._traffic = {}
        self.asked: list[str] = []

    def request_gate(self, icao, label):
        self.asked.append(label)
        return (label == "B25", "Gate B25 approved." if label == "B25" else "Gate B26 is occupied.")


def _own(t: float, **kw):
    from localtc.sim_api import OwnshipState

    base = dict(t=t, lat=47.44, lon=-122.3, alt_msl_ft=400, alt_indicated_ft=400, alt_agl_ft=0, hdg_true=0, hdg_mag=0,
                gs_kt=0, ias_kt=0, vs_fpm=0, on_ground=True, squawk="1200", xpdr_mode="standby", com1_mhz=0,
                com2_mhz=0, altimeter_inhg=29.92, engine_running=False, fuel_lb=10000.0, temperature_c=10.0)
    base.update(kw)
    return OwnshipState(**base)


def test_airport_ops_fuel_from_the_sim_deicing_and_gate_requests():
    ops = AirportOps(_Engine(), clock=lambda: 0.0)
    ops.observe(_own(0))
    assert ops.s.role == "departure" and set(ops.s.services) == {"cleaning", "catering", "fuel", "boarding"}
    assert ops.s.services["boarding"].state == "not tracked"  # no GSX: not made up
    ops.observe(_own(10, fuel_lb=12000.0))
    assert ops.s.services["fuel"].state == "in progress"
    ops.observe(_own(200, fuel_lb=12000.0))
    assert ops.s.services["fuel"].state == "done"
    assert any("Fuelling complete" in m["text"] for m in ops.s.messages)
    ops.observe(_own(210, temperature_c=-2.0, precip=8))
    assert ops.s.deice["required"] and ops.s.deice["position"] == 1 and "snow" in ops.s.deice["why"]
    assert not ops.request_gate("B26")["ok"]
    assert ops.request_gate("B25")["ok"]
    assert [r["state"] for r in ops.s.requests] == ["refused", "approved"]


def test_airport_ops_follows_gsx():
    from localtc.sim_api import AircraftVars

    ops = AirportOps(_Engine(), clock=lambda: 0.0)
    ops.observe(_own(0))
    ops.observe(AircraftVars(t=1, values={"L:FSDT_GSX_BOARDING_STATE": 5.0, "L:FSDT_GSX_CATERING_STATE": 6.0}))
    assert ops.s.services["boarding"].state == "in progress" and ops.s.services["boarding"].source == "GSX"
    assert ops.s.services["catering"].state == "done" and ops.to_json()["gsx"]


# --- achievements --------------------------------------------------------------------------------------------------


def test_achievements_are_awarded_once_with_what_met_them(tmp_path):
    a = Achievements(tmp_path / "a.json", clock=lambda: 1.0)
    facts = FlightFacts(flight="f1", origin="KSEA", destination="KLAX", arrived_at="KLAX", gate="Gate B25",
                        ifr_cleared=True, readbacks=["correct"] * 6, landing_vs_fpm=-140)
    new = {n["id"] for n in a.award(facts)}
    assert {"first_flight", "first_readback", "first_ifr_clearance", "clean_readbacks", "planned_destination",
            "gate_arrival", "best:smoothest_landing"} == new
    assert a.award(facts) == []  # idempotent: the same flight again earns nothing
    assert Achievements(tmp_path / "a.json").earned.keys() == a.earned.keys()


def test_a_flight_that_broke_a_rule_gets_no_clean_achievements(tmp_path):
    a = Achievements(None)
    facts = FlightFacts(flight="f2", origin="KSEA", destination="KLAX", arrived_at="KLAX", gate="Gate B25",
                        readbacks=["correct"] * 6, alerts=["runway_incursion"], went_around=True)
    got = {n["id"] for n in a.award(facts)}
    assert "first_flight" in got and not got & {"clean_readbacks", "planned_destination", "gate_arrival", "missed_approach"}


def test_milestones_and_streaks_from_the_logbook():
    from localtc.logbook import FlightRecord

    days = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-05"]
    flights = [FlightRecord(id=str(i), started_at=f"{d}T10:00:00Z", ended_at=f"{d}T12:00:00Z", callsign=cs, aircraft=ty,
                            origin=o, destination=de, landed=True)
               for i, (d, cs, ty, o, de) in enumerate(zip(days, ["BAW1", "AFR2", "DLH3", "UAL4"], ["A320", "B738", "A333", "B77W"],
                                                          ["EGLL", "LFPG", "EDDF", "KSFO"], ["LFPG", "EDDF", "KSFO", "RJTT"]))]
    a = Achievements(None)
    new = {n["id"] for n in a.milestones(flights)}
    assert new == {"streak_3"}
    row = next(r for r in a.to_json()["achievements"] if r["id"] == "airports_10")
    assert row["progress"] == [5, 10] and not row["earned"]


def test_a_recordings_achievements():
    facts = facts_from_recording(ROOT / "tests" / "fixtures" / "real_kdvt_kgeu_2")
    assert facts.ifr_cleared and "first_readback" in met_by(facts)


# --- ChartFox -------------------------------------------------------------------------------------------------------


def test_chartfox_signs_in_through_the_server_and_lists_the_charts():
    calls: list[tuple] = []
    answers = {
        "/v1/chartfox/token": (200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}),
        "/v2/airports/KSEA/charts/grouped": (200, {"data": {"3": [{"id": "c1", "name": "AIRPORT DIAGRAM", "type": 3}],
                                                            "6": [{"id": "c2", "name": "ILS OR LOC RWY 16L", "type": 6,
                                                                   "meta": [{"type": "Runways", "value": "16L"}]},
                                                                  {"id": "c3", "name": "RNAV (GPS) RWY 34R", "type": 6}]}}),
    }

    def http(method, url, body, headers):
        calls.append((method, url, headers))
        for path, answer in answers.items():
            if url.endswith(path):
                return answer
        return 404, None

    cf = ChartFox(DataKeys(memory={}), Cache(), account_url="https://api.example", http=http, clock=lambda: 0.0)
    url = cf.sign_in_url()
    assert url.startswith("https://api.example/v1/chartfox/authorize?state=") and "secret" not in url
    assert cf.collect() and cf.connected
    groups, _ = cf.airport_charts("ksea")
    assert [c["name"] for c in groups["Approach"]] == ["ILS OR LOC RWY 16L", "RNAV (GPS) RWY 34R"]
    assert calls[-1][2]["Authorization"] == "Bearer at"
    first = for_phase(groups, "APPROACH", runway="34R")
    assert first[0]["id"] == "c3"  # the runway's own approach first
    cf.disconnect()
    with pytest.raises(ChartFoxError):
        cf.airport_charts("KLAX")


def test_own_position_on_a_georeferenced_chart():
    x0, y0 = mercator(47.45, -122.31)
    georef = {"tx": x0, "ty": y0, "k": 1000.0, "transform_angle": 0.0}
    assert position_on(georef, 47.45, -122.31, 1000.0) == pytest.approx((0.0, 0.0), abs=1e-6)
    x, y = position_on(georef, 47.46, -122.31, 1000.0)  # north of the origin: up the chart (smaller y)
    assert y < 0 and abs(x) < 1e-6
    assert position_on({"tx": 1}, 1, 1, 100) is None
    assert grouped({"data": [{"id": 1, "type": 4, "name": "SID"}]})["SID"][0]["id"] == "1"
    assert math.isfinite(mercator(89.9, 0)[1])


# --- the phone's EFB commands -----------------------------------------------------------------------------------------


def test_the_phones_efb_commands_on_the_local_network():
    from localtc.ui.companion import CompanionHub, CompanionServer
    from test_companion import post

    got: list[dict] = []

    async def efb(cmd: dict) -> dict:
        got.append(cmd)
        if cmd.get("action") == "gate":
            return {"ok": True, "why": "Gate B25 approved."}
        raise RuntimeError("unknown")

    async def go():
        hub = CompanionHub()
        server = CompanionServer(hub, efb=efb)
        port = await server.start(0, "127.0.0.1", advertise=False)
        try:
            ok = await post(port, "/companion/v1/efb", hub.key, {"action": "gate", "gate": "B25"})
            bad = await post(port, "/companion/v1/efb", hub.key, {"action": "nope"})
            hub.set_efb({"checklist": {"id": "before_start"}})
            return ok, bad, hub.snapshot()["efb"]
        finally:
            await server.close()

    ok, bad, efb_state = asyncio.run(go())
    assert ok == (200, {"ok": True, "why": "Gate B25 approved."}) and bad[0] == 409
    assert efb_state == {"checklist": {"id": "before_start"}}


def test_an_efb_command_through_the_account():
    from localtc.account import Account

    account = Account.__new__(Account)
    account.calls = []
    account._relayed({"watchers": 1, "calls": [{"text": json.dumps({"action": "gate", "gate": "B25"}), "to": "efb"},
                                               {"text": "Ground, radio check", "to": "com2"}]})
    assert account.calls == [('{"action": "gate", "gate": "B25"}', "efb"), ("Ground, radio check", "com2")]
