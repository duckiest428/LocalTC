"""The ground crew: reading the pilot, driving GSX's menu (its real rows, recorded at Zurich), the crew's decisions, the
Remote API over a real WebSocket, and whole turnarounds on the stand-in."""

import asyncio
import base64
import hashlib
import json
import struct
from pathlib import Path

import msgspec
import pytest

from localtc.bus import EventBus
from localtc.ground import grammar as g
from localtc.ground.api import GroundState, Menu, Service
from localtc.ground.crew import Close, Pick, RampCrew, RampLink, Send
from localtc.ground.gsx_remote import GsxRemote, parse_state
from localtc.ground.menu import MenuRun, Open, Want, find, is_question, row_of
from localtc.ground.mock import MockGsx
from localtc.ground.model import RampModel, checked
from localtc.ground.service import GroundService
from localtc.ground.ws import GUID, WebSocket, WebSocketClosed
from localtc.sim_api import CrewSpeech, GroundStatus, OwnshipState, PttPressed, RampSpeech, SendSimEvent, Transcript
from localtc.sim_api.ramp import on_ramp

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "gsx" / "lszh.json").read_text(encoding="utf-8"))
OWN = {f: 0.0 for f in ("t", "lat", "lon", "alt_msl_ft", "alt_indicated_ft", "alt_agl_ft", "altimeter_inhg", "hdg_mag", "hdg_true",
                        "ias_kt", "gs_kt", "vs_fpm", "com2_mhz")}


def page(name: str) -> Menu:
    raw = FIXTURE[name]
    return Menu(raw["title"], tuple(raw["entries"]), tuple(raw.get("disabled", [False] * len(raw["entries"]))))


def own(*, brake=True, engines=False, ground=True, gs=0.0, com1=122.8, fuel=26455.0) -> OwnshipState:
    return OwnshipState(**{**OWN, "gs_kt": gs}, on_ground=ground, parking_brake=brake, engine_running=engines, squawk="1000",
                        xpdr_mode="standby", com1_mhz=com1, fuel_lb=fuel)


def said(outputs) -> list[str]:
    return [o.text for o in outputs if isinstance(o, RampSpeech)]


def asks(text):
    return [(a.action, a.services, a.direction, a.gate, a.option) for a in g.parse(text)]


# --- the grammar -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text,action,services", [
    ("catering, fuel and a GPU please", g.REQUEST, ("Catering", "Refueling", "GPU")),
    ("I'd like the toilets and potable water", g.REQUEST, ("Lavatory", "Water")),
    ("ramp, connect the jetway", g.REQUEST, ("OperateJetways",)),
    ("de-ice please", g.REQUEST, ("DeIce",)),
    ("start boarding", g.REQUEST, ("Boarding",)),
    ("deboard please", g.REQUEST, ("Deboarding",)),
    ("de-boarding", g.REQUEST, ("Deboarding",)),
    ("cancel catering", g.CANCEL, ("Catering",)),
    ("forget the fuel", g.CANCEL, ("Refueling",)),
    ("speed up boarding", g.HURRY, ("Boarding",)),
    ("complete boarding now", g.FINISH, ("Boarding",)),
    ("what's still outstanding?", g.STATUS, ()),
    ("how's the fuel coming along", g.STATUS, ("Refueling",)),
    ("ground crew, is the fuel done?", g.STATUS, ("Refueling",)),
    ("disconnect the jetway please", g.OFF, ("OperateJetways",)),
    ("take the stairs away", g.OFF, ("OperateStairs",)),
    ("ground power off", g.OFF, ("GPU",)),
    ("remove GPU, jetway and stairs", g.OFF, ("GPU", "OperateJetways", "OperateStairs")),
])
def test_what_the_pilot_asks_for(text, action, services):
    assert asks(text) == [(action, services, "", "", 0)]


@pytest.mark.parametrize("text,direction", [
    ("pushback, nose left", g.NOSE_LEFT),
    ("push back tail right", g.NOSE_LEFT),  # GSX's own definition: nose left is tail right
    ("ramp, pushback tail left", g.NOSE_RIGHT),
    ("request pushback nose right", g.NOSE_RIGHT),
    ("straight back", g.STRAIGHT),
    ("left", g.NOSE_RIGHT),  # GSX's left: the tail
    ("request pushback", ""),
])
def test_the_way_to_push(text, direction):
    assert asks(text) == [(g.PUSH, (), direction, "", 0)]


def test_two_calls_in_one_breath_and_the_little_words():
    assert [a.action for a in g.parse("forget the catering but send water")] == [g.CANCEL, g.REQUEST]
    assert asks("change to gate B40")[0][:4] == (g.GATE, (), "", "B40")
    assert asks("we'd like stand 12 please")[0][3] == "12"
    assert asks("full service please")[0][0] == g.FULL
    assert asks("yes")[0][0] == g.YES and asks("negative")[0][0] == g.NO
    assert asks("option two")[0][4] == 2 and asks("the first one")[0][4] == 1
    assert asks("brakes set")[0][0] == g.BRAKES_SET and asks("brakes released")[0][0] == g.BRAKES_RELEASED
    assert asks("good start")[0][0] == g.GOOD_START and asks("stop the push")[0][0] == g.STOP_PUSH
    assert g.parse("hello how are you") == () and g.parse("nice weather") == ()
    assert asks("sixty percent")[0][0] == g.LEVEL and asks("sixty percent")[0][4] == 60
    assert asks("fuel 70 percent please")[0][:2] == (g.REQUEST, ("Refueling",)) and asks("fuel 70 percent please")[0][4] == 70
    assert asks("fill it up")[0][4] == 100
    assert asks("catering and fuel, 50 percent please")[0][1:] == (("Catering", "Refueling"), "", "", 50)


def test_only_the_ramp_is_addressed_not_atc():
    assert g.is_addressed("ramp, fuel please") and g.is_addressed("ground crew, fuel please") and g.is_addressed("Chief, the GPU")
    assert not g.is_addressed("ground, request pushback") and not g.is_addressed("ask ground for pushback")


# --- the frequency ---------------------------------------------------------------------------------------------------


def test_the_ramp_frequency_and_where_a_radio_stops_short_of_it():
    assert on_ramp(119.999) and on_ramp(119.990) and on_ramp(120.000) and on_ramp(119.995)
    assert not on_ramp(119.975) and not on_ramp(121.9) and not on_ramp(None)


