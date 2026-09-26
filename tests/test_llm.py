"""The language model path, tested without a model: scripted answers, including deliberately bad ones.

The same seeded edge cases run against a real model with ``localtc llm eval`` (or test_llm_live.py).
"""

import json
import tomllib
from dataclasses import replace
from pathlib import Path

import msgspec
import pytest
from helpers.llm import TIMEOUT, ScriptedBackend

from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.llm import (
    LlmInterpreter,
    LlmPhraser,
    RecordedBackend,
    build_request,
    is_question,
    trigger,
)
from localtc.atc_core.llm.phrase import PhraseError, check_reply
from localtc.atc_core.llm.understand import (
    AnswerError,
    load_examples,
    parse_answer,
    parse_value,
)
from localtc.atc_core.phraseology import TemplateLibrary
from localtc.atc_core.readback import (
    GrammarInterpreter,
    InterpretContext,
    PendingReadback,
)
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.values import Callsign
from localtc.llm.eval import CorpusBackend, load_cases, run_cases, setup
from localtc.replay import Recording
from localtc.scenario import run_scenario
from localtc.sim_api import (
    SIM_EVENT_TYPES,
    AirportData,
    AtcAlert,
    AtcTransmission,
    LlmExchange,
    OwnshipState,
    Transcript,
)

HERE = Path(__file__).parent
EDGE = HERE / "scenarios_llm" / "edge_cases.toml"
CASES = load_cases()
LIBRARY = TemplateLibrary.load()
DP69 = InterpretContext(callsign=Callsign("DP69"), phase="RUNWAY_HOLD", station="Montreal Tower")


def edge_backend() -> ScriptedBackend:
    answers = tomllib.loads(EDGE.with_suffix(".answers.toml").read_text(encoding="utf-8"))
    return ScriptedBackend(answers["understand"], answers["phrase"])


# --- the seeded edge cases ------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_seeded_case(case):
    [result] = run_cases(LlmInterpreter(CorpusBackend(CASES)), [case])
    assert result.passed, result.problems


def test_seeded_cases_in_a_whole_flight(request):
    """Awkward calls seeded into the synthetic IFR flight: model answers, rejections, retries, a timeout."""
    backend = edge_backend()
    result = run_scenario(EDGE, interpreter=LlmInterpreter(backend, mode="fallback"), phraser=LlmPhraser(backend))
    golden = EDGE.with_suffix(".golden.txt")
    if request.config.getoption("--update-goldens") or not golden.exists():
        golden.write_text(result.transcript, encoding="utf-8")
    assert result.transcript == golden.read_text(encoding="utf-8")
    alerts = [o.kind for o in result.outputs if isinstance(o, AtcAlert)]
    assert alerts == ["emergency"]  # no readback loops, no incursions, and the emergency raised once


# --- when the model is asked --------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "pending", "expected"), [
    ("Mayday mayday, DP69, engine fire", False, "emergency"),
    ("what's the altimeter", False, "question"),
    ("say altimeter for DP69", False, "question"),
    ("uh the thing", False, "parser_failure"),
    ("DP69 request direct Quebec", False, "parser_failure"),
    # The grammar hears a check-in and would miss the request riding along with it.
    ("Montreal Center, DP69, level 12000, request direct Quebec", False, "out_of_grammar"),
    ("Montreal Ground, DP69, request taxi", False, None),  # "request" is part of a known call
    ("Runway 06L, DP69", True, "readback_rejected"),  # cleared for takeoff missing
    ("Runway 06L, cleared for takeoff, DP69", True, None),
    ("say again for DP69", False, None),
])
def test_triggers(text, pending, expected):
    readback = LIBRARY.render("tower.takeoff", {"runway": "06L", "callsign": Callsign("DP69")})
    waiting = PendingReadback("tower.takeoff", "tower", readback.expected, readback.required) if pending else None
    grammar = GrammarInterpreter().interpret(text, waiting, DP69)
    assert trigger(grammar, text) == expected


