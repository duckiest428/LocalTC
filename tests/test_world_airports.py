"""The Live Map's airports around you (airports/world.py): the world's airline airports in a map's view."""

from localtc.airports.world import MAX_SHOWN, within, world


def test_the_airline_airports_are_there_with_their_codes():
    by_icao = {a.icao: a for a in world()}
    assert len(by_icao) > 4000
    assert by_icao["EDDM"].iata == "MUC" and by_icao["EDDM"].large and by_icao["EDDM"].city == "Munich"
    assert by_icao["KSEA"].iata == "SEA"


def test_a_view_has_the_airports_in_it_the_big_ones_first():
    bavaria = {a.icao for a in within(47.5, 10.5, 49.0, 13.0)}
    assert "EDDM" in bavaria and "EDDF" not in bavaria
    europe = within(35.0, -10.0, 60.0, 30.0)
    assert len(europe) == MAX_SHOWN and all(a.large for a in europe[:100])
    pacific = {a.icao for a in within(-20.0, 170.0, 25.0, -150.0)}  # across the date line
    assert "PHNL" in pacific and "NFFN" in pacific
