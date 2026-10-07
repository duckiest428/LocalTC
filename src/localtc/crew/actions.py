"""What each command does in the cockpit, how the copilot knows it took, and when it won't do it.

``plan`` turns a ``Command`` into the sim commands to send, a check on the sim's state ("the flap handle is at 2"),
and the words for "done". ``safety`` says whether to do it at all: ``refuse`` with the reason, or ``confirm`` first
(the pilot answers "confirm"). The copilot acts first and reports after; only what can't be undone easily, or what
goes against the clearance, waits for a "confirm".
"""

from collections.abc import Callable
from dataclasses import dataclass

from localtc.atc_core.phraseology import speech
from localtc.atc_core.facilities import channel_khz
from localtc.crew.commands import Command
from localtc.crew.profiles import Profile, Write
from localtc.sim_api import (
    AircraftSystems,
    OwnshipState,
    SendSimEvent,
    SetComFrequency,
    SetInputEvent,
    SetSimVar,
    SimCommand,
)

FLAPS_MAX = 16383  # FLAPS_SET's full travel
DETENT_EVENTS = ("FLAPS_UP", "FLAPS_1", "FLAPS_2", "FLAPS_3", "FLAPS_DOWN")
LIGHT_EVENTS = {"landing": "LANDING_LIGHTS_SET", "taxi": "TAXI_LIGHTS_SET", "strobe": "STROBES_SET",
                "beacon": "BEACON_LIGHTS_SET", "nav": "NAV_LIGHTS_SET", "logo": "LOGO_LIGHTS_SET"}
LIGHT_NAMES = {"landing": "landing lights", "taxi": "taxi lights", "strobe": "strobes", "beacon": "beacon",
               "nav": "nav lights", "logo": "logo lights"}
MODE_EVENTS = {"heading": "AP_HDG_HOLD_ON", "nav": "AP_NAV1_HOLD_ON", "approach": "AP_APR_HOLD_ON",
               "altitude": "AP_ALT_HOLD_ON", "vs": "AP_VS_ON", "flc": "FLIGHT_LEVEL_CHANGE_ON"}
MODE_NAMES = {"heading": "heading mode", "nav": "nav mode", "approach": "approach mode", "altitude": "altitude hold",
              "vs": "vertical speed mode", "flc": "level change"}
EMERGENCY_SQUAWKS = {"7500": "hijack", "7600": "radio failure", "7700": "emergency"}
POSITIVE_RATE_FPM = 100.0
ROLL_KT = 30.0  # on the ground faster than this: the takeoff (or landing) roll
MOVING_KT = 5.0
SPOILERS_MIN_AGL = 1000.0
AP_OFF_CONFIRM_AGL = 500.0
SPEED_MARGIN_KT = 5.0  # a placard speed is exceeded by more than this


@dataclass
class Cockpit:
    """What the copilot sees: the aircraft (own-ship), its switches (``systems``, None until the sim sends them),
    its profile, and what ATC has cleared."""

    own: OwnshipState | None = None
    systems: AircraftSystems | None = None
    profile: Profile = Profile()
    cleared_altitude_ft: int | None = None
    assigned_squawk: str | None = None

    @property
    def airborne(self) -> bool:
        return self.own is not None and not self.own.on_ground

    @property
    def flap_positions(self) -> int:
        if self.systems is not None and self.systems.flaps_positions:
            return self.systems.flaps_positions
        return max(len(self.profile.detents) - 1, 0)


Check = Callable[[Cockpit], bool | None]  # True: the sim shows it; False: not (yet); None: can't tell


@dataclass(frozen=True)
class Plan:
    action: str
    value: str
    writes: tuple[SimCommand, ...]
    check: Check
    done: str  # what the copilot says when it took: "Flaps 2."
    done_spoken: str = ""
    already: str = ""  # said instead, when the sim already shows it: "Flaps already 2."


@dataclass(frozen=True)
class Verdict:
    kind: str  # ok, refuse, confirm
    reason: str = ""


OK = Verdict("ok")


def _sys(attr: str, want) -> Check:
    def check(c: Cockpit) -> bool | None:
        if c.systems is None:
            return None
        value = getattr(c.systems, attr)
        return abs(value - want) <= 1.0 if isinstance(want, float) else value == want
    return check


def _own(get: Callable[[OwnshipState], bool]) -> Check:
    return lambda c: None if c.own is None else get(c.own)


