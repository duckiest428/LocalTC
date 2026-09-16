"""The live bridge, driven by a fake DLL that emits real SimConnect message layouts."""

import asyncio
import sys

import pytest
from helpers.contract import check_source_contract
from helpers.sim_fakes import USER_OBJECT_ID, FakeSimConnect, data_message, open_message

from localtc.config import LiveConfig
from localtc.sim_api import (
    AircraftIdentity,
    ConnectionStatus,
    OwnshipState,
    SimLifecycle,
    SourceUnavailable,
    TrafficSnapshot,
)
from localtc.sim_bridge import definitions as defs
from localtc.sim_bridge.dll import find_dll
from localtc.sim_bridge.protocol import (
    SIMOBJECT_DATA_OFFSET,
    EventInfo,
    ExceptionInfo,
    ObjectData,
    OpenInfo,
    ProtocolError,
    RecvId,
    parse_message,
)
from localtc.sim_bridge.simconnect_source import SimConnectSource, _TrafficRound

FAST = LiveConfig(ownship_hz=50, traffic_interval_s=0.05, retry_max_s=0.05)


# --- protocol & definitions ----------------------------------------------------


def test_simobject_data_offset_matches_simconnect_h():
    assert SIMOBJECT_DATA_OFFSET == 40


def test_parse_open():
    msg = parse_message(open_message(12))
    assert isinstance(msg, OpenInfo)
    assert msg.app_name == "KittyHawk" and msg.app_version == (12, 1, 0, 0)


def test_parse_data_and_short_buffer():
    payload = defs.pack(defs.TRAFFIC, {**_traffic(), "atc_id": "N1"})
    msg = parse_message(data_message(RecvId.SIMOBJECT_DATA_BYTYPE, 3, 77, 2, 5, payload))
    assert isinstance(msg, ObjectData)
    assert (msg.request_id, msg.object_id, msg.entry, msg.out_of) == (3, 77, 2, 5)
    assert defs.unpack(defs.TRAFFIC, msg.payload)["atc_id"] == "N1"
    with pytest.raises(ProtocolError):
        parse_message(b"\x00" * 8)


def test_definitions_round_trip_and_decode():
    from helpers.sim_fakes import OWNSHIP_VALUES

    raw = defs.unpack(defs.OWNSHIP, defs.pack(defs.OWNSHIP, OWNSHIP_VALUES))
    own = defs.ownship_from_raw(raw, t=1.0)
    assert own.squawk == "1200" and own.xpdr_mode == "alt"
    assert own.com1_mhz == 118.3 and own.com1_type == "TWR"
    assert own.lat == 47.900512 and own.on_ground is True and own.parking_brake is True


def _traffic():
    from helpers.sim_fakes import traffic_values

    return traffic_values("X")


def test_traffic_round_counts_replies_not_entry_numbers():
    rnd = _TrafficRound()
    assert rnd.begin() is None
    assert rnd.add("a", out_of=2) is None
    assert rnd.add("b", out_of=2) == ("a", "b")
    assert rnd.add("late", out_of=2) is None
    rnd.begin()
    assert rnd.begin() == ()  # a round with no replies means nothing in range


# --- the source, end to end ------------------------------------------------------


async def _collect_until(source, predicate, timeout=3.0):
    seen = []

    async def run():
        async for ev in source.events():
            seen.append(ev)
            if predicate(seen):
                return

    await asyncio.wait_for(run(), timeout)
    return seen


