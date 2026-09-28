"""ATIS and weather: sampling at the airport, the letter, the runway in use, the ATC notes, and the console."""

import io
from pathlib import Path

import msgspec
import pytest

from localtc.airports import load_airport_dir
from localtc.atc_core.airport import AirportGeometry
from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.values import Wind
from localtc.atc_core.weather import AtisBoard, Weather, WeatherTracker, remarks
from localtc.console import FlightConsole
from localtc.replay import Recording
from localtc.sim_api import (
    AirportData,
    AtcAlert,
    AtcTransmission,
    AtisBroadcast,
    OwnshipState,
    PhaseChanged,
    RadioTuned,
    ReadbackEvaluated,
    Transcript,
)

FIXTURES = Path(__file__).parent / "fixtures"
AIRPORTS = {a.icao: a for a in load_airport_dir(FIXTURES / "airports")}


def parked(**kw) -> OwnshipState:
    own = next(e for e in Recording(FIXTURES / "ifr_kpae_kbfi").events() if isinstance(e, OwnshipState))
    return msgspec.structs.replace(own, **({"temperature_c": 14.0, "visibility_m": 16000.0, "zulu_s": 63180.0} | kw))


def geometry(icao: str) -> AirportGeometry:
    return AirportGeometry(AIRPORTS[icao])


def weather(direction_true: float, kt: int, *, gust: int | None = None, **kw) -> Weather:
    return Weather(Wind(int(direction_true) % 360 or 360, kt), gust_kt=gust, wind_dir_true=direction_true,
                   **({"altimeter_inhg": 29.92} | kw))


def test_surface_weather_is_sampled_on_the_ground_at_the_airport():
    tracker = WeatherTracker()
    geos = {"KPAE": geometry("KPAE"), "KBFI": geometry("KBFI")}
    tracker.update(parked(wind_dir_true=340.0, wind_kt=9.0, altimeter_setting_inhg=30.05), geos)
    kpae = tracker.surface("KPAE", geos)
    assert kpae is not None and kpae.wind.speed_kt == 9 and kpae.altimeter_inhg == 30.05 and kpae.temperature_c == 14
    assert kpae.visibility_sm == 9.9
    # KBFI hasn't been sampled: the nearby KPAE sample stands in until the aircraft gets there.
    stand_in = tracker.surface("KBFI", geos)
    assert (stand_in.wind, stand_in.altimeter_inhg, stand_in.visibility_sm) == (kpae.wind, kpae.altimeter_inhg, kpae.visibility_sm)


def test_the_letter_advances_only_on_a_real_change_and_not_too_often():
    board, geo = AtisBoard(seed=1), geometry("KPAE")
    first = board.update("KPAE", geo, weather(340, 8), 63180.0, "Paine Field", t=0.0)
    # Timed as the latest routine observation, 16:53 (it's 17:33).
    assert first is not None and first.runway == "34L" and first.zulu == "1653"
    assert board.update("KPAE", geo, weather(345, 9), 63180.0, "Paine Field", t=700.0) is None  # same weather
    assert board.update("KPAE", geo, weather(340, 8, altimeter_inhg=29.80), 63180.0, "Paine Field", t=100.0) is None  # too soon
    changed = board.update("KPAE", geo, Weather(Wind(340, 8), altimeter_inhg=29.80, wind_dir_true=340.0), 63900.0,
                           "Paine Field", t=700.0)
    assert changed is not None and changed.letter == chr(ord(first.letter) + 1) if first.letter != "Z" else "A"


def test_the_runway_in_use_holds_until_the_tailwind_is_too_strong():
    board, geo = AtisBoard(), geometry("KPAE")
    assert board.update("KPAE", geo, weather(340, 8), 0.0, "Paine Field").runway == "34L"
    assert board.update("KPAE", geo, weather(170, 4), 0.0, "Paine Field", t=700.0).runway == "34L"  # light tailwind
    assert board.update("KPAE", geo, weather(160, 12), 0.0, "Paine Field", t=1400.0).runway.startswith("16")