# --- the menu --------------------------------------------------------------------------------------------------------


def test_the_real_rows_are_found_in_whatever_state_they_are():
    assert find(page("top"), row_of("Catering").words).index == 1
    assert find(page("top_busy"), row_of("Catering").words).index == 1  # "Catering vehicles are in position"
    assert find(page("top"), row_of("Boarding").words).index == 3  # not Deboarding's
    assert find(page("top"), row_of("Deboarding").words).index == 0
    assert find(page("top"), row_of("Departure").words).index == 4
    assert find(page("additional_busy"), ("dismiss ground power",)).index == 0
    assert find(page("additional"), row_of("GPU").words).index == 0
    assert find(page("additional"), ("cameras",)).why == "disabled"
    assert is_question(page("handler")) and is_question(page("caterer"))
    assert not is_question(page("top")) and not is_question(page("additional")) and not is_question(page("in_progress"))


def test_nothing_is_picked_unless_it_is_the_only_one():
    menu = Menu("x", ("Nose Left", "Nose Left then Straight", "Back"))
    assert find(menu, ("nose left",)).why == "ambiguous"
    assert find(menu, ("catering",)).why == "missing"


def test_an_errand_through_additional_services_reads_the_menu_each_time():
    run = MenuRun((row_of("GPU"), Want(("abort service",))), 0.0)
    assert isinstance(run.step(None, 0.0), Open)
    assert run.step(None, 1.0) is None  # waiting for it to come up
    assert run.step(page("top"), 2.0) == Pick(7, "Additional Services ▶")  # GSX's own page for it
    assert run.step(page("top"), 3.0) is None  # waiting for the pick to show
    assert run.step(page("additional"), 4.0) == Pick(0, "Request Ground Power Unit")
    assert run.step(page("in_progress"), 5.0) == Pick(1, "Abort service")
    assert run.step(None, 6.0) is None and run.done


def test_a_stale_title_isnt_a_menu_and_a_menu_that_never_comes_says_so():
    state = parse_state({"menuShown": False, "menu": {"title": "Old", "entries": ["a"]}}, connected=True, running=True, capabilities=frozenset())
    assert state.menu is None  # (the title stays for a moment after it's gone)
    run = MenuRun((row_of("Catering"),), 0.0)
    assert run.step(None, 0.0) == Open(fallback=False)
    assert run.step(None, 5.0) == Open(fallback=True)  # GSX's documented LVar, when the API says yes and nothing comes
    assert run.step(None, 10.0) is None and run.failed == "never_opened"


def test_a_menu_that_changes_underneath_is_read_again():
    run = MenuRun((row_of("Catering"),), 0.0)
    busy = Menu(page("top").title, ("Catering vehicles are in position", "Request Boarding"))
    run.step(None, 0.0)
    assert run.step(busy, 1.0) == Pick(0, "Catering vehicles are in position")


def test_greyed_ambiguous_or_gone_fails_and_leaves_the_menu():
    run = MenuRun((Want(("cameras",)),), 0.0)
    assert run.step(page("additional"), 0.0) is None and run.failed == "disabled"
    run = MenuRun((Want(("catering",)),), 0.0)
    assert run.step(page("handler"), 0.0) is None and run.failed == "missing"
    run = MenuRun((Want(("request",)),), 0.0)
    assert run.step(page("top"), 0.0) is None and run.failed == "ambiguous" and "Request Boarding" in run.choices


def test_a_pick_that_does_nothing_is_tried_then_given_up_on():
    run = MenuRun((Want(("complete now",)),), 0.0)
    menu = page("in_progress")
    assert run.step(menu, 0.0) is not None
    run._at = 0
    assert run.step(menu, 1.0) is None
    assert run.step(menu, 6.0) is not None  # tried again
    run._at = 0
    assert run.step(menu, 12.0) is not None
    run._at = 0
    assert run.step(menu, 18.0) is None and run.failed == "no_change"


# --- the crew --------------------------------------------------------------------------------------------------------


def services(**states) -> dict[str, Service]:
    names = ("GPU", "OperateJetways", "OperateStairs", "Deboarding", "Cleaning", "Catering", "Refueling", "Water", "Lavatory", "DeIce", "Boarding", "Departure")
    return {n: Service(id=n, name=n, state=states.get(n, "available"), can_trigger=states.get(n, "available") == "available") for n in names}


def crew(link=None, **kw) -> RampCrew:
    c = RampCrew(link or RampLink(), seed=1, **kw)
    c.own = own()
    c.state = GroundState(connected=True, running=True, services=services(), parking="Gate B 37", airport="LSZH")
    return c


def state_with(c, **kw) -> GroundState:
    return GroundState(**{"connected": True, "running": True, "services": c.state.services, "parking": "Gate B 37", "airport": "LSZH", **kw})


def test_several_services_are_asked_for_one_at_a_time_gpu_as_said():
    c = crew()
    out = c.heard("catering, fuel and a GPU please", 0.0)
    assert len(said(out)) == 1 and "catering" in said(out)[0] and "GPU" in said(out)[0]
    assert Send("Catering") in c.tick(1.0)
    assert not [o for o in c.tick(2.0) if isinstance(o, Send)]  # the next waits for GSX to show it took: quick ones get dropped
    c.update(state_with(c, services=services(Catering="requested")), 3.0)
    assert c._queue == ["GPU"] or c._sending == ("Refueling", 3.0)


def test_never_the_same_service_twice():
    c = crew()
    c.state = state_with(c, services=services(Catering="performing"))
    assert any("atering" in s for s in said(c.heard("catering please", 0.0)))
    assert c._queue == []


def test_status_says_whats_active_waiting_and_done():
    c = crew()
    c._finished = ["GPU"]
    svc = services()
    svc["Catering"] = Service(id="Catering", state="requested", status="front on the way, ETA 25 secs", can_trigger=False)
    svc["Refueling"] = Service(id="Refueling", state="requested", waiting=True, status="waiting for Catering\naircraft 26455 lb", can_trigger=False)
    c.state = state_with(c, services=svc)
    report = said(c.heard("what's still outstanding?", 0.0))[0]
    assert "front on the way, ETA 25 secs" in report and "waiting for Catering" in report and "Done: the GPU" in report
    assert "26455" not in report  # (the first line's enough to say)
    assert "Nothing" in said(crew().heard("status", 0.0))[0] or "quiet" in said(crew().heard("status", 0.0))[0]