def test_live_source_emits_decoded_events():
    fake = FakeSimConnect()

    async def main():
        source = SimConnectSource(FAST, dll_factory=lambda: fake)
        info = await asyncio.wait_for(source.start(), 3)
        fake.push_event("Pause_EX1", data=1)
        fake.push_event("FlightLoaded", filename="flights\\test.flt")
        fake.push_exception()  # logged, not fatal
        types_needed = {ConnectionStatus, OwnshipState, AircraftIdentity, TrafficSnapshot, SimLifecycle}
        seen = await _collect_until(
            source, lambda s: types_needed <= {type(e) for e in s} and sum(isinstance(e, SimLifecycle) for e in s) >= 2
        )
        await source.stop()
        return info, seen

    info, seen = asyncio.run(main())
    assert (info.source_kind, info.sim_product, info.sim_version) == ("live", "MSFS 2024", "12.1.0.0")
    assert "PLANE LATITUDE" in fake.definitions[1] and "Pause_EX1" in fake.system_events

    own = next(e for e in seen if isinstance(e, OwnshipState))
    assert own.squawk == "1200" and own.com1_mhz == 118.3
    assert next(e for e in seen if isinstance(e, AircraftIdentity)).atc_id == "N172LT"
    snapshot = next(e for e in seen if isinstance(e, TrafficSnapshot) and e.targets)
    assert [t.atc_id for t in snapshot.targets] == ["N12345"]  # own aircraft filtered out
    assert all(t.object_id != USER_OBJECT_ID for t in snapshot.targets)
    lifecycle = [(e.kind, e.detail) for e in seen if isinstance(e, SimLifecycle)]
    assert ("paused", "full") in lifecycle and ("flight_loaded", "flights\\test.flt") in lifecycle
    ts = [e.t for e in seen]
    assert ts == sorted(ts)


def test_live_source_waits_for_sim_then_reconnects_after_quit():
    fake = FakeSimConnect(failed_opens=2)

    async def main():
        source = SimConnectSource(FAST, dll_factory=lambda: fake)
        await asyncio.wait_for(source.start(), 3)
        fake.push_quit()
        seen = await _collect_until(
            source, lambda s: sum(isinstance(e, ConnectionStatus) and e.connected for e in s) >= 2
        )
        await source.stop()
        return seen

    seen = asyncio.run(main())
    statuses = [(e.connected, e.detail) for e in seen if isinstance(e, ConnectionStatus)]
    assert statuses[0][0] is False and "waiting for simulator" in statuses[0][1]  # reported once, not per retry
    assert [c for c, _ in statuses] == [False, True, False, True]
    assert fake.opens == 4 and fake.closes == 2  # the quit session, then stop()


def test_live_source_passes_contract():
    source = SimConnectSource(FAST, dll_factory=FakeSimConnect)
    asyncio.run(check_source_contract(source, min_events=20, max_events=20))


def test_wrong_sim_version_still_connects(caplog):
    async def main():
        source = SimConnectSource(FAST, dll_factory=lambda: FakeSimConnect(sim_major=11))
        info = await asyncio.wait_for(source.start(), 3)
        await source.stop()
        return info

    assert asyncio.run(main()).sim_product == "MSFS 2020"
    assert "targets MSFS 2024" in caplog.text


def test_start_raises_when_dll_cannot_load():
    def broken():
        raise SourceUnavailable("no dll")

    async def main():
        await SimConnectSource(FAST, dll_factory=broken).start()

    with pytest.raises(SourceUnavailable, match="no dll"):
        asyncio.run(main())


def test_connect_timeout():
    async def main():
        await SimConnectSource(
            LiveConfig(connect_timeout_s=0.2, retry_max_s=0.05), dll_factory=lambda: FakeSimConnect(failed_opens=10**6)
        ).start()

    with pytest.raises(TimeoutError):
        asyncio.run(main())


@pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows message")
def test_find_dll_explains_windows_only():
    with pytest.raises(SourceUnavailable, match="only runs on Windows"):
        find_dll()


def test_unhandled_message_types_are_ignored():
    from localtc.sim_bridge.protocol import RecvEvent, build_message

    assert parse_message(build_message(RecvEvent(), RecvId.EVENT_FRAME)) is None
    assert isinstance(parse_message(build_message(RecvEvent(uEventID=3, dwData=0), RecvId.EVENT)), EventInfo)
    from localtc.sim_bridge.protocol import RecvException

    exc = parse_message(build_message(RecvException(dwException=7), RecvId.EXCEPTION))
    assert isinstance(exc, ExceptionInfo) and exc.name == "NAME_UNRECOGNIZED"
