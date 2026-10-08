"""Controllers as people (atc_core.personality): the same one at a station every time, different from the next,
their manner in the words around an instruction and in the model's prompt, never in the instruction itself."""

import random

import pytest

from localtc.atc_core import personality as pers
from localtc.atc_core.llm.backend import LlmRequest
from localtc.tts.voices import delivery_for

STATIONS = [("Las Vegas Ground", "ground"), ("Las Vegas Tower", "tower"), ("Socal Approach", "approach"),
            ("Los Angeles Center", "center"), ("Los Angeles Tower", "tower"), ("Denver Center", "center"),
            ("Salt Lake Center", "center"), ("Seattle Departure", "departure"), ("Paine Clearance", "clearance"),
            ("Montreal Ground", "ground"), ("Toronto Tower", "tower"), ("Boeing Tower", "tower")]


def test_the_same_controller_every_time_and_not_the_same_everywhere():
    first = [pers.profile(s, r) for s, r in STATIONS]
    again = [pers.profile(s, r) for s, r in STATIONS]
    assert first == again  # a station's controller, the same every flight and in every replay
    assert len({p.kind for p in first}) >= 4  # the stations aren't all alike
    for p in first:
        assert p.kind in pers.ROLE_KINDS[p.role]  # the kind fits the job
        assert p.ack in p.traits.acks and 0.05 <= p.greets <= 0.95


def test_icao_stations_sign_off_the_icao_way():
    p = pers.profile("Zurich Tower", "tower", icao=True)
    assert p.sign_off in p.traits.icao_sign_offs


@pytest.mark.parametrize("kind", pers.KINDS)
def test_what_a_controller_says_around_an_instruction_never_changes_it(kind):
    t = pers.TRAITS[kind]
    for ack in t.acks:
        assert pers.acknowledge("DP69, roger.", "DP69", ack) in (f"DP69, {ack}.",)
    for words in t.say_again:
        assert "say again" in pers.say_again("DP69, say again.", "DP69", words)
    assert pers.acknowledge("DP69, roger, taxi via A.", "DP69", t.acks[0]) == "DP69, roger, taxi via A."  # not an ack alone
    for sign in (*t.sign_offs, *t.icao_sign_offs):
        out = pers.sign_off("DP69, contact Montreal Tower 119.3.", sign)
        assert out.startswith("DP69, contact Montreal Tower 119.3") and "119.3" in out


def test_a_correction_gets_firmer_never_different():
    assert pers.firmer("DP69, negative, taxi via Q1, Q, C1.", "DP69") == "DP69, negative, I say again, taxi via Q1, Q, C1."
    assert pers.firmer("DP69, read back cleared for takeoff.", "DP69") == \
        "DP69, I still need the readback of cleared for takeoff."
    assert pers.gentler("DP69, negative, taxi via Q1.", "DP69") == "DP69, not quite, negative, taxi via Q1."


def test_workload_from_the_frequency_and_the_traffic():
    assert pers.workload(0, 0) == "quiet"
    assert pers.workload(4, 6) == "normal"
    assert pers.workload(7, 9) == "busy"


def test_the_model_is_told_who_is_talking_but_never_what_to_say():
    p = pers.profile("Las Vegas Ground", "ground")
    text = p.describe("busy")
    assert p.station in text and p.kind in text and "busy" in text
    assert "never the instruction" in text
    plain = LlmRequest("phrase", "s", (("user", "u"),), {})
    assert plain.key("m") == LlmRequest("phrase", "s", (("user", "u"),), {}, persona=text).key("m")  # replays still match


def test_the_voice_follows_the_controller():
    tower = delivery_for("Las Vegas Tower", "tower")
    hurried = delivery_for("Las Vegas Tower", "tower:hurried")
    busy = delivery_for("Las Vegas Tower", "tower:hurried:busy")
    calm = delivery_for("Las Vegas Tower", "tower:calm")
    assert busy.pace > hurried.pace > tower.pace > calm.pace
    assert delivery_for("X", "tower:unknown") == delivery_for("X", "tower")


def test_wording_habit_is_stable_with_a_little_drift():
    rng = random.Random(1)
    picks = [pers.wording("Denver Center", "center.radar_contact", 3, rng) for _ in range(200)]
    usual = max(set(picks), key=picks.count)
    assert picks.count(usual) > 150  # the habit, nearly every time


def test_in_a_flight_the_controller_shapes_the_words_and_the_prompt():
    from helpers.llm import ScriptedBackend
    from test_llm import atc, cyul_engine, say

    from localtc.sim_api import AtcTransmission

    backend = ScriptedBackend(phrase={"Montreal Ground, DP69, nice evening for it": '{"reply":"it is, enjoy the flight"}'})
    engine, own = cyul_engine(backend)
    engine.interpreter.mode = "llm"
    out = say(engine, own, "Montreal Ground, DP69, nice evening for it", mhz=121.0)
    [tx] = [o for o in out if isinstance(o, AtcTransmission)]
    who = pers.profile("Montreal Ground", "ground")
    assert tx.manner.startswith(f"ground:{who.kind}")
    asked = [r for r in backend.requests if r.purpose == "phrase"]
    assert asked and "Montreal Ground" in asked[-1].persona and who.kind in asked[-1].persona
    engine.cfg.personalities = False  # off: no manner for the model, the role's voice
    assert engine._persona(engine.facility("ground")) == "" and engine._manner(engine.facility("ground")) == "ground"
    assert atc(out)


def test_a_new_shift_after_a_long_break_brings_other_controllers_and_voices():
    from localtc.tts.voices import shift_key, speaker_for
    from localtc.ui.controller import SHIFT_BREAK_S, next_shift

    assert next_shift(0.0, 0, 1e9) == 0  # the first flight ever
    assert next_shift(1000.0, 2, 1000.0 + SHIFT_BREAK_S - 60) == 2  # a short break: the same people
    assert next_shift(1000.0, 2, 1000.0 + SHIFT_BREAK_S) == 3  # five hours: the next shift
    assert pers.profile("Denver Center", "center", shift=4) == pers.profile("Denver Center", "center", shift=4)
    assert pers.profile("Denver Center", "center") == pers.profile("Denver Center", "center", shift=0)  # as before
    shifts = [pers.profile(s, r, shift=n) for s, r in STATIONS for n in range(6)]
    by_station = {}
    for p in shifts:
        by_station.setdefault(p.station, set()).add((p.kind, p.ack, p.sign_off, p.pace))
    assert all(len(v) > 1 for v in by_station.values())  # each station has other people on other shifts
    assert pers.shift_key("Denver Center", 3) == shift_key("Denver Center", 3)  # the voice follows the same person
    voices = {speaker_for(shift_key("Denver Center", n), 904) for n in range(8)}
    assert len(voices) > 1


def test_the_engine_uses_the_shift():
    from helpers.llm import ScriptedBackend
    from test_llm import cyul_engine

    engine, _ = cyul_engine(ScriptedBackend())
    engine.cfg.shift = 5
    ground = engine.facility("ground")
    who = engine._controller(ground)
    assert who == pers.profile(ground.station, "ground", icao=engine.region.icao, shift=5)