def test_on_the_stand_only():
    c = crew()
    c.own = own(gs=20.0)
    assert not c._queue and "stand" in said(c.heard("fuel please", 0.0))[0]
    assert not c._queue
    c.own = own(engines=True)
    assert "shut down" in said(c.heard("fuel please", 0.0))[0].lower() and not c._queue


def test_cancel_goes_through_the_services_row_and_aborts():
    c = crew()
    c.state = state_with(c, services=services(Catering="requested"))
    assert any(type(o).__name__ == "Open" for o in c.heard("cancel catering", 0.0))
    c.state = state_with(c, services=c.state.services, menu=page("top_busy"))
    assert c.tick(1.0)[0] == Pick(1, "Catering vehicles are in position")
    c.state = state_with(c, services=c.state.services, menu=page("in_progress"))
    assert c.tick(2.0)[0] == Pick(1, "Abort service")
    c.state = state_with(c, services=services())
    out = c.tick(3.0)
    assert any("atering" in s for s in said(out))
    assert not said(c.update(state_with(c, services=services()), 4.0))  # no "catering's done" after it was called off


def test_cancel_of_nothing_and_of_what_hasnt_gone_yet():
    c = crew()
    assert "isn't" in said(c.heard("cancel catering", 0.0))[0] or "Nothing" in said(c.heard("cancel catering", 0.0))[0]
    c.heard("water and catering please", 1.0)
    out = c.do(g.Ask(g.CANCEL, ("Catering",)), 1.0)
    assert not c._queue.count("Catering") and said(out)


def test_a_failed_errand_says_what_went_wrong():
    c = crew()
    c.state = state_with(c, services=services(Catering="requested"), menu=Menu("Activate Services at LSZH", ("Request Boarding",)))
    out = c.heard("cancel catering", 0.0)
    assert "isn't on its menu" in said(out)[0]
    assert "Catering" not in c._cancelled


def test_speed_up_is_honest_and_offers_to_finish():
    c = crew()
    c.state = state_with(c, services=services(Catering="performing"))
    out = c.heard("speed up catering", 0.0)
    assert "from here" in said(out)[0]
    c.state = state_with(c, services=c.state.services, menu=page("top"))
    assert c.claims("yes", 1.0)
    assert any(isinstance(o, Pick) for o in c.heard("yes", 1.0))


def test_gsx_saying_no_is_passed_on_and_a_call_that_does_nothing_too():
    c = crew()
    out = c.update(state_with(c, message="Cannot operate stairs while water/lavatory servicing"), 0.0)
    assert any("Cannot operate stairs" in s for s in said(out))
    c = crew()
    c.heard("catering please", 0.0)
    c.tick(0.0)
    assert any("Catering" in s or "atering" in s for s in said(c.tick(30.0)))
    assert c._sending is None


def test_the_operator_question_answered_with_gsxs_choice():
    c = crew()
    c.heard("catering please", 0.0)
    c.tick(0.0)
    assert Pick(0, FIXTURE["handler"]["entries"][0]) in c.update(state_with(c, menu=page("handler")), 1.0)
    assert Pick(0, FIXTURE["caterer"]["entries"][0]) in c.update(state_with(c, menu=page("caterer")), 2.0)


def test_questions_are_left_alone_when_the_pilot_opened_the_menu_themselves():
    c = crew()
    assert not c.update(state_with(c, menu=page("handler")), 0.0)


def test_a_question_it_doesnt_know_is_read_out_and_answered_by_voice():
    c = crew()
    c.heard("catering please", 0.0)
    c.tick(0.0)
    menu = Menu("Do you want the tug connected early?", ("Yes please", "No thanks", "Back"))
    out = c.update(state_with(c, menu=menu), 1.0)
    assert any("connected early" in s and "1: Yes please" in s for s in said(out))
    assert c.claims("option two", 2.0)
    assert Pick(1, "No thanks") in c.heard("option two", 2.0)
    c.state = state_with(c, menu=menu)
    c._asking = menu
    assert Pick(1, "No thanks") in c.heard("no thanks", 3.0) or Pick(1, "No thanks") in c.heard("number two", 3.0)


def test_gsx_asks_how_much_fuel_and_wont_wait():
    c = crew()
    c.heard("fuel please", 0.0)
    c.tick(0.0)
    assert Pick(0, "Jetaviation [GSX choice]") in c.update(state_with(c, menu=page("fuel_operator")), 1.0)
    out = c.update(state_with(c, menu=page("fuel_level")), 2.0)
    assert "35 to 100 percent" in said(out)[0] and not [o for o in out if isinstance(o, Pick)]  # said at once, not queued
    assert c.claims("sixty percent", 3.0)
    assert Pick(2, FIXTURE["fuel_level"]["entries"][2]) in c.heard("sixty percent", 3.0)


@pytest.mark.parametrize("text,row", [("fuel 70 percent please", 3), ("fuel, 65% please", 3), ("fill it up", 6), ("fuel to seventy five percent", 4), ("fuel 10 percent", 0)])
def test_the_fuel_said_up_front_answers_gsx_when_it_asks(text, row):
    c = crew()
    c.heard(text, 0.0)
    c.tick(0.0)
    assert Pick(row, FIXTURE["fuel_level"]["entries"][row]) in c.update(state_with(c, menu=page("fuel_level")), 2.0)


def test_how_much_fuel_said_before_gsx_asks_is_kept():
    c = crew()
    assert "60" in said(c.heard("sixty percent", 0.0))[0]
    c._ours["Refueling"] = 0.0
    assert Pick(2, FIXTURE["fuel_level"]["entries"][2]) in c.update(state_with(c, menu=page("fuel_level")), 2.0)


def test_what_gsx_says_the_pilot_needs_is_passed_on_and_the_rest_isnt():
    for message in ("You already have the fuel you asked for, the Fuel Truck will go away.", "Jetway not close enough to any door. Using stairs instead."):
        c = crew()
        assert any(message in s for s in said(c.update(state_with(c, message=message), 0.0)))
    for message in ("GPU service: $50.00 connection + $30.00/hr while connected", "Invoice from Bijoux available - see GSX menu", "Fuel Truck is in position"):
        c = crew()
        assert not said(c.update(state_with(c, message=message), 0.0))


