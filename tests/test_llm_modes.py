"""How much ATC leans on the language model: the five settings of ``[llm] mode`` (four modes and off), for reading
the pilot's call and for the words of ATC's reply, and what's kept of how each answer came about.

All without a model: scripted answers, keyed by what the pilot said (``helpers.llm.ScriptedBackend``).
"""

import msgspec
import pytest
from helpers.llm import TIMEOUT, ScriptedBackend
from test_llm import DP69, _takeoff_pending, atc, cyul_engine, say

from localtc.atc_core.llm import LlmInterpreter
from localtc.atc_core.llm.phrase import PhraseError, check_reply, check_reworded, spoken_as
from localtc.atc_core.llm.understand import parse_answer
from localtc.atc_core.readback import GrammarInterpreter, InterpretContext
from localtc.atc_core.readback.normalize import normalize
from localtc.atc_core.values import Callsign
from localtc.config import load_config
from localtc.sim_api import AtcAlert, AtcDecision, AtcTransmission, LlmExchange

MODES = ("off", "scripted", "semi", "mostly_llm", "llm")
GROUND = InterpretContext(callsign=Callsign("DP69"), phase="PARKED", station="Montreal Ground", station_role="ground")
ROUTINE = "Montreal Ground, DP69 at the gate with information X, ready to taxi"
OFF_SCRIPT = "Montreal Ground, DP69 ready to taxi, we will need a short delay"  # read, with a word it has no call for
GARBLED = "Montreal Ground, DP69, the the uh blue thing"  # nothing the grammar can read
QUESTION = "Montreal Ground, DP69, what's the altimeter?"
RUNWAY_LENGTH = "Montreal Ground, DP69. How long is the 24 right runway?"  # the flight report's call, at Montreal


def asked(mode: str, text: str, pending=None, context=GROUND) -> bool:
    backend = ScriptedBackend()
    LlmInterpreter(backend, mode=mode).interpret(text, pending, context)
    return bool(backend.requests)


# --- reading the call ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("mode", "routine", "off_script", "garbled", "question"), [
    ("off", False, False, False, False),  # the grammar alone
    ("scripted", False, False, True, False),  # the model only for what the grammar can't read
    ("semi", False, True, True, False),  # ... and for anything off the script
    ("mostly_llm", False, True, True, True),  # the model, but for the routine calls the grammar is sure of
    ("llm", True, True, True, True),  # the model reads every call
])
def test_each_mode_sends_the_calls_it_should_to_the_model(mode, routine, off_script, garbled, question):
    assert asked(mode, ROUTINE) == routine
    assert asked(mode, OFF_SCRIPT) == off_script
    assert asked(mode, GARBLED) == garbled
    assert asked(mode, QUESTION) == question


@pytest.mark.parametrize("mode", MODES)
def test_a_correct_readback_goes_to_the_model_only_when_it_reads_every_call(mode):
    assert asked(mode, "Runway 06L, cleared for takeoff, DP69", _takeoff_pending(), DP69) == (mode == "llm")


def test_the_old_settings_are_the_nearest_modes():
    backend = ScriptedBackend()
    assert LlmInterpreter(backend, mode="primary").mode == "llm"  # "the model reads every call"
    assert LlmInterpreter(backend, mode="fallback").mode == "semi"  # "the grammar first, the model when stuck"
    assert LlmInterpreter(None, mode="llm").mode == "off"  # no model: the grammar
    with pytest.raises(ValueError):
        LlmInterpreter(backend, mode="sometimes")


def test_scripted_turns_what_the_model_read_back_into_the_script():
    """Fully scripted: the model reads what the grammar couldn't, and the script answers it in its own words."""
    garbled = "Montreal Ground DP69 uh with xray uh ready ready to taxi"
    backend = ScriptedBackend({garbled: {"kind": "request", "intent": "ready_to_taxi", "atis": "X"}})
    engine, own = cyul_engine(backend)
    engine.interpreter.mode = "scripted"
    out = say(engine, own, garbled, mhz=121.0)
    assert [e.purpose for e in out if isinstance(e, LlmExchange)] == ["understand"]  # never asked for words
    [reply] = [o for o in out if isinstance(o, AtcTransmission)]
    assert reply.text == "DP69, taxi to runway 06L at C via G, C." and reply.worded_by == "template"
    [decision] = [o for o in out if isinstance(o, AtcDecision)]
    assert (decision.trigger, decision.used, decision.model) == ("hesitation", "model", "request ready_to_taxi atis=X")


# --- the flight report: a question the checks turned away -------------------------------------------------------------


