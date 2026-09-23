"""FAA or ICAO: which wording a controller uses, from where it is, and how the words change."""

from pathlib import Path

import pytest

from localtc.atc_core.phraseology import TemplateLibrary, speech
from localtc.atc_core.region import FAA, Region, forced, region_for, speaking
from localtc.atc_core.values import Wind
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run


@pytest.mark.parametrize(("code", "style", "transition"), [
    ("KPHX", "faa", 18000), ("PHNL", "faa", 18000), ("TJSJ", "faa", 18000),
    ("CYVR", "faa", 18000), ("CZVR", "faa", 18000),  # Canada: FAA-style wording
    ("LIRF", "icao", 6000), ("LIRR", "icao", 6000), ("EGLL", "icao", 6000), ("EGGX", "icao", 6000),
    ("EDDF", "icao", 5000), ("YSSY", "icao", 10000), ("MMMX", "icao", 18500), ("FACT", "icao", 18000),
    ("XXXX", "icao", 6000), ("", "faa", 18000),
])
def test_the_region_of_a_place(code, style, transition):
    assert region_for(code) == Region(style, transition)


def test_a_setting_forces_one_style():
    assert forced("auto") is None
    assert forced("icao") == Region("icao", 6000)
    assert forced("faa", 10000) == Region("faa", 10000)


def test_icao_words():
    with speaking(Region("icao", 6000)):
        assert speech.frequency(128.3) == "one two eight decimal three"
        assert speech.altitude(5000) == "five thousand feet" and speech.altitude_display(5000) == "5,000 feet"
        assert speech.altitude(8000) == "flight level eight zero" and speech.altitude_display(8000) == "FL080"
        assert speech.altimeter(29.92) == "one zero one three" and speech.altimeter_display(29.92) == "1013"
        assert speech.wind(Wind(270, 10)) == "two seven zero degrees one zero knots"


def test_faa_words_are_unchanged():
    with speaking(FAA):
        assert speech.frequency(128.3) == "one two eight point three"
        assert speech.altitude(8000) == "eight thousand"
        assert speech.altimeter_display(29.92) == "29.92"
        assert speech.wind(Wind(270, 10)) == "two seven zero at one zero"


def test_icao_templates_reword_only_the_words():
    faa, icao = TemplateLibrary.load(), TemplateLibrary.load(style="icao")
    assert icao.get("ground.taxi_out").text != faa.get("ground.taxi_out").text
    assert icao.get("ground.taxi_out").readback == faa.get("ground.taxi_out").readback
    assert set(icao.templates) == set(faa.templates)


FLIGHT = Path(__file__).parent / "fixtures" / "real_ksea_lirf"


@pytest.fixture(scope="module")
def ksea_lirf() -> list[str]:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return [line for line in run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True).lines if " ATC " in line]


def test_seattle_to_rome_is_faa_at_home_and_icao_in_italy(ksea_lirf):
    seattle = [line for line in ksea_lirf if "Seattle" in line.split(":")[0]]
    rome = [line for line in ksea_lirf if "Fiumicino" in line.split(":")[0]]
    assert seattle and rome
    assert any("cleared to land" in line and "degrees" in line and "knots" in line for line in rome)
    assert not any("degrees" in line for line in seattle)
    assert any("vacate" in line for line in rome)