def test_the_gpu_is_said_when_it_connects_not_when_it_goes():
    c = crew()
    assert any("power" in s.lower() for s in said(c.update(state_with(c, services=services(GPU="performing")), 0.0)))
    assert not said(c.update(state_with(c, services=services()), 10.0))


def test_done_is_remembered_after_gsx_stops_showing_it():
    c = crew()
    c.state = state_with(c, services=services(Catering="performing"))
    c.update(state_with(c, services=services(Catering="completed")), 0.0)
    c.update(state_with(c, services=services()), 10.0)  # (a few seconds later it's "available" again)
    assert "catering" in said(c.heard("status", 11.0))[0].lower()


def test_gsxs_boarding_questions_are_settled_without_asking():
    c = crew()
    c.heard("start boarding", 0.0)
    c.tick(0.0)
    assert Pick(1, "No [GSX choice]") in c.update(state_with(c, menu=page("attach_tug")), 1.0)
    assert Pick(0, "Nobody") in c.update(state_with(c, menu=page("board_crew")), 2.0)


def test_a_question_read_out_reads_properly():
    c = crew()
    c.heard("catering please", 0.0)
    c.tick(0.0)
    line = said(c.update(state_with(c, menu=Menu("Are you sure?", ("Yes", "No"))), 1.0))[0]
    assert "??" not in line and "1: Yes, 2: No" in line and "Are you sure?" in line


def test_how_far_along_goes_in_the_status():
    c = crew()
    svc = services()
    svc["Refueling"] = Service(id="Refueling", state="performing", status="pumping\nfuel 1652/114244 lb", progress="1%", can_trigger=False)
    c.state = state_with(c, services=svc)
    assert "pumping, 1%" in said(c.heard("status", 0.0))[0]


def test_door_messages_are_passed_on_not_repeated_and_not_over_atc():
    c = crew()
    c.atc_until = 5.0
    assert not said(c.update(state_with(c, message="Waiting for your action: open R4"), 0.0))  # ATC's talking
    assert any("open R4" in s for s in said(c.tick(6.0)))
    c.update(state_with(c, message=""), 7.0)
    c.update(state_with(c, message="Waiting for your action: open R4"), 8.0)
    assert not said(c.tick(15.0))  # (GSX repeats itself every 15 seconds)


def test_done_calls_by_verbosity_with_the_fuel_on_board():
    c = crew()
    c.state = state_with(c, services=services(Refueling="performing"))
    assert any("26,460" in s for s in said(c.update(state_with(c, services=services(Refueling="completed")), 0.0)))
    quiet = crew(verbosity="quiet")
    quiet.state = state_with(quiet, services=services(Refueling="performing"))
    assert not said(quiet.update(state_with(quiet, services=services(Refueling="completed")), 0.0)) and not said(quiet.tick(10.0))


def test_a_quick_service_finishing_without_saying_completed_still_counts():
    c = crew()
    c.state = state_with(c, services=services(Water="performing"))
    assert any("ater" in s for s in said(c.update(state_with(c, services=services()), 0.0)))


def test_chatter_isnt_said_once_sterile():
    c = crew(verbosity="chatty")
    c.own = own(engines=True)
    c.state = state_with(c, services=services(Departure="performing"))
    assert not said(c.update(state_with(c, services=services(Departure="completed")), 0.0)) and not said(c.tick(10.0))


def test_the_intercom_is_theirs_only_when_addressed_or_answering():
    c = crew()
    assert c.claims("ramp, fuel please", 0.0)
    assert not c.claims("fuel please", 0.0) and not c.claims("request pushback", 0.0)
    assert not c.claims("ramp, fuel please", 0.0, "cabin")
    c.state = state_with(c, services=services(Catering="performing"))
    c.heard("speed up catering", 10.0)
    assert c.claims("no", 15.0) and not c.claims("no", 100.0)


# --- the push --------------------------------------------------------------------------------------------------------


DIRECTION = Menu("Select pushback direction", ("Nose Right/Tail Left (LEFT)", "Nose Left/Tail Right (RIGHT)", "Straight Pushback", "Back"))


def cleared_link(cleared: list) -> RampLink:
    return RampLink(pushback_cleared=lambda: bool(cleared), atc_working=lambda: True)


def test_the_push_is_refused_until_the_aircraft_is_ready():
    c = crew()
    c.own = own(gs=3.0)
    assert "stop" in said(c.heard("pushback nose left", 0.0))[0].lower()
    c.own = own(brake=False)
    assert "parking brake" in said(c.heard("pushback nose left", 1.0))[0].lower()
    c.own = own(engines=True)
    assert "shut down" in said(c.heard("pushback nose left", 2.0))[0].lower()
    assert c._push is None


def test_the_push_warns_about_whats_still_going_and_asks():
    c = crew()
    c.state = state_with(c, services=services(Catering="performing"))
    assert "catering" in said(c.heard("pushback", 0.0))[0].lower()
    assert c._push is None
    assert "Okay" in said(c.heard("no", 1.0))[0] or said(c.heard("no", 1.0))
    c.heard("pushback", 2.0)
    c.heard("yes", 3.0)
    assert c._push is not None


def test_the_push_waits_for_atc_then_goes_by_itself():
    cleared: list = []
    c = crew(cleared_link(cleared))
    out = c.heard("pushback, nose left", 0.0)
    assert any("ground" in s.lower() for s in said(out))
    assert not [o for o in c.tick(1.0) if isinstance(o, Send)]  # no clearance, no tug
    cleared.append(True)
    moved = c.tick(2.0)
    assert any("cleared" in s.lower() for s in said(moved)) and Send("Departure") in moved


def test_with_no_controller_there_is_nothing_to_wait_for_and_the_setting_can_turn_it_off():
    c = crew(RampLink(atc_working=lambda: False))
    c.heard("pushback, nose left", 0.0)
    assert Send("Departure") in c.tick(0.0)
    c = crew(cleared_link([]), wait_for_clearance=False)
    c.heard("pushback, nose left", 0.0)
    assert Send("Departure") in c.tick(0.0)