def test_questions():
    assert is_question("which runway are we landing on")
    assert is_question("any weather at quebec?")
    assert not is_question("say again")
    assert not is_question("Montreal Departure, DP69, 6000 climbing 12000")


def test_fallback_mode_asks_the_model_only_when_triggered():
    backend = ScriptedBackend({"what's the altimeter": {"kind": "question", "topic": "altimeter"}})
    interpreter = LlmInterpreter(backend, mode="fallback")
    interpreter.interpret("Montreal Tower, DP69, holding short runway 06L, ready for departure", None, DP69)
    assert backend.requests == []
    assert interpreter.interpret("what's the altimeter", None, DP69).intent == "question"
    assert len(backend.requests) == 1


def test_without_a_model_it_is_the_grammar():
    interpreter = LlmInterpreter(None)
    assert interpreter.mode == "off"
    result = interpreter.interpret("uh the thing", None, DP69)
    assert (result.intent, result.source, result.exchanges) == ("say_again", "fallback", ())


# --- the prompt ------------------------------------------------------------------------------------------------


def _takeoff_pending() -> PendingReadback:
    r = LIBRARY.render("tower.takeoff", {"runway": "06L", "callsign": Callsign("DP69")})
    return PendingReadback("tower.takeoff", "tower", r.expected, r.required, r.optional)


def test_prompt_is_narrow():
    context = InterpretContext(callsign=Callsign("DP69"), phase="RUNWAY_HOLD", station="Montreal Tower", station_role="tower",
                               last_atc="DP69, runway 06L, fly runway heading, cleared for takeoff.", squawk="5015",
                               cleared_altitude_ft=5000, runway="06L")
    request = build_request("Clear for takeoff, DP69", _takeoff_pending(), context, load_examples())
    # The moment in fixed keys, in a fixed order, "none" when empty: what a small model reads best.
    assert request.prompt == (
        "Callsign: DP69\nPhase: RUNWAY_HOLD\nStation: Montreal Tower (tower)\n"
        "Cleared: altitude 5000; squawk 5015; runway 06L\nTraffic called: none\n"
        'ATC last said: "DP69, runway 06L, fly runway heading, cleared for takeoff."\n'
        "Readback expected: runway 06L; cleared for takeoff\n"
        'Pilot: "Clear for takeoff, DP69"'
    )
    # Only the elements being read back, plus kind and intent; no free-text fields to roleplay in.
    assert list(request.schema["properties"]) == ["kind", "intent", "runway", "cleared_for_takeoff"]
    assert request.schema["additionalProperties"] is False
    assert "never reply to the pilot" in request.system
    examples = [text for role, text in request.messages[:-1] if role == "user"]
    assert all(text.startswith("Callsign: ") and "\nTraffic called: " in text for text in examples)  # the same keys

    open_request = build_request("what's the altimeter", None, context, load_examples())
    assert list(open_request.schema["properties"]) == [
        "kind", "intent", "topic", "runway", "atis", "altitude", "fix", "approach", "conditions", "emergency", "souls",
        "fuel"]
    assert open_request.prompt.endswith("Readback expected: none\nPilot: \"what's the altimeter\"")


def test_calls_of_a_kind_share_the_prompt_up_to_their_own_last_message():
    # Ollama reuses what it has already read of a prompt: two readbacks (or two requests) must not start
    # differing before the last message, whatever the moment, or every example is read again (seconds on a CPU).
    # Readbacks and requests keep their own examples: one mixed set made the small model worse (llm eval).
    ex = load_examples()
    first = InterpretContext(callsign=Callsign("DP69"), phase="RUNWAY_HOLD", station="Montreal Tower", runway="06L")
    later = InterpretContext(callsign=Callsign("DP69"), phase="CRUISE", station="Montreal Center", cleared_altitude_ft=24000,
                             squawk="5015", traffic="2 o'clock, 3 miles", last_atc="DP69, climb and maintain FL240.")
    for pending in (_takeoff_pending(), None):
        a, b = build_request("one", pending, first, ex), build_request("two", pending, later, ex)
        assert a.system == b.system and a.messages[:-1] == b.messages[:-1] and a.prompt != b.prompt


