"""The real ATIS outside the US: a VATSIM controller's, from the network's data feed, read in Europe's words."""

from localtc.atc_core.atis import real
from localtc.metar_data import fetch_vatsim_atis

ROWS = [
    {"callsign": "EGKK_ATIS", "atis_code": "A", "text_atis": [
        "EGKK COM ATIS A. 2150Z. RWY IN USE 26L. TRANSITION-LEVEL FL 70. ILS APPROACH TO BE EXPECTED.",
        "DATALINK CLEARANCES ARE AVAILABLE. REPORT IF TWY L IS NOT USEABLE BETWEEN TWY QA AND TWY P ON FIRST CONTACT.",
        "24013KT 9999 BKN029 09/05 Q1008 ACKNOWLEDGE RECEIPT OF INFORMATION A AND ADVISE AIRCRAFT TYPE ON FIRST CONTACT."]},
    {"callsign": "LEPA_A_ATIS", "atis_code": "J", "text_atis": [
        "THIS IS LEPA ATIS INFORMATION J AT TIME 2200Z. RWY IN USE FOR ARRIVALS 24 LEFT.",
        "EXPECT ILS Z APPROACH RWY 24 LEFT.. . 11001KT CAVOK 13/11 QNH 1020HPA NOSIG. TRANSITION LEVEL 70."]},
    {"callsign": "LEPA_D_ATIS", "atis_code": "J", "text_atis": [
        "THIS IS LEPA ATIS INFORMATION J AT TIME 2200Z. RWY IN USE FOR DEPARTURES 24 RIGHT.. . 11001KT CAVOK"]},
    {"callsign": "EDDV_ATIS", "atis_code": "B", "text_atis": [
        "ATIS EDDV B METAR 252150 RWY 27R 27L  DEPENDENT PARALLEL APPROACHES IN PROGRESS. A-CDM IN USE.",
        "ALL DEPARTURES SHALL SUBMIT THEIR TOBT AT VATS.IM/VDGS. 23016KT 190V260 CAVOK T10 DP04 QNH1005 TREND"]},
    {"callsign": "EGYP_ATIS", "atis_code": "J", "text_atis": [
        "EGYP ATIS INFORMATION J. 2150Z. 19008KT CAVOK 10/06 A1017. DEP AND ARR RWY 10. EXP RNAV APP RWY 10."]},
    {"callsign": "LFBO_ATIS", "atis_code": "D", "text_atis": [
        "BONJOUR THIS IS BLAGNAC INFORMATION D RECORDED AT 2130Z. EXPECT ILS Z APP RWY 32L. RWY IN USE 32L AND 32R.",
        "TRANSITION LEVEL 60. EXPECT DEPARTURE RWY 32R. WIND 330/08KT. QNH 1023. INFORM ON FIRST CONTACT INFORMATION D"]},
    {"callsign": "EGKK_DEL", "atis_code": None, "text_atis": None},
]


def one(icao: str, kind: str = "both") -> real.RealAtis:
    report = next(r for r in fetch_vatsim_atis(icao, rows=ROWS, now_zulu="2205") if r.kind == kind)
    assert report.source == "vatsim"
    return real.parse(report.text, report.kind, source=report.source, zulu=report.zulu)


def test_a_controllers_atis_is_found_by_its_callsign_with_its_arrival_and_departure_ones():
    assert [(r.kind, r.letter) for r in fetch_vatsim_atis("LEPA", rows=ROWS)] == [("arrival", "J"), ("departure", "J")]
    assert fetch_vatsim_atis("OMDB", rows=ROWS) is None  # nobody on there


def test_europes_words_for_the_runways_in_use():
    gatwick = one("EGKK")
    assert gatwick.landing == ("26L",) and gatwick.departing == ("26L",) and gatwick.zulu == "2150"
    assert one("LEPA", "arrival").landing == ("24L",) and one("LEPA", "departure").departing == ("24R",)
    assert one("EGYP").landing == one("EGYP").departing == ("10",)
    assert one("EDDV").landing == ("27R", "27L")
    toulouse = one("LFBO")
    assert toulouse.landing == ("32L",) and toulouse.departing == ("32R",) and toulouse.approaches == {"32L": "ILS"}


def test_the_networks_own_notes_and_the_weather_in_words_are_not_read_out_as_remarks():
    assert not [r for r in one("EDDV").remarks if "TOBT" in r or "ATIS" in r]
    assert not [r for r in one("LFBO").remarks if r.startswith(("WIND", "QNH", "TRANSITION"))]
    gatwick = one("EGKK").remarks
    assert any("TWY L" in r for r in gatwick) and not any("DATALINK" in r for r in gatwick)


def test_a_runway_notice_is_still_read_out():
    parsed = real.parse("SEA ATIS INFO K 0553Z. 00000KT 10SM A2998. DEPG RWY 16L. RWY 16C CLSD.")
    assert "RWY 16C CLSD" in parsed.remarks