def _write(profile: Profile, key: str, default: SimCommand, value: float | None = None,
           on: bool | None = None) -> SimCommand:
    """The profile's own way for this action (an add-on's event or L:var), else the standard event."""
    custom: Write | None = profile.actions.get(key)
    if custom is None:
        return default
    if custom.input:
        level = custom.on if on else custom.off if on is not None else (value or 0.0)
        return SetInputEvent(name=custom.input, value=float(level))
    if custom.lvar:
        level = custom.on if on else custom.off if on is not None else (value or 0.0)
        return SetSimVar(name=custom.lvar, unit=custom.unit, value=float(level))
    if custom.event:
        return SendSimEvent(name=custom.event, value=custom.value if custom.value is not None else int(value or 0))
    return default


def plan(cmd: Command, c: Cockpit) -> Plan | str:  # noqa: C901 - one branch per command, flat
    """What to send for ``cmd``, or why it can't be done on this aircraft ("unable ...")."""
    p = c.profile
    a, v = cmd.action, cmd.value
    if a == "gear":
        down = v == "down"
        event = SendSimEvent(name="GEAR_DOWN" if down else "GEAR_UP")
        return Plan(a, v, (_write(p, f"gear_{v}", event),), _own(lambda o: o.gear_down == down), f"Gear {v}.",
                    already=f"Gear's already {v}.")
    if a == "flaps":
        positions = c.flap_positions
        index = p.detent_index(v.replace("+f", "") if v.endswith("+f") else v, positions)
        if index is None:
            return f"Unable, there's no flaps {v.replace('+f', ' plus F')} on this aircraft."
        on_ground = c.own is not None and c.own.on_ground
        name = p.detent_name(index, positions, on_ground=on_ground)
        if p.flaps == "detents" and positions + 1 == len(DETENT_EVENTS):
            event = SendSimEvent(name=DETENT_EVENTS[index])
        else:
            event = SendSimEvent(name="FLAPS_SET", value=round(index / positions * FLAPS_MAX) if positions else 0)
        said = "Flaps up." if index == 0 else f"Flaps {name.upper() if '+' in name else name}."
        spoken = said.replace("+F", " plus F").replace("+f", " plus F")
        return Plan(a, name, (_write(p, f"flaps_{index}", event, index),), _own(lambda o: o.flaps_index == index),
                    said, spoken, already=f"Flaps are already {name.replace('+F', ' plus F')}.")
    if a == "light":
        on = v == "on"
        event = SendSimEvent(name=LIGHT_EVENTS[cmd.target], value=int(on))
        what = LIGHT_NAMES[cmd.target]
        return Plan(a, f"{cmd.target} {v}", (_write(p, f"light_{cmd.target}", event, on=on),),
                    _sys(f"light_{cmd.target}", on), f"{what.capitalize()} {v}.", already=f"{what.capitalize()} already {v}.")
    if a == "spoilers":
        if v in ("arm", "disarm"):
            arm = v == "arm"
            event = SendSimEvent(name="SPOILERS_ARM_ON" if arm else "SPOILERS_ARM_OFF")
            return Plan(a, v, (_write(p, f"spoilers_{v}", event, on=arm),), _sys("spoilers_armed", arm),
                        "Spoilers armed." if arm else "Spoilers disarmed.",
                        already="Spoilers are already armed." if arm else "Spoilers aren't armed.")
        out = v == "extend"
        event = SendSimEvent(name="SPOILERS_ON" if out else "SPOILERS_OFF")
        check: Check = lambda c2: None if c2.systems is None else (c2.systems.spoilers_pct > 40 if out else c2.systems.spoilers_pct < 5)  # noqa: E731
        return Plan(a, v, (_write(p, f"spoilers_{v}", event, on=out),), check,
                    "Speedbrakes extended." if out else "Speedbrakes retracted.")
    if a == "autopilot":
        on = v == "on"
        event = SendSimEvent(name="AUTOPILOT_ON" if on else "AUTOPILOT_OFF")
        return Plan(a, v, (_write(p, f"autopilot_{v}", event, on=on),), _sys("ap_master", on),
                    "Autopilot on." if on else "Autopilot off.", already=f"Autopilot's already {v}.")
    if a == "autothrottle":
        on = v == "on"
        if c.systems is not None and c.systems.athr_armed == on:
            return Plan(a, v, (), _sys("athr_armed", on), "", already=f"Autothrottle's already {'armed' if on else 'off'}.")
        event = SendSimEvent(name="AUTO_THROTTLE_ARM")  # a toggle: only sent when it isn't already so
        return Plan(a, v, (_write(p, f"autothrottle_{v}", event, on=on),), _sys("athr_armed", on),
                    "Autothrottle armed." if on else "Autothrottle off.")
    if a == "ap_mode":
        mode = cmd.target
        event = SendSimEvent(name=MODE_EVENTS[mode])
        field = {"heading": "ap_heading", "nav": "ap_nav", "approach": "ap_approach", "altitude": "ap_altitude",
                 "vs": "ap_vs", "flc": "ap_flc"}[mode]
        return Plan(a, mode, (_write(p, f"ap_{mode}", event, on=True),), _sys(field, True),
                    f"{MODE_NAMES[mode].capitalize()}.", already=f"Already in {MODE_NAMES[mode]}.")
    if a == "heading":
        deg = int(v)
        return Plan(a, v, (_write(p, "heading", SendSimEvent(name="HEADING_BUG_SET", value=deg), deg),),
                    _sys("ap_heading_sel", float(deg % 360)), f"Heading {deg:03d} set.",
                    f"heading {speech.heading(deg)} set")
    if a == "altitude":
        ft = int(v)
        return Plan(a, v, (_write(p, "altitude", SendSimEvent(name="AP_ALT_VAR_SET_ENGLISH", value=ft), ft),),
                    _sys("ap_altitude_sel", float(ft)), f"{speech.altitude_display(ft)} set.",
                    f"{speech.altitude(ft)} set")
    if a == "speed":
        kt = int(v)
        return Plan(a, v, (_write(p, "speed", SendSimEvent(name="AP_SPD_VAR_SET", value=kt), kt),),
                    _sys("ap_speed_sel", float(kt)), f"Speed {kt} set.", f"speed {speech.speed(kt)} set")
    if a == "vs":
        fpm = int(v)
        said = f"{'Minus' if fpm < 0 else 'Plus'} {abs(fpm):,} set."
        spoken = f"vertical speed {'minus' if fpm < 0 else 'plus'} {speech._feet(abs(fpm))} set" if abs(fpm) >= 100 else "vertical speed zero set"
        check_vs: Check = lambda c2: None if c2.systems is None else abs(c2.systems.ap_vs_sel - fpm) <= 50  # noqa: E731
        return Plan(a, v, (_write(p, "vs", SendSimEvent(name="AP_VS_VAR_SET_ENGLISH", value=fpm), fpm),), check_vs,
                    said, spoken)
    if a == "squawk":
        return Plan(a, v, (_write(p, "squawk", SendSimEvent(name="XPNDR_SET", value=int(v, 16))),),
                    _own(lambda o: o.squawk == v), f"Squawk {v} set.", f"squawk {speech.squawk(v)} set",
                    already=f"Squawk's already {v}.")
    if a == "com_active":
        mhz = float(v)
        hz = channel_khz(mhz) * 1000
        return Plan(a, v, (_write(p, "com_active", SetComFrequency(hz=hz)),),
                    _own(lambda o: channel_khz(o.com1_mhz) == channel_khz(mhz)), f"{speech.frequency_display(mhz)} set.",
                    f"{speech.frequency(mhz)} set")
    if a == "com_standby":
        mhz = float(v)
        hz = channel_khz(mhz) * 1000
        check_stby: Check = lambda c2: None if c2.systems is None else channel_khz(c2.systems.com1_standby_mhz) == channel_khz(mhz)  # noqa: E731
        return Plan(a, v, (_write(p, "com_standby", SendSimEvent(name="COM_STBY_RADIO_SET_HZ", value=hz)),), check_stby,
                    f"{speech.frequency_display(mhz)} in standby.", f"{speech.frequency(mhz)} in standby")
    if a == "com_swap":
        if c.systems is None or not c.systems.com1_standby_mhz:
            check_swap: Check = lambda c2: None  # noqa: E731
        else:
            standby = channel_khz(c.systems.com1_standby_mhz)
            check_swap = _own(lambda o: channel_khz(o.com1_mhz) == standby)
        return Plan(a, v, (_write(p, "com_swap", SendSimEvent(name="COM_STBY_RADIO_SWAP")),), check_swap, "Swapped.")
    if a == "altimeter":
        hpa = float(v) if cmd.target == "hpa" else float(v) * 33.8639
        inhg = hpa / 33.8639
        said = f"QNH {int(v)} set." if cmd.target == "hpa" else ("Standard set." if v == "29.92" else f"Altimeter {v} set.")
        spoken = (f"Q N H {speech.digits(v)} set" if cmd.target == "hpa" else "standard set" if v == "29.92"
                  else f"altimeter {speech.digits(v.replace('.', ''))} set")
        # The first officer's own altimeter (index 2): the captain's is the captain's. The sim reports the captain's
        # setting, so whether it took can't be seen: said as done.
        return Plan(a, v, (_write(p, "altimeter", SendSimEvent(name="KOHLSMAN_SET", value=round(hpa * 16), index=2), hpa),),
                    lambda c2: None, said.replace(" set.", " set on my side."), spoken + " on my side")
    if a == "parking_brake":
        on = v == "on"
        event = SendSimEvent(name="PARKING_BRAKE_SET", value=int(on))
        return Plan(a, v, (_write(p, f"parking_brake_{v}", event, on=on),), _own(lambda o: o.parking_brake == on),
                    "Parking brake set." if on else "Parking brake released.",
                    already="Parking brake's already set." if on else "Parking brake's already off.")
    return f"Unable, I can't do {cmd} yet."