def test_request_key_identifies_the_question():
    a = build_request("Clear for takeoff, DP69", _takeoff_pending(), DP69, load_examples())
    b = build_request("Clear for takeoff, DP69", _takeoff_pending(), DP69, load_examples())
    c = build_request("Cleared for takeoff, DP69", _takeoff_pending(), DP69, load_examples())
    assert a.key("m") == b.key("m") != c.key("m") and a.key("m") != a.key("other-model")


# --- reading and checking answers -------------------------------------------------------------------------------


@pytest.mark.parametrize(("element", "raw", "value"), [
    ("runway", "06L", "06L"), ("runway", "6 left", "06L"), ("runway", "RWY 24R", "24R"), ("runway", "", None),
    ("frequency", "120.425", 120.425), ("squawk", "5015", "5015"), ("altitude", "FL240", 24000),
    ("altitude", "12,000", 12000), ("approach", "ILS 06", "ILS RWY 06"), ("atis", "information B", "B"),
    ("cleared_for_takeoff", True, True), ("cleared_for_takeoff", False, None),
])
def test_parse_value(element, raw, value):
    parsed = parse_value(element, raw)
    assert (parsed.display if hasattr(parsed, "display") else parsed) == value


@pytest.mark.parametrize(("element", "raw"), [
    ("runway", "37"), ("runway", "zero six"), ("frequency", "1204"), ("frequency", "99.5"), ("squawk", "7800"),
    ("altitude", "high"), ("cleared_for_takeoff", "yes"),
])
def test_parse_value_rejects(element, raw):
    with pytest.raises(AnswerError):
        parse_value(element, raw)


def test_invented_values_are_rejected():
    tokens = normalize("roger, DP69")
    with pytest.raises(AnswerError, match="did not say squawk=5015"):
        parse_answer(json.dumps({"kind": "readback", "intent": "", "squawk": "5015"}),
                     PendingReadback("x", "clearance", {"squawk": "5015"}, ("squawk",)), tokens)
    with pytest.raises(AnswerError, match="not valid JSON"):
        parse_answer("kind: readback", None, tokens)
    with pytest.raises(AnswerError, match="needs an intent"):
        parse_answer(json.dumps({"kind": "request", "intent": ""}), None, tokens)


def test_bad_answer_is_retried_with_the_problem_named():
    backend = ScriptedBackend({"tower DP69 holding short zero six left": [
        "Sure! The pilot is holding short.",
        {"kind": "request", "intent": "ready_for_departure", "runway": "06L"},
    ]})
    result = LlmInterpreter(backend).interpret("tower DP69 holding short zero six left", None, DP69)
    assert (result.intent, result.values, result.source) == ("ready_for_departure", {"runway": "06L"}, "llm")
    assert [e.outcome for e in result.exchanges] == ["invalid", "used"]
    assert "can't be used: not valid JSON" in backend.requests[1].messages[-1][1]
    assert backend.requests[1].messages[-2] == ("assistant", "Sure! The pilot is holding short.")


def test_timeout_uses_the_grammar_and_does_not_retry():
    backend = ScriptedBackend({"Montreal Ground, DP69, request taxi": TIMEOUT})
    result = LlmInterpreter(backend, mode="primary").interpret("Montreal Ground, DP69, request taxi", None, DP69)
    assert (result.kind, result.intent, result.source) == ("request", "ready_to_taxi", "grammar")
    assert [(e.outcome, e.trigger) for e in result.exchanges] == [("timeout", "")]


