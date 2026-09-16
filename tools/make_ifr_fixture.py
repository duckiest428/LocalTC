"""Generate tests/fixtures/ifr_kpae_kbfi (synthetic IFR hop) and tests/fixtures/airports/*.json.

    python tools/make_ifr_fixture.py
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from helpers.airports import kbfi, kpae  # noqa: E402
from helpers.flightgen import ifr_kpae_kbfi  # noqa: E402

from localtc import __version__  # noqa: E402
from localtc.airports import dump_airport  # noqa: E402
from localtc.recorder import SCHEMA_VERSION, Recorder, RecordingHeader  # noqa: E402
from localtc.sim_api import SessionInfo  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


async def main() -> None:
    for airport in (kpae(), kbfi()):
        dump_airport(airport, FIXTURES / "airports" / f"{airport.icao}.json")
    out = FIXTURES / "ifr_kpae_kbfi"
    for old in out.glob("session.jsonl*"):
        old.unlink()
    header = RecordingHeader(
        schema=SCHEMA_VERSION, localtc_version=__version__, created="2026-09-16T00:00:00+00:00",
        session=SessionInfo(source_kind="live", sim_product="MSFS 2024", sim_version="12.1.0.0"),
        config={"note": "synthetic IFR KPAE-KBFI from tools/make_ifr_fixture.py", "destination": "KBFI", "cruise_ft": 5000},
    )
    recorder = Recorder(out, header, compress=True)
    async with recorder:
        for event in ifr_kpae_kbfi(kpae(), kbfi()).sorted_events():
            recorder.record(event)
    print(f"Wrote {recorder.events_written} events to {recorder.session_file}")


if __name__ == "__main__":
    asyncio.run(main())
