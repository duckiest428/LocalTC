"""Calgary to Toronto: no SID when none is filed, a rejected takeoff answered, the copilot and the APU, one meal, and
GSX's invoice and de-icing questions."""

from types import SimpleNamespace

from localtc.atc_core.engine import REJECT_ONTO, REJECTED_TAKEOFF, AtcEngine, EngineConfig
from localtc.atc_core.readback.written import with_expected
from localtc.crew import commands


def test_no_sid_filed_is_no_sid_given():
    engine = AtcEngine(EngineConfig(callsign="ACA148", destination="CYYZ", origin="CYYC"))
    engine.state.flight.origin = "CYYC"
    engine.route.fixes = [SimpleNamespace(ident="LOMLO")]
    cwboy = SimpleNamespace(name="CWBOY2", runways=("17R",), fixes=("LOMLO",), enroute=())
    engine._departures["CYYC"] = [cwboy]
    engine.cfg.sid = ""
    assert engine._sid_from("17R") is None  # (it was CWBOY2: the one from 17R that reaches LOMLO)


def test_a_rejected_takeoff_is_heard_and_answered():
    said = "Air Canada 148. We have a rejected takeoff. We're going to vacate left onto Juliet and we'd like to try again"
    assert REJECTED_TAKEOFF.search(said) and REJECT_ONTO.search(said).group(1) == "Juliet"
    assert REJECTED_TAKEOFF.search("Rejecting takeoff, Air Canada 148") and REJECTED_TAKEOFF.search("aborting the takeoff")
    assert not REJECTED_TAKEOFF.search("Cleared for takeoff runway 17R, Air Canada 148")
    from localtc.atc_core.facilities import Facility

    engine = AtcEngine(EngineConfig(callsign="ACA148", destination="CYYZ", origin="CYYC"))
    tower = Facility("tower", "Calgary Tower", 118.4, "CYYC")
    engine.state.clearances["takeoff"] = object()
    engine._rejected_takeoff(said, tower, 10.0)
    assert "takeoff" not in engine.state.clearances
    item = engine._scheduled[-1]
    assert item.instruction_id == "common.info" and "vacate onto J when able" in item.slots["message"].display
    assert "advise when ready for departure" in item.slots["message"].display


def test_talk_about_the_apu_doesnt_start_it():
    talk = "It doesn't seem it works without the APU. So I'll wait for the startup for the winning and the engine anti-ice."
    assert commands.parse(talk) == []
    assert commands.parse("We'll start the APU after the push") == []
    assert [c.value for c in commands.parse("Start the APU")] == ["start"]
    assert [c.value for c in commands.parse("APU please")] == ["start"]
    assert [c.value for c in commands.parse("APU off")] == ["stop"]


def test_the_log_writes_the_level_to_expect():
    heard = "Runways 17R, come maintain 8,000. Expect 5L, 3300, 23M, departure. Departure frequency is on 119.8."
    assert with_expected(heard, "FL330", 23) == \
        "Runways 17R, come maintain 8,000. Expect FL330 23 minutes after departure. Departure frequency is on 119.8."
    assert with_expected("Climb 8,000, squawk 6674.", "FL330", 23) == "Climb 8,000, squawk 6674."


def test_gsx_invoice_isnt_read_out_and_its_deicing_question_is_asked():
    from test_ground import crew, said, state_with

    from localtc.ground.api import Menu

    c = crew()
    c.heard("pushback", 0.0)
    c.tick(0.0)
    out = c.update(state_with(c, services=c.state.services, menu=Menu("Latest Invoice?", ("Open in browser", "Close"), (False, False))), 1.0)
    assert not said(out)  # ("Latest Invoice? 1: Open in browser, 2: Close" was read over the radio)
    c = crew()
    c.heard("pushback", 0.0)  # (the push called: GSX's pages are the crew's to see to)
    c.tick(0.0)
    ice = Menu("Ice warning: do you request the de-icing treatment?", ("Yes", "No [GSX choice]"), (False, False))
    out = c.update(state_with(c, services=c.state.services, menu=ice), 1.0)
    assert said(out) and "de-icing" in said(out)[0].lower() and not [o for o in out if type(o).__name__ == "Pick"]