def test_a_correct_readback_never_waits_for_the_model():
    """The model shares the PC with the sim: a readback the grammar finds correct is the script, read back."""
    backend = ScriptedBackend({"Runway 06L, cleared for takeoff, DP69": TIMEOUT})
    result = LlmInterpreter(backend, mode="primary").interpret("Runway 06L, cleared for takeoff, DP69", _takeoff_pending(), DP69)
    assert (result.kind, result.status, result.source) == ("readback", "correct", "grammar")
    assert result.exchanges == () and backend.requests == []


def test_the_budget_caps_retries():
    now = [0.0]

    class Slow(ScriptedBackend):
        def complete(self, request, *, timeout_s):
            now[0] += 3.9
            return super().complete(request, timeout_s=timeout_s)

    backend = Slow({"what's the altimeter": ["not json", {"kind": "question", "topic": "altimeter"}]})
    result = LlmInterpreter(backend, budget_s=4.0, clock=lambda: now[0]).interpret("what's the altimeter", None, DP69)
    assert [e.outcome for e in result.exchanges] == ["invalid"]  # no time left for a second try
    assert (result.intent, result.values) == ("question", {"topic": "altimeter"})  # the topic word is enough


def test_an_intent_that_makes_no_sense_now_is_rejected():
    context = InterpretContext(callsign=Callsign("DP69"), phase="CRUISE", station="Montreal Center")
    backend = ScriptedBackend({"DP69 ready": {"kind": "request", "intent": "ready_to_taxi"}})
    result = LlmInterpreter(backend).interpret("DP69 ready", None, context)
    assert [e.detail for e in result.exchanges] == ["ready_to_taxi is not possible in phase CRUISE"] * 2
    assert result.intent != "ready_to_taxi"


# --- phrasing -------------------------------------------------------------------------------------------------------

FACTS = {"destination": "Quebec", "wind": "240@8", "altimeter": "29.92"}


def test_phrasing_checks():
    assert check_reply('{"reply":"Quebec wind 240 at 8, altimeter 29.92."}', FACTS, ()) == "Quebec wind 240 at 8, altimeter 29.92"
    assert check_reply('{"reply":"DP69, unable, continue as filed"}', FACTS, ("DP69",)) == "unable, continue as filed"
    for reply, problem in [
        ("cleared direct Quebec", "instruction"), ("climb and maintain 24000", "instruction"),
        ("roger, approved", "instruction"), ("altimeter 30.01", "not in the facts"), ("", "non-empty"),
        (" ".join(["unable"] * 30), "longer"),
    ]:
        with pytest.raises(PhraseError, match=problem):
            check_reply(json.dumps({"reply": reply}), FACTS, ())


def test_phrasing_falls_back_to_the_template():
    backend = ScriptedBackend(phrase={"request direct": ['{"reply":"approved"}', '{"reply":"proceed direct"}']})
    phrase, exchanges = LlmPhraser(backend).reply(pilot="request direct", decision="decline", facts=FACTS,
                                                  callsigns=(), t=1.0)
    assert phrase is None and [e.outcome for e in exchanges] == ["invalid", "invalid"]


# --- the engine with a model, on real recordings -------------------------------------------------------------------

AIRPORTS = HERE / "fixtures" / "airports_real"
CYUL = HERE / "fixtures" / "real_cyul"


def cyul_engine(backend, **cfg) -> tuple[AtcEngine, OwnshipState]:
    from localtc.airports import load_airport

    engine = AtcEngine(EngineConfig(seed=5, destination="CYQB", cruise_ft=12000, callsign="DP69", **cfg),
                       interpreter=LlmInterpreter(backend), phraser=LlmPhraser(backend))
    for icao in ("CYUL", "CYQB"):
        engine.handle(AirportData(t=0.0, airport=load_airport(AIRPORTS / f"{icao}.json")))
    own = next(e for e in Recording(CYUL).events() if isinstance(e, OwnshipState))
    return engine, own


