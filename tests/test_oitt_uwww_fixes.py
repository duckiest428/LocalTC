"""A Boeing 757 from Tabriz to Samara (Kurumoch), and what it showed: an Indian voice at Tabriz, an American one for
Tehran's radar, a low altitude alert at 2,600 ft over the Volga's lowland ("minimum FL076"), a nine-day-old METAR for
Tabriz, Samara Control keeping the arrival at FL180 over the field and past it, and Whisper's "Tabriz Ground, Tabriz
Ground, ..." from half a second of speech."""

from pathlib import Path

import numpy as np
import pytest

from localtc import metar_data
from localtc.atc_core import region
from localtc.config import load_config, with_recorded
from localtc.ops.terrain import SPIKE_FT, despike
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run
from localtc.stt.service import made_up
from localtc.tts import library

FLIGHT = Path(__file__).parent / "fixtures" / "real_oitt_uwww"


@pytest.fixture(scope="module")
def replay() -> list[str]:
    """The flight with the pilot's own calls, the copilot reading back and tuning as it did."""
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True, copilot="assist").lines


def when(line: str) -> float:
    return float(line.split("]")[0].strip("[ "))


def test_approach_takes_the_arrival_kept_at_fl180(replay):
    """Samara Control's descent stopped at the STAR's FL180: approach used to take arrivals only below 17,000 ft, and
    the flight crossed the field at FL180 and went 18 miles past (handed over at 10,685 s). Now ~50 miles out."""
    handoff = next(line for line in replay if "Samara Control" in line and "contact Samara Approach" in line)
    assert when(handoff) < 10000


def test_the_apron_called_on_the_way_in_sends_to_ground_and_the_gate_is_kept(replay):
    """Off the runway the pilot called Samara Apron (118.8) for gate 18: nobody was there at all."""
    apron = next(line for line in replay if "ATC       Samara Apron 118.8" in line)
    assert "contact Samara Ground 119.0" in apron
    taxi = next(line for line in replay if "ATC       Samara Ground 119.0" in line and "taxi" in line)
    assert "Gate 18" in taxi


def test_tehrans_radar_sectors_have_irans_voice():
    for sector in ("TEH-1N", "TEH-3S", "TEH-7", "TEH-W"):
        assert region.accent(sector) == "fa-IR"
    assert region.accent("OITT") == "fa-IR"
    assert region.accent("UBBA") == "az-AZ"  # the FIRs named by their ICAO code are as they were
    assert region.accent("BAIRES") == "es-AR"


def test_the_middle_easts_voices_arent_indian():
    for accent in ("fa", "me", "tr"):
        for voices in library.VOICES[accent].values():
            assert not any(part.startswith(("hf_", "hm_")) for v in voices for part in v.split("+"))


def test_a_lone_bad_pixel_isnt_ground():
    """Near Samara: 5,551 ft among 78 ft, a few of them together (4,146 beside 2,457)."""
    ground = np.full((9, 9), 100, dtype=np.int32)
    ground[2, 2] = 5551
    ground[6, 5], ground[6, 6], ground[5, 6] = 4146, 2457, 659
    out = despike(ground)
    assert out.max() <= 100 + SPIKE_FT
    assert out[2, 2] == 100


def test_a_real_summit_stays():
    """A peak with its ridges: each neighbour along a ridge within SPIKE_FT of it, as real ground is 300 m away."""
    yy, xx = np.mgrid[0:11, 0:11]
    mountain = (14000 - 700 * np.maximum(abs(yy - 5), abs(xx - 5))).astype(np.int32)
    assert (despike(mountain) == mountain).all()


def test_a_metar_days_old_is_none():
    now = 1_800_000_000.0
    assert metar_data.too_old({"obsTime": now - 9 * 86400, "rawOb": "METAR OITT 141530Z 00000KT"}, now)
    assert not metar_data.too_old({"obsTime": now - 3000, "rawOb": "METAR UWWW 230700Z 01003MPS"}, now)
    assert not metar_data.too_old({"rawOb": "METAR UWWW 230700Z"}, now)  # no time given: kept


def test_a_name_said_over_and_over_is_whisper_in_a_loop():
    looped = "Tabriz Ground, Tabriz Ground, Tabriz Ground, Tabriz Ground, Tabriz Ground, Tabriz Ground, Tabriz Ground,"
    assert made_up(looped, 3.1)
    assert not made_up("Squawk two two two two, Azure Air 4454", 3.0)
    assert not made_up("squawk seven seven seven seven", 2.5)
    assert not made_up("Descend flight level one one zero, cleared ILS one one one approach", 4.0)