def test_a_question_the_model_files_as_a_request_is_the_question():
    """"How long is the 24 right runway?" came back as request_runway, which needs "can", "could", "request" ...:
    rejected twice, and ATC answered a different question ("expect runway 24L for departure")."""
    said = normalize(RUNWAY_LENGTH)
    answer = parse_answer('{"kind":"request","intent":"request_runway","runway":"24R"}', None, said, asked=True)
    assert (answer.kind, answer.topic) == ("question", "other") and "read as a question" in answer.note
    # Asking for something is still a request, and a wrong intent for it still gets the retry.
    with pytest.raises(Exception, match="request_altitude needs"):
        parse_answer('{"kind":"request","intent":"request_altitude"}', None, normalize("can we get direct BLAKO"),
                     asked=False)


@pytest.mark.parametrize("mode", ["scripted", "semi", "mostly_llm", "llm"])
def test_the_runway_length_is_answered_from_the_airport_data(mode):
    backend = ScriptedBackend({RUNWAY_LENGTH: {"kind": "request", "intent": "request_runway", "runway": "24R"}},
                              phrase={RUNWAY_LENGTH: '{"reply":"24R is 11,000 feet long"}'})
    engine, own = cyul_engine(backend)
    engine.interpreter.mode = mode
    out = say(engine, own, RUNWAY_LENGTH, mhz=121.0)
    [decision] = [o for o in out if isinstance(o, AtcDecision)]
    phrased = [r for r in backend.requests if r.purpose == "phrase"]
    if mode in ("scripted", "semi"):  # a plain question the data answers: the script's words, no model
        assert atc(out) == ["DP69, runway 24R is 11,000 feet."] and not phrased and decision.wording == "template"
    else:  # the model's words, holding the data's answer
        assert atc(out) == ["DP69, 24R is 11,000 feet long."] and decision.wording == "model"
        assert "here and now runway 24R is 11,000 feet" in phrased[-1].prompt
        assert "06L/24R 11000 ft" in phrased[-1].prompt  # the airport's runways are facts too


@pytest.mark.parametrize("mode", ["mostly_llm", "llm"])
def test_a_reply_about_another_runway_is_turned_away_for_the_datas_answer(mode):
    # What llama3.2:3b did with the flight report's call: 24L's length for 24R.
    backend = ScriptedBackend(phrase={RUNWAY_LENGTH: '{"reply":"24L runway 9600 ft"}'})
    engine, own = cyul_engine(backend)
    engine.interpreter.mode = mode
    out = say(engine, own, RUNWAY_LENGTH, mhz=121.0)
    assert atc(out) == ["DP69, runway 24R is 11,000 feet."]
    phrases = [e for e in out if isinstance(e, LlmExchange) and e.purpose == "phrase"]
    assert [e.outcome for e in phrases] == ["invalid", "invalid"] and "leaves out 11000" in phrases[0].detail
    [decision] = [o for o in out if isinstance(o, AtcDecision)]
    assert decision.wording == "template" and "ATC gave the sim's data" in decision.fallback


@pytest.mark.parametrize("mode", ["scripted", "semi", "mostly_llm", "llm"])
def test_when_the_model_cant_answer_ATC_says_so_instead_of_answering_something_else(mode):
    wide = "Montreal Ground, DP69. How wide is the 24 right runway?"  # not in the data
    backend = ScriptedBackend(phrase={wide: '{"reply":"runway 24R is 200 feet wide"}'})  # made up
    engine, own = cyul_engine(backend)
    engine.interpreter.mode = mode
    out = say(engine, own, wide, mhz=121.0)
    assert atc(out) == ["DP69, unable, that information is not available."]  # never "expect runway 06L"
    phrases = [e for e in out if isinstance(e, LlmExchange) and e.purpose == "phrase"]
    assert [(e.outcome, e.attempt) for e in phrases] == [("invalid", 1), ("invalid", 2)]
    assert all("200" in e.detail and e.response for e in phrases)  # the raw answer and why, kept
    [decision] = [o for o in out if isinstance(o, AtcDecision)]
    assert "the model's answer was turned away (invalid: reply has numbers that are not in the facts: 200); " \
        'ATC said "unable, that information is not available"' in decision.fallback
    [alert] = [o for o in out if isinstance(o, AtcAlert) and o.kind == "llm_rejected"]  # and the pilot is told
    assert alert.detail in decision.fallback and "the model's answer was turned away" in alert.detail
    # (A reading of the call the grammar took over is in the record, not the alert: the pilot heard the model either
    # way, and those alerts were most of what made the model look like it wasn't there.)
    assert "model gave no usable reading" not in alert.detail


# --- wording the reply --------------------------------------------------------------------------------------------------

TAXI = "taxi to runway 06L at C via G, C"
REWORDED = '{"reply":"runway 06L at C, taxi via G and C"}'


