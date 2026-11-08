"""What speech-to-text wrote, the way a pilot would write it."""

import pytest

from localtc.stt.vocabulary import fixup


@pytest.mark.parametrize("heard, written", [
    ("Departure frequency is on 119. Decimal 7. Squawk 1725.", "Departure frequency is on 119.7. Squawk 1725."),
    ("Contact Ground on 121 decimal 7 Delta 2321.", "Contact Ground on 121.7 Delta 2321."),
    ("Crime and maintain 8,000.", "climb and maintain 8,000."),
    ("Climate 17,000, Delta 2321.", "climb 17,000, Delta 2321."),
    ("Comment in 230.2321.", "climb and maintain 230, 2321."),
    ("Climb maintain flight level 370.2321.", "Climb maintain flight level 370, 2321."),
    ("Runway 10, right? Via Tengo, Charlie, Sierra, Quebec, foxtrot, Lima, H4, Juliet, J1, Delta 2321.",
     "runway 10R Via T, C, S, Q, F, L, H4, J, J1, Delta 2321."),
    ("Taxiway 10, right? Via Tango, Charlie", "runway 10R Via T, C"),
    ("Request pushback and startup gate, Delta 1.", "Request pushback and startup gate, Delta 1."),
    ("Request pushback and startup gate Delta 1.", "Request pushback and startup gate D1."),
    ("taxi via Hotel 4, Juliet and Alpha, Delta 2321", "taxi via H4, J and A, Delta 2321"),
    ("via Charlie, Delta 2321", "via C, Delta 2321"),
])
def test_written_as_a_pilot_writes_it(heard, written):
    assert fixup(heard) == written


def test_a_frequency_keeps_its_decimals_and_a_callsign_its_name():
    assert fixup("contact tower 118.700") == "contact tower 118.700"
    assert "Delta 2321" in fixup("Delta 2321 request taxi")


def test_a_frequency_after_the_station_and_a_route_after_a_full_stop():
    from localtc.stt.vocabulary import VocabularyHints, fixup, match_names

    assert fixup("Tower 118, 3, Frontier 4990.") == "Tower 118.3, Frontier 4990."
    assert "via A. A3 B and B1" in fixup("Taxi runway 27 via at B1 via A. A3 Bravo and B1 Frontier 4990.")
    hints = VocabularyHints(callsign="FFT4990", callsign_spoken="Frontier 4990", fixes=("JETTI", "ZZOOO", "CENZA"))
    assert match_names("Cleared for takeoff runway 27, Frontier 4990 on F2 jet T.", hints).endswith("RNAV to JETTI.")
    assert match_names("Cleared to land runway 27 on to the left", hints) == "Cleared to land runway 27 on to the left"


def test_fonz_is_fawns():
    from localtc.stt.vocabulary import VocabularyHints, match_names

    hints = VocabularyHints(callsign="ROU1638", callsign_spoken="Rouge 1638", fixes=("FAWNS", "BUGSY", "SYR"))
    assert match_names("Direct Fonz. Rouge 1638.", hints) == "Direct FAWNS. Rouge 1638."
