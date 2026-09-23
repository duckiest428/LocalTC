"""Airport data: Facilities API parsing, bridge fetching and the JSON cache."""

import asyncio
import math

import pytest
from helpers.airports import kbfi, kpae, make_airport
from helpers.sim_fakes import FakeSimConnect, airport_list_message, airport_messages

from localtc.airports import AirportCache, load_airport
from localtc.config import LiveConfig
from localtc.sim_api import AirportData, RequestAirportData, SetComFrequency
from localtc.sim_bridge import facilities as fac
from localtc.sim_bridge.protocol import AirportList, FacilityData, FacilityDataEnd, parse_message
from localtc.sim_bridge.simconnect_source import SimConnectSource


def test_definition_is_balanced():
    lines = fac.definition_lines()
    assert lines[0] == "OPEN AIRPORT" and lines[-1] == "CLOSE AIRPORT"
    opens = [line[5:] for line in lines if line.startswith("OPEN ")]
    closes = [line[6:] for line in lines if line.startswith("CLOSE ")]
    assert sorted(opens) == sorted(closes)


def _meters(lat1, lon1, lat2, lon2):
    return fac.haversine_nm(lat1, lon1, lat2, lon2) * 1852


def assemble(messages):
    assembler = fac.AirportAssembler("KPAE")
    for raw in messages:
        msg = parse_message(raw)
        if isinstance(msg, FacilityData):
            assembler.add(msg)
        else:
            assert isinstance(msg, FacilityDataEnd)
    return assembler


@pytest.mark.parametrize("documented_ids", [False, True], ids=["sim-ids-28-29", "doc-ids-29-30"])
@pytest.mark.parametrize("bool8", [False, True], ids=["BOOL-header", "bool-header"])
def test_assembler_round_trip(bool8, documented_ids):
    source = kpae()
    assembler = assemble(airport_messages(source, 101, bool8=bool8, documented_ids=documented_ids))
    airport = assembler.build()
    assert not assembler.unclassified
    assert set(assembler.offsets) == {37 if bool8 else 40}

    assert (airport.icao, airport.name, airport.region) == (source.icao, source.name, source.region)
    assert airport.elev_ft == pytest.approx(source.elev_ft, abs=0.2)
    assert [r.name for r in airport.runways] == ["16R/34L"]
    assert airport.runways[0].secondary.ils_ident == "IPAE"
    assert [(f.kind, f.mhz) for f in airport.frequencies] == [(f.kind, f.mhz) for f in source.frequencies]
    assert [(p.kind, p.name, p.runway, p.start, p.end) for p in airport.taxi_paths] == [
        (p.kind, p.name, p.runway, p.start, p.end) for p in source.taxi_paths
    ]
    for got, want in zip(airport.taxi_points, source.taxi_points, strict=True):
        assert got.kind == want.kind
        assert _meters(got.lat, got.lon, want.lat, want.lon) < 0.5  # float32 bias precision
    assert [p.name for p in airport.parking] == ["PARKING 1", "PARKING 2"]
    assert "UNCLASSIFIED" not in assembler.report()


def test_unclassified_items_are_reported():
    msgs = airport_messages(kpae(), 5)
    garbage = bytearray(msgs[1])
    garbage[0] += 4  # dwSize: payload no longer matches any definition
    garbage += b"\0" * 4
    assembler = assemble([msgs[0], bytes(garbage)])
    assert assembler.unclassified and "UNCLASSIFIED" in assembler.report()


@pytest.mark.parametrize("ident_len", [6, 9])
def test_airport_list_element_size_is_detected(ident_len):
    msg = parse_message(airport_list_message(4, [kpae(), kbfi()], ident_len=ident_len))
    assert isinstance(msg, AirportList)
    assert [(a.icao, a.region) for a in msg.airports] == [("KPAE", "K1"), ("KBFI", "K1")]
    assert msg.airports[1].lat == pytest.approx(47.53)


FAST = LiveConfig(ownship_hz=50, traffic_interval_s=0, nearest_airport_interval_s=0.05, retry_max_s=0.05)


async def _airports(source, count, timeout=3.0):
    got = []

    async def run():
        async for ev in source.events():
            if isinstance(ev, AirportData):
                got.append(ev.airport)
                if len(got) >= count:
                    return

    await asyncio.wait_for(run(), timeout)
    return got


def test_bridge_fetches_nearest_and_requested_airports():
    fake = FakeSimConnect(airports=(kpae(), kbfi()), facility_bool8=True)

    async def main():
        source = SimConnectSource(FAST, dll_factory=lambda: fake)
        await asyncio.wait_for(source.start(), 3)
        nearest = await _airports(source, 1)
        await source.send(RequestAirportData(icao="kbfi"))
        await source.send(RequestAirportData(icao="XXXX"))  # unknown: no event, no crash
        requested = await _airports(source, 1)
        await asyncio.sleep(0.2)
        await source.stop()
        return nearest, requested

    nearest, requested = asyncio.run(main())
    # The fake aircraft sits at KPAE, so KPAE is nearest; it's fetched once despite repeated list polls.
    assert [a.icao for a in nearest] == ["KPAE"]
    assert [a.icao for a in requested] == ["KBFI"]
    assert fake.facility_requests.count("KPAE") == 1
    assert "XXXX" in fake.facility_requests
    assert fake.facility_definition == fac.definition_lines()


