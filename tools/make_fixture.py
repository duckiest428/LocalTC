"""Generate the synthetic test recording in tests/fixtures/pattern_short.

A 60-second departure: parked, squawk change 7000 -> 1200, PTT call to tower,
takeoff, climb-out, COM1 change, a pause, and two traffic targets. Output is
deterministic, so re-running only changes the fixture when this script changes.

    python tools/make_fixture.py
"""

import asyncio
import math
from array import array
from pathlib import Path

from localtc import __version__
from localtc.recorder import SCHEMA_VERSION, Recorder, RecordingHeader
from localtc.sim_api import (
    AircraftIdentity,
    AtcTransmission,
    ConnectionStatus,
    OwnshipState,
    PttPressed,
    PttReleased,
    SessionInfo,
    SimLifecycle,
    TrafficSnapshot,
    TrafficTarget,
    Transcript,
)

OUT_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "pattern_short"
DURATION_S = 60.0
TICK_S = 0.25
FIELD_ELEV_FT = 606.0
MAG_VAR = 16.0  # east
RUNWAY_TRUE = 340.0
START = (47.9005, -122.2790)


def tone(seconds: float, sample_rate: int = 16_000, freq: float = 440.0) -> bytes:
    n = int(seconds * sample_rate)
    samples = array("h", (int(6000 * math.sin(2 * math.pi * freq * i / sample_rate)) for i in range(n)))
    return samples.tobytes()


def ownship_track() -> list[OwnshipState]:
    lat, lon = START
    alt_agl = 0.0
    states = []
    for i in range(int(DURATION_S / TICK_S) + 1):
        t = i * TICK_S
        paused = 45.0 <= t < 47.0
        if t < 15.0:
            gs, vs = (0.0 if t < 8.0 else 6.0), 0.0
        elif t < 25.0:
            gs, vs = 6.0 + (t - 15.0) * 5.4, 0.0
        else:
            gs, vs = 72.0, 700.0
        if not paused:
            dist_nm = gs * TICK_S / 3600
            lat += dist_nm * math.cos(math.radians(RUNWAY_TRUE)) / 60
            lon += dist_nm * math.sin(math.radians(RUNWAY_TRUE)) / (60 * math.cos(math.radians(lat)))
            alt_agl += vs * TICK_S / 60
        tower = t < 40.0
        states.append(
            OwnshipState(
                t=t,
                lat=round(lat, 6),
                lon=round(lon, 6),
                alt_msl_ft=round(FIELD_ELEV_FT + alt_agl, 1),
                alt_indicated_ft=round(FIELD_ELEV_FT + alt_agl + 20, 1),
                alt_agl_ft=round(alt_agl, 1),
                altimeter_inhg=30.12,
                hdg_mag=RUNWAY_TRUE - MAG_VAR,
                hdg_true=RUNWAY_TRUE,
                ias_kt=round(max(0.0, gs - 2) if gs else 0.0, 1),
                gs_kt=round(gs, 1),
                vs_fpm=0 if paused else round(vs),
                on_ground=alt_agl <= 0,
                squawk="7000" if t < 5.0 else "1200",
                xpdr_mode="standby" if t < 14.0 else "alt",
                com1_mhz=120.2 if tower else 124.675,
                com2_mhz=128.65,
                com1_tx=10.0 <= t < 11.0,
                com1_type="TWR" if tower else "APPR",
                com1_ident="KPAE" if tower else "SEATTLE",
                gear_down=True,
                flaps_index=1 if t < 35.0 else 0,
                parking_brake=t < 8.0,
                engine_running=True,
            )
        )
    return states


def traffic() -> list[TrafficSnapshot]:
    snaps = []
    for k in range(int(DURATION_S // 3) + 1):
        t = k * 3.0 + 0.1
        angle = math.radians(k * 12)
        snaps.append(
            TrafficSnapshot(
                t=t,
                targets=(
                    TrafficTarget(
                        object_id=4242, atc_id="N12345", atc_model="PA28",
                        lat=round(47.91 + 0.02 * math.cos(angle), 6), lon=round(-122.29 + 0.03 * math.sin(angle), 6),
                        alt_ft=1600.0, hdg_true=round((k * 12 + 90) % 360, 1), gs_kt=95.0, on_ground=False,
                    ),
                    TrafficTarget(
                        object_id=4243, atc_id="N737AS", airline="Alaska", flight_number="123", atc_model="B738",
                        lat=round(47.80 + 0.004 * k, 6), lon=-122.30,
                        alt_ft=round(6000.0 - 50 * k, 1), hdg_true=0.0, gs_kt=210.0, on_ground=False,
                    ),
                ),
            )
        )
    return snaps


async def main() -> None:
    header = RecordingHeader(
        schema=SCHEMA_VERSION,
        localtc_version=__version__,
        created="2026-09-16T00:00:00+00:00",
        session=SessionInfo(source_kind="live", sim_product="MSFS 2024", sim_version="12.1.0.0",
                            simconnect_version="12.1.0.0"),
        config={"note": "synthetic fixture from tools/make_fixture.py"},
    )
    for old in OUT_DIR.glob("**/*"):
        if old.is_file():
            old.unlink()
    recorder = Recorder(OUT_DIR, header)
    await recorder.start()
    audio_ref = recorder.save_audio(tone(1.0), sample_rate=16_000)

    events = [
        ConnectionStatus(t=0.0, connected=True, detail="MSFS 2024 12.1.0.0 (synthetic)"),
        SimLifecycle(t=0.0, kind="flight_loaded", detail="flights\\synthetic_pattern.flt"),
        AircraftIdentity(t=0.0, title="Cessna Skyhawk G1000", atc_id="N172LT", atc_type="Cessna", atc_model="C172"),
        SimLifecycle(t=0.05, kind="sim_start"),
        PttPressed(t=10.0),
        PttReleased(t=11.0, audio_ref=audio_ref),
        Transcript(t=11.4, text="Paine tower, Skyhawk one seven two lima tango, holding short runway three four left, "
                                "ready for departure", confidence=0.93, audio_ref=audio_ref),
        AtcTransmission(t=13.0, station="Paine Tower", frequency_mhz=120.2,
                        text="Skyhawk one seven two lima tango, runway three four left, cleared for takeoff"),
        SimLifecycle(t=45.0, kind="paused", detail="full"),
        SimLifecycle(t=47.0, kind="unpaused"),
        *ownship_track(),
        *traffic(),
    ]
    for ev in sorted(events, key=lambda e: e.t):
        recorder.record(ev)
    await recorder.close()
    print(f"Wrote {recorder.events_written} events to {recorder.session_file}")


if __name__ == "__main__":
    asyncio.run(main())
