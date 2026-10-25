"""The runways each flight is given, a real ATIS read whole, and its closures worked to (Honolulu, from a flight there)."""

from pathlib import Path

from localtc.airports import load_airport
from localtc.atc_core.airport import AirportGeometry
from localtc.atc_core.airport.closures import closed_paths
from localtc.atc_core.airport.taxi_route import TaxiGraph
from localtc.atc_core.atis import notes, real, runway_use
from localtc.atc_core.atis.board import AtisBoard
from localtc.atc_core.atis.observation import Weather
from localtc.atc_core.values import Wind
from localtc.ui.airport_layout import layout, runway_use as layout_use

PHNL = AirportGeometry(load_airport(Path(__file__).parent / "fixtures" / "airports_real" / "PHNL.json"))
HNL_ATIS = (
    "HNL ATIS INFO K 0153Z. 03010KT 10SM FEW025 FEW040 BKN220 29/18 A2993 (TWO NINER NINER THREE). LNDG/DEPG RWYS 4/8 "
    "EXP ILS, RNAV, OR VISUAL APCH. TWY, E BTN TWY, A AND TWY B CLSD, TWY, K BTN RWY, 4R AND TWY A CLSD, TWY T CLSD TWY "
    "H CLSD. RWY 8R DEP END BARRIER OTS. EWABE OM OTS. HNL TACAN AZM OTS. CKH TACAN AZM OTS. READ BACK ALL CONTROL "
    "INSTRUCTIONS. INCR BIRD STRIKE HAZ ALL RWYS. NUMEROUS CRANES IN THE VCNTY HNL ARPT CHECK NOTAMS FOR ADNL INFO. "
    "SIGMET R 13 VALID. MORE INFO AT HNL AFSS. WARNING RWY 8L IS SHORTENED 7000 FT AVAILABLE LAST EXIT TWY Y, S, D "
    "INTERSECTION. TAXIWAY KILO NOT AVAILABLE EXPECT TO EXIT AT THE END. ...ADVS YOU HAVE INFO K.")
PHX_ATIS = (
    "PHX ATIS INFO J 1951Z. 28011KT 10SM FEW085 BKN100 BKN120 23/07 A2990 (TWO NINER NINER ZERO) RMK AO2 SLP111 SHRA "
    "DSNT N AND SW-NW T02330067. SIMUL APCHS TO PARL RWYS IN USE. USE CTN FOR ACFT ON PARL FINAL APCHS, DO NOT "
    "OVERSHOOT FINAL. EXPECT ILS OR VISUAL APCH RWY 26, ILS OR VISUAL APCH RWY 25L. DEPARTING RWY 25R. NOTICES TO "
    "AIRMEN. CLNC DLVY FREQ 133.7. MULT CRANES INVOF ARPT. BIRD ACTIVITY INVOF ARPT. READBACK HOLD SHORT "
    "INSTRUCTIONS. ...ADVS YOU HAVE INFO J.")


def honolulu():
    board = AtisBoard(seed=1)
    weather = Weather(wind=Wind(20, 10), wind_dir_true=30.0, visibility_sm=10, altimeter_inhg=29.93, temperature_c=29)
    parsed = real.parse(HNL_ATIS)
    return board.update("PHNL", PHNL, weather, 7000.0, "Honolulu International", real=parsed,
                        source=("real ATIS", None, parsed.zulu))


def test_a_seaplane_runway_is_never_the_runway_in_use():
    """Honolulu's lagoon runway, "08/26" in the sim: "expect visual approach runway 08", in the water."""
    assert {e.ident for e in PHNL.ends} == {"04L", "22R", "04R", "22L", "08L", "26R", "08R", "26L"}
    assert PHNL.end("08") is None and PHNL.end("04W") is None
    info = honolulu()
    assert "08" not in info.operations.landing and "08" not in info.operations.departing


def test_a_real_atis_landing_and_departing_both_ways_is_worked_apart():
    """ "LNDG/DEPG RWYS 4/8": each runway number Honolulu has, the ILS ones landing, their parallels departing."""
    parsed = real.parse(HNL_ATIS)
    assert parsed.landing == parsed.departing == ("04", "08")
    info = honolulu()
    assert set(info.operations.landing) == {"04R", "08L"}
    assert set(info.operations.departing) == {"04L", "08R"}


def test_the_real_atis_is_read_whole_in_words():
    text = honolulu().text
    assert "Expect ILS, RNAV, or visual approach" in text
    assert "Taxiway E between taxiway A and taxiway B closed" in text
    assert "Increased bird strike hazard all runways" in text
    assert "Warning runway 8L is shortened 7,000 feet available" in text
    assert "Read back all control instructions" in text and "hold short instructions" not in text
    said = notes.say("TWY, K BTN RWY, 4R AND TWY A CLSD")[1]
    assert said == "taxiway kilo between runway four right and taxiway alpha closed"


def test_its_closures_are_what_atc_works_to():
    ops = honolulu().operations
    assert {"T", "H"} <= ops.closed_taxiways and "E" not in ops.closed_taxiways  # E only between A and B
    assert ("E", "A", "B") in ops.closed_segments and ("K", "RWY 04R", "A") in ops.closed_segments
    pieces = closed_paths(PHNL, ops.closed_segments)
    assert pieces
    names = {PHNL.airport.taxi_paths[i].name for i in pieces}
    assert names <= {"E", "K"}
    open_e = [i for i, p in enumerate(PHNL.airport.taxi_paths) if p.name == "E" and i not in pieces]
    assert open_e  # the rest of E is open
    graph = TaxiGraph(PHNL, closed=ops.closed_taxiways, closed_paths=pieces)
    assert graph is not None
    drawn = layout(PHNL.airport, use=layout_use(ops.landing, ops.departing), closed=(ops.closed_taxiways, pieces))
    shut = [s for s in drawn["taxiways"] if s["closed"]]
    assert shut and {s["name"] for s in shut} <= {"E", "K", "T", "H"}
    uses = {e["ident"]: e["use"] for r in drawn["runways"] for e in r["ends"]}
    assert uses["04R"] == "arr" and uses["08R"] == "dep" and uses["22L"] == ""


def test_drawn_notices_never_take_a_runway_a_real_atis_uses():
    """Phoenix's drawn "runway 07L/25R closed" took away 25R, the runway its real ATIS was departing on."""
    parsed = real.parse(PHX_ATIS)
    assert parsed.weather.precip == ""  # "SHRA DSNT" in its remarks: showers in the distance, not rain here
    assert parsed.departing == ("25R",) and parsed.landing == ("26", "25L")
    assert ("birds", "", "") in parsed.notices


def test_each_flight_gets_a_runway_fit_for_it_spread_over_the_ones_in_use():
    ends = runway_use.expand(PHNL, ("04", "08"))
    assert {e.ident for e in ends} == {"04L", "04R", "08L", "08R"}
    short = PHNL.end("04L")  # 2,123 m: no runway for a 777 to take off from
    assert runway_use.pick([short, PHNL.end("08R")], landing=False, aircraft_type="B77W").ident == "08R"
    assert runway_use.pick([short], landing=False, aircraft_type="B77W", fallback=PHNL.ends).ident != "04L"
    # Two departures the same size from the same spot: the second to the other runway, once the first has its own
    xy = PHNL.end("08R").threshold
    first = runway_use.pick(ends, landing=False, aircraft_type="A320", xy=xy)
    second = runway_use.pick(ends, landing=False, aircraft_type="A320", xy=xy, load={first.ident: 3})
    assert first.ident != second.ident