def taxi_engine(mode: str, reword: str | None = REWORDED, understand=None):
    backend = ScriptedBackend({ROUTINE: understand or {"kind": "request", "intent": "ready_to_taxi", "atis": "X"}},
                              reword={ROUTINE: reword} if reword else None)
    engine, own = cyul_engine(backend)
    engine.interpreter.mode = mode
    return engine, own, backend


@pytest.mark.parametrize(("mode", "worded"), [("off", "template"), ("scripted", "template"), ("semi", "template"),
                                              ("mostly_llm", "template"), ("llm", "model")])
def test_a_routine_reply_is_the_models_words_only_when_the_model_words_every_call(mode, worded):
    engine, own, backend = taxi_engine(mode)
    out = say(engine, own, ROUTINE, mhz=121.0)
    [reply] = [o for o in out if isinstance(o, AtcTransmission)]
    assert reply.worded_by == worded
    assert reply.text == ("DP69, runway 06L at C, taxi via G and C." if worded == "model" else f"DP69, {TAXI}.")
    if worded == "model":  # said the way the script says the same values
        assert reply.spoken.lower().endswith(", runway six left at charlie, taxi via golf and charlie.")
    # What's read back is the script's, whoever worded it.
    assert engine.state.pending.expected["runway"] == "06L"
    assert [r.purpose for r in backend.requests] == {"llm": ["understand", "reword"]}.get(mode, [])


def test_mostly_llm_words_the_replies_to_the_calls_the_model_read():
    hesitant = "Montreal Ground, DP69 uh ready to taxi with xray"
    backend = ScriptedBackend({hesitant: {"kind": "request", "intent": "ready_to_taxi", "atis": "X"}},
                              reword={hesitant: REWORDED})
    engine, own = cyul_engine(backend)
    engine.interpreter.mode = "mostly_llm"
    out = say(engine, own, hesitant, mhz=121.0)
    [reply] = [o for o in out if isinstance(o, AtcTransmission)]
    assert (reply.text, reply.worded_by) == ("DP69, runway 06L at C, taxi via G and C.", "model")
    [decision] = [o for o in out if isinstance(o, AtcDecision)]
    assert (decision.used, decision.decision, decision.wording, decision.fallback) == \
        ("model", "ground.taxi_out_at", "model", "")


@pytest.mark.parametrize(("reword", "why"), [
    ('{"reply":"taxi via G and C, hold short of runway 06R"}', "reply leaves out runway 06L"),  # the wrong runway
    ('{"reply":"runway 06L at C, taxi via G and C, cleared for takeoff"}', "reply adds cleared, for, takeoff"),
    ('{"reply":"runway 06L at C via G"}', "reply leaves out taxi"),
    ("Sure! Here you go", "not valid JSON"),
])
def test_a_reworded_reply_that_changes_the_clearance_is_said_as_the_script_has_it(reword, why):
    engine, own, backend = taxi_engine("llm", reword=reword)
    out = say(engine, own, ROUTINE, mhz=121.0)
    [reply] = [o for o in out if isinstance(o, AtcTransmission)]
    assert (reply.text, reply.worded_by) == (f"DP69, {TAXI}.", "template")
    rewords = [e for e in out if isinstance(e, LlmExchange) and e.purpose == "reword"]
    assert [e.outcome for e in rewords] == ["invalid", "invalid"] and why.split(",")[0] in rewords[0].detail
    [decision] = [o for o in out if isinstance(o, AtcDecision)]
    assert decision.wording == "template" and "the model's words for ground.taxi_out_at were turned away (invalid" \
        in decision.fallback and "ATC said the same in the script's words" in decision.fallback


def test_no_rewording_when_the_model_just_ran_out_of_time():
    engine, own, backend = taxi_engine("llm", understand=TIMEOUT)
    out = say(engine, own, ROUTINE, mhz=121.0)
    assert [r.purpose for r in backend.requests] == ["understand"]  # the pilot isn't kept waiting twice
    assert atc(out) == [f"DP69, {TAXI}."]
    [decision] = [o for o in out if isinstance(o, AtcDecision)]
    assert decision.used == "grammar" and "model gave no usable reading (timeout" in decision.fallback


