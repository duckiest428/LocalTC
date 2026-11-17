"""SID, STAR and fix names read back right, as Whisper wrote them: LocalTC's voices read correct readbacks in ten
accents, Whisper (base.en, the app's prompt) heard them, and these are what it wrote. Each must be taken as right, and
a wrong name, said clearly, must still be caught.

The names are said as words ("Diamond" for DYAMD, "Lobster" LBSTA, "Ocean five" OOSHN5), Europe's as their whole fix
("Vuteb three echo" for VUTE3E), and heard through an accent ("Dehect Opail", "hold a carob", "flight level 1 under")."""

from contextlib import nullcontext

import pytest

import test_readback as T
from localtc.atc_core.readback import GrammarInterpreter, InterpretContext, PendingReadback
from localtc.atc_core.readback.normalize import normalize, render
from localtc.atc_core.region import Region, speaking
from localtc.atc_core.values import Approach, Callsign, Phrase
from localtc.stt.vocabulary import VocabularyHints, fixup, match_names, sounds_like

US = dict(callsign=Callsign("SWA2201", telephony="Southwest", flight_number="2201"),
          hints=VocabularyHints(callsign="SWA2201", callsign_spoken="Southwest 2201", airports=("Los Angeles", "San Francisco"),
                                procedures=("HOLTZ9", "BDEGA4"), fixes=("DYAMD", "ARCHI", "LOZIT", "CEDES", "FAITH", "SEAVU")))
ATL = dict(callsign=Callsign("DAL1123", telephony="Delta", flight_number="1123"),
           hints=VocabularyHints(callsign="DAL1123", callsign_spoken="Delta 1123", airports=("Atlanta", "Boston"),
                                 procedures=("CHPPR1", "OOSHN5"), fixes=("JJEDI", "LBSTA", "PATSS", "ROBUC", "BLZZR")))
SEA = dict(callsign=Callsign("ASA123", telephony="Alaska", flight_number="123"),
           hints=VocabularyHints(callsign="ASA123", callsign_spoken="Alaska 123", airports=("Seattle", "Phoenix"),
                                 procedures=("MONTN2", "EAGUL6"), fixes=("HAROB", "BANGR", "CHINS", "JAWBN", "BRUSR")))
DXB = dict(callsign=Callsign("UAE98", telephony="Emirates", flight_number="98"), icao=True,
           hints=VocabularyHints(callsign="UAE98", callsign_spoken="Emirates 98", airports=("Fiumicino", "Dubai"),
                                 procedures=("LAZE1A", "VUTE3E"), fixes=("VUTEB", "ITLOV", "BUBIN", "SENPA", "DESDI")))
LHR = dict(callsign=Callsign("BAW304", telephony="Speedbird", flight_number="304"), icao=True,
           hints=VocabularyHints(callsign="BAW304", callsign_spoken="Speedbird 304", airports=("Heathrow", "Charles de Gaulle"),
                                 procedures=("DET2F", "MOPA4W"), fixes=("LORNI", "OPALE", "MOPAR", "BANOX", "ROMAK")))
FRA = dict(callsign=Callsign("DLH1112", telephony="Lufthansa", flight_number="1112"), icao=True,
           hints=VocabularyHints(callsign="DLH1112", callsign_spoken="Lufthansa 1112", airports=("Frankfurt", "Madrid"),
                                 procedures=("OBOKA1G", "TERSA1J"), fixes=("TOBAK", "SOBRA", "NANDO", "LOTOS", "ANEKI")))
ZRH = dict(callsign=Callsign("SWR1726", telephony="Swiss", flight_number="1726"), icao=True,
           hints=VocabularyHints(callsign="SWR1726", callsign_spoken="Swiss 1726", airports=("Zurich", "Fiumicino"),
                                 procedures=("DEGE2W", "ELKA2C"), fixes=("ELKAP", "RITEB", "XIBIL", "GILIO")))
KSFO = dict(callsign=Callsign("UAL1549", telephony="United", flight_number="1549"),
            hints=VocabularyHints(callsign="UAL1549", callsign_spoken="United 1549", airports=("San Francisco", "Los Angeles"),
                                  procedures=("SSTIK5", "ANJLL4"), fixes=("SERFR", "BDEGA", "DYAMD", "HUULL", "SEAVU")))
MIA = dict(callsign=Callsign("AAL2410", telephony="American", flight_number="2410"),
           hints=VocabularyHints(callsign="AAL2410", callsign_spoken="American 2410", airports=("Miami", "Dallas Fort Worth"),
                                 procedures=("FOLZZ3", "SEEVR4"), fixes=("WINCO", "HILEY", "BOOVE", "JOVEM", "CURSO")))