def test_the_way_said_is_picked_and_if_not_said_is_asked():
    c = crew()
    c.heard("pushback, nose left", 0.0)
    c.tick(0.0)
    assert Pick(1, "Nose Left/Tail Right (RIGHT)") in c.update(state_with(c, menu=DIRECTION), 1.0)
    c = crew()
    c.heard("pushback", 0.0)
    c.tick(0.0)
    out = c.update(state_with(c, menu=DIRECTION), 1.0)
    assert "nose left" in said(out)[0] and not [o for o in out if isinstance(o, Pick)]
    assert Pick(2, "Straight Pushback") in c.heard("straight back", 2.0)


def test_a_way_this_stand_hasnt_got_is_said_not_guessed():
    c = crew()
    c.heard("pushback, nose left", 0.0)
    c.tick(0.0)
    out = c.update(state_with(c, menu=Menu("Select pushback direction", ("Nose Right/Tail Left (LEFT)", "Straight Pushback", "Back"))), 1.0)
    assert not [o for o in out if isinstance(o, Pick)] and "nose right" in said(out)[0]


def test_the_headsets_calls_in_order_following_the_aircraft():
    c = crew()
    c.heard("pushback, nose left", 0.0)
    c.tick(0.0)
    assert any("Release parking brake" in s for s in said(c.update(state_with(c, services=services(Departure="performing")), 1.0)))
    c.own = own(brake=False)
    assert any("pushing back" in s for s in said(c.tick(2.0)))
    c.own = own(brake=False, gs=2.0)
    assert any("Cleared to start" in s for s in said(c.tick(3.0)))
    assert any("Set parking brake" in s for s in said(c.update(state_with(c, services=services(Departure="completing")), 4.0)))
    assert not said(c.tick(5.0))
    assert any("confirm" in s for s in said(c.tick(25.0)))  # and again till it's set: an aircraft rolls otherwise
    c.own = own(brake=True, engines=True)
    c.tick(26.0)
    c.state = state_with(c, services=services(Departure="completed"))
    out = c.tick(27.0)
    assert any("right" in s and "hand signal" in s for s in said(out)) and c._push is None


def test_a_good_start_answered_by_the_engines_or_by_the_pilot():
    question = Menu("Confirm Good Engine Start", ("Good Engine Start", "Complete without waiting", "Abort service"))
    c = crew()
    c.heard("pushback, nose left", 0.0)
    c.tick(0.0)
    c.own = own(engines=True)
    assert Pick(0, "Good Engine Start") in c.update(state_with(c, services=services(Departure="completing"), menu=question), 1.0)
    c = crew()
    c.heard("pushback", 0.0)
    c.tick(0.0)
    out = c.update(state_with(c, services=services(Departure="completing"), menu=question), 1.0)
    assert any("good start" in s.lower() for s in said(out))
    assert Pick(0, "Good Engine Start") in c.heard("good start", 2.0)


def test_stop_the_push():
    c = crew()
    assert "no push" in said(c.heard("stop the push", 0.0))[0].lower()
    c.heard("pushback", 1.0)
    c.tick(1.0)
    out = c.heard("stop the push", 2.0)
    assert any("Set parking brake" in s for s in said(out))


# --- gates -----------------------------------------------------------------------------------------------------------


def test_a_gate_goes_through_atc_and_says_when_gsx_cant_follow():
    asked = []

    def request(label):
        asked.append(label)
        return (label == "B40", "Gate B40 is yours." if label == "B40" else "It's taken.")

    c = crew(RampLink(request_gate=request))
    out = said(c.heard("change to gate B40", 0.0))
    assert asked == ["B40"] and "B40 is yours" in out[0] and any("GSX" in s for s in out)
    assert len(said(c.heard("change to gate B40", 1.0))) == 1  # told once
    assert "taken" in said(c.heard("change to gate C2", 2.0))[0]


def test_parked_on_another_stand_than_atcs_is_said_and_the_aircraft_wins():
    c = crew(RampLink(gate=lambda: "B40"))
    assert any("B 37" in s and "B40" in s for s in said(c.update(state_with(c, parking="Gate B 37"), 0.0) + c.tick(10.0)))
    assert not said(c.tick(20.0))
    same = crew(RampLink(gate=lambda: "B37"))
    assert not said(same.update(state_with(same), 0.0) + same.tick(10.0))


# --- boarding: the cabin crew's --------------------------------------------------------------------------------------


def test_boarding_is_passed_to_the_cabin_crew():
    told = []

    def relay(what, t):
        told.append(what)
        return [CrewSpeech(t=t, text="Boarding now")] if what.endswith("start") else []

    c = crew(RampLink(relay=relay))
    out = c.heard("start boarding", 0.0)
    assert told == ["boarding_start"] and any(isinstance(o, CrewSpeech) for o in out) and any("cabin crew" in s for s in said(out))
    assert Send("Boarding") in c.tick(1.0)
    c.state = state_with(c, services=services(Boarding="performing"))
    done = c.update(state_with(c, services=services(Boarding="completed")), 2.0)
    assert told == ["boarding_start", "boarding_done"] and not said(done + c.tick(10.0))  # it's theirs to say


def test_with_nobody_in_the_cabin_the_ramp_says_it():
    c = crew()
    assert "boarding" in said(c.heard("start boarding", 0.0))[0].lower()
    c.state = state_with(c, services=services(Boarding="performing"))
    assert any("oarding" in s for s in said(c.update(state_with(c, services=services(Boarding="completed")), 2.0)))


def test_the_cabin_crew_told_directly_start_it_without_the_ramp_saying_anything():
    c = crew()
    assert c.called_by_cabin("Boarding", 0.0) and c._queue == ["Boarding"]
    c.own = own(gs=20.0)
    assert not c.called_by_cabin("Deboarding", 1.0)


# --- the language model ----------------------------------------------------------------------------------------------


def test_the_models_reading_stays_within_the_list():
    assert checked('{"asks": [{"action": "request", "services": ["Catering"]}], "understood": true}').asks == (g.Ask(g.REQUEST, ("Catering",)),)
    assert checked('{"asks": [{"action": "launch_missiles"}]}').rejected
    assert checked('{"asks": [{"action": "request", "services": ["Pizza"]}]}').rejected
    assert checked('{"asks": [{"action": "push", "direction": "up"}]}').rejected
    assert checked("nonsense").rejected and checked('{"asks": 3}').rejected