@pytest.mark.parametrize(("scripted", "reply", "problem"), [
    ("climb and maintain 9,000", "climb and maintain 9000", None),
    ("climb and maintain FL240", "climb and maintain flight level 240", None),
    ("climb and maintain FL240", "climb and maintain 240", "say it as a flight level"),  # what llama3.2:3b did
    ("climb and maintain 9,000", "climb and maintain 8,000", "leaves out 9000"),
    ("climb and maintain 9,000", "climb and maintain 9,000, squawk 4521", "adds 4521"),
    ("contact Montreal Departure 124.65", "contact departure on 124.65, good day", None),
    ("contact Montreal Departure 124.65", "departure 124.65", "leaves out contact"),
    ("hold short of runway 24R", "hold short runway 24 right", None),
    ("hold short of runway 24R", "hold short runway 24L", "leaves out runway 24R"),
    ("expect FL340 10 minutes after departure", "climb FL340 10 minutes after departure", "leaves out expect"),
    ("taxi to runway 06L via G, C", "taxi to runway 06L via G, C, cross runway 06R", "adds cross"),
    ("taxi to runway 06L via G, C", "taxi to runway 06L via G, B", "leaves out C"),
    ("taxi to runway 06L via G, C", "taxi via G and C to runway 06L", None),
    ("taxi to runway 06L at C via G, C", "taxi to runway 06L via C and G", "changes the order"),  # what llama3.2:3b did
    ("taxi to runway 06L via G, C", "taxi to runway 06L via C, G", "changes the order"),
    ("unable higher at this time", "unable, higher approved", "adds approved"),
    ("unable higher at this time", "higher at this time", "leaves out unable"),
    ("cleared to land runway 24R", "not cleared to land runway 24R", "adds not"),
])
def test_rewording_keeps_every_number_name_and_instruction(scripted, reply, problem):
    raw = msgspec.json.encode({"reply": reply}).decode()
    if problem is None:
        assert check_reworded(raw, scripted, ("DP69",)) == reply
    else:
        with pytest.raises(PhraseError, match=problem):
            check_reworded(raw, scripted, ("DP69",))


def test_the_voice_says_the_models_words_the_way_the_script_says_its_values():
    forms = [("06L", "zero six left"), ("G, C", "Golf, Charlie"), ("G", "Golf"), ("C", "Charlie"), ("5,000", "five thousand")]
    assert spoken_as("runway 06L, taxi via G and C, climb 5000", forms) == \
        "runway zero six left, taxi via Golf and Charlie, climb five thousand"


# --- off, and the record ------------------------------------------------------------------------------------------------


def test_off_is_the_grammar_and_the_templates():
    engine, own, backend = taxi_engine("off")
    engine.interpreter.mode = "off"
    out = say(engine, own, "Montreal Ground, DP69, how long is the taxi?", mhz=121.0)
    assert not [r for r in backend.requests if r.purpose == "understand"]
    [decision] = [o for o in out if isinstance(o, AtcDecision)]
    assert decision.mode == "off" and decision.model == ""


def test_every_pilot_call_gets_a_decision_record_in_the_recording():
    engine, own, _ = taxi_engine("llm")
    out = say(engine, own, ROUTINE, mhz=121.0)
    [decision] = [o for o in out if isinstance(o, AtcDecision)]
    again = msgspec.json.decode(msgspec.json.encode(decision), type=AtcDecision)  # a recording's file format
    assert again == decision and msgspec.json.encode(decision).startswith(b'{"type":"atc_decision"')
    assert (decision.mode, decision.grammar, decision.model, decision.used, decision.wording) == \
        ("llm", "request ready_to_taxi atis=X", "request ready_to_taxi atis=X", "model", "model")


# --- the setting --------------------------------------------------------------------------------------------------------


def test_mostly_llm_is_the_default_and_old_settings_carry_over(tmp_path):
    empty = tmp_path / "empty.toml"
    empty.write_text("", encoding="utf-8")
    assert load_config(empty, env={}, settings=None).llm.mode == "mostly_llm"
    assert load_config(env={}, settings=None).llm.mode == "mostly_llm"  # config/localtc.toml says so too
    config = tmp_path / "localtc.toml"
    config.write_text('[llm]\nmode = "semi"\n', encoding="utf-8")
    saved = tmp_path / "settings.toml"
    saved.write_text('[llm]\nunderstanding = "primary"\n', encoding="utf-8")  # "the model reads every call"
    assert load_config(config, env={}, settings=saved).llm.mode == "llm"  # the pilot's saved choice wins
    saved.write_text('[llm]\nunderstanding = "fallback"\n', encoding="utf-8")
    assert load_config(config, env={}, settings=saved).llm.mode == "semi"
    config.write_text('[llm]\nunderstanding = "off"\n', encoding="utf-8")
    assert load_config(config, env={}, settings=None).llm.mode == "off"


def test_the_grammar_reads_a_clearance_question_as_a_question():
    heard = GrammarInterpreter().interpret(RUNWAY_LENGTH, None, GROUND)
    assert heard.intent == "question"


# --- the second report: Fully LLM, and the script answered anyway ----------------------------------------------------