def say(engine: AtcEngine, own: OwnshipState, text: str, *, mhz: float, wait: float = 6.0) -> list:
    engine.handle(msgspec.structs.replace(own, com1_mhz=mhz))
    out = engine.handle(Transcript(t=own.t + 1, text=text))
    return out + engine.handle(msgspec.structs.replace(own, t=own.t + wait, com1_mhz=mhz))


def atc(outputs) -> list[str]:
    return [o.text for o in outputs if isinstance(o, AtcTransmission)]


def test_questions_are_answered_from_the_sim():
    backend = ScriptedBackend({"ground, what's the altimeter": {"kind": "question", "topic": "altimeter"},
                               "and the winds?": {"kind": "question", "topic": "wind"}})
    engine, own = cyul_engine(backend)
    assert atc(say(engine, own, "ground, what's the altimeter", mhz=121.0)) == ["DP69, altimeter 30.10."]
    later = msgspec.structs.replace(own, t=own.t + 20)
    assert atc(say(engine, later, "and the winds?", mhz=121.0)) == ["DP69, wind calm."]


def test_unknown_questions_are_worded_by_the_model_or_unable():
    backend = ScriptedBackend(
        {"how long is the taxi": {"kind": "question", "topic": "other"},
         "is the cafe open": {"kind": "question", "topic": "other"}},
        phrase={"how long is the taxi": '{"reply":"unable, information not available"}',
                "is the cafe open": '{"reply":"cleared to the cafe"}'},
    )
    engine, own = cyul_engine(backend)
    assert atc(say(engine, own, "how long is the taxi", mhz=121.0)) == ["DP69, unable, information not available."]
    later = msgspec.structs.replace(own, t=own.t + 20)
    assert atc(say(engine, later, "is the cafe open", mhz=121.0)) == ["DP69, unable."]


def test_altitude_requests_are_decided_by_the_engine():
    text = "request flight level two four zero"
    backend = ScriptedBackend({text: {"kind": "request", "intent": "request_altitude", "altitude": "24000"}})
    engine, own = cyul_engine(backend)
    assert atc(say(engine, own, text, mhz=121.0)) == ["DP69, unable."]  # on the ground, talking to ground
    assert engine.state.assignments.altitude_ft is None

    engine, _ = cyul_engine(backend)
    climbing = None
    for event in Recording(CYUL).events():
        if isinstance(event, SIM_EVENT_TYPES) and event.t < 640:
            engine.handle(event)
            climbing = event if isinstance(event, OwnshipState) else climbing
    assert engine.state.phase == "DEPARTURE" and climbing.alt_indicated_ft > 5000
    assert atc(say(engine, climbing, text, mhz=120.425)) == ["DP69, climb and maintain FL240."]
    assert engine.state.pending.expected == {"altitude": 24000}
    assert engine.tracker.detector.cruise_ft == 24000  # cruise is where the pilot now levels off


def test_recorded_answers_replay_the_same_session():
    """Model calls go into the recording; replaying them gives the same ATC without a model."""
    backend = ScriptedBackend({"ground, what's the altimeter": {"kind": "question", "topic": "altimeter"}})
    engine, own = cyul_engine(backend)
    first = say(engine, own, "ground, what's the altimeter", mhz=121.0)
    exchanges = [e for e in first if isinstance(e, LlmExchange)]
    assert [e.outcome for e in exchanges] == ["used"]

    replayed, own = cyul_engine(RecordedBackend.from_events(exchanges))
    again = say(replayed, own, "ground, what's the altimeter", mhz=121.0)
    assert atc(again) == atc(first)
    assert [e.response for e in again if isinstance(e, LlmExchange)] == [e.response for e in exchanges]

    changed, own = cyul_engine(RecordedBackend.from_events(exchanges))
    miss = say(changed, own, "ground, what is the altimeter", mhz=121.0)  # a different prompt: not in the recording
    assert [e.outcome for e in miss if isinstance(e, LlmExchange)] == ["recorded_miss"]


