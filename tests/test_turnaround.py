"""The plan's off-block time against the sim's clock, and the copilot's word when the flight is behind it."""

from localtc.config import PlanPerf
from localtc.crew.answers import Picture
from localtc.crew.monitor import Monitor
from localtc.crew.actions import Cockpit
from localtc.crew.turnaround import from_clock_s, off_block_in
from localtc.sim_api import AircraftVars, OwnshipState

NOON = 12 * 3600
PLAN = 1793620800 - 1793620800 % 86400 + NOON


def test_the_time_goes_the_nearest_way_round_the_day():
    assert from_clock_s(PLAN, NOON - 600.0) == 600.0
    assert from_clock_s(PLAN, NOON + 600.0) == -600.0
    midnight = PLAN - NOON
    assert from_clock_s(midnight, 86400 - 300.0) == 300.0  # just before midnight, off block just after
    assert from_clock_s(0, NOON) is None and from_clock_s(PLAN, None) is None


def test_a_plan_hours_away_isnt_held_to():
    assert off_block_in(PLAN, NOON - 7 * 3600.0) is None
    assert off_block_in(PLAN, NOON - 2 * 3600.0) == 2 * 3600.0


class _State:
    clearances: dict = {}

    class flight:
        origin, destination, callsign, aircraft_type = "LEMD", "LSZH", "", ""


class _Engine:
    state = _State()
    _vfr = False


def _monitor() -> Monitor:
    monitor = Monitor(Picture(Cockpit(), _Engine(), perf=PlanPerf(sched_out_s=PLAN)), perf=PlanPerf(sched_out_s=PLAN))
    monitor.f.said["greeting"] = 0.0
    monitor.phase = "PARKED"
    return monitor


def _own(t: float, late_s: float, gs: float = 0.0) -> OwnshipState:
    return OwnshipState(t=t, lat=40.47, lon=-3.57, alt_msl_ft=1900, alt_indicated_ft=1900, alt_agl_ft=0, altimeter_inhg=30.0,
                        hdg_mag=0, hdg_true=0, ias_kt=0, gs_kt=gs, vs_fpm=0, on_ground=True, squawk="1200", xpdr_mode="standby",
                        com1_mhz=121.0, com2_mhz=121.0, zulu_s=NOON + late_s)


def _words(m: Monitor) -> list[str]:
    return [c.text for c in m.queue]


def test_the_copilot_keeps_time():
    m = _monitor()
    m._schedule(_own(0.0, -1100.0), 0.0)
    assert "no clearance" in _words(m)[0] or "don't have our clearance" in _words(m)[0]
    m._schedule(_own(1.0, -200.0), 1.0)
    assert "minutes to off-block, at 1200" in _words(m)[-1]
    m._schedule(_own(2.0, 10.0), 2.0)
    assert _words(m)[-1] == "That's our off-block time."
    m._schedule(_own(3.0, 310.0), 3.0)
    assert _words(m)[-1] == "Five minutes late off block. We still need our clearance."
    m._schedule(_own(4.0, 320.0), 4.0)
    assert _words(m).count("Five minutes late off block. We still need our clearance.") == 1


def test_it_says_whats_holding_the_flight_up():
    m = _monitor()
    m.observe(AircraftVars(t=0.0, values={"L:FSDT_GSX_BOARDING_STATE": 5.0}))
    m._schedule(_own(1.0, 950.0), 1.0)
    assert _words(m)[-1] == "Fifteen minutes late off block. Boarding's still going."
    m._schedule(_own(2.0, 1000.0), 2.0)  # (no "five minutes late" after the fifteen)
    assert not [w for w in _words(m) if w.startswith("Five")]


def test_how_the_push_went():
    m = _monitor()
    m.phase = "PUSHBACK"
    m._schedule(_own(1.0, 720.0, gs=3.0), 1.0)
    assert _words(m) == ["Off blocks, 12 minutes late."]
    m = _monitor()
    m.phase = "PUSHBACK"
    m._schedule(_own(1.0, 30.0, gs=3.0), 1.0)
    assert _words(m) == ["Off blocks, right on time."]


def test_one_reading_of_a_few_knots_at_the_gate_isnt_off_blocks():
    """GSX's jetway coming up: 2.5 knots for a moment, with nobody on board."""
    m = _monitor()
    m._schedule(_own(1.0, 30.0, gs=2.5), 1.0)
    m._schedule(_own(1.5, 30.0, gs=0.0), 1.5)
    assert "Off blocks" not in " ".join(_words(m))
    m._schedule(_own(10.0, 30.0, gs=3.0), 10.0)
    m._schedule(_own(14.0, 30.0, gs=3.0), 14.0)
    assert "Off blocks, right on time." in _words(m)


def test_a_plan_hours_from_the_clock_is_said_once_and_left():
    m = _monitor()
    m._schedule(_own(1.0, -7 * 3600.0), 1.0)
    m._schedule(_own(2.0, -7 * 3600.0), 2.0)
    assert len(_words(m)) == 1 and "won't hold us to it" in _words(m)[0] and "7 hours" in _words(m)[0]


def test_no_plan_time_no_word():
    m = _monitor()
    m.perf = PlanPerf()
    m._schedule(_own(1.0, 0.0), 1.0)
    assert not _words(m)
