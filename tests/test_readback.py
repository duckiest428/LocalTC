"""Readback checking and intent recognition: the TOML corpus, the interpreter chain, and round-trip properties."""

import random
from dataclasses import replace
import tomllib
from pathlib import Path

import pytest

from localtc.atc_core.phraseology import TemplateLibrary
from localtc.atc_core.readback import (
    ChainInterpreter,
    GrammarInterpreter,
    InterpretContext,
    PendingReadback,
    SayAgainInterpreter,
    normalize,
)
from localtc.atc_core.readback.extract import values_close
from localtc.atc_core.readback.normalize import render
from localtc.atc_core.values import Approach, Callsign, Phrase, Wind

CORPUS = tomllib.loads((Path(__file__).parent / "data" / "readbacks.toml").read_text(encoding="utf-8"))
CALLSIGN = Callsign("N172LT", type_name="Skyhawk")
CONTEXT = InterpretContext(callsign=CALLSIGN)
LIBRARY = TemplateLibrary.load()


def slots_from_toml(raw: dict) -> dict:
    slots = {"callsign": CALLSIGN}
    for key, value in raw.items():
        if key == "taxi_route":
            value = tuple(value)
        elif key == "approach":
            value = Approach(**value)
        elif key == "wind":
            value = Wind(**value)
        slots[key] = value
    return slots


def pending_for(instruction: str, slots: dict, attempts: int = 0) -> PendingReadback:
    rendered = LIBRARY.render(instruction, slots)
    return PendingReadback(
        instruction, rendered.controller, rendered.expected, rendered.required, rendered.optional, attempts=attempts
    )


@pytest.mark.parametrize("case", CORPUS["case"], ids=lambda c: f"{c['instruction']}:{c['text'][:40]}")
def test_readback_corpus(case):
    slots = slots_from_toml(CORPUS["slots"][case["slots"]])
    result = GrammarInterpreter().interpret(case["text"], pending_for(case["instruction"], slots), CONTEXT)
    debug = render(normalize(case["text"]))
    if case["status"] == "no_match":
        assert result.kind != "readback", debug
        return
    assert result.kind == "readback", debug
    assert result.status == case["status"], (debug, result)
    assert sorted(result.missing) == sorted(case.get("missing", [])), (debug, result)
    assert sorted(result.mismatched) == sorted(case.get("mismatched", [])), (debug, result)
    assert sorted(result.unclear) == sorted(case.get("unclear", result.unclear)), (debug, result)


@pytest.mark.parametrize("case", CORPUS["intent"], ids=lambda c: c["text"][:40])
def test_intent_corpus(case):
    result = GrammarInterpreter().interpret(case["text"], None, CONTEXT)
    if not case["intent"]:
        assert result.kind == "unknown" and result.needs_fallback
        return
    assert (result.kind, result.intent) == ("request", case["intent"]), render(normalize(case["text"]))
    for key, value in case.get("values", {}).items():
        assert result.values.get(key) == value, result


def test_callsign_detection():
    g = GrammarInterpreter()
    assert g.interpret("ready to taxi, Skyhawk 2LT", None, CONTEXT).callsign_heard
    assert g.interpret("ready to taxi november one seven two lima tango", None, CONTEXT).callsign_heard
    assert not g.interpret("ready to taxi, 5XY", None, CONTEXT).callsign_heard
    airline = InterpretContext(callsign=Callsign("ASA123", telephony="Alaska", flight_number="123"))
    assert g.interpret("ready to taxi, Alaska one twenty-three", None, airline).callsign_heard


def test_strict_callsign_makes_readback_incomplete():
    slots = slots_from_toml(CORPUS["slots"]["takeoff"])
    strict = InterpretContext(callsign=CALLSIGN, strict_callsign=True)
    result = GrammarInterpreter().interpret("Cleared for takeoff 34L", pending_for("tower.takeoff", slots), strict)
    assert (result.status, result.missing) == ("incomplete", ("callsign",))


def test_second_failed_attempt_needs_fallback():
    slots = slots_from_toml(CORPUS["slots"]["takeoff"])
    g = GrammarInterpreter()
    first = g.interpret("Runway 34L, 2LT", pending_for("tower.takeoff", slots, attempts=0), CONTEXT)
    second = g.interpret("Runway 34L, 2LT", pending_for("tower.takeoff", slots, attempts=1), CONTEXT)
    assert (first.status, first.needs_fallback) == ("incomplete", False)
    assert (second.status, second.needs_fallback) == ("incomplete", True)


def test_chain_uses_fallback_only_when_needed():
    chain = ChainInterpreter(GrammarInterpreter(), SayAgainInterpreter())
    assert chain.interpret("ready to taxi, 2LT", None, CONTEXT).source == "grammar"
    unknown = chain.interpret("purple monkey dishwasher", None, CONTEXT)
    assert (unknown.source, unknown.intent) == ("fallback", "say_again")
    mayday = chain.interpret("mayday mayday engine fire", None, CONTEXT)
    assert (mayday.intent, mayday.source, mayday.needs_fallback) == ("emergency", "grammar", True)


