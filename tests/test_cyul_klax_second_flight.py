"""A second real CYUL-KLAX flight (25 September 2026, the A220 on LocalTC 0.3.2): what went wrong in it.

The recording is too big to keep (78 MB with the voice), so each of its calls is here as it was said, with
what ATC should have made of it:
- "Request taxi to a gate" after landing at KLAX got a taxi *out*, to CYUL's runway 06L.
- "06L left via Charlie. Sorry, 06L left at Charlie, Golf Charlie" was heard as the route L, C, G, C.
- "Hold short, Air Canada 779" (no runway) and "6500, Air Canada 779" (the altitude alone) got "say again".
- Chicago Center's frequency, tuned the moment Cleveland handed off (before the flight was in its airspace),
  was nobody's: "no ATC on this frequency", and the copilot read the handoff back all over again.
"""

import pytest

from localtc.atc_core.facilities import Facility
from localtc.atc_core.phraseology import TemplateLibrary
from localtc.atc_core.readback import GrammarInterpreter, InterpretContext, PendingReadback
from localtc.atc_core.readback.interpreter import ChainInterpreter, SayAgainInterpreter
from localtc.atc_core.values import Callsign, Phrase

LIBRARY = TemplateLibrary.load()
ACA779 = Callsign("ACA779")


def pending(instruction: str, **slots) -> PendingReadback:
    r = LIBRARY.render(instruction, {**slots, "callsign": ACA779})
    return PendingReadback(instruction, instruction.split(".")[0], r.expected, r.required, r.optional)


def heard(text: str, waiting: PendingReadback | None, phase: str = "TAXI_OUT"):
    return GrammarInterpreter().interpret(text, waiting, InterpretContext(callsign=ACA779, phase=phase))


@pytest.mark.parametrize("text", ["Air Canada 779. Request taxi to a gate.", "request taxi to our gate", "taxi to the stand please"])
def test_a_taxi_to_the_gate_is_asked_for_in_any_words(text):
    assert heard(text, None, "TAXI_IN").intent == "request_taxi_parking"


TAXI = {"runway": "06L", "hold_point": "C", "taxi_route": ("G", "C"), "atis": "U", "altimeter": 30.21}


@pytest.mark.parametrize("text", [
    "06L left via Charlie. Sorry, 06L left at Charlie Golf Charlie at Canada 779.",  # as said: corrected halfway
    "zero six left at charlie via golf charlie, air canada 779",
    "runway 06L at C, G, C, Air Canada 779",  # the holding point, then the route, no "via"
])
def test_the_taxi_readback_with_its_holding_point_and_a_correction(text):
    result = heard(text, pending("ground.taxi_out_at", **TAXI))
    assert (result.status, result.values) == ("correct", {"runway": "06L", "taxi_route": ("G", "C")})


def test_a_gate_is_not_taxiway_a():
    result = heard("Negative Air Canada 779. We'd like a gate, not a runway.", pending("ground.taxi_to_gate", gate="Gate 139",
                                                                                      taxi_route=("B7", "B")), "TAXI_IN")
    assert "taxi_route" not in result.mismatched


def test_hold_short_without_the_runway_is_asked_for_again_not_say_again():
    waiting = pending("tower.hold_short_traffic", hold_short="06L", message=Phrase("traffic A320 on 2 mile final", "traffic"))
    result = heard("Hold short Air Canada 779.", waiting, "RUNWAY_HOLD")
    assert (result.kind, result.status, result.missing) == ("readback", "incomplete", ("hold_short",))
    # A second try, with no model to ask: still the readback ("read back hold short runway 06L"), not "say again".
    again = PendingReadback(waiting.instruction_id, waiting.controller, waiting.expected, waiting.required, waiting.optional,
                            attempts=1)
    chained = ChainInterpreter(GrammarInterpreter(), SayAgainInterpreter()).interpret(
        "Hold short Air Canada 779.", again, InterpretContext(callsign=ACA779, phase="RUNWAY_HOLD"))
    assert (chained.kind, chained.missing) == ("readback", ("hold_short",))


@pytest.mark.parametrize(("text", "status"), [
    ("6500 Air Canada 779.", "correct"),
    ("six thousand five hundred, air canada 779", "correct"),
    ("6000 Air Canada 779", "no_match"),  # not the altitude given: not taken as a readback of it
])
def test_the_altitude_alone_is_a_readback(text, status):
    assert heard(text, pending("common.descend", altitude=6500), "APPROACH").status == status


def test_the_new_centre_answers_on_its_frequency_from_the_handoff():
    from localtc.atc_core.engine import AtcEngine, EngineConfig

    engine = AtcEngine(EngineConfig(callsign="ACA779", destination="KLAX", cruise_ft=36000))
    chicago = Facility("center", "Chicago Center", 132.175)
    assert engine._facility_for(132.175) is None or engine._facility_for(132.175) == chicago
    engine.state.comms.expected = chicago  # Cleveland: "contact Chicago Center 132.175"
    assert engine._facility_for(132.175) == chicago