def test_a_junk_value_in_a_request_is_left_out_not_the_whole_reading():
    """llama3.2:3b filled "atis" with "doing well", "none" and the pilot's whole sentence: every one of those readings
    was thrown away for it, and the script answered."""
    said = normalize("San Francisco Clearance, United 1596, we'd like to get our IFR to Denver, please")
    answer = parse_answer('{"kind":"request","intent":"request_ifr_clearance","atis":"good evening, united 1596"}',
                          None, said)
    assert (answer.kind, answer.intent, answer.values) == ("request", "request_ifr_clearance", {})
    assert "left out: atis" in answer.note
    assert parse_answer('{"kind":"request","intent":"ready_to_taxi","atis":"none"}', None,
                        normalize("ground, ready to taxi")).values == {}
    # A readback's: the same ("expect 390, 25 minutes of departure" for the cruise lost the altitude, frequency and
    # squawk the model read right). What the grammar heard of it stands.
    answer = parse_answer('{"kind":"readback","runway":"the long one"}', _takeoff_pending(),
                          normalize("runway 06L cleared for takeoff"))
    assert answer.values == {} and "is not a runway" in answer.note


def test_a_question_with_can_in_it_and_a_topic_is_that_question():
    text = "can we get a wind report? How the winds gonna be like when we depart?"
    answer = parse_answer('{"kind":"request","intent":"request_ifr_clearance","topic":"wind"}', None, normalize(text),
                          asked=False, question=True)
    assert (answer.kind, answer.topic) == ("question", "wind")


@pytest.mark.parametrize(("mode", "model_replies"), [("scripted", False), ("semi", False), ("mostly_llm", True), ("llm", True)])
def test_small_talk_and_remarks_get_the_models_reply_where_it_words_atcs(mode, model_replies):
    coffee = "Montreal Ground, DP69, would you like to grab a coffee after your shift today?"
    doing = "Montreal Ground DP69, how are you doing today?"
    backend = ScriptedBackend({coffee: {"kind": "unintelligible"}, doing: {"kind": "request", "intent": "pleasantry"}},
                              phrase={coffee: '{"reply":"appreciate it, maybe another time"}',
                                      doing: '{"reply":"doing well, thanks for asking"}'})
    engine, own = cyul_engine(backend)
    engine.interpreter.mode = mode
    first = atc(say(engine, own, coffee, mhz=121.0))
    second = atc(say(engine, msgspec.structs.replace(own, t=own.t + 30), doing, mhz=121.0))
    if model_replies:
        assert first == ["DP69, appreciate it, maybe another time."]
        assert second == ["DP69, doing well, thanks for asking."]
    else:
        assert first == ["DP69, say again."] and second[0].startswith("DP69, ") and "thanks" in second[0]


@pytest.mark.parametrize(("said", "instead"), [
    # Never something unrelated, and never silently: what fits the call, with the app saying the model's was turned away.
    ("Montreal Ground, DP69, that taxiway sign back there was pretty faded", "DP69, roger."),  # a remark
    ("Montreal Ground, DP69, would you like to grab a coffee after your shift today?",
     "DP69, unable, that information is not available."),  # a question
    ("DP69 the uh thing", "DP69, say again."),  # nothing to it but noise
])
def test_when_the_models_reply_to_an_unclassified_call_is_turned_away(said, instead):
    backend = ScriptedBackend({said: {"kind": "unintelligible"}}, phrase={said: '{"reply":"cleared for coffee"}'})
    engine, own = cyul_engine(backend)
    engine.interpreter.mode = "llm"
    out = say(engine, own, said, mhz=121.0)
    assert atc(out) == [instead]
    assert [e.purpose for e in out if isinstance(e, LlmExchange)] == ["understand", "phrase", "phrase"]  # it was asked
    [alert] = [o for o in out if isinstance(o, AtcAlert) and o.kind == "llm_rejected"]
    assert "the model's reply was turned away (invalid: reply gives an instruction (cleared)" in alert.detail


@pytest.mark.parametrize("mode", ["mostly_llm", "llm"])
def test_a_call_nothing_classifies_still_gets_the_models_reply(mode):
    """However short or unclear, and with a readback still owed: in the model's modes, its reply goes out."""
    engine, own, _ = taxi_engine(mode)
    say(engine, own, ROUTINE, mhz=121.0)  # the taxi clearance: a readback is owed now
    garbled = "DP69 uh the thing"
    backend = ScriptedBackend({garbled: {"kind": "unintelligible"}},
                              phrase={garbled: '{"reply":"say again, I didn\'t get your readback"}'})
    engine.interpreter.backend = engine.phraser.backend = backend
    out = say(engine, msgspec.structs.replace(own, t=own.t + 20), garbled, mhz=121.0)
    assert atc(out) == ["DP69, say again, I didn't get your readback."]
    assert "waiting for the pilot to read back" in [r for r in backend.requests if r.purpose == "phrase"][-1].prompt
    assert engine.state.pending is not None  # still owed


FACTS = {"controller": "San Francisco Ground", "runway in use": "28R", "landing runways": "28R", "departing runways": "28R",
         "parallel landings": "no", "notices": "none",
         "San Francisco International runways": "19L/01R 8700 ft, 19R/01L 7700 ft, 28L/10R 11400 ft, 28R/10L 11900 ft"}


