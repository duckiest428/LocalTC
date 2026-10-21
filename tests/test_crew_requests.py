"""The copilot asked for things in its own words on the intercom: a radio call with the radio the captain's, "don't
forget" and "change it", a command the model reads that the captain didn't name, no "done" for what it didn't do."""

import json

from msgspec.structs import replace
from test_crew_monitor import crew, said
from test_engine import parked_engine

from localtc.crew import speech_acts
from localtc.crew.commands import Command, parse
from localtc.crew.model import CrewModel
from localtc.sim_api import IntercomHeard, SetComFrequency, Transcript


def test_dont_forget_asks_for_it():
    reading = speech_acts.read("Don't forget to set your altimeter to 30.39.", 0.94)
    assert reading.act == "command" and reading.commands == (Command("altimeter", "30.39"),)
    assert speech_acts.read("Don't touch the flaps.", 0.94).act == "negation"


def test_seapelt_is_the_seatbelt_sign():
    assert parse("Seapelt signs please.") == [Command("seatbelt", "on")]


def test_do_the_call_is_the_call_the_words_name_or_the_next_one():
    assert parse("Alright, getting ready for pushback. Can you do the call? On ground.") == [Command("radio", "pushback")]
    assert parse("Can you make the call for us?") == [Command("radio", "next")]
    assert parse("Give tower a call, we're ready.") == [Command("radio", "departure")]
    assert speech_acts.read("Can you do the call? On ground.", 0.9).commands == (Command("radio", "next"),)


def test_with_the_radio_the_captains_an_asked_for_call_is_made_once():
    """"Can you do the call? On ground": the radio copilot was off, so it was "your radios" (and the model made up a
    radio call on the intercom). Asked, it makes this one call, tuned to ground first."""
    engine, own = parked_engine()
    pm = crew(engine, radio_mode=lambda: "off")
    out = pm.observe(IntercomHeard(t=2.0, text="Getting ready for pushback. Can you do the call? On ground.",
                                   confidence=0.9))
    assert "Asking." in said(out)
    assert any(isinstance(o, SetComFrequency) for o in out) or engine.state.comms.tuned.controller == "ground"
    later = []
    for t in (3.0, 5.0, 7.0):
        later += pm.observe(replace(own, t=t))
    calls = [o for o in out + later if isinstance(o, Transcript)]
    assert len(calls) == 1 and calls[0].source == "copilot"
    assert "Paine Ground" in calls[0].text and "request push and start" in calls[0].text


class Backend:
    model = "fake"
    rich = False

    def __init__(self, replies):
        self.replies = list(replies)

    def complete(self, request, timeout_s=0.0):
        from types import SimpleNamespace

        return SimpleNamespace(text=self.replies.pop(0), error=None, latency_ms=1.0)


def test_change_it_names_what_the_line_before_did():
    model = CrewModel(Backend([json.dumps({"kind": "command", "action": "altimeter", "value": "30.39", "reply": "Altimeter set."})]),
                      mode="llm")
    model.heard("Captain", "Don't forget to set your altimeter to 30.39.")
    model.heard("Captain", "No, please change it to 30.39.")  # (the copilot keeps the line before asking)
    reading, exchanges = model.ask(10.0, "No, please change it to 30.39.", {})
    assert reading is not None and reading.command == Command("altimeter", "30.39")


def test_a_command_it_didnt_do_is_asked_not_said_done():
    """"Seapelt signs please": the model's "seatbelt on" was turned away (the captain didn't say "seatbelt"), and asked
    again for a reply it said "Seatbelt sign on." with nothing done. Now it asks."""
    engine, own = parked_engine()
    pm = crew(engine)
    pm.model = CrewModel(Backend([
        json.dumps({"kind": "command", "action": "seatbelt", "value": "on", "reply": "Seatbelt sign on."}),
        json.dumps({"kind": "reply", "reply": "Seatbelt sign on."}),
    ]), mode="llm")
    out = pm.observe(IntercomHeard(t=2.0, text="Seaport sines please.", confidence=0.9))
    assert said(out) == ["Seatbelt sign on, confirm?"]
