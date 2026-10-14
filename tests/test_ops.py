"""Real-world data (localtc.ops) without the network: what each source answers, read; the cache's ages and the stale
copy kept when a fetch fails; NOTAMs read into what ATC works to; terrain tiles decoded."""

import struct
import urllib.error
import zlib

import numpy as np

from localtc.atc_core.atis.board import AtisBoard
from localtc.ops.fetch import Cache
from localtc.ops.keys import DataKeys
from localtc.ops.notams import Notam, Notams, notices_of
from localtc.ops.restrictions import Area, parse_tfr
from localtc.ops.terrain import Terrain, decode_png, tile_xy
from localtc.ops.weather import Weather, parse_sigmet, parse_taf


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_the_cache_keeps_an_answer_until_it_expires_and_the_old_one_when_a_fetch_fails(tmp_path):
    clock = Clock()
    cache = Cache(tmp_path / "c.json", clock=clock)
    calls = []

    def fetch():
        calls.append(1)
        return {"n": len(calls)}

    got = cache.get("k", 60, fetch, source="test")
    assert got.data == {"n": 1} and not got.stale and got.expires == clock.t + 60
    assert cache.get("k", 60, fetch, source="test").data == {"n": 1} and len(calls) == 1
    clock.t += 61

    def broken():
        raise urllib.error.URLError("offline")

    old = cache.get("k", 60, broken, source="test")
    assert old.data == {"n": 1} and old.stale and "offline" in old.error
    cache.save()
    assert Cache(tmp_path / "c.json", clock=clock).peek("k").data == {"n": 1}


def test_a_source_with_nothing_known_says_why():
    got = Cache(clock=Clock()).get("x", 60, lambda: (_ for _ in ()).throw(urllib.error.URLError("no route")), source="t")
    assert got.data is None and got.stale and "no route" in got.error


def test_taf_and_sigmets_from_aviationweather():
    taf = parse_taf({"icaoId": "EGLL", "rawTAF": "TAF EGLL 140457Z 1406/1512 20004KT 9999 FEW035",
                     "issueTime": "2026-10-14T04:57:00.000Z", "validTimeFrom": 1, "validTimeTo": 2})
    assert taf.icao == "EGLL" and taf.raw.startswith("TAF EGLL") and taf.valid_to == 2
    s = parse_sigmet({"firId": "UHMM", "validTimeFrom": 10, "validTimeTo": 20, "seriesId": "P01", "hazard": "VA",
                      "base": 0, "top": 13000, "coords": [{"lon": 164.25, "lat": 56.75}, {"lon": 167.28, "lat": 56.35},
                                                          {"lon": 167.2, "lat": 55.88}]})
    assert s.hazard == "VA" and s.top_ft == 13000 and s.polygon[0] == (56.75, 164.25) and s.kind == "sigmet"
    assert parse_sigmet({"coords": [{"lat": 1, "lon": 2}], "validTimeFrom": 1, "validTimeTo": 2}) is None  # no area


def test_the_radar_frame_is_a_tile_template():
    w = Weather(Cache(clock=Clock()), fetch=lambda url, **kw: {"host": "https://tiles", "radar": {"past": [
        {"time": 1, "path": "/v2/radar/a"}, {"time": 2, "path": "/v2/radar/b"}]}})
    radar = w.radar()
    assert radar["url"] == "https://tiles/v2/radar/b/256/{z}/{x}/{y}/2/1_1.png" and radar["time"] == 2
    assert "RainViewer" in radar["attribution"]


def notam(text: str) -> Notam:
    return Notam(id="A1/26", icao="EGLL", text=text, start=None, end=None)


def test_notams_read_into_what_atc_works_to():
    assert notices_of(notam("RWY 09L/27R CLSD DUE TO WIP")) == [("runway_closed", "09L/27R")]
    assert notices_of(notam("TWY A10E, B CLSD")) == [("taxiway_closed", "A10E"), ("taxiway_closed", "B")]
    assert notices_of(notam("ILS RWY 27L U/S")) == [("ils_out", "27L")]
    assert notices_of(notam("ILS RWY 27L GP U/S")) == [("glideslope_out", "27L")]
    assert notices_of(notam("ALS RWY 09R U/S")) == [("approach_lights_out", "09R")]
    assert notices_of(notam("BIG VOR/DME U/S")) == [("navaid_out", "VOR")]
    assert notices_of(notam("CRANE ERECTED 512341N 0002812W 230FT AMSL")) == []  # nothing ATC works to
    assert notices_of(notam("TWY K BTN TWY J AND RWY 22 CLSD")) == [("taxiway_closed", "K")]


def test_notams_need_the_faa_key_and_say_so():
    n = Notams(DataKeys(memory={}), Cache(clock=Clock()))
    assert n.for_airport("EGLL") == ([], None) and not n.available