@pytest.mark.parametrize(("reply", "problem"), [
    ("no parallel landings in use today", None),
    ("you're correct, only 28R for landing, United 1596", None),  # the callsign at the end is the template's job
    ("runway 19L/01R closed due to maintenance", "says closed, maintenance"),  # what llama3.2:3b made up
    ("restricted airspace is active", "says active, restricted"),
    ("runway 19L/01L in use", "names runway 19L/01L"),
    ("runway 28L in use", "says runway 28L is in use"),
    ("runway 28R in use, 19R expected shortly", "says expected, shortly"),
])
def test_a_reply_says_only_what_the_facts_say(reply, problem):
    raw = msgspec.json.encode({"reply": reply}).decode()
    if problem is None:
        check_reply(raw, FACTS, ("United 1596",))
    else:
        with pytest.raises(PhraseError, match=problem):
            check_reply(raw, FACTS, ("United 1596",))


# --- [llm] beyond_facts --------------------------------------------------------------------------------------------


def test_beyond_facts_lets_the_models_own_knowledge_through_but_never_an_instruction():
    """Off (the default), "runway 28L is 200 feet wide" is turned away: the sim never said so. On, it goes out."""
    raw = '{"reply":"runway 24R is 200 feet wide"}'
    with pytest.raises(PhraseError, match="not in the facts"):
        check_reply(raw, FACTS, ("DP69",))
    assert check_reply(raw, FACTS, ("DP69",), beyond_facts=True) == "runway 24R is 200 feet wide"
    assert check_reply('{"reply":"runway 19R closed for maintenance"}', FACTS, (), beyond_facts=True)
    with pytest.raises(PhraseError, match="instruction"):  # still never an instruction
        check_reply('{"reply":"cleared to land runway 28R"}', FACTS, (), beyond_facts=True)
    with pytest.raises(PhraseError, match="leaves out 11400"):  # nor another figure than the data's own answer
        check_reply('{"reply":"runway 28L is 9000 feet"}', FACTS, (), required="runway 28L is 11,400 feet",
                    beyond_facts=True)


def test_beyond_facts_end_to_end_and_the_notice_points_to_it():
    wide = "Montreal Ground, DP69. How wide is the 24 right runway?"
    backend = ScriptedBackend(phrase={wide: '{"reply":"runway 24R is 200 feet wide"}'})
    engine, own = cyul_engine(backend)
    out = say(engine, own, wide, mhz=121.0)
    assert atc(out) == ["DP69, unable, that information is not available."]
    [alert] = [o for o in out if isinstance(o, AtcAlert) and o.kind == "llm_rejected"]
    assert "Let the model answer beyond the sim's data" in alert.detail
    assert all("Quick Settings" not in r.messages[-1][1] for r in backend.requests)  # the model isn't told about the app
    engine, own = cyul_engine(backend)
    engine.phraser.beyond_facts = True
    out = say(engine, own, wide, mhz=121.0)
    assert atc(out) == ["DP69, runway 24R is 200 feet wide."]
    assert "you may answer from what a controller would know" in backend.requests[-1].system
    assert load_config(env={}, settings=None).llm.beyond_facts is False  # off unless the pilot turns it on


# --- the third report: the script came back mid-conversation --------------------------------------------------------

SFO_GROUND = InterpretContext(callsign=Callsign("UAL1596"), phase="PARKED", station="San Francisco Clearance",
                              station_role="clearance")


def test_a_question_to_clearance_is_not_the_clearance_by_the_stations_name():
    text = "And San Francisco Clearance United 1596. Is there any restricted airspace around San Francisco?"
    assert GrammarInterpreter().interpret(text, None, SFO_GROUND).intent != "request_ifr_clearance"
    answer = parse_answer('{"kind":"request","intent":"request_ifr_clearance","topic":"weather"}', None,
                          normalize(text), asked=True, question=True, station="San Francisco Clearance")
    assert answer.kind == "question"


def test_a_question_about_another_flight_mid_conversation_is_still_this_flights():
    """"What aircraft is United 2117 in?" got "station calling, say again your callsign": the script, and never the
    model, until the end of the flight."""
    asked = "Montreal Ground, what aircraft is United 2117 in?"
    backend = ScriptedBackend({asked: {"kind": "question", "topic": "other"}},
                              phrase={asked: '{"reply":"unable, information not available"}'})
    engine, own = cyul_engine(backend)
    say(engine, own, "Montreal Ground, DP69, what's the altimeter?", mhz=121.0)  # talking already
    out = say(engine, msgspec.structs.replace(own, t=own.t + 30), asked, mhz=121.0)
    assert atc(out) == ["DP69, unable, information not available."]