class FakeResult:
    def __init__(self, text, error=""):
        self.text, self.error, self.latency_ms = text, error, 1.0


class FakeBackend:
    model = "fake"

    def __init__(self, answers):
        self.answers, self.requests = list(answers), []

    def complete(self, request, timeout_s=0):
        self.requests.append(request)
        return FakeResult(self.answers.pop(0) if self.answers else "")


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


async def _turn(service, clock, seconds=0.0):
    """A moment for what's on the event loop, the clock moved on."""
    clock.t += seconds
    await service.step(changed=True)
    await asyncio.sleep(0)


def _rig(link=None, model=None, **kw):
    clock = Clock()
    bus = EventBus()
    heard = bus.subscribe(RampSpeech, GroundStatus)
    adapter = MockGsx(clock)
    c = RampCrew(link or RampLink(), seed=1, **kw)
    c.own = own()
    c.state = adapter.state
    service = GroundService(c, adapter, bus, model=model, clock=clock)
    return service, adapter, c, clock, heard


def lines(queue) -> list[str]:
    out = []
    while not queue._queue.empty():
        ev = queue._queue.get_nowait()
        if isinstance(ev, RampSpeech):
            out.append(ev.text)
    return out


def run(coro):
    return asyncio.run(coro)


def test_without_a_cloud_model_only_the_grammar_with_one_it_reads_the_rest():
    async def scenario():
        service, adapter, c, clock, heard = _rig()
        assert not c._queue and said(await service._heard("could you sort out the thingy", 0.0))
        # a model reads what the grammar can't, and goes through the same checks
        backend = FakeBackend(['{"asks": [{"action": "request", "services": ["Water"]}], "understood": true}'])
        service.model = RampModel(backend, "semi")
        await service._heard("top up the drinking tank would you", 0.0)
        assert c._queue == ["Water"] and len(backend.requests) == 1
        # what the grammar can read doesn't go to the model, unless fully LLM
        await service._heard("catering please", 1.0)
        assert len(backend.requests) == 1
    run(scenario())


def test_a_rejected_model_reading_falls_back_and_the_pilot_is_told():
    from localtc.sim_api import AtcAlert

    async def scenario():
        service, adapter, c, clock, _ = _rig()
        alerts = service.bus.subscribe(AtcAlert)
        service.model = RampModel(FakeBackend(['{"asks": [{"action": "launch"}]}']), "llm")
        out = await service._heard("catering please", 0.0)
        assert c._queue == ["Catering"] and said(out)
        assert alerts._queue.get_nowait().kind == "llm_rejected"
    run(scenario())


def test_the_model_cant_get_a_push_past_the_brake():
    async def scenario():
        service, adapter, c, clock, _ = _rig()
        c.own = own(brake=False)
        service.model = RampModel(FakeBackend(['{"asks": [{"action": "push", "direction": "nose_left"}]}']), "llm")
        out = await service._heard("just push us back mate", 0.0)
        assert "parking brake" in said(out)[0].lower() and c._push is None
    run(scenario())


def test_the_models_words_but_never_for_the_headsets_calls():
    async def scenario():
        service, adapter, c, clock, _ = _rig()
        service.model = RampModel(FakeBackend(['{"reply": "On the way, mate."}']), "mostly_llm")
        worded = await service._in_own_words("fuel", [RampSpeech(t=0.0, text="Copy, the fuel truck coming up.")], 0.0)
        assert worded[0].text == "On the way, mate."
        kept = await service._in_own_words("x", [RampSpeech(t=0.0, text="Pushback complete. Set parking brake.")], 0.0)
        assert kept[0].text == "Pushback complete. Set parking brake."
        service.model = RampModel(FakeBackend([""]), "mostly_llm")  # no answer: the script's words for the same call
        same = await service._in_own_words("fuel", [RampSpeech(t=0.0, text="Copy, the fuel truck coming up.")], 0.0)
        assert same[0].text == "Copy, the fuel truck coming up."
    run(scenario())


# --- the Remote API, over a real socket ------------------------------------------------------------------------------