def test_notams_from_the_faa_api(monkeypatch):
    monkeypatch.setenv("LOCALTC_FAA_ID", "id")
    monkeypatch.setenv("LOCALTC_FAA_SECRET", "secret")
    seen = {}

    def fetch(url, params=None, headers=None, **kw):
        seen.update(headers)
        return {"totalPages": 1, "items": [{"properties": {"coreNOTAMData": {"notam": {
            "number": "A5000/26", "icaoLocation": "EGLL", "text": "RWY 09L/27R CLSD",
            "effectiveStart": "2026-10-01T00:00:00.000Z", "effectiveEnd": "PERM",
            "icaoMessage": "A5000/26 NOTAMN Q) EGTT/QMRLC E) RWY 09L/27R CLSD"}}}}]}

    clock = Clock(1_792_000_000.0)
    n = Notams(DataKeys(memory={}), Cache(clock=clock), fetch=fetch, clock=clock)
    notams, got = n.for_airport("egll")
    assert seen == {"client_id": "id", "client_secret": "secret"}
    assert [x.id for x in notams] == ["A5000/26"] and notams[0].end is None and notams[0].in_force(clock.t)
    assert got.source == "FAA NOTAM API"


def test_real_notams_replace_the_drawn_ones_on_the_atis():
    from localtc.airports import load_airport
    from localtc.atc_core.airport import AirportGeometry
    from localtc.atc_core.atis.operations import Notice

    geo = AirportGeometry(load_airport("tests/fixtures/airports_real/CYUL.json"))
    board = AtisBoard(seed=1)
    drawn = board.notices("CYUL", geo)
    assert all(not n.real for n in drawn)
    board.real["CYUL"] = (Notice("taxiway_closed", "B", real="A1/26"),)
    assert board.notices("CYUL", geo) == (Notice("taxiway_closed", "B", real="A1/26"),)  # never mixed


TFR = """<XNOTAM-Update><Group><Add><Not><dateEffective>2026-10-14T15:00:00</dateEffective>
<dateExpire>2026-10-19T03:00:00</dateExpire><TfrNot><TFRAreaGroup><aseTFRArea><txtName>Hazard Area1</txtName>
<valDistVerUpper>6100</valDistVerUpper><valDistVerLower>0</valDistVerLower></aseTFRArea><abdMergedArea>
<Avx><geoLat>44.06666667N</geoLat><geoLong>118.81666667W</geoLong></Avx>
<Avx><geoLat>44.06666667N</geoLat><geoLong>118.73333333W</geoLong></Avx>
<Avx><geoLat>44.0N</geoLat><geoLong>118.81666667W</geoLong></Avx></abdMergedArea></TFRAreaGroup></TfrNot></Not>
</Add></Group></XNOTAM-Update>"""


def test_a_tfrs_area_from_the_faas_xml():
    [area] = parse_tfr(TFR, "6/9375", "HAZARDS")
    assert area.polygon[0] == (44.06666667, -118.81666667) and area.ceiling_ft == 6100 and area.floor_ft == 0
    assert area.end is not None and area.start < area.end and "6/9375" in area.name
    assert area.contains(44.04, -118.78) and not area.contains(45.0, -118.78)


def png(rgb: np.ndarray) -> bytes:
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_terrain_tiles_read_and_only_what_is_here(tmp_path):
    rgb = np.zeros((256, 256, 3), dtype=np.uint8)
    metres = 1000
    v = metres + 32768
    rgb[..., 0], rgb[..., 1] = v // 256, v % 256
    x, y = tile_xy(46.6, 8.0)  # a 3,000 m peak 6 nm north of the point
    px, py = int((x % 1) * 256), int((y % 1) * 256)
    rgb[py - 2:py + 2, px - 2:px + 2, 0] = (3000 + 32768) // 256
    rgb[py - 2:py + 2, px - 2:px + 2, 1] = (3000 + 32768) % 256
    assert decode_png(png(rgb)).shape == (256, 256, 3)
    t = Terrain(tmp_path, get=lambda url: png(rgb))
    assert t.elevation_ft(46.5, 8.0) is None  # not fetched: not known, never guessed
    assert t.fetch_around(46.5, 8.0, 5) >= 1
    assert abs(t.elevation_ft(46.5, 8.0) - 3281) < 2  # 1,000 m
    assert t.highest_ft(46.5, 8.0, 3) < 3300 and t.highest_ft(46.5, 8.0, 10) >= 9800  # the peak, within 10 nm


def test_areas_contain_points():
    a = Area(id="r1", kind="restricted", name="R-1", polygon=((0, 0), (0, 1), (1, 1), (1, 0)))
    assert a.contains(0.5, 0.5) and not a.contains(2, 2)
