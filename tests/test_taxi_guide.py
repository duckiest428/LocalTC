"""Taxi guidance (atc_core/airport/taxi_guide.py, taxi_guidance.py, simpkg): the arrows along the route ground gave,
the stop bar short of a runway not cleared, the objects' package in the sim's Community folder."""

import asyncio
import json
import math

from msgspec.structs import replace

from localtc.atc_core.airport import taxi_guide
from localtc.sim_api import GroundMarks, OwnshipState

PATH = [(0.0, 0.0), (0.0, 500.0), (400.0, 500.0)]  # north 500 m, then east 400 m


class Runway:
    """A runway crossing the second leg, east-west across x = 250."""

    def contains(self, xy, margin_m=0.0):
        return abs(xy[0] - 250.0) <= 30.0 + margin_m and 400.0 <= xy[1] <= 600.0


def test_arrows_ahead_along_the_route_and_turning_with_it():
    marks = taxi_guide.marks(PATH, (0.0, 300.0), 0.0)
    arrows = [m for m in marks if m.kind == "arrow"]
    assert arrows and all(m.kind == "arrow" for m in marks)
    first = arrows[0]
    assert 300.0 + taxi_guide.FIRST_M <= first.y < 300.0 + taxi_guide.FIRST_M + taxi_guide.SPACING_M
    assert abs(first.hdg - 0.0) < 1e-6 or abs(first.hdg - 360.0) < 1e-6
    assert max(m.y for m in arrows) <= 500.0 and arrows[-1].x > 0.0  # round the corner, pointing east
    assert abs(arrows[-1].hdg - 90.0) < 1e-6
    along = [taxi_guide.project(PATH, (m.x, m.y))[0] for m in arrows]
    assert along[-1] - 300.0 <= taxi_guide.AHEAD_M


def test_a_mark_keeps_its_key_and_place_as_the_aircraft_comes_on():
    before = {m.key: (m.x, m.y) for m in taxi_guide.marks(PATH, (0.0, 100.0), 0.0)}
    after = {m.key: (m.x, m.y) for m in taxi_guide.marks(PATH, (0.0, 130.0), 0.0)}
    kept = set(before) & set(after)
    assert kept and all(before[k] == after[k] for k in kept)  # made once, never slid along the ground
    assert set(before) - set(after)  # the ones passed go
    assert set(after) - set(before)  # new ones further on


def test_a_runway_not_cleared_across_stops_the_arrows_with_a_bar():
    stops = taxi_guide.stops(PATH, [Runway()])
    assert len(stops) == 1
    x, y, _ = taxi_guide.point_at(PATH, stops[0])
    assert y == 500.0 and 250.0 - 30.0 - taxi_guide.HOLD_MARGIN_M - 4.0 <= x <= 250.0 - 30.0 - taxi_guide.HOLD_MARGIN_M
    marks = taxi_guide.marks(PATH, (0.0, 400.0), 0.0, stops)
    arrows = [m for m in marks if m.kind == "arrow"]
    bar = [m for m in marks if m.kind == "stop"]
    assert arrows and max(taxi_guide.project(PATH, (m.x, m.y))[0] for m in arrows) < stops[0]
    assert len(bar) >= 3 and all(abs(m.x - bar[0].x) < 1e-6 for m in bar)  # across the way (it runs east)
    assert max(m.y for m in bar) - min(m.y for m in bar) >= 2 * taxi_guide.STOP_BAR_STEP_M


def test_off_the_route_the_arrows_start_from_its_nearest_point():
    marks = taxi_guide.marks(PATH, (150.0, 100.0), 0.0)  # 150 m off it, on the apron
    assert marks and math.isclose(marks[0].x, 0.0) and marks[0].y <= 100.0 + taxi_guide.SPACING_M


def test_a_new_route_is_new_marks():
    other = [(0.0, 0.0), (0.0, 500.0), (-400.0, 500.0)]
    assert taxi_guide.route_key(PATH) != taxi_guide.route_key(other)
    assert not {m.key for m in taxi_guide.marks(PATH, (0, 0), 0)} & {m.key for m in taxi_guide.marks(other, (0, 0), 0)}


class Source:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, command) -> None:
        self.sent.append(command)


class Engine:
    def __init__(self) -> None:
        self.marks = [("r:a24", "arrow", 45.0, -73.0, 90.0)]

    def taxi_guide(self):
        return self.marks


