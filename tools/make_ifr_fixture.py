"""Generate tests/fixtures/ifr_kpae_kbfi (synthetic IFR hop), tests/fixtures/vfr_kpae_pattern (closed traffic
with a touch and go) and tests/fixtures/airports/*.json.

    python tools/make_ifr_fixture.py                    # all of them
    python tools/make_ifr_fixture.py vfr_kpae_pattern   # just one recording
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from helpers.airports import kbfi, kpae  # noqa: E402
from helpers.flightgen import ifr_kpae_kbfi, vfr_kpae_pattern  # noqa: E402

from localtc import __version__  # noqa: E402
from localtc.airports import dump_airport  # noqa: E402
from localtc.recorder import SCHEMA_VERSION, Recorder, RecordingHeader  # noqa: E402
from localtc.sim_api import SessionInfo  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


FLIGHTS = {
    "ifr_kpae_kbfi": (lambda: ifr_kpae_kbfi(kpae(), kbfi()), "synthetic IFR KPAE-KBFI", "KBFI", 5000),
    "vfr_kpae_pattern": (lambda: vfr_kpae_pattern(kpae()), "synthetic VFR closed traffic at KPAE", "KPAE", 1600),
}


async def write(name: str) -> None:
    build, note, destination, cruise = FLIGHTS[name]
    out = FIXTURES / name
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("session.jsonl*"):
        old.unlink()
    header = RecordingHeader(
        schema=SCHEMA_VERSION, localtc_version=__version__, created="2026-09-16T00:00:00+00:00",
        session=SessionInfo(source_kind="live", sim_product="MSFS 2024", sim_version="12.1.0.0"),
        config={"note": f"{note} from tools/make_ifr_fixture.py", "destination": destination, "cruise_ft": cruise},
    )
    recorder = Recorder(out, header, compress=True)
    async with recorder:
        for event in build().sorted_events():
            recorder.record(event)
    print(f"Wrote {recorder.events_written} events to {recorder.session_file}")


async def main(names: list[str]) -> None:
    if not names:
        for airport in (kpae(), kbfi()):
            dump_airport(airport, FIXTURES / "airports" / f"{airport.icao}.json")
    for name in names or FLIGHTS:
        await write(name)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