def test_bridge_lists_the_airports_around_for_diversions():
    from localtc.sim_api import NearbyAirports

    fake = FakeSimConnect(airports=(kpae(), kbfi()), facility_bool8=True)

    async def main():
        source = SimConnectSource(FAST, dll_factory=lambda: fake)
        await asyncio.wait_for(source.start(), 3)

        async def first():
            async for ev in source.events():
                if isinstance(ev, NearbyAirports):
                    return ev

        found = await asyncio.wait_for(first(), 3)
        await source.stop()
        return found

    found = asyncio.run(main())
    assert [a.icao for a in found.airports] == ["KPAE", "KBFI"]  # nearest first (the fake sits at Paine)
    assert found.airports[1].lat == pytest.approx(kbfi().lat, abs=1e-4)


def test_airport_cache_round_trip(tmp_path):
    cache = AirportCache(tmp_path)
    assert cache.get("KPAE") is None
    path = cache.put(kpae())
    assert path.name == "KPAE.json"
    assert cache.get("kpae") == kpae() == load_airport(path)
    path.write_text("{broken", encoding="utf-8")
    assert cache.get("KPAE") is None


def test_haversine():
    assert fac.haversine_nm(47.9063, -122.2816, 47.53, -122.302) == pytest.approx(22.6, abs=0.2)
    assert math.isclose(fac.haversine_nm(0, 0, 1, 0), 60.04, rel_tol=1e-3)


def test_debug_airport_report(tmp_path, capsys):
    from localtc.app import debug_airport
    from localtc.cli import format_airport
    from localtc.config import Config

    cfg = Config()
    cfg.live.ownship_hz, cfg.live.traffic_interval_s = 50, 0
    raw = tmp_path / "raw.bin"
    fake = FakeSimConnect(airports=(kpae(),))
    report = asyncio.run(debug_airport(cfg, "KPAE", raw_path=raw, timeout=3, dll_factory=lambda: fake))
    assert report.airport is not None and report.airport.icao == "KPAE"
    assert (28, 0, 108) in report.messages  # airport item: id 28, type 0, 108 data bytes
    assert raw.stat().st_size > 0
    text = format_airport(report.airport)
    assert "16R/34L" in text and "tower" in text and "A1" in text and "(2 hold-short)" not in text


def test_facility_data_end_with_the_id_msfs_2024_actually_sends():
    """Regression: MSFS 2024 sent a 16-byte FACILITY_DATA_END with ID 29, which crashed the bridge."""
    import struct as _struct

    raw = _struct.pack("<IIII", 16, 1, 29, 101)
    msg = parse_message(raw)
    assert isinstance(msg, FacilityDataEnd) and msg.request_id == 101


def test_unparseable_message_does_not_drop_the_connection():
    import struct as _struct

    fake = FakeSimConnect(airports=(kpae(),))

    async def main():
        source = SimConnectSource(FAST, dll_factory=lambda: fake)
        await asyncio.wait_for(source.start(), 3)
        fake.push(_struct.pack("<III", 12, 1, 29))  # truncated facility message
        await source.send(RequestAirportData(icao="KPAE"))
        airports = await _airports(source, 1)
        await source.stop()
        return airports

    assert [a.icao for a in asyncio.run(main())] == ["KPAE"]


def test_bridge_tunes_com1_for_the_copilot():
    fake = FakeSimConnect()

    async def main():
        source = SimConnectSource(FAST, dll_factory=lambda: fake)
        await asyncio.wait_for(source.start(), 3)
        await source.send(SetComFrequency(hz=120_425_000))
        await source.send(SetComFrequency(hz=121_500_000, radio=2))
        for _ in range(100):
            if len(fake.transmitted) >= 2:
                break
            await asyncio.sleep(0.02)
        await source.stop()

    asyncio.run(main())
    assert fake.client_events == {20: "COM_RADIO_SET_HZ", 21: "COM2_RADIO_SET_HZ"}
    assert [(name, data) for name, data, *_ in fake.transmitted] == [
        ("COM_RADIO_SET_HZ", 120_425_000), ("COM2_RADIO_SET_HZ", 121_500_000)
    ]
    assert all(obj == 0 and flags == 0x10 for _, _, obj, _, flags in fake.transmitted)  # user aircraft, priority group


def test_published_approaches_are_read_and_offered():
    from localtc.atc_core.airport import published, select_approach

    source = make_airport(
        "KTST", "TEST FIELD", (47.0, -122.0), 500.0, 340.0, ("16R", "34L"), {"tower": (120.0, "TEST TOWER")},
        approaches=(("ils", "34L"), ("rnav", "34L"), ("vor", "16R")),
    )
    airport = assemble(airport_messages(source, 7)).build()
    assert [(a.kind, a.runway) for a in airport.approaches] == [("ils", "34L"), ("rnav", "34L"), ("vor", "16R")]
    assert published(airport, "34L") == ("ILS", "RNAV")
    assert published(airport, "16R") == ("VOR",)
    assert select_approach(airport, "34L", has_ils=True) == "ILS"
    assert select_approach(airport, "16R", visibility_sm=10.0) == "VISUAL"  # only a VOR approach: the visual is simpler
    assert select_approach(airport, "16R", visibility_sm=1.0) == "VOR"  # in the weather, fly the VOR
    assert select_approach(airport, "05", visibility_sm=10.0) == "VISUAL"  # nothing published to that runway
    assert select_approach(airport, "34L", aircraft_type="Piper Cub") == "VISUAL"  # no instrument capability
    assert select_approach(None, "34L", has_ils=True) == "ILS"  # airport data with no approaches at all
    assert select_approach(None, "34L", has_ils=False) == "RNAV"