def test_the_service_sends_the_marks_when_they_change_and_takes_them_away_when_off():
    from localtc.bus import EventBus
    from localtc.taxi_guidance import TaxiGuidance

    async def go():
        bus, source, engine = EventBus(), Source(), Engine()
        guide = TaxiGuidance(engine, source, bus, enabled=True)
        task = asyncio.create_task(guide.run())
        own = OwnshipState(t=1.0, lat=45.0, lon=-73.0, alt_msl_ft=100, alt_indicated_ft=100, alt_agl_ft=0,
                           altimeter_inhg=29.92, hdg_mag=90, hdg_true=90, ias_kt=10, gs_kt=10, vs_fpm=0, on_ground=True,
                           squawk="1200", xpdr_mode="alt", com1_mhz=121.9, com2_mhz=121.9)
        for t in (1.0, 1.5, 2.5):
            bus.publish(replace(own, t=t))
            await asyncio.sleep(0.01)
        engine.marks = engine.marks + [("r:a36", "arrow", 45.0, -72.99, 90.0)]
        bus.publish(replace(own, t=4.0))
        await asyncio.sleep(0.01)
        await guide.set_enabled(False)
        task.cancel()
        return source.sent

    sent = asyncio.run(go())
    assert all(isinstance(c, GroundMarks) for c in sent)
    assert [len(c.marks) for c in sent] == [1, 2, 0]  # once each change (not every second), none when turned off


def test_the_package_goes_in_each_community_folder_once(tmp_path):
    from localtc import simpkg

    root = tmp_path / "Packages"
    (root / "Community").mkdir(parents=True)
    put = simpkg.install([root, tmp_path / "nowhere"])
    target = root / "Community" / "localtc-taxi-guidance"
    assert put == [target] and simpkg.installed([root])
    layout = json.loads((target / "layout.json").read_text(encoding="utf-8"))
    paths = {c["path"] for c in layout["content"]}
    assert "SimObjects/Misc/localtc-taxi-arrow/sim.cfg" in paths
    assert "SimObjects/Misc/localtc-taxi-stop/model/localtc_taxi_stop.gltf" in paths
    assert all(c["size"] > 0 and c["date"] > 130_000_000_000_000_000 for c in layout["content"])
    assert simpkg.install([root]) == []  # the same version there already
    sim_cfg = (target / "SimObjects/Misc/localtc-taxi-arrow/sim.cfg").read_text(encoding="utf-8")
    assert "title=LocalTC Taxi Arrow" in sim_cfg and "category=StaticObject" in sim_cfg


def test_the_models_are_what_the_bridge_asks_for():
    from localtc import simpkg
    from localtc.sim_bridge.simconnect_source import MARK_TITLES

    titles = {line.split("=", 1)[1].strip() for cfg in simpkg.PACKAGE.rglob("sim.cfg")
              for line in cfg.read_text(encoding="utf-8").splitlines() if line.startswith("title=")}
    assert titles == set(MARK_TITLES.values())
    for gltf in simpkg.PACKAGE.rglob("*.gltf"):
        doc = json.loads(gltf.read_text(encoding="utf-8"))
        size = doc["buffers"][0]["byteLength"]
        assert (gltf.parent / doc["buffers"][0]["uri"]).stat().st_size == size
        assert doc["bufferViews"][0]["byteStride"] == 36  # the sim's own compiled layout


def test_off_the_route_the_arrows_lead_back_onto_it():
    """Denver, taxiing in: off the route ground gave, the arrows still pointed along it from its nearest point ("guides
    should guide the user from its current position back on track"). They lead along the taxiways back onto it; the
    route ground gave (the Live Map's line) is left as it was."""
    from helpers.airports import kpae

    from localtc.atc_core.airport import AirportGeometry, TaxiGraph
    from localtc.atc_core.engine import AtcEngine, EngineConfig
    from localtc.sim_api import AirportData

    engine = AtcEngine(EngineConfig(callsign="N172LT", destination="KBFI"))
    engine.handle(AirportData(t=0.0, airport=kpae()))
    geo = engine.geometry("KPAE")
    graph = TaxiGraph(geo)
    spot = kpae().parking[0]
    route = graph.departure_route(spot.lat, spot.lon, geo.end("34L"))
    path = [graph.positions[n] for n in route.nodes if n in graph.positions]
    on = taxi_guide.point_at(path, taxi_guide.length(path) * 0.3)
    side = (on[0] + 60 * math.cos(math.radians(on[2])), on[1] - 60 * math.sin(math.radians(on[2])))  # 60 m to the right
    lat, lon = geo.frame.to_latlon(*side)
    own = OwnshipState(t=1.0, lat=lat, lon=lon, alt_msl_ft=600, alt_indicated_ft=600, alt_agl_ft=0, altimeter_inhg=29.92,
                       hdg_mag=on[2] - 15, hdg_true=on[2], ias_kt=10, gs_kt=10, vs_fpm=0, on_ground=True, squawk="1200",
                       xpdr_mode="alt", com1_mhz=121.8, com2_mhz=121.5)
    assert engine._back_on_route(AirportGeometry(kpae()), path, replace(own, lat=geo.frame.to_latlon(*on[:2])[0],
                                                                         lon=geo.frame.to_latlon(*on[:2])[1])) is path
    back = engine._back_on_route(geo, path, own)
    assert back is not path and back[-1] == path[-1]  # on to the same hold short
    assert back[0] == graph.positions[graph.nearest_node(lat, lon)]  # from where the aircraft is
    assert engine._back_on_route(geo, path, own) is back  # the same while the nearest node is: no new objects