def safety(cmd: Command, c: Cockpit) -> Verdict:  # noqa: C901 - one rule per line, flat
    """Whether to do it: ``refuse`` (with the reason, said to the pilot), ``confirm`` first, or OK."""
    own, p, a, v = c.own, c.profile, cmd.action, cmd.value
    ias = own.ias_kt if own is not None else 0.0
    rolling = own is not None and own.on_ground and own.gs_kt > ROLL_KT
    if a == "gear" and v == "up":
        if own is not None and own.on_ground:
            return Verdict("refuse", "Negative, we're on the ground.")
        if own is not None and own.vs_fpm < POSITIVE_RATE_FPM and own.alt_agl_ft < 1500:
            return Verdict("refuse", "Negative, no positive rate yet.")
        if p.gear_retract_kt and ias > p.gear_retract_kt + SPEED_MARGIN_KT:
            return Verdict("refuse", f"Unable, speed {ias:.0f}, gear limit {p.gear_retract_kt:.0f}.")
    if a == "gear" and v == "down" and own is not None and not own.gear_down and c.airborne:
        if p.gear_extend_kt and ias > p.gear_extend_kt + SPEED_MARGIN_KT:
            return Verdict("refuse", f"Unable, speed {ias:.0f}, gear limit {p.gear_extend_kt:.0f}.")
    if a == "flaps":
        if rolling:
            return Verdict("refuse", "Negative, not on the roll.")
        index = p.detent_index(v.replace("+f", ""), c.flap_positions)
        current = own.flaps_index if own is not None else 0
        if index is not None and index > current and c.airborne and (limit := p.vfe_for(index)) and ias > limit + SPEED_MARGIN_KT:
            name = p.detent_name(index, c.flap_positions)
            return Verdict("refuse", f"Unable, speed {ias:.0f}, flaps {name} limit {limit:.0f}.")
    if a == "spoilers" and v == "extend" and c.airborne and own is not None and own.alt_agl_ft < SPOILERS_MIN_AGL:
        return Verdict("refuse", "Negative, not below 1,000 feet.")
    if a == "parking_brake" and v == "on" and own is not None and own.gs_kt > MOVING_KT:
        return Verdict("refuse", "Negative, not while we're moving.")
    if a == "autopilot" and v == "off" and c.airborne and own is not None and own.alt_agl_ft < AP_OFF_CONFIRM_AGL \
            and (c.systems is None or c.systems.ap_master):
        return Verdict("confirm", f"Autopilot off at {own.alt_agl_ft:.0f} feet, confirm?")
    if a == "squawk":
        if v in EMERGENCY_SQUAWKS:
            return Verdict("confirm", f"Squawk {v}, {EMERGENCY_SQUAWKS[v]}, confirm?")
        if c.assigned_squawk and v != c.assigned_squawk:
            return Verdict("confirm", f"ATC gave us {c.assigned_squawk}. Squawk {v}, confirm?")
    if a == "altitude" and c.cleared_altitude_ft and abs(int(v) - c.cleared_altitude_ft) >= 100:
        return Verdict("confirm", f"We're cleared to {speech.altitude_display(c.cleared_altitude_ft)}. "
                                  f"Set {speech.altitude_display(int(v))}, confirm?")
    return OK