KLAX = dict(callsign=Callsign("SWA2201", telephony="Southwest", flight_number="2201"),
            hints=VocabularyHints(callsign="SWA2201", callsign_spoken="Southwest 2201", procedures=("HOLTZ9", "BDEGA4"),
                                  fixes=("DYAMD",)))


def heard_as(flight: dict, instruction: str, slots: dict, said: str):
    hints = flight["hints"]
    heard = match_names(fixup(said), hints)
    slots = {"callsign": flight["callsign"], "station": "Center", "destination": "Dallas Fort Worth", "frequency": 124.675,
             "squawk": "4521", "heading": 270, "turn": "right", "minutes": 10, "cruise": 35000,
             "approach": Approach("ILS", "30L"), "message": Phrase("", ""), **slots}
    with speaking(Region("icao", 6000)) if flight.get("icao") else nullcontext():
        library = T.ICAO if flight.get("icao") else T.LIBRARY
        rendered = library.render(instruction, slots)
        pending = PendingReadback(instruction, rendered.controller, rendered.expected, rendered.required, rendered.optional)
        return heard, GrammarInterpreter().interpret(heard, pending, InterpretContext(callsign=flight["callsign"]))


@pytest.mark.parametrize("flight, instruction, slots, said", [
    # Fixes said as the words they're made to sound like
    (US, "common.direct", {"fix": "DYAMD"}, "Direct diameters, SW2201."),
    (US, "departure.radar_contact_direct", {"fix": "DYAMD", "altitude": 12000}, "Climb flight level 120. Direct diamond, SW2201."),
    (US, "departure.radar_contact_direct", {"fix": "LOZIT", "altitude": 12000}, "Climb flight level 120, direct loss at SW2201."),
    (US, "common.direct", {"fix": "CEDES"}, "Direct Seeds, Southwest 2201."),
    (US, "common.direct", {"fix": "SEAVU"}, "Climb flight level 120, direct sea view. United 1549."
     .replace("Climb flight level 120, ", "").replace("United 1549", "Southwest 2201")),
    (ATL, "common.direct", {"fix": "LBSTA"}, "Direct Lobster Delta 1123."),
    (ATL, "departure.radar_contact_direct", {"fix": "BLZZR", "altitude": 12000}, "Climb flight level 120, direct blizzard. Delta 1123."),
    (DXB, "common.direct", {"fix": "ITLOV"}, "Direct Itloaf Emirates 98."),
    (DXB, "departure.radar_contact_direct", {"fix": "VUTEB", "altitude": 10000}, "Climb flight level 100, direct viewdeb, Emirates 98."),
    (DXB, "common.direct", {"fix": "DESDI"}, "Direct DISTI Emirates 98."),
    (LHR, "departure.radar_contact_direct", {"fix": "LORNI", "altitude": 10000}, "Climb flight level 100, direct to Lonnie Speedbird 304."),
    (LHR, "departure.radar_contact_direct", {"fix": "BANOX", "altitude": 10000}, "Climb flight level 100, direct Banach Speedbird 304."),
    (ZRH, "common.direct", {"fix": "XIBIL"}, "Direct Z-Bill, Swiss 1726."),
    (FRA, "departure.radar_contact_direct", {"fix": "ANEKI", "altitude": 10000}, "Climb flight level 100. Direct Enki. Lufthansa 1112."),
    # "direct" and "hold at" through an accent
    (LHR, "common.direct", {"fix": "OPALE"}, "Dehect Opail, Speedbird 304."),
    (LHR, "common.direct", {"fix": "MOPAR"}, "They hacked Mopper, Speedbird 304."),
    (DXB, "common.direct", {"fix": "SENPA"}, "Directs Semper Emirates 98."),
    (DXB, "common.direct", {"fix": "BUBIN"}, "Dishect Bubin, Emirates 98."),
    (SEA, "common.hold_minutes", {"fix": "HAROB", "altitude": 12000},
     "Hold a carob inbound 270. Right turns. Maintain flight level 120. Alaska 123."),
    (ATL, "common.hold_minutes", {"fix": "JJEDI", "altitude": 12000},
     "Older Jedi, inbound 270, right turns, maintain flight level 120, Delta 1123."),
    (US, "common.hold_minutes", {"fix": "DYAMD", "altitude": 12000},
     "Halter Diamond, inbound 270, right turns. Maintain flight level 120, Southwest 2201."),
    (SEA, "clearance.ifr_sid", {"procedure": "MONTN2", "altitude": 5000, "destination": "Phoenix"},
     "Cleared to Phoenix, mountain 2 departure, 5350 in 10, 124.675. Isquark 4521, Alaska 123."),
    (KSFO, "clearance.ifr_sid", {"procedure": "SSTIK5", "altitude": 5000, "destination": "Los Angeles"},
     "Cleared to Los Angeles, stick 5 Departure, 5350 and 10, 124.675, squawk 4521, United 1549."),
    # Levels through an accent
    (FRA, "departure.radar_contact_direct", {"fix": "TOBAK", "altitude": 10000}, "Climb flight level 1 under the direct towback. Lufthansa 1112."),
    (ATL, "departure.radar_contact_direct", {"fix": "LBSTA", "altitude": 12000}, "Climb flight level 1 to 0, direct lobster, Delta 1123."),
    (MIA, "departure.radar_contact_direct", {"fix": "CURSO", "altitude": 12000}, "Climb flight level 1-2-Z-A-Hoe. Dehect-Curso. American 2410."),
    # Procedures: the number heard as a word, the name as one, Europe's letter
    (KSFO, "center.descend_via", {"procedure": "ANJLL4"}, "Descend via the Angel for arrival. United 1549."),
    (ATL, "center.descend_via", {"procedure": "OOSHN5"}, "Descend via the Ocean 5 arrival, Delta 1123."),
    (ATL, "center.descend_star", {"procedure": "OOSHN5", "altitude": 12000},
     "Down to flight level 120, cleared the Ocean 5 arrival. Delta 1123."),
    (DXB, "center.descend_via", {"procedure": "VUTE3E"}, "Descend via the viewedab 3 echo arrival. Emirates 98."),
    (FRA, "clearance.ifr_sid_at_cruise", {"procedure": "OBOKA1G", "destination": "Madrid", "altitude": 6000},
     "Cleared to Madrid via Zobuker 1 Golf departure. Flight planned route, climb to 6,000 feet. Departure 124 Decimal 675, "
     "squawk 4521. Lufthansa 1112."),
    (MIA, "clearance.ifr_sid", {"procedure": "FOLZZ3", "altitude": 5000},
     "Cleared to Dallas Fort Worth by as a full 3 departure. Zenas filed, climb and maintain 5,000. Departure 124.675, "
     "squawk 4521, American 2410."),
    (KLAX, "clearance.ifr_sid", {"procedure": "HOLTZ9", "altitude": 5000, "destination": "San Francisco"},
     "Holt's Niner Dennis filed. Maintain 5,000, squawk 4521, Southwest 2201."),
    (LHR, "clearance.ifr_sid_at_cruise", {"procedure": "DET2F", "destination": "Charles de Gaulle", "altitude": 6000},
     "Cleared to Charles de Gaulle via the Detling two foxtrot departure. Flight planned route, climb to 6,000 feet. "
     "Departure 124 Decimal 675, Esquark 4521, Speedbird 304."),
])
def test_a_name_read_back_right_and_heard_as_whisper_wrote_it_is_right(flight, instruction, slots, said):
    heard, result = heard_as(flight, instruction, slots, said)
    assert (result.kind, result.status) == ("readback", "correct"), (heard, render(normalize(heard)), result)


