"""The shared cards' data model (site/cardmodel.js), run in Node on the two real flights' replays: the quote
a card offers first is the clearance, the controller's words only, and the snapshot carries nothing private.
The same file is bundled into the account server and loaded by the dashboard and the app.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from localtc.replay import Recording
from localtc.replay.rewatch import build_replay

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node")


def run(script: str, data: object) -> object:
    """``script`` in Node with the model imported as ``m`` and ``data`` as ``input``; prints its result."""
    code = (f"const m = await import({json.dumps((ROOT / 'site' / 'cardmodel.js').as_uri())});\n"
            f"const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
            f"console.log(JSON.stringify(await (async () => {{ {script} }})()));")
    out = subprocess.run([NODE, "--input-type=commonjs", "-e", f"(async () => {{ {code} }})()"], input=json.dumps(data),
                         capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def replays() -> dict[str, dict]:
    return {name: build_replay(Recording(FIXTURES / name)) for name in ("real_kden_ksea", "real_cyul_klax")}


def test_the_clearance_comes_first_then_the_best_of_the_rest(replays):
    kden = run("return m.moments(input.radio)", replays["real_kden_ksea"])
    assert [x["kind"] for x in kden] == ["clearance", "takeoff", "landing", "approach", "welcome"]
    assert kden[0]["text"].startswith("Delta 2543, cleared to Seattle-tacoma International airport via the ZIMMR3")
    assert kden[0]["station"] == "Denver Clearance" and kden[0]["mhz"] == 118.75
    cyul = run("return m.moments(input.radio)", replays["real_cyul_klax"])
    assert cyul[0]["kind"] == "clearance" and "Los Angeles" in cyul[0]["text"]
    assert cyul[1]["kind"] == "takeoff" and "good afternoon" in cyul[1]["text"]  # the first, with its greeting


def test_only_the_controllers_words(replays):
    for replay in replays.values():
        said = {line["text"] for line in replay["radio"] if line["kind"] == "atc"}
        assert all(x["text"] in said for x in run("return m.moments(input.radio)", replay))


def test_the_airports_are_named_for_the_card(replays):
    names = run("return m.airportNames(input.airports)", replays["real_cyul_klax"])
    assert names == {"CYUL": "Montreal", "KLAX": "Los Angeles"}


def test_the_snapshot_has_nothing_private():
    row = {"id": "x", "started_at": "2026-09-23T22:18:00Z", "callsign": "DAL2543", "aircraft": "A220-300", "origin": "KDEN",
           "destination": "KSEA", "departure_gate": "B32", "arrival_gate": "A7", "air_min": 149.4, "block_min": 178.2,
           "distance_nm": 882.4, "max_alt_ft": 38020, "landing_vs_fpm": -290, "readbacks": 20, "readbacks_correct": 17,
           "landed": True, "origin_lat": 39.86, "origin_lon": -104.67, "destination_lat": 47.45, "destination_lon": -122.31}
    card = run("return m.flightCard(input, {names: {KSEA: 'Seattle'}})", row)
    assert card["date"] == "2026-09-23" and card["readback_pct"] == 85 and card["landing_fpm"] == -290
    assert card["destination"] == {"icao": "KSEA", "name": "Seattle", "lat": 47.45, "lon": -122.31}
    assert "B32" not in json.dumps(card) and "22:18" not in json.dumps(card) and card["quote"] is None


@pytest.mark.parametrize(("nm", "words"), [(10, "short hop"), (320, "marathons"), (4500, "New York to London"),
                                           (30000, "around the Earth"), (250000, "to the Moon")])
def test_distances_are_framed(nm, words):
    assert words in run("return m.distanceFraming(input)", nm)
