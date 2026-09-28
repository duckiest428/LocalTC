"""Every approach type: what's published, the minima each aircraft flies to, what's out of service, circling, and
how ATC words each clearance."""

import pytest

from localtc.atc_core.airport.approaches import (
    Aircraft,
    Outages,
    category,
    choose_approach,
    circling_radius_nm,
    minima_for,
    options,
    published,
)
from localtc.atc_core.phraseology import TemplateLibrary
from localtc.atc_core.readback.extract import approaches as heard_approaches
from localtc.atc_core.readback.extract import values_equal
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.values import Approach, Callsign
from localtc.sim_api import Airport, ApproachProcedure, Runway, RunwayEnd

LIBRARY = TemplateLibrary.load()
CESSNA, AIRBUS = Aircraft("Cessna Skyhawk"), Aircraft("A320neo", airline=True)


def airport(*procs: ApproachProcedure) -> Airport:
    runways = (
        Runway(lat=47.0, lon=-122.0, elev_ft=400, heading_true=160.0, length_m=3000, width_m=45,
               primary=RunwayEnd(number=16, ils_ident="ISEA"), secondary=RunwayEnd(number=34)),
        Runway(lat=47.0, lon=-122.0, elev_ft=400, heading_true=90.0, length_m=2000, width_m=45,
               primary=RunwayEnd(number=9), secondary=RunwayEnd(number=27)),
    )
    return Airport(icao="KTST", name="TEST", lat=47.0, lon=-122.0, elev_ft=400, runways=runways, approaches=procs)


FIELD = airport(
    ApproachProcedure(kind="ils", runway="16", suffix="Z"),
    ApproachProcedure(kind="rnav", runway="16", suffix="Y", lines=("lpv", "lnav/vnav", "lnav")),
    ApproachProcedure(kind="rnav", runway="16", suffix="X", rnp_ar=True),
    ApproachProcedure(kind="backcourse", runway="34"),
    ApproachProcedure(kind="vordme", runway="34"),
    ApproachProcedure(kind="ndb", runway="27"),
    ApproachProcedure(kind="lda", runway="09"),
    ApproachProcedure(kind="vor", runway="", suffix="A"),
)


def test_what_the_field_publishes_by_name_best_first():
    assert published(FIELD, "16") == ("ILS Z", "RNAV (RNP) X", "RNAV (GPS) Y (LPV, LNAV/VNAV, LNAV)")
    assert published(FIELD, "34") == ("VOR/DME", "LOC BC")


def test_rnav_minima_lines_depend_on_the_aircraft():
    # A GA navigator with WAAS flies the LPV; an airliner the LNAV/VNAV, and it may fly the RNP AR one.
    rnav = lambda a: next(o for o in options(FIELD, "16", a) if o.approach.kind == "RNAV")
    assert rnav(CESSNA).approach.minima == "LPV" and rnav(CESSNA).minima.height_ft == 250
    assert rnav(AIRBUS).approach.minima == "LNAV/VNAV" and rnav(AIRBUS).minima.height_ft == 350
    assert "RNP" in [o.approach.kind for o in options(FIELD, "16", AIRBUS)]
    assert "RNP" not in [o.approach.kind for o in options(FIELD, "16", CESSNA)]  # RNP AR: not for a Cessna


def test_lowest_minima_first_and_the_weather_decides():
    kw = {"airline": True, "aircraft_type": "A320"}
    assert choose_approach(FIELD, "16", visibility_sm=0.75, ceiling_ft=300, **kw).kind == "ILS"
    assert choose_approach(FIELD, "34", visibility_sm=2.0, ceiling_ft=600, **kw).kind == "VOR/DME"  # MDA 450, 1 mile
    assert choose_approach(FIELD, "34", visibility_sm=2.0, ceiling_ft=480, **kw).kind == "VOR/DME"
    assert choose_approach(FIELD, "27", visibility_sm=2.0, ceiling_ft=800, **kw).kind == "NDB"
    assert choose_approach(FIELD, "09", visibility_sm=2.0, ceiling_ft=800, **kw).kind == "LDA"
    # Good weather in the US: the visual; ICAO: the instrument approach.
    assert choose_approach(FIELD, "16", visibility_sm=10, ceiling_ft=None, visual_first=True, **kw).kind == "VISUAL"
    assert choose_approach(FIELD, "16", visibility_sm=10, ceiling_ft=None, visual_first=False, **kw).kind == "ILS"