def test_after_say_again_your_callsign_the_call_is_answered():
    cold = "Montreal Ground, what aircraft is United 2117 in?"  # nobody talked to ground yet: who is it?
    both = f"DP69, {cold}"
    backend = ScriptedBackend({both: {"kind": "question", "topic": "other"}},
                              phrase={both: '{"reply":"unable, information not available"}'})
    engine, own = cyul_engine(backend)
    assert atc(say(engine, own, cold, mhz=121.0)) == ["Station calling Montreal Ground, say again your callsign."]
    out = say(engine, msgspec.structs.replace(own, t=own.t + 10), "DP69.", mhz=121.0)
    assert atc(out) == ["DP69, unable, information not available."]  # the question, answered: not a repeat


def test_one_last_thing_is_not_runway_one():
    engine, own = cyul_engine(ScriptedBackend())
    assert engine._runways_said("And one last thing. How long is the 24 right runway?") == {(24, "R")}
    assert engine._runways_said("how long is runway 6") == {(6, "")}
    assert engine._runways_said("one last thing, how long is the runway") == set()


def test_say_again_from_the_model_needs_words_that_ask_for_a_repeat():
    """The model read "what aircraft is United 2117 in?" as say_again, and ATC repeated its last answer."""
    with pytest.raises(Exception, match="say_again needs"):
        parse_answer('{"kind":"request","intent":"say_again"}', None, normalize("United 1596"))
    asked = parse_answer('{"kind":"request","intent":"say_again"}', None, normalize("what aircraft is United 2117 in"),
                         asked=True, question=True)
    assert asked.kind == "question"
    assert parse_answer('{"kind":"request","intent":"say_again"}', None, normalize("say again the squawk")).intent == "say_again"


# --- three long flights in Fully LLM: what the model was shown, and what was kept of what it said -----------------


def _reworded(scripted: str, reply: str) -> str:
    return check_reworded(msgspec.json.encode({"reply": reply}).decode(), scripted, ("Delta 2672", "DAL2672"))


def test_radar_contact_is_no_instruction_to_contact_anybody():
    """ "Radar contact, maintain FL390" came out as "maintain FL390, contact approach": the "contact" of "radar contact"
    let an invented handoff through, and dropping it was turned away."""
    assert _reworded("Oakland Center, radar contact, maintain FL390", "maintain FL390") == "maintain FL390"
    with pytest.raises(PhraseError, match="adds contact"):
        _reworded("Oakland Center, radar contact, maintain FL390", "maintain FL390, contact approach")


def test_the_model_is_shown_what_to_keep_and_not_handed_a_level_to_copy():
    """It wrote "maintain FL240" for FL360, four times: FL240 was the instructions' own example."""
    from localtc.atc_core.llm.phrase import REWORD_SYSTEM, keep_line, reword_request

    assert "FL240" not in REWORD_SYSTEM
    assert keep_line("Chicago Center, radar contact, maintain FL360") == "FL360"
    assert reword_request("level 35,900", "Chicago Center, radar contact, maintain FL360").prompt.endswith("Keep: FL360")
    assert keep_line("readback correct, contact Seattle Ground 126.875 when ready") == "readback correct; 126.875"


def test_what_the_reworded_reply_may_not_lose_or_say_twice():
    with pytest.raises(PhraseError, match="readback correct"):
        _reworded("readback correct, contact Seattle Ground 126.875 when ready", "contact Seattle Ground 126.875 when ready")
    with pytest.raises(PhraseError, match="twice"):
        _reworded("expect runway 33R for departure", "expect runway 33R, expect runway 33R for departure")
    assert _reworded("push and start approved, tail left", "tail left, pushback and start approved")  # push is push


def test_the_facts_are_what_the_call_is_about():
    """ "Not sure." got "runway 33R is in use, and parallel landings are not available"; a give-way remark got "I did
    say parallel landings are not available"; a garbled hold-short call got the runway lengths and a closure."""
    backend = ScriptedBackend(phrase={"DP69 uh the thing": '{"reply":"roger"}'})
    engine, own = cyul_engine(backend)
    engine.interpreter.mode = "llm"
    say(engine, msgspec.structs.replace(own, zulu_s=50000.0), "DP69 uh the thing", mhz=121.0)
    facts = [r for r in backend.requests if r.purpose == "phrase"][-1].prompt
    assert "parallel" not in facts and "notices" not in facts and "runways" not in facts and " ft" not in facts
    assert "time 1353Z" in facts and "departure runway" in facts  # "what time is it?" had nothing to go on
    assert "notices" in engine._facts(engine.facility("ground"), "is anything closed today?")
    assert " ft" in engine._facts(engine.facility("ground"), "how long is the runway?").get("Montreal-Trudeau International runways", " ft")