class FakeServer:
    """GSX's server as it behaved: speaks first, unmasked frames, whole-key patches. Splits and pings to keep the client honest."""

    def __init__(self):
        self.commands, self.writers, self.subscribed = [], [], asyncio.Event()
        self.answer: dict[str, dict] = {}

    async def __aenter__(self):
        self.server = await asyncio.start_server(self.client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        for w in self.writers:
            w.close()
        self.server.close()

    @staticmethod
    def frame(op, data, final=True):
        n = len(data)
        head = bytes(((0x80 if final else 0) | op,)) + (bytes((n,)) if n < 126 else bytes((126,)) + struct.pack(">H", n))
        return head + data

    def send(self, writer, obj):
        writer.write(self.frame(1, json.dumps(obj).encode()))

    async def client(self, reader, writer):
        self.writers.append(writer)
        head = await reader.readuntil(b"\r\n\r\n")
        key = next(line.split(b":")[1].strip() for line in head.split(b"\r\n") if line.lower().startswith(b"sec-websocket-key"))
        accept = base64.b64encode(hashlib.sha1(key + GUID).digest())
        writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        self.send(writer, FIXTURE["hello"])
        try:
            while True:
                first, second = await reader.readexactly(2)
                n = second & 0x7F
                if n == 126:
                    n = struct.unpack(">H", await reader.readexactly(2))[0]
                mask = await reader.readexactly(4)
                data = bytes(c ^ mask[i & 3] for i, c in enumerate(await reader.readexactly(n)))
                if first & 0x0F == 10:
                    self.pong = data
                    continue
                message = json.loads(data)
                if message["type"] == "subscribe":
                    self.subscribed.set()
                    payload = json.dumps({"v": 1, "type": "snapshot", "state": 5, "parking": "Gate B 37", "airport": {"icao": "LSZH"},
                                          "services": FIXTURE["services"], "menuShown": False, "menu": {"title": "", "entries": []}}).encode()
                    writer.write(self.frame(1, payload[:10], final=False) + self.frame(9, b"hi") + self.frame(0, payload[10:]))
                elif message["type"] == "command":
                    self.commands.append(message)
                    answer = self.answer.get(message["verb"], {"ok": True})
                    self.send(writer, {"v": 1, "type": "result", "id": message["id"], **answer})
        except (asyncio.IncompleteReadError, ConnectionError):
            pass


def _until(check, seconds=3.0):
    async def wait():
        for _ in range(int(seconds / 0.01)):
            if check():
                return True
            await asyncio.sleep(0.01)
        return False
    return wait()


def test_the_remote_api_end_to_end():
    async def scenario():
        async with FakeServer() as server:
            changes = []
            gsx = GsxRemote(port_of=lambda: server.port, retry_s=0.05)
            await gsx.start(lambda: changes.append(1))
            assert await _until(lambda: gsx.state.parking == "Gate B 37")
            state = gsx.state
            assert state.connected and state.running and state.airport == "LSZH" and "menu" in state.capabilities
            assert "gate" not in state.capabilities  # this GSX: fall back without it
            assert state.services["Refueling"].waiting and state.services["Catering"].operator == "Bijoux"
            assert (await gsx.trigger("Catering")).ok and server.commands[-1]["args"] == {"service": "Catering"}
            assert (await gsx.open_menu()).ok and "args" not in server.commands[-1]  # argument-less verbs carry no key
            server.answer["menu.pick"] = {"ok": False, "error": {"code": "disabled", "message": "greyed"}}
            result = await gsx.pick(5)
            assert not result.ok and result.code == "disabled"
            # patches replace one whole key, null removes it
            w = server.writers[0]
            server.send(w, {"v": 1, "type": "patch", "path": "/menuShown", "value": True})
            server.send(w, {"v": 1, "type": "patch", "path": "/menu", "value": FIXTURE["handler"]})
            assert await _until(lambda: gsx.state.menu is not None)
            assert gsx.state.menu.title == "Select handling operator"
            server.send(w, {"v": 1, "type": "patch", "path": "/parking", "value": None})
            assert await _until(lambda: gsx.state.parking == "")
            server.send(w, {"v": 1, "type": "event", "topic": "engine", "gsxRunning": False})
            assert await _until(lambda: not gsx.state.running)
            # gone: commands say so, and it comes back and subscribes again by itself
            server.subscribed.clear()
            w.close()
            assert await _until(lambda: not gsx.state.connected)
            assert (await gsx.trigger("Water")).code in ("not_connected", "")
            assert await _until(server.subscribed.is_set)
            await gsx.stop()
    run(scenario())


def test_nobody_listening_isnt_an_error_and_says_not_connected():
    async def scenario():
        gsx = GsxRemote(port_of=lambda: 1, retry_s=0.05)
        await gsx.start(lambda: None)
        await asyncio.sleep(0.1)
        assert not gsx.state.connected and gsx.state.services == {}
        assert (await gsx.trigger("Water")).code == "not_connected"
        await gsx.stop()
    run(scenario())


def test_a_server_that_isnt_a_websocket_is_refused():
    async def scenario():
        async def plain(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()

        server = await asyncio.start_server(plain, "127.0.0.1", 0)
        with pytest.raises(WebSocketClosed):
            await WebSocket.connect("127.0.0.1", server.sockets[0].getsockname()[1])
        server.close()
    run(scenario())


# --- whole turnarounds on the stand-in -------------------------------------------------------------------------------


async def _drive(service, clock, seconds, step=1.0):
    t = 0.0
    while t < seconds:
        t += step
        clock.t += step
        await service.step(changed=True)


def test_a_turnaround_by_voice_on_the_stand_in():
    async def scenario():
        service, gsx, c, clock, heard = _rig()
        await gsx.start(service._changed.set)
        await service._out(c.heard("catering, a GPU and water please", 0.0))
        await _drive(service, clock, 3)
        assert ("trigger", "Catering") in gsx.sent
        # GSX's questions answered without the pilot: its choice of handler and caterer
        assert ("pick", "Swissport International [GSX choice]") in gsx.sent and ("pick", "Bijoux [GSX choice]") in gsx.sent
        assert gsx.services["Catering"].state == "requested"
        await _drive(service, clock, 2)
        assert ("trigger", "GPU") in gsx.sent
        await _drive(service, clock, 2)
        assert ("trigger", "Water") in gsx.sent
        # asked for one at a time, each after the last took
        order = [s for k, s in gsx.sent if k == "trigger"]
        assert order == ["Catering", "GPU", "Water"]
        report = said(c.heard("what's still outstanding?", clock.t))[0]
        assert "Catering" in report
        await _drive(service, clock, 60)
        everything = " ".join(lines(heard))
        assert "atering" in everything and gsx.services["Catering"].state == "completed"
        assert "Nothing" not in said(c.heard("status", clock.t))[0]
    run(scenario())


def test_cancel_mid_service_on_the_stand_in():
    async def scenario():
        service, gsx, c, clock, heard = _rig()
        await gsx.start(service._changed.set)
        await service._out(c.heard("water please", 0.0))
        await _drive(service, clock, 2)
        assert gsx.services["Water"].state == "requested"
        await service._out(c.heard("cancel the water", clock.t))
        await _drive(service, clock, 4)
        assert ("pick", "Additional Services ▶") in gsx.sent
        assert gsx.services["Water"].state == "available"  # (the row dismissed it)
        assert gsx.state.menu is None
    run(scenario())


def test_the_push_on_the_stand_in_waits_goes_and_closes_out():
    async def scenario():
        cleared: list = []
        service, gsx, c, clock, heard = _rig(cleared_link(cleared))
        await gsx.start(service._changed.set)
        await service._out(c.heard("pushback, tail right", 0.0))
        await _drive(service, clock, 5)
        assert ("trigger", "Departure") not in gsx.sent
        cleared.append(True)
        await _drive(service, clock, 3)
        assert ("trigger", "Departure") in gsx.sent and ("pick", "Nose Left/Tail Right (RIGHT)") in gsx.sent
        await _drive(service, clock, 8)
        assert "Release parking brake" in " ".join(lines(heard))  # the tug's on, and the brake still set
        c.own = own(brake=False, gs=2.0)
        await _drive(service, clock, 40)
        heard_all = " ".join(lines(heard))
        assert gsx.services["Departure"].state == "completing" and "Cleared to start" in heard_all and "Set parking brake" in heard_all
        assert gsx.state.menu is not None  # GSX's good-start question, waiting
        c.own = own(brake=True, engines=True)
        await _drive(service, clock, 3)
        assert ("pick", "Good Engine Start") in gsx.sent
        assert "hand signal" in " ".join(lines(heard)) and c._push is None
    run(scenario())


def test_gsx_going_away_mid_service_is_said_plainly():
    async def scenario():
        service, gsx, c, clock, heard = _rig()
        await gsx.start(service._changed.set)
        await service._out(c.heard("water please", 0.0))
        await _drive(service, clock, 2)
        gsx.running = False
        await _drive(service, clock, 2)
        out = c.heard("catering please", clock.t)
        assert "GSX" in said(out)[0]
    run(scenario())


def test_on_the_ramps_frequency_its_theirs_and_elsewhere_it_isnt():
    async def scenario():
        service, gsx, c, clock, heard = _rig()
        await gsx.start(service._changed.set)
        c.own = own(com1=119.999)
        await service._on(PttPressed(t=0.0, radio=1))
        await service._on(Transcript(t=0.1, text="water please", radio=1, source="voice"))
        await asyncio.sleep(0)
        for task in asyncio.all_tasks() - {asyncio.current_task()}:
            await task
        assert c._queue == ["Water"]
        c.own = own(com1=121.9)
        c._queue.clear()
        await service._on(PttPressed(t=1.0, radio=1))
        await service._on(Transcript(t=1.1, text="ground, request pushback", radio=1, source="voice"))
        for task in asyncio.all_tasks() - {asyncio.current_task()}:
            await task
        assert c._push is None and not c._queue
        # the copilot's own transmissions aren't the pilot asking
        c.own = own(com1=119.999)
        await service._on(Transcript(t=2.0, text="water please", radio=1, source="copilot"))
        assert not c._queue
    run(scenario())


def test_the_line_on_the_ramp_is_on_the_radio_and_otherwise_the_interphone():
    c = crew()
    assert [o for o in c.heard("status", 0.0) if isinstance(o, RampSpeech)][0].channel == "interphone"
    c.own = own(com1=119.999)
    assert [o for o in c.heard("status", 1.0) if isinstance(o, RampSpeech)][0].channel == "radio"


def test_status_for_the_phone():
    async def scenario():
        service, gsx, c, clock, heard = _rig()
        await gsx.start(service._changed.set)
        await service.step(changed=True)
        events = []
        while not heard._queue.empty():
            events.append(heard._queue.get_nowait())
        status = next(e for e in events if isinstance(e, GroundStatus))
        assert status.provider == "mock" and status.connected and status.parking == "Gate B 37"
        assert {s.id for s in status.services} >= {"Catering", "Departure"}
    run(scenario())


def test_the_sims_own_services_without_gsx():
    from localtc.ground.standalone import SimServices

    async def scenario():
        sent = []
        clock = Clock()
        sim = SimServices(sent.append, clock)
        assert (await sim.trigger("OperateJetways")).ok and sent[-1] == SendSimEvent(name="TOGGLE_JETWAY")
        assert (await sim.trigger("Departure")).ok and sent[-1] == SendSimEvent(name="TOGGLE_PUSHBACK")
        assert sim.state.services["OperateJetways"].state == "performing"
        clock.t = 20.0
        assert sim.state.services["OperateJetways"].state == "completed"
        assert not (await sim.trigger("Lavatory")).ok and not (await sim.open_menu()).ok
        c = crew()
        c.state = sim.state
        answer = said(c.heard("water please", 0.0))[0]
        assert "isn't available" in answer or "can't" in answer
    run(scenario())


def test_events_round_trip_in_a_recording():
    status = GroundStatus(t=1.0, provider="gsx", connected=True, parking="Gate B 37")
    speech = RampSpeech(t=1.0, text="Fuel's done.", channel="radio")
    assert msgspec.json.decode(msgspec.json.encode(status), type=GroundStatus) == status
    assert msgspec.json.decode(msgspec.json.encode(speech), type=RampSpeech) == speech


def test_close_and_send_are_what_the_service_understands():
    assert isinstance(Close(), Close) and Send("GPU").service == "GPU"


def test_the_phones_buttons_go_through_the_same_checks():
    async def scenario():
        service, gsx, c, clock, heard = _rig()
        await gsx.start(service._changed.set)
        await service.from_phone("request", "Catering")
        assert c._queue == ["Catering"]
        c.own = own(brake=False)
        await service.from_phone("push", direction="nose_left")
        assert c._push is None and "parking brake" in " ".join(lines(heard)).lower()
        for bad in (("launch",), ("request", "Pizza"), ("push", "", "sideways")):
            with pytest.raises(ValueError):
                await service.from_phone(*bad)
    run(scenario())


def test_the_phone_sees_the_services_in_airport_ops():
    from localtc.efb.airport_ops import AirportOps
    from localtc.sim_api import GroundServiceState

    ops = AirportOps()
    assert ops.to_json()["ground"] is None
    ops.observe(GroundStatus(t=0.0, provider="gsx", connected=True, parking="Gate B 37",
                             services=(GroundServiceState(id="Catering", state="requested", status="on the way"),)))
    ground = ops.to_json()["ground"]
    assert ground["parking"] == "Gate B 37"
    assert ground["services"][0] == {"id": "Catering", "name": "", "state": "requested", "waiting": False, "status": "on the way"}


def test_the_phones_command_reaches_the_crew_and_says_when_theyre_off():
    from types import SimpleNamespace

    from localtc.ui.efb_api import EfbRoutes
    from localtc.ui.server import HttpError

    async def scenario():
        asked = []

        async def from_phone(action, service, direction):
            asked.append((action, service, direction))

        efb = EfbRoutes.__new__(EfbRoutes)
        efb.c = SimpleNamespace(live=SimpleNamespace(ground=SimpleNamespace(from_phone=from_phone)))
        assert await efb.command({"action": "ground", "do": "request", "service": "Catering"}) == {"ok": True}
        assert asked == [("request", "Catering", "")]
        efb.c = SimpleNamespace(live=SimpleNamespace(ground=None))
        with pytest.raises(HttpError):
            await efb.command({"action": "ground", "do": "request", "service": "Catering"})
    run(scenario())