def test_cautions_for_the_weather():
    geo = geometry("KPAE")
    end = geo.end("34L")
    assert remarks(geo, end, weather(340, 8)) == ()
    assert "caution gusty winds" in remarks(geo, end, weather(340, 15, gust=27))
    assert "caution strong crosswind" in remarks(geo, end, weather(250, 20))
    assert "caution low visibility" in remarks(geo, end, weather(340, 5, visibility_sm=1.5))
    assert "caution wet runway" in remarks(geo, end, weather(340, 5, precip="rain"))


def test_atis_text_and_spoken_form():
    info = AtisBoard(seed=1).update("KPAE", geometry("KPAE"), weather(340, 8, temperature_c=14, visibility_sm=10.0),
                                   63180.0, "Paine Field")
    assert info.text.startswith(f"Paine Field information {info.text.split()[3]}")
    assert "Wind 340 at 8. Visibility 10. Temperature 14. Altimeter 29.92." in info.text
    assert "ILS runway 34L approach in use" in info.text
    assert "altimeter two niner niner two" in info.spoken and "three four left" in info.spoken


def engine_at_kpae() -> tuple[AtcEngine, OwnshipState]:
    engine = AtcEngine(EngineConfig(destination="KBFI", cruise_ft=5000, callsign="N172LT", seed=7))
    for airport in AIRPORTS.values():
        engine.handle(AirportData(t=0.0, airport=airport))
    own = parked()
    engine.handle(own)
    return engine, own


def test_tuning_the_atis_frequency_broadcasts_it():
    engine, own = engine_at_kpae()
    out = engine.handle(msgspec.structs.replace(own, t=own.t + 1, com1_mhz=128.65))
    tuned = next(e for e in out if isinstance(e, RadioTuned))
    atis = next(e for e in out if isinstance(e, AtisBroadcast))
    assert tuned.controller == "atis" and tuned.station == "Paine Field ATIS"
    assert atis.airport == "KPAE" and atis.letter == engine.current_atis("KPAE").letter
    # Tuned away and back: broadcast again (the pilot may have missed it).
    engine.handle(msgspec.structs.replace(own, t=own.t + 2, com1_mhz=121.8))
    again = engine.handle(msgspec.structs.replace(own, t=own.t + 3, com1_mhz=128.65))
    assert any(isinstance(e, AtisBroadcast) for e in again)


def test_taxi_clearance_gives_the_atis_only_to_a_pilot_without_it():
    for report, expect_note in (("", True), (", with information {letter}", False)):
        engine, own = engine_at_kpae()
        own = msgspec.structs.replace(own, com1_mhz=121.8)
        engine.handle(own)
        letter = engine.current_atis("KPAE").letter
        from localtc.atc_core.phraseology import speech

        text = f"Paine Ground, N172LT, ready to taxi{report}".format(letter=speech.letter(letter))
        engine.handle(Transcript(t=own.t + 1, text=text))
        out = []
        for dt in range(2, 8):
            out += [o for o in engine.handle(msgspec.structs.replace(own, t=own.t + dt)) if isinstance(o, AtcTransmission)]
        assert ("is current, altimeter" in out[0].text) == expect_note, out[0].text


def test_console_shows_the_radio_and_hides_the_noise():
    console = FlightConsole(io.StringIO(), color=False, width=80)
    long = "N172LT, cleared to Boeing Field airport as filed, climb and maintain 5,000, expect 5,000 one zero " \
           "minutes after departure, departure frequency 124.675, squawk 4521."
    lines = console.lines(AtcTransmission(t=75.0, station="Paine Clearance", frequency_mhz=127.25, text=long))
    assert lines[0] == "01:15  ATC  Paine Clearance 127.250"
    assert all(line.startswith(" " * 12) and len(line) <= 80 for line in lines[1:]) and len(lines) >= 3
    assert console.lines(Transcript(t=80.0, text="Cleared to Boeing Field, 2LT", source="voice")) == [
        "01:20  YOU  Cleared to Boeing Field, 2LT"]
    assert "readback ok" in console.lines(ReadbackEvaluated(t=80.0, instruction_id="clearance.ifr", status="correct"))[0]
    assert console.lines(PhaseChanged(t=90.0, previous="PARKED", phase="TAXI_OUT", reason="moving")) == ["01:30  -- Taxiing"]
    assert "Taxiing without a taxi clearance" in console.lines(AtcAlert(t=91.0, kind="taxi_without_clearance"))[0]
    tuned = RadioTuned(t=95.0, frequency_mhz=121.8, controller="ground", station="Paine Ground")
    assert console.lines(tuned) == ["01:35  COM1  121.800  Paine Ground"] and console.lines(tuned) == []  # once


