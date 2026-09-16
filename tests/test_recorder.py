import asyncio
import gzip
import wave
from datetime import datetime

import msgspec

from localtc.recorder import Recorder
from localtc.replay import Recording
from localtc.sim_api import PttReleased, SessionInfo, SimLifecycle

SESSION = SessionInfo(source_kind="live", sim_product="MSFS 2024", sim_version="12.1.0.0")


def test_writes_header_then_events(tmp_path):
    events = [SimLifecycle(t=0.0, kind="sim_start"), PttReleased(t=1.5, audio_ref=None)]

    async def main():
        rec = Recorder.create(tmp_path, SESSION, config={"k": 1}, now=datetime(2026, 9, 16, 11, 45, 0))
        async with rec:
            for ev in events:
                rec.record(ev)
        return rec

    rec = asyncio.run(main())
    assert rec.session_dir.name == "20260916-114500_live"
    lines = rec.session_file.read_bytes().splitlines()
    header = msgspec.json.decode(lines[0])
    assert header["type"] == "header" and header["schema"] == 1 and header["config"] == {"k": 1}
    assert header["session"]["sim_product"] == "MSFS 2024"
    assert rec.events_written == 2
    assert list(Recording(rec.session_dir).events()) == events


def test_directory_names_are_unique(tmp_path):
    now = datetime(2026, 9, 16, 11, 45, 0)
    first = Recorder.create(tmp_path, SESSION, now=now)
    first.session_dir.mkdir()
    second = Recorder.create(tmp_path, SESSION, now=now, label="my flight!")
    assert second.session_dir.name == "20260916-114500_my_flight"
    third = Recorder.create(tmp_path, SESSION, now=now)
    assert third.session_dir.name == "20260916-114500_live-2"


def test_flushes_while_running(tmp_path):
    async def main():
        rec = Recorder.create(tmp_path, SESSION, flush_interval=0.05)
        await rec.start()
        rec.record(SimLifecycle(t=0.0, kind="sim_start"))
        await asyncio.sleep(0.2)
        on_disk = rec.session_file.read_bytes().count(b"\n")
        await rec.close()
        return on_disk

    assert asyncio.run(main()) == 2  # header + event, before close


def test_consume_and_compress(tmp_path):
    async def source():
        for i in range(100):
            yield SimLifecycle(t=float(i), kind="paused")

    async def main():
        rec = Recorder.create(tmp_path, SESSION, compress=True)
        async with rec:
            await rec.consume(source())
        return rec

    rec = asyncio.run(main())
    assert rec.session_file.name == "session.jsonl.gz"
    assert not (rec.session_dir / "session.jsonl").exists()
    with gzip.open(rec.session_file) as fh:
        assert len(fh.read().splitlines()) == 101
    assert len(list(Recording(rec.session_dir).events())) == 100


def test_save_audio(tmp_path):
    async def main():
        rec = Recorder.create(tmp_path, SESSION)
        async with rec:
            return rec, rec.save_audio(b"\x00\x01" * 800, sample_rate=16_000), rec.save_audio(b"", sample_rate=16_000)

    rec, ref1, ref2 = asyncio.run(main())
    assert (ref1, ref2) == ("audio/0001.wav", "audio/0002.wav")
    with wave.open(str(Recording(rec.session_dir).resolve_audio(ref1))) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getnframes()) == (16_000, 1, 800)
