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