# --- 0.4: the ATIS from every part of the weather and the airport's operations ---------------------------------------


from localtc.atc_core.atis import Layer, Notice
from localtc.atc_core.atis.observation import CloudProfile
from localtc.atc_core.region import Region

ICAO = Region("icao", 6000)


def atis(w: Weather, *, region=None, notams=False, zulu=63180.0, icao="KPAE"):
    return AtisBoard(seed=1, notams=notams, region=region).update(icao, geometry(icao), w, zulu, "Paine Field")


def test_the_faa_broadcast_in_its_order_with_every_weather_element():
    w = weather(160, 20, gust=32, visibility_sm=0.5, precip="rain", precip_rate_mm=9.0, temperature_c=2, dewpoint_c=1,
                sky=(Layer("FEW", 200), Layer("OVC", 300)), variable_from=130, variable_to=190)
    text = atis(w).text
    order = ["information", "Wind 160 at 20 gusts 32, variable between 130 and 190", "Visibility 1/2, heavy rain, fog",
             "RVR 4000", "Few clouds at 200, ceiling 300 overcast", "Temperature 2, dew point 1", "Altimeter 29.92",
             "approach in use", "Landing and departing runway", "condition codes 5, 5, 5", "wind shear", "Deicing",
             "Read back all runway hold short instructions", "Advise on initial contact"]
    places = [text.index(part) for part in order]
    assert places == sorted(places), text
    assert "RNAV runway 16R approach in use" in text  # below a visual: the instrument approach (16R has no ILS)


@pytest.mark.parametrize(("w", "said"), [
    (weather(0, 1), "Wind calm"),
    (weather(340, 4, variable_from=300, variable_to=20), "Wind variable at 4"),
    (weather(340, 8, visibility_sm=1.25), "Visibility 1 1/4"),
    (weather(340, 8, visibility_sm=0.25, in_cloud=True, sky=(Layer("VV", 100),)), "Indefinite ceiling 100, sky obscured"),
    (weather(340, 8, visibility_sm=4, temperature_c=25), "Visibility 4, haze"),
    (weather(340, 8, visibility_sm=4, smoke=True), "Visibility 4, smoke"),
    (weather(340, 8, precip="rain", precip_rate_mm=1.0, temperature_c=-1), "light freezing rain"),
    (weather(340, 8, sky=(), clear_below_ft=12500), "Sky clear"),
])
def test_faa_phrasing(w, said):
    assert said.lower() in atis(w).text.lower()


def test_the_icao_broadcast():
    info = atis(weather(270, 12, gust=25, visibility_sm=10, temperature_c=15, dewpoint_c=10, sky=(), clear_below_ft=6000),
                region=ICAO)
    text = info.text
    assert text.index("Expect ILS") < text.index("Transition level 70") < text.index("Wind 270 degrees 12 knots, gusting 25")
    assert "CAVOK" in text and "QNH 1013" in text and "Acknowledge receipt of information" in text
    low = atis(weather(270, 5, visibility_sm=0.3, in_cloud=True, sky=(Layer("VV", 100),), temperature_c=-2, dewpoint_c=-2),
               region=ICAO).text
    assert "Visibility 500 metres" in low and "RVR runway" in low and "Freezing fog" in low
    assert "Vertical visibility 100 feet" in low and "Low visibility procedures in operation" in low
    assert "Q N H one zero one three" in info.spoken and "transition level seven zero" in info.spoken