def test_outages_change_the_approach():
    ils_only = airport(ApproachProcedure(kind="ils", runway="16", suffix="Z"))
    assert choose_approach(ils_only, "16", visibility_sm=1, outages=Outages(glideslope=frozenset({"16"}))) == \
        Approach("LOC", "16", "Z")  # "ILS or LOC": the localizer approach without the glideslope
    # With an RNAV to the runway too, its LPV (250 ft) beats the localizer's MDA (450 ft).
    assert choose_approach(FIELD, "16", visibility_sm=1, outages=Outages(glideslope=frozenset({"16"}))).minima == "LPV"
    no_ils = choose_approach(FIELD, "16", visibility_sm=1, outages=Outages(localizer=frozenset({"16"})))
    assert no_ils.kind == "RNAV" and no_ils.minima == "LPV"
    assert choose_approach(FIELD, "34", visibility_sm=1, outages=Outages(navaids=frozenset({"VOR/DME"}))).kind == "LOC BC"
    lights = Outages(approach_lights=frozenset({"16"}))
    assert next(o for o in options(FIELD, "16", CESSNA, lights) if o.approach.kind == "ILS").minima.visibility_sm == 1.0


def test_no_approach_to_the_runway_in_poor_weather_is_a_circle():
    only = airport(ApproachProcedure(kind="vor", runway="16"))
    circle = choose_approach(only, "34", visibility_sm=2.0, ceiling_ft=900, in_cloud=True)
    assert (circle.kind, circle.runway, circle.circle_to) == ("VOR", "16", "34")
    assert (circle.circle_side, circle.circle_pattern) != ("", "")  # the other way round: restricted to one side
    assert minima_for(circle, Aircraft("B737", True)).height_ft == 700 and circling_radius_nm(Aircraft("B777", True)) == 3.6
    assert category("Cessna 172") == "A" and category("King Air 350") == "B" and category("B747-8") == "D"
    vor_a = airport(ApproachProcedure(kind="vor", runway="", suffix="A"))
    assert choose_approach(vor_a, "27", visibility_sm=2.0, in_cloud=True) == Approach("VOR", "", "A", circle_to="27")


def test_a_pilot_asks_for_one_by_name():
    assert choose_approach(FIELD, "34", requested="VOR").kind == "VOR/DME"
    assert choose_approach(FIELD, "16", requested="LOC").kind == "LOC"
    assert choose_approach(FIELD, "34", requested="LOC BC").kind == "LOC BC"


@pytest.mark.parametrize(("approach", "template", "shown"), [
    (Approach("ILS", "16", "Z"), "approach.intercept_cleared", "established on the localizer, cleared ILS Z RWY 16 approach"),
    (Approach("LOC BC", "34"), "approach.intercept_cleared", "cleared LOC BC RWY 34 approach"),
    (Approach("VOR/DME", "34"), "approach.intercept_course", "established on the final approach course, cleared VOR/DME RWY 34"),
    (Approach("VOR", "16", circle_to="34", circle_side="west", circle_pattern="left"), "approach.intercept_course",
     "cleared VOR RWY 16 approach, circle west of the airport for a left downwind to runway 34"),
    (Approach("VOR", "", "A", circle_to="27"), "approach.intercept_course", "cleared VOR-A approach, circle to runway 27"),
])
def test_the_clearance_names_each_approach(approach, template, shown):
    r = LIBRARY.render(template, {"callsign": Callsign("N172LT"), "turn": "left", "heading": 340, "altitude": 3000,
                                  "approach": approach})
    assert shown in r.text


@pytest.mark.parametrize(("said", "kind", "runway"), [
    ("cleared V O R DME runway three four approach", "VOR/DME", "34"),
    ("cleared localizer back course two six", "LOC BC", "26"),
    ("cleared NDB runway two seven approach", "NDB", "27"),
    ("cleared R NAV yankee one six", "RNAV", "16"),
    ("cleared LDA one niner", "LDA", "19"),
])
def test_readbacks_of_every_kind(said, kind, runway):
    [heard] = heard_approaches(normalize(said))
    assert (heard.kind, heard.runway) == (kind, runway)
    assert values_equal("approach", heard, Approach(kind, runway, "Y" if kind == "RNAV" else ""))
    assert values_equal("approach", Approach("LOC", "16"), Approach("ILS", "16"))  # glideslope out, read as the LOC


def test_display_round_trips():
    for a in (Approach("ILS", "34R", "Z"), Approach("RNP", "19", "Y"), Approach("LOC BC", "26"), Approach("VOR", "", "A"),
              Approach("VOR/DME", "34")):
        assert Approach.parse(a.display) == a
