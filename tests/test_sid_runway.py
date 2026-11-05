"""A SID leaves from a runway: ATC never clears a flight on one that doesn't leave from the runway it gives."""

from localtc.atc_core.procedures import sid_for_runway
from localtc.sim_api import DepartureProcedure as D

BIRMINGHAM = (D(name="UNGA1M", runways=("33",), fixes=("UNGAP",)), D(name="UNGA1Y", runways=("15",), fixes=("UNGAP",)),
              D(name="DTY2Y", runways=("15",), fixes=("DTY",)), D(name="DTY4M", runways=("33",), fixes=("DTY",)),
              D(name="LUXTO1Y", runways=("15",), fixes=("LUXTO",)))


def test_the_filed_one_stands_on_its_own_runway():
    assert sid_for_runway(BIRMINGHAM, "33", "UNGA1M", joins=("UNGAP",)) == "UNGA1M"
    assert sid_for_runway(BIRMINGHAM, "15", "DTY2Y", joins=("DTY",)) == "DTY2Y"


def test_another_runway_gets_the_one_from_there_going_the_same_way():
    assert sid_for_runway(BIRMINGHAM, "15", "UNGA1M", joins=("UNGAP",)) == "UNGA1Y"  # filed off 33, given 15
    assert sid_for_runway(BIRMINGHAM, "33", "DTY2Y", joins=("DTY",)) == "DTY4M"  # filed off 15, given 33


def test_by_its_name_when_the_route_says_nothing():
    assert sid_for_runway(BIRMINGHAM, "15", "UNGA1M") == "UNGA1Y"


def test_none_from_there_going_that_way_is_no_sid():
    assert sid_for_runway(BIRMINGHAM, "33", "LUXTO1Y", joins=("LUXTO",)) is None


def test_a_flight_filed_without_one_gets_the_one_that_joins_its_route():
    assert sid_for_runway(BIRMINGHAM, "15", None, joins=("LUXTO",)) == "LUXTO1Y"
    assert sid_for_runway(BIRMINGHAM, "15", None, joins=("NOWHERE",)) is None


def test_navdata_cuts_a_name_short_and_some_are_for_every_runway():
    assert sid_for_runway((D(name="ANJL4", runways=("25",)),), "25", "ANJLL4") == "ANJLL4"
    assert sid_for_runway((D(name="ANY1A", runways=()),), "07", "ANY1A") == "ANY1A"
    assert sid_for_runway((D(name="X1A", runways=("25R",)),), "25L", "X1A") is None


def test_runway_zero_padding_doesnt_matter():
    assert sid_for_runway((D(name="AAA1A", runways=("06L",)),), "6L", "AAA1A") == "AAA1A"
