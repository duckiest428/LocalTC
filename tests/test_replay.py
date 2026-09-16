import asyncio
from pathlib import Path

import pytest
from helpers.contract import check_source_contract

from localtc.recorder.format import SESSION_FILE, RecordingHeader, encode_line
from localtc.replay import Recording, RecordingFormatError, ReplaySource
from localtc.sim_api import (
    SIM_EVENT_TYPES,
    AircraftIdentity,
    ConnectionStatus,
    OwnshipState,
    PttPressed,
    SessionInfo,
    SimLifecycle,
)

FIXTURE = Path(__file__).parent / "fixtures" / "pattern_short"


def write_recording(tmp_path: Path, events, extra_lines: list[bytes] = ()) -> Path:
    header = RecordingHeader(schema=1, localtc_version="0.1.0", created="2026-09-16T00:00:00+00:00",
                             session=SessionInfo(source_kind="live", sim_product="MSFS 2024"))
    body = encode_line(header) + b"".join(encode_line(e) for e in events) + b"".join(extra_lines)
    (tmp_path / SESSION_FILE).write_bytes(body)
    return tmp_path


def collect(source: ReplaySource, limit: int | None = None) -> list:
    async def main():
        await source.start()
        out = []
        async for ev in source.events():
            out.append(ev)
            if limit and len(out) >= limit:
                break
        await source.stop()
        return out

    return asyncio.run(main())


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(round(delay, 6))
        self.now += delay


def test_fixture_satisfies_source_contract():
    info, events = asyncio.run(check_source_contract(ReplaySource(FIXTURE, speed=0), max_events=10_000))
    assert info.source_kind == "replay" and info.sim_product == "MSFS 2024"
    assert info.recording == str(FIXTURE)
    assert len(events) == 272


def test_pacing_scales_with_speed(tmp_path):
    path = write_recording(tmp_path, [SimLifecycle(t=t, kind="paused") for t in (10.0, 11.0, 13.0)])
    fake = FakeTime()
    events = collect(ReplaySource(path, speed=2.0, time_fn=fake.time, sleep_fn=fake.sleep))
    assert [e.t for e in events] == [10.0, 11.0, 13.0]
    assert fake.sleeps == [0.5, 1.0]


def test_speed_zero_never_sleeps(tmp_path):
    path = write_recording(tmp_path, [SimLifecycle(t=float(t), kind="paused") for t in range(5)])
    fake = FakeTime()
    assert len(collect(ReplaySource(path, speed=0, time_fn=fake.time, sleep_fn=fake.sleep))) == 5
    assert fake.sleeps == []


def test_start_at_primes_state_and_end_at_stops(tmp_path):
    events = [
        ConnectionStatus(t=0.0, connected=True),
        AircraftIdentity(t=0.0, atc_id="OLD"),
        AircraftIdentity(t=1.0, atc_id="N172LT"),
        SimLifecycle(t=2.0, kind="sim_start"),
        SimLifecycle(t=5.0, kind="paused"),
        SimLifecycle(t=6.0, kind="unpaused"),
        SimLifecycle(t=9.0, kind="sim_stop"),
    ]
    out = collect(ReplaySource(write_recording(tmp_path, events), speed=0, start_at=4.0, end_at=6.0))
    assert [(type(e).__name__, e.t) for e in out] == [
        ("ConnectionStatus", 5.0), ("AircraftIdentity", 5.0), ("SimLifecycle", 5.0), ("SimLifecycle", 6.0),
    ]
    assert out[1].atc_id == "N172LT"


def test_loop_keeps_time_increasing(tmp_path):
    path = write_recording(tmp_path, [SimLifecycle(t=t, kind="paused") for t in (0.0, 2.0)])
    out = collect(ReplaySource(path, speed=0, loop=True), limit=6)
    assert [e.t for e in out] == [0.0, 2.0, 3.0, 5.0, 6.0, 8.0]


def test_include_radio_false_filters_radio_events():
    out = collect(ReplaySource(FIXTURE, speed=0, include_radio=False))
    assert out and all(isinstance(e, SIM_EVENT_TYPES) for e in out)


def test_stop_interrupts_sleep(tmp_path):
    path = write_recording(tmp_path, [SimLifecycle(t=0.0, kind="paused"), SimLifecycle(t=3600.0, kind="unpaused")])

    async def main():
        source = ReplaySource(path, speed=1.0)
        await source.start()
        events = source.events()
        assert (await anext(events)).t == 0.0
        waiter = asyncio.create_task(anext(events, None))
        await asyncio.sleep(0.05)
        await source.stop()
        return await asyncio.wait_for(waiter, 1.0)

    assert asyncio.run(main()) is None


def test_skips_unknown_and_truncated_lines(tmp_path):
    path = write_recording(
        tmp_path,
        [PttPressed(t=1.0)],
        extra_lines=[b'{"type":"from_the_future","t":2.0}\n', b'{"type":"ptt_pressed","t":3.0}\n', b'{"type":"ptt_pre'],
    )
    rec = Recording(path)
    assert [e.t for e in rec.events()] == [1.0, 3.0]
    assert rec.skipped_lines == 2


def test_rejects_bad_or_newer_header(tmp_path):
    (tmp_path / SESSION_FILE).write_bytes(b'{"not":"a header"}\n')
    with pytest.raises(RecordingFormatError):
        Recording(tmp_path)
    (tmp_path / SESSION_FILE).write_bytes(
        b'{"type":"header","schema":99,"localtc_version":"9","created":"x","session":{"source_kind":"live"}}\n'
    )
    with pytest.raises(RecordingFormatError, match="newer"):
        Recording(tmp_path)


def test_missing_recording():
    with pytest.raises(FileNotFoundError):
        Recording("/nonexistent/recording")


def test_resolve_audio_stays_inside_recording():
    rec = Recording(FIXTURE)
    assert rec.resolve_audio("audio/0001.wav").is_file()
    with pytest.raises(ValueError):
        rec.resolve_audio("../../../etc/passwd")


def test_fixture_squawk_and_com_changes():
    own = [e for e in Recording(FIXTURE).events() if isinstance(e, OwnshipState)]
    assert own[0].squawk == "7000" and own[-1].squawk == "1200"
    assert own[0].com1_mhz == 120.2 and own[-1].com1_mhz == 124.675