def test_a_new_letter_with_each_hourly_observation_even_unchanged():
    board, geo = AtisBoard(seed=1), geometry("KPAE")
    first = board.update("KPAE", geo, weather(340, 8), 63180.0, "Paine Field", t=0.0)  # 17:33, from the 16:53 METAR
    same_hour = board.update("KPAE", geo, weather(340, 8), 63900.0, "Paine Field", t=720.0)  # 17:45: nothing new
    next_hour = board.update("KPAE", geo, weather(340, 8), 64500.0, "Paine Field", t=1320.0)  # 17:55: the 17:53 METAR
    assert same_hour is None and next_hour is not None and next_hour.zulu == "1753"
    assert next_hour.letter == chr(ord(first.letter) + 1) if first.letter != "Z" else "A"


def test_notices_are_read_and_acted_on():
    geo = geometry("KPAE")
    board = AtisBoard(seed=1, notams=True)
    board._notices["KPAE"] = (Notice("glideslope_out", "34L"), Notice("taxiway_closed", "A"), Notice("birds"))
    info = board.update("KPAE", geo, weather(340, 8, visibility_sm=2, sky=(Layer("OVC", 600),)), 63180.0, "Paine Field")
    assert info.approach_for("34L").kind in ("LOC", "RNAV")  # no ILS without its glideslope
    assert info.approach_for("34L").kind != "ILS"
    assert "runway 34l ils glideslope out of service" in info.text.lower() and "taxiway a closed" in info.text.lower()
    assert "bird activity in the vicinity of the airport" in info.text.lower()
    assert info.operations.closed_taxiways == frozenset({"A"})
    from localtc.airports import load_airport

    cyul = AirportGeometry(load_airport(FIXTURES / "airports_real" / "CYUL.json"))
    usual = AtisBoard(seed=1).update("CYUL", cyul, weather(240, 10), 63180.0, "Montreal").runway
    closed = AtisBoard(seed=1, notams=True)
    closed._notices["CYUL"] = (Notice("runway_closed", cyul.end(usual).runway.name),)
    info = closed.update("CYUL", cyul, weather(240, 10), 63180.0, "Montreal")
    assert info.runway != usual and info.closed(usual)  # never a closed runway
    assert f"runway {cyul.end(usual).runway.name} closed".lower() in info.text.lower()


def test_notices_are_the_same_for_the_same_seed():
    from localtc.atc_core.atis.operations import notices_for

    assert notices_for(geometry("KBFI"), 7) == notices_for(geometry("KBFI"), 7)
    seen = {n.kind for seed in range(60) for n in notices_for(geometry("KBFI"), seed)}
    assert {"birds", "ils_out", "glideslope_out", "approach_lights_out"} <= seen  # a range of kinds across sessions


def test_the_loop_is_read_a_little_differently_each_time():
    info = atis(weather(340, 8, visibility_sm=10))
    variants = info.variants
    assert len(set(variants)) > 1
    assert all("altimeter two niner niner two" in v and "three four left" in v for v in variants)


def test_clouds_seen_climbing_out():
    profile = CloudProfile()
    t = 0.0
    for feet in range(0, 9000, 50):  # a climb: clear to 2,500, a broken layer to 4,000, clear above
        t += 1
        profile.add(t, feet, 2500 <= feet < 4000 and feet % 200 != 0)
    sky, clear_below = profile.layers()
    assert sky == (Layer("OVC", 2500),) or sky[0].cover in ("BKN", "OVC") and sky[0].base_ft == 2500
    assert clear_below == 2500


def test_atis_and_atc_give_the_same_approach():
    engine, own = engine_at_kpae()
    engine.handle(msgspec.structs.replace(own, t=own.t + 1))
    info = engine.current_atis("KBFI") or engine.current_atis("KPAE")
    runway = info.runway
    assert engine._approach_for(runway, has_ils=True) == info.approach_for(runway) or info.approach_for(runway).kind == "VISUAL"