def test_the_corpus_context_matches_the_engine():
    """``localtc llm eval`` builds the same pending readback the engine would."""
    case = next(c for c in CASES if c.instruction == "clearance.ifr")
    pending, context = setup(case, LIBRARY)
    assert pending.required == ("altitude", "frequency", "squawk") and context.last_atc.startswith("DP69, cleared to Quebec")


def test_llm_exchanges_record_the_whole_flight():
    """Everything the model was asked during the real CYUL recording's replay is in the output, in order."""
    backend = ScriptedBackend()
    engine, own = cyul_engine(backend)
    outputs = []
    for event in Recording(CYUL).events():
        if isinstance(event, (*SIM_EVENT_TYPES, Transcript)):
            outputs += engine.handle(event)
    exchanges = [o for o in outputs if isinstance(o, LlmExchange)]
    transcripts = [e for e in Recording(CYUL).events() if isinstance(e, Transcript)]
    # One call per pilot transmission, except the readbacks the grammar found correct.
    assert {e.t for e in exchanges} < {t.t for t in transcripts}
    assert [e.t for e in exchanges] == sorted(e.t for e in exchanges)
    assert all(e.outcome == "error" for e in exchanges)  # nothing scripted: every one fell back to the grammar


# --- what the model is shown of the moment -----------------------------------------------------------------------


def _shown(backend: ScriptedBackend, purpose: str = "understand") -> str:
    # The transmission's own message: a retry adds the correction after it, so the first attempt's.
    return next(r.prompt for r in reversed(backend.requests) if r.purpose == purpose and "can't be used" not in r.prompt)


def test_the_model_sees_this_moment_and_nothing_older():
    backend = ScriptedBackend({q: {"kind": "question", "topic": "altimeter"} for q in ("ground, what's the altimeter",
                                                                                       "altimeter again please")})
    engine, own = cyul_engine(backend)
    say(engine, own, "ground, what's the altimeter", mhz=121.0)
    first = _shown(backend)
    assert first.startswith("Callsign: DP69\nPhase: ") and "\nStation: Montreal Ground (ground)\n" in first
    assert "\nTraffic called: none\n" in first and "\nReadback expected: none\n" in first

    soon = msgspec.structs.replace(own, t=own.t + 60)
    say(engine, soon, "altimeter again please", mhz=121.0)
    assert 'ATC last said: "DP69, altimeter 30.10."' in _shown(backend)  # what ground just said

    # An hour on, the same controller's last words are another moment: not shown as if just said.
    later = msgspec.structs.replace(own, t=own.t + 3600)
    say(engine, later, "altimeter again please", mhz=121.0)
    assert "\nATC last said: none\n" in _shown(backend)


def test_traffic_called_is_shown_for_a_few_minutes():
    backend = ScriptedBackend({"looking": {"kind": "request", "intent": "traffic_report"}})
    engine, own = cyul_engine(backend)
    engine._last_traffic = (own.t, "2 o'clock, 3 miles, crossing left to right, 4,000 ft")
    say(engine, own, "looking", mhz=121.0)
    assert "\nTraffic called: 2 o'clock, 3 miles, crossing left to right, 4,000 ft\n" in _shown(backend)
    say(engine, msgspec.structs.replace(own, t=own.t + 600), "looking", mhz=121.0)
    assert "\nTraffic called: none\n" in _shown(backend)


def test_the_phrasing_model_knows_who_it_speaks_for_and_the_runway_in_use():
    backend = ScriptedBackend({"is the cafe open": {"kind": "question", "topic": "other"}},
                              phrase={"is the cafe open": '{"reply":"unable, information not available"}'})
    engine, own = cyul_engine(backend)
    say(engine, own, "is the cafe open", mhz=121.0)
    facts = _shown(backend, "phrase")
    assert "controller Montreal Ground" in facts and "runway in use " in facts
