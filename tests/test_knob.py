"""Turning an FCU knob to a value in closed loop (sim_bridge/knob.py), and the profile's way to ask for it."""

from localtc.sim_bridge.knob import KnobTurn


def _run(turn: KnobTurn, value: float, per_step: float, *, accel: bool = False) -> float:
    """A knob against a fake reading: each step moves it ``per_step`` (or more once turned steadily)."""
    now, streak = 0.0, 0
    for _ in range(2000):
        what = turn.due(now)
        if what == "step":
            streak += 1
            value += turn.pending.pop() * per_step * (3 if accel and streak > 10 else 1)
        elif what == "read":
            streak = 0
            turn.on_value(value, now)
        if turn.done:
            return value
        now += 0.01
    raise AssertionError("never done")


def test_a_knob_is_turned_until_it_reads_the_target():
    assert _run(KnobTurn("SPD", 250, 1), 100, 1) == 250
    assert _run(KnobTurn("SPD", 140, 1), 399, 1, accel=True) == 140  # the speed-up overshoots: it comes back


def test_a_heading_goes_the_short_way_round():
    turn = KnobTurn("HDG", 5, 1, wrap=360)
    assert _run(turn, 350, 1) % 360 == 5
    assert turn.reads < 10


def test_an_altitude_knob_in_thousands_stops_at_the_nearest():
    assert _run(KnobTurn("ALT", 12000, 1000), 5000, 1000) == 12000


def test_a_knob_that_moves_nothing_is_given_up():
    turn = KnobTurn("X", 250, 1)
    assert _run(turn, 100, 0) == 100 and turn.reads <= 5


def test_a_profile_knob_turns_to_the_value():
    from localtc.crew.actions import KNOB_CHECK_S, Cockpit, plan
    from localtc.crew.commands import Command
    from localtc.crew.profiles import Profile, Write
    from localtc.sim_api import TurnKnob

    p = Profile(name="x", actions={"altitude": Write(knob="INSTRUMENT_FCU_ALT_KNOB", step=1000)})
    got = plan(Command("altitude", "12000"), Cockpit(profile=p))
    assert got.writes == (TurnKnob(name="INSTRUMENT_FCU_ALT_KNOB", var="AUTOPILOT ALTITUDE LOCK VAR", unit="feet",
                                   target=12000.0, step=1000.0),)
    assert got.check_s == KNOB_CHECK_S


def _fenix():
    from localtc.crew.profiles import for_aircraft, load_all
    return for_aircraft(load_all(), "FenixA319 IAE WF SD", "A319")


def test_the_fenix_fcu_is_turned_past_its_stop_then_up():
    from localtc.crew.actions import Cockpit, plan
    from localtc.crew.commands import Command
    from localtc.sim_api import NudgeVar

    c = Cockpit(profile=_fenix())
    assert plan(Command("speed", "250"), c).writes == (NudgeVar(name="L:E_FCU_SPEED", deltas=(-500, 150)),)
    # in thousands from 100: 12 clicks is 12,000 (11,900 would round up the same)
    assert plan(Command("altitude", "12000"), c).writes == (NudgeVar(name="L:E_FCU_ALTITUDE", deltas=(-600, 12)),)
    assert plan(Command("speed", "250"), c).check(c) is None  # nothing reads the Fenix's FCU back: sent, not checked


def test_a_switch_each_side_is_two_writes_and_an_unread_light_isnt_checked():
    from localtc.crew.actions import Cockpit, plan
    from localtc.crew.commands import Command
    from localtc.sim_api import AircraftSystems

    c = Cockpit(profile=_fenix(), systems=AircraftSystems(t=0.0, light_landing=True))
    got = plan(Command("light", "on", "landing"), c)
    assert [w.name for w in got.writes] == ["L:S_OH_EXT_LT_LANDING_L", "L:S_OH_EXT_LT_LANDING_R"]
    assert got.check(c) is None and not got.already  # the sim's landing light is the Fenix's nose T.O. light


def test_what_an_aircraft_cant_do_is_the_pilots_from_the_start():
    from localtc.crew.pm import PilotMonitoring
    from localtc.sim_api import AircraftIdentity

    from tests.test_copilot_deck import FakeEngine, heard, said, sent

    pm = PilotMonitoring(FakeEngine())
    pm.observe(AircraftIdentity(t=0.0, title="FenixA320 CFM", atc_model="A320"))
    out = heard(pm, 1.0, "heading two four zero", 0.95)
    assert not sent(out) and any("that one's yours" in w for w in said(out))
