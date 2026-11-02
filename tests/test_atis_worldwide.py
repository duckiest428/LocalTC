"""The real ATIS outside the US: a VATSIM controller's, from the network's data feed, read in Europe's words."""

import calendar
import html

import pytest

from localtc.atc_core.atis import real
from localtc.metar_data import read_guru_page

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
    words = {"both": "", "arrival": "A", "departure": "D"}
    for row in ROWS:
        parts = row["callsign"].split("_")
        if parts[0] == icao and (parts[1] if len(parts) == 3 else "") in (words[kind], kind[:3].upper()):
            return real.parse(" ".join(row["text_atis"]), kind, source="vatsim", zulu="2205")
    raise KeyError(icao)


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


ON_THE_PAGE = (
    ("LEMD", "Arrival", "LEMD  ATIS A\nRR W 0010Z\n\tEXPECT ILS Z APCH\n\tRWY IN USE 32R\n\tTRL FL140\n\tTWY H2 AND H3 CLSD ,\n\tTWY EA5 CLSD , GATE 12\n\tCLSD , EXIT L3 CLSD\n\tWIND 010 DEG 3 KT .\n\tCAVOK.\n\tT7 DP2\n\tQNH 1021HPA\n\tNOSIG\n\tCONFIRM ATIS ARR W", "W", ("32R",), ()),
    ("EDDM", "Arrival", "EDDM ARR ATIS B\n2120Z -ATIS EDDM B METAR 012120\r\n-EXPECT VECTORS FOR INDEPENDENT PARALLEL ILS APPROACH\r\n-RWY 08L 08R\r\n-NIGHT OPERATION ,, RWY08L  CLOSED FROM TIME 2200 UNTIL 0500 UTC\r\n-TRL 60\r\n-\r\n-03003KT\r\n-CAVOK\r\n-T04 DP02\r\n-QNH1029\r\n-TREND NOSIG", "B", ("08L", "08R"), ()),
    ("EGLL", "Arrival", "EGLL ARR ATIS I\n2335Z\r\n09R SINGLE RUNWAY OPERATIONS\r\nIN FORCE\r\nLANDING RWY 09R\r\nTRANSITION-LEVEL: FL 65\r\nAUTO 36003KT 350V070 9000\r\nOVC031 09/07 Q1035\r\nACKNOWLEDGE RECEIPT OF\r\nINFORMATION I", "I", ("09R",), ()),
    ("LSZH", "Departure", "LSZH DEP ATIS T\n2050Z DEP RWY 32.\r\nDEPENDENT OPS ON\r\nCONVERGING RWY.\r\nTRL 70.\r\nARRIVALS, EXP ILS APCH,\r\nRWY 34.\r\nWIND 050 DEG, 7 KT.\r\nCAVOK.\r\nQNH 1028.", "T", None, ("32",)),
    ("RJTT", "Arrival", "RJTT ARR ATIS Y\n0053Z M0030\n\t\n\t(APCH)ILS Z RWY34L/ILS Z\n\t RWY34R\n\tLDG RWY 34L/34R\n\tDEP RWY 05/34R\n\tSIMUL PARL ILS APCHS TO\n\t RWY34L/R \n\tARE INPR,\n\tM\n\t020030Z 09006KT 15KM FEW030CU\n\t BKN080AC\n\t BKN140AC 18/11 Q1013/A2994=", "Y", ("34L", "34R"), ("05", "34R")),
    ("ZBAA", "Departure", "ZBAA DEP ATIS Z\n2132Z DEP RWY 36L & 01\n\tRWY 36R IS CLSD\n\tWIND VRB DEG 1 MPS\n\tCAVOK\n\tQNH 1021 HPA\n\tADZ YOU HAVE INFO Z.", "Z", None, ("36L", "01")),
    ("OMDB", "Arrival", "OMDB ARR ATIS A\n0031Z OMDB ARR A.\r\nAT TIME 0030.\r\nEXP ILS APPROACH, ARR RWY 30 LEFT.\r\nSFC WIND 150 DEG, 4 KT\r\nQNH 1015 HPA.", "A", ("30L",), ()),
    ("SBGR", "Arrival", "SBGR ARR ATIS G\n2259Z ATIS G\n\tEXP ILS Y RWY 10R APP/\n\tLDG 10R DEP 10L IN USE\n\tTWY B CLSD BTN TWY O AND\n\tTWY Q \n\tTWY FF CLSD \n\tRUNWAY 1 0 LEFT RUNWAY\n\tCOND CODE 5 SLASH 5\n\tWIND 060 06 KT \n\tEND OF ATIS G", "G", ("10R",), ()),
    ("YSSY", "Arrival", "YSSY ARR ATIS S\n0014Z ATIS YSSY S   020014\r\nAPCH: EXP INDEPENDENT VISUAL APCH.\r\nRWY: 34L AND R FOR ARRS AND DEPS\r\nWIND: 030/10\r\nQNH: 1016", "S", ("34L", "34R"), ()),
    ("LFPG", "Arrival", "LFPG ARR ATIS Q\n2222Z\r\nEXPECT 1E 1N ILS 09L AND 1E 1N ILS 08R\r\nARR RWY 09L AND 08R DEP RWY 09R AND 08L SID 7G\r\nTRL 60\r\nWIND 020/06 KT", "Q", ("09L", "08R"), ("09R", "08L")),
)
NOW = calendar.timegm((2026, 11, 2, 1, 0, 0))


def page(title: str, body: str, when: str = "2026-11-02 00:19 UTC") -> str:
    return (f'<h5 class="card-title" data-toggle="tooltip">\n {title} ATIS\n</h5>\n<h6 class="card-subtitle mb-2 text-muted" '
            f'title="Requested by N1">{when}</h6>\n\n<div class="atis">{html.escape(body)}</div></div>')


@pytest.mark.parametrize("icao,title,body,letter,landing,departing", ON_THE_PAGE, ids=[row[0] for row in ON_THE_PAGE])
def test_the_real_atis_from_around_the_world_is_read(icao, title, body, letter, landing, departing):
    report = read_guru_page(page(title, body), icao, NOW)[0]
    assert report.letter == letter and report.source == "atis.guru" and report.kind == title.lower()
    parsed = real.parse(report.text, report.kind, source=report.source, zulu=report.zulu)
    assert parsed.letter == letter and parsed.zulu == report.zulu
    if landing is not None:
        assert parsed.landing == landing, parsed.text
    for runway in departing or ():
        assert runway in parsed.departing, parsed.text


def test_madrids_notices_are_read_and_nothing_else():
    parsed = real.parse(read_guru_page(page(*ON_THE_PAGE[0][1:3]), "LEMD", NOW)[0].text, "arrival", source="atis.guru")
    assert parsed.remarks == ("TWY H2 AND H3 CLSD", "TWY EA5 CLSD", "GATE 12 CLSD", "EXIT L3 CLSD")
    assert parsed.approach == "ILS"


def test_an_old_atis_on_the_page_is_not_used():
    old = page("Departure", "EDDM DEP ATIS M\n0920Z -RWY 08L 08R", "2026-10-31 09:32 UTC")
    assert read_guru_page(old, "EDDM", NOW) is None
    assert [r.kind for r in read_guru_page(old + page("Arrival", "EDDM ARR ATIS B\n0010Z -RWY 08L"), "EDDM", NOW)] == ["arrival"]
    assert read_guru_page("<p>No ATIS available...</p>", "XXXX", NOW) is None
