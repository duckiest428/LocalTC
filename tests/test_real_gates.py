"""Real gate names and international gates from OpenStreetMap, matched onto the sim's stands.

Fixtures: tests/fixtures/real_gates: the sim's parking at Las Vegas (numbered "GATE 1".."GATE 248") and Seattle
(lettered "GATE S 2"), and OSM's gates and terminals there (Overpass, trimmed to the tags used).
"""

import json
from pathlib import Path

import msgspec

from localtc.atc_core.airport import AirportGeometry
from localtc.atc_core.airport import gates as stands
from localtc.atc_core.airport import real_gates
from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.readback.normalize import normalize
from localtc.gate_data import GateStore
from localtc.sim_api import Airport

HERE = Path(__file__).parent / "fixtures" / "real_gates"


def load(icao: str) -> tuple[AirportGeometry, real_gates.GateData]:
    geo = AirportGeometry(msgspec.convert(json.loads((HERE / f"{icao}_sim.json").read_text()), Airport))
    return geo, real_gates.parse_overpass(icao, json.loads((HERE / f"{icao}_osm.json").read_text()))


def by_scenery_name(geo, data) -> dict[str, stands.Gate]:
    return {g.spot.name: g for g in stands.gates(geo, data)}


def test_las_vegas_numbered_stands_take_the_real_names():
    geo, data = load("KLAS")
    named = by_scenery_name(geo, data)
    assert named["GATE 88"].display == "Gate D36"  # the scenery's "Gate 88", which doesn't exist
    assert named["GATE 44"].display == "Gate E9"
    assert stands.named(geo, "E9", data) == named["GATE 44"]
    assert stands.named(geo, "E9") is None  # without the real gates, the scenery has no E9


def test_international_flights_go_to_international_gates():
    geo, data = load("KLAS")
    for seed in ("a", "b", "c", "d"):
        intl = stands.assign(geo, airline=True, aircraft_type="B738", traffic=[], seed=seed, real=data, international=True)
        home = stands.assign(geo, airline=True, aircraft_type="B738", traffic=[], seed=seed, real=data)
        assert intl.label.startswith("E") and intl.international
        assert not home.international and home.label[0].isalpha()


def test_seattle_terminal_and_letters():
    geo, data = load("KSEA")
    assert {g.ref for g in data.gates if g.international} >= {"S1", "S2", "S16"}
    named = by_scenery_name(geo, data)
    assert named["GATE S 2"].label == "S2" and named["GATE S 2"].international
    assert named["GATE Q 15"].label == "Q15"  # the scenery's own lettered names stay


def test_without_real_gates_nothing_changes():
    geo, data = load("KLAS")
    assert by_scenery_name(geo, None)["GATE 88"].display == "Gate 88"
    assert stands.assign(geo, airline=True, aircraft_type="B738", traffic=[], seed="a") == \
        stands.assign(geo, airline=True, aircraft_type="B738", traffic=[], seed="a", real=real_gates.GateData("KLAS"))


def test_requested_gate_echo_nine():
    assert stands.requested(normalize("we'd like gate echo 9")) == "E9"


def test_international_by_country():
    assert real_gates.is_international("CYYZ", "KLAX")
    assert not real_gates.is_international("KSEA", "KSAN")
    assert real_gates.is_international("EGLL", "EHAM")
    assert not real_gates.is_international("KSEA", None)


def test_store_caches_on_disk_and_survives_failures(tmp_path):
    calls = []

    def fetcher(icao):
        calls.append(icao)
        if icao == "KBAD":
            raise OSError("offline")
        return real_gates.GateData(icao, (real_gates.RealGate("E9", 36.0, -115.0, True),))

    store = GateStore(tmp_path, fetcher=fetcher, background=False)
    assert store.get("klas").gates[0].ref == "E9"
    assert store.get("KBAD") is None and store.get("KBAD") is None  # not asked again straight away
    assert calls == ["KLAS", "KBAD"]
    again = GateStore(tmp_path, fetcher=fetcher, background=False)
    assert again.get("KLAS").gates[0].international and calls == ["KLAS", "KBAD"]  # from the disk


def test_engine_names_the_real_gate():
    geo, data = load("KLAS")
    engine = AtcEngine(EngineConfig(destination="KLAS"))
    assert engine._real_gates("KLAS") is None
    engine.gate_source = {"KLAS": data}.get
    assert engine._real_gates("KLAS") is data
    engine.gate_source = lambda icao: 1 / 0  # a broken source: the scenery's names, ATC carries on
    assert engine._real_gates("KLAS") is None
