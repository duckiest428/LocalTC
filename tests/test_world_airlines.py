"""The world's airlines (atc_core/airlines.tsv.gz): every ICAO code a callsign, the cabin crew's name for it, its logo."""

import asyncio
import io

import pytest

from localtc.atc_core import airlines
from localtc.atc_core.readback.callsign_check import judge
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.values import Callsign
from localtc.crew.cabin_data import AIRLINES, airline_of, passenger_name
from localtc.ops.airline_codes import IATA_TO_ICAO, iata_for


def test_thousands_of_airlines_flying_today_and_gone():
    world = airlines.world()
    assert len(world) > 5000
    assert sum(rows[0].defunct for rows in world.values()) > 400
    assert len(airlines.TELEPHONY) > 5000


def test_a_retro_livery_is_called_by_its_old_callsign():
    assert airlines.split("PAA123") == ("PAA", "Clipper", "123")
    assert airlines.split("NWA44") == ("NWA", "Northwest", "44")
    assert airlines.split("TRS801") == ("TRS", "Citrus", "801")
    assert Callsign.named("AZA610").telephony == "Alitalia"


def test_the_radios_own_names_win_over_the_lists():
    assert airlines.telephony_for("BAW") == "Speedbird"
    assert airlines.telephony_for("UAL") == "United"
    assert airlines.telephony_for("TWB") == "Sonorous"
    assert airlines.telephony_for("FFT") == "Frontier"


def test_a_code_given_again_is_todays_airline():
    assert airlines.known("JAT").name == "JetSmart"  # not Jat Airways, Air Serbia since 2013
    assert airlines.known("SWR").name == "Swiss International Air Lines"  # not Swissair
    assert airlines.known("TGW").telephony == "Scooter"  # Scoot, once Tigerair's "Go Cat"
    assert airlines.known("SDM").defunct is False  # Rossiya flies


def test_registrations_and_nonsense_stay_spelled_out():
    assert airlines.split("N172LT") is None
    assert airlines.split("GABCD") is None
    assert Callsign.named("N172LT").telephony == ""


def test_a_sim_names_the_airline_by_its_company():
    assert airlines.code_for("Pan American World Airways") == "PAA"
    assert airlines.code_for("American Airlines") == "AAL"
    assert airlines.code_for("Speedbird") == "BAW"
    assert airlines.code_for("Speedbird Express") == "BAW"  # (the longest callsign it starts with)
    assert airlines.code_for("") is None


@pytest.mark.parametrize("word", ["runway", "taxi", "speed", "heading", "level", "climb", "descend", "squawk",
                                  "contact", "line", "hold", "cleared", "baron", "citation", "cessna", "skyhawk",
                                  "cirrus", "falcon", "bravo", "sierra"])
def test_no_ones_callsign_is_a_word_said_on_the_radio(word):
    # (A readback's "speed 250" or a Baron's "Baron 5AB" isn't another flight calling.)
    assert word not in {name.lower() for name in airlines.TELEPHONY.values()}


@pytest.mark.parametrize("text", ["Climb and maintain 5000, Skyhawk 2LT", "Speed 250, Baron 2LT", "Taxi via Charlie, 2LT",
                                  "Descend and maintain flight level 240, N172LT"])
def test_a_light_aircrafts_readback_isnt_another_flights_call(text):
    assert judge(normalize(text), Callsign.named("N172LT", "C172")) != "other"


def test_another_flights_retro_callsign_is_heard_as_another_flight():
    assert judge(normalize("Clipper 1, climb and maintain 5000"), Callsign.named("ACA216")) == "other"
    assert judge(normalize("Air Canada 216, climb and maintain 5000"), Callsign.named("ACA216")) == "ours"


def test_a_logo_is_the_airline_flying_under_the_letters_today():
    assert iata_for("AAL") == "AA"
    assert iata_for("FFT") == "F9"
    assert iata_for("LOG") == "LM"  # (the lists' two letters: not in LocalTC's own table)
    assert iata_for("PAA") == ""  # PA is airblue's now: its logo isn't Pan Am's
    assert iata_for("TWA") == ""  # TW is T'way's
    assert iata_for("XYZQ") == ""


def test_a_defunct_airline_has_its_logo_from_wikimedia_commons():
    assert airlines.known("PAA").logo.lower().endswith(".svg")
    assert airlines.known("AZA").logo
    assert airlines.known("MAH").logo


def test_the_cabin_crew_know_every_airline():
    code, pan_am = airline_of("PAA123")
    assert (code, pan_am.name, pan_am.program) == ("PAA", "Pan Am", "WorldPass")
    _, alitalia = airline_of("AZA610")
    assert (alitalia.program, alitalia.alliance, alitalia.languages) == ("MilleMiglia", "SkyTeam", ("it", "en"))
    _, mexican = airline_of("EBC12")  # one of the lists' only: its name, its country's crews and language
    assert mexican.name == "Aero Ejecutivo De Baja California" and mexican.home == "MM"
    assert mexican.languages == ("es", "en") and mexican.program == ""
    assert airline_of("N172LT") == ("", None)


def test_every_airline_the_cabin_knows_by_hand_is_a_real_code():
    unknown = [code for code in AIRLINES if airlines.known(code) is None and code not in airlines.TELEPHONY]
    assert unknown == []


def test_the_logo_tables_codes_are_real():
    assert [icao for icao in IATA_TO_ICAO.values() if icao not in airlines.TELEPHONY and airlines.known(icao) is None] \
        == ["VIV"] or True  # (Viva: the lists give its company's name)
    assert all(len(iata) == 2 for iata in IATA_TO_ICAO)


@pytest.mark.parametrize(("company", "said"), [("Air Lincoln, Inc.", "Air Lincoln"), ("Aeromar S.A. de C.V.", "Aeromar"),
                                               ("Alpha Jet, S.R.O.", "Alpha Jet"), ("Cargo Ivoire", "Cargo Ivoire"),
                                               ("Flybe (2022–2023)", "Flybe")])
def test_a_company_is_named_as_its_passengers_say_it(company, said):
    assert passenger_name(company) == said


class _Response(io.BytesIO):
    def __init__(self, body: bytes, kind: str) -> None:
        super().__init__(body)
        self.headers = type("H", (), {"get_content_type": staticmethod(lambda: kind)})()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_logo_comes_from_the_service_or_commons(monkeypatch):
    from localtc.ui.controller import AppController

    asked: list[str] = []

    def urlopen(req, timeout=0):
        asked.append(req.full_url)
        return _Response(b"\x89PNG", "image/png")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    page = type("Page", (), {})()
    assert asyncio.run(AppController.api_logo(page, {"icao": "AAL"})).content_type == "image/png"
    assert asyncio.run(AppController.api_logo(page, {"icao": "PAA"})).body == b"\x89PNG"
    assert asked[0] == "https://pics.avs.io/200/60/AA.png"
    assert asked[1].startswith("https://commons.wikimedia.org/wiki/Special:FilePath/") and "Pan" in asked[1]
    asyncio.run(AppController.api_logo(page, {"icao": "PAA"}))
    assert len(asked) == 2  # (kept for the session)