@pytest.mark.parametrize("flight, instruction, slots, said", [
    (US, "common.direct", {"fix": "DYAMD"}, "Direct Archie, Southwest 2201."),  # another fix of the flight's
    (US, "common.direct", {"fix": "DYAMD"}, "Direct Faith, Southwest 2201."),
    (ATL, "common.direct", {"fix": "LBSTA"}, "Direct Robuck, Delta 1123."),
    (ATL, "center.descend_via", {"procedure": "OOSHN5"}, "Descend via the Ocean 4 arrival, Delta 1123."),
    (DXB, "common.direct", {"fix": "ITLOV"}, "Direct Bubin, Emirates 98."),
])
def test_a_wrong_name_said_clearly_is_still_wrong(flight, instruction, slots, said):
    heard, result = heard_as(flight, instruction, slots, said)
    assert (result.kind, result.status) == ("readback", "incorrect"), (heard, result)


@pytest.mark.parametrize("said", [
    "request direct Boston",  # the airport, not a fix sounding like it
    "proceed direct to the airport",
    "we'd like to request lower",
])
def test_words_that_arent_a_fix_stay_words(said):
    assert match_names(said, ATL["hints"]) == said


def test_a_name_sounds_like_its_word_more_than_another_name():
    assert sounds_like("Diamond", "DYAMD") > 0.7 > sounds_like("Diamond", "FAITH")
    assert sounds_like("Itloaf", "ITLOV") > sounds_like("Itloaf", "DESDI")