def test_a_readback_of_a_taxi_to_the_gate_is_no_request_for_one():
    """LAX and San Diego: the readback, right, taken for a request to taxi to the gate; ATC said the whole taxi again,
    the readback came again, and again."""
    from localtc.atc_core.llm.understand import Answer, LlmInterpreter as Interpreter
    from localtc.atc_core.readback import PendingReadback

    pending = PendingReadback("ground.taxi_to_gate", "ground", {"taxi_route": ("B7", "B")}, ("taxi_route",))
    asked = Answer(kind="request", intent="request_taxi_parking")
    assert Interpreter._follow_up(asked, "Gate 49 via Bravo 7, Bravo, Air Canada 795", pending) is None
    landing = PendingReadback("tower.land", "tower", {"runway": "14R"}, ("runway",))
    option = Answer(kind="request", intent="request_option")
    assert Interpreter._follow_up(option, "cleared to land 14R, can we make it a low approach?", landing) is not None


def test_nothing_asked_is_no_question():
    with pytest.raises(Exception, match="asked nothing"):
        parse_answer('{"kind":"question","topic":"wind"}', None, normalize("Not sure."))
    assert parse_answer('{"kind":"question","topic":"runway"}', None, normalize("what runway can we expect"),
                        question=True).kind == "question"


def test_a_flight_level_said_as_three_digits_is_the_level():
    """ "Clementine 390" (climb and maintain three nine zero): the model's 39000 turned away, "the pilot did not say it"."""
    from localtc.atc_core.llm.grounding import grounded

    assert grounded("altitude", 39000, normalize("Clementine 390, 2672"))
    assert not grounded("altitude", 3900, normalize("Clementine 39, 2672"))


def test_a_reading_the_grammar_took_over_is_kept_not_alerted():
    """ "Roger that" read as ready_to_taxi, turned away, the grammar's acknowledge used: the record keeps why, and the
    pilot isn't shown "the model's answer was turned away" for a reply that was never the model's to lose."""
    roger = "Roger that, DP69"
    backend = ScriptedBackend({roger: {"kind": "request", "intent": "ready_to_taxi"}})
    engine, own = cyul_engine(backend)
    engine.interpreter.mode = "llm"
    out = say(engine, own, roger, mhz=121.0)
    [decision] = [o for o in out if isinstance(o, AtcDecision)]
    assert "model gave no usable reading" in decision.fallback
    assert not [o for o in out if isinstance(o, AtcAlert) and o.kind == "llm_rejected"]


def test_asking_what_to_expect_is_a_question_not_a_request():
    """ "We'd like to know what runway to expect for departure, and which way we'll tail": read as a runway request with
    the runway in use filled in, and answered "unable"."""
    said = normalize("we're not requesting to push back, but we'd like to know what is our expected runway for departure")
    answer = parse_answer('{"kind":"request","intent":"request_runway","runway":"33R"}', None, said, asked=True, question=True)
    assert (answer.kind, answer.topic) == ("question", "runway")
    asking_for_one = parse_answer('{"kind":"request","intent":"request_runway","runway":"33R"}', None,
                                  normalize("can we get runway 33R instead"), question=True)
    assert (asking_for_one.kind, asking_for_one.values) == ("request", {"runway": "33R"})


def test_the_grammars_unsure_reading_beats_unable():
    """ "Tail left. Actually, sorry. Can we get a tail right?": the model called it an IFR clearance request twice,
    the grammar heard a pushback (unsure: a turn in it too), and the guess "a request ATC can't do" got "unable"."""
    tail = "Tail left. Actually, sorry. Can we get a tail right? We'd like a direct turn to Bravo after, DP69"
    wrong = {"kind": "request", "intent": "request_ifr_clearance"}
    backend = ScriptedBackend({tail: [wrong, wrong]}, reword={tail: '{"reply":"tail right, push and start approved"}'})
    engine, own = cyul_engine(backend)
    engine.interpreter.mode = "llm"
    engine.state.phase = "PARKED"
    out = say(engine, own, tail, mhz=121.0)
    texts = atc(out)
    assert texts and "unable" not in texts[0] and "tail right" in texts[0], texts


def test_a_routine_call_the_grammar_is_sure_of_isnt_made_a_question():
    """ "Are we clear to land?": the model said a class B request, the words don't fit, and it became a question that
    got "unable". The grammar heard the call for the landing clearance."""
    clear = "Are we clear to land, DP69?"
    backend = ScriptedBackend({clear: {"kind": "request", "intent": "request_class_b"}})
    tower = InterpretContext(callsign=Callsign("DP69"), phase="APPROACH", station="Montreal Tower", station_role="tower")
    result = LlmInterpreter(backend, mode="llm").interpret(clear, None, tower)
    assert result.intent == "report_final" and "gave way to the grammar" in result.fallback