def test_emergency_beats_a_pending_readback():
    slots = slots_from_toml(CORPUS["slots"]["takeoff"])
    result = GrammarInterpreter().interpret("mayday, cleared for takeoff 34L", pending_for("tower.takeoff", slots), CONTEXT)
    assert result.intent == "emergency"


# --- round-trip properties ------------------------------------------------------------------------


def random_slots(rng: random.Random) -> dict:
    def runway() -> str:
        return f"{rng.randint(1, 36):02d}" + rng.choice(["", "L", "R", "C"])

    def squawk() -> str:
        while (code := "".join(rng.choice("01234567") for _ in range(4))) in {"0000", "1200"} or code.startswith("7"):
            pass
        return code

    names = ["A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "Q", "S", "T", "W", "Y", "Z"]
    return {
        "callsign": rng.choice([CALLSIGN, Callsign("N5512K", type_name="Cherokee"), Callsign("ASA123", telephony="Alaska", flight_number="123")]),
        "destination": rng.choice(["Boeing Field", "Paine Field", "Renton Municipal"]),
        "station": rng.choice(["Seattle Departure", "Paine Tower", "Boeing Ground", "Seattle Center"]),
        "runway": runway(),
        "hold_short": runway(),
        "frequency": round(rng.randint(118000, 136975) // 25 * 25 / 1000, 3),
        "squawk": squawk(),
        "altitude": rng.randrange(2000, 17001, 500),
        "cruise": rng.randrange(3000, 17001, 1000),
        "heading": rng.randint(1, 360),
        "taxi_route": tuple(rng.choice(names) + rng.choice(["", "", str(rng.randint(1, 9))]) for _ in range(rng.randint(1, 4))),
        "approach": Approach(rng.choice(["ILS", "RNAV"]), runway()),
        "wind": Wind(rng.randrange(10, 361, 10), rng.randint(0, 25)),
        "fix": rng.choice(["BLAKO", "SEA", "Boeing Field", "Olympia"]),
    }


READBACK_TEMPLATES = sorted(t.id for t in LIBRARY.templates.values() if t.readback.required or t.readback.optional)


@pytest.mark.parametrize("instruction", READBACK_TEMPLATES)
def test_ideal_readback_is_correct(instruction):
    rng = random.Random(instruction)
    for _ in range(40):
        slots = random_slots(rng)
        text = LIBRARY.pilot_readback(instruction, slots)
        result = GrammarInterpreter().interpret(text, pending_for(instruction, slots), InterpretContext(callsign=slots["callsign"]))
        assert (result.kind, result.status) == ("readback", "correct"), (text, render(normalize(text)), result)
        assert result.callsign_heard, text


@pytest.mark.parametrize("instruction", READBACK_TEMPLATES)
def test_dropping_or_changing_an_element_is_caught(instruction):
    """Readbacks built from fragments: dropping a required element is incomplete, changing one is incorrect."""
    rng = random.Random(instruction + "drop")
    template = LIBRARY.get(instruction)
    elements = [*template.readback.required, *template.readback.optional]
    for _ in range(20):
        slots = random_slots(rng)
        pending = pending_for(instruction, slots)

        def readback(omit: str | None = None, changed: dict | None = None) -> str:
            parts = [LIBRARY.fragment(e, {**slots, **(changed or {})}) for e in elements if e != omit]
            return (sum(parts[1:], parts[0]) if parts else Phrase("", "")).spoken + ", " + LIBRARY.fill("{callsign_short}", slots)[1]

        for element in template.readback.required:
            if len(elements) > 1:
                result = GrammarInterpreter().interpret(readback(omit=element), pending, CONTEXT)
                assert result.status == "incomplete" and element in result.missing, (element, readback(omit=element), result)
        for element in elements:
            if element == "destination":
                continue  # matched by name presence only, so a different airport reads as "not mentioned"
            other = random_slots(random.Random(rng.random()))
            if element not in other or other[element] == slots[element] or values_close(element, other[element], slots[element]):
                continue  # a near miss ("15 left" for 15) gets "confirm", tested in the corpus
            text = readback(changed={element: other[element]})
            result = GrammarInterpreter().interpret(text, pending, CONTEXT)
            assert result.status == "incorrect" and element in result.mismatched, (element, text, render(normalize(text)), result)


def test_affirm_answers_a_confirm():
    slots = slots_from_toml(CORPUS["slots"]["contact_tower"])
    pending = pending_for("ground.handoff_tower", slots)
    unclear = GrammarInterpreter().interpret("Paine Tower on 12.2, 2LT", pending, CONTEXT)
    assert unclear.status == "unclear" and list(unclear.unclear) == ["frequency"]
    confirming = replace(pending, required=("frequency",), confirming=True)
    assert GrammarInterpreter().interpret("Affirm, 2LT", confirming, CONTEXT).status == "correct"
    assert GrammarInterpreter().interpret("120.2, 2LT", confirming, CONTEXT).status == "correct"
    assert GrammarInterpreter().interpret("Affirm, 2LT", pending, CONTEXT).kind != "readback"  # nothing to confirm
