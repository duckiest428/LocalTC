"""What each command does in the cockpit, how the copilot knows it took, and when it won't do it.

``plan`` turns a ``Command`` into the sim commands to send, a check on the sim's state ("the flap handle is at 2"),
and the words for "done". ``safety`` says whether to do it at all: ``refuse`` with the reason, or ``confirm`` first
(the pilot answers "confirm"). The copilot acts first and reports after; only what can't be undone easily, or what
goes against the clearance, waits for a "confirm".
"""

from collections.abc import Callable
from typing import Any
from dataclasses import dataclass, field, replace

from msgspec.structs import replace as replace_struct

from localtc.atc_core.phraseology import speech
from localtc.atc_core.facilities import channel_khz
from localtc.crew.commands import Command
from localtc.crew.profiles import Profile, Write, read_var
from localtc.sim_api import (
    AircraftSystems,
    ClickSequence,
    OwnshipState,
    SendSimEvent,
    SetComFrequency,
    SetInputEvent,
    SetSimVar,
    NudgeVar,
    SimCommand,
    TurnKnob,
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
# What a knob (TurnKnob) is turned until, for each action: the variable, its unit, the wrap (a heading's 360).
KNOB_READS = {"heading": ("AUTOPILOT HEADING LOCK DIR", "degrees", 360.0),
              "altitude": ("AUTOPILOT ALTITUDE LOCK VAR", "feet", 0.0),
              "speed": ("AUTOPILOT AIRSPEED HOLD VAR", "knots", 0.0),
              "vs": ("AUTOPILOT VERTICAL HOLD VAR", "feet per minute", 0.0)}
KNOB_CHECK_S = 20.0  # a knob takes a while to turn (a few seconds for 150 kt): the check waits this long
# Whose hands: the pilot flying's side of the cockpit (the captain's seat, the thrust and the speedbrake levers, the
# parking brake, the autopilot's engagement). The copilot works its own side and the shared panels on request; these it
# leaves, saying so in a word. ("spoilers": the speedbrake lever, armed or out.)
PILOT_SIDE = frozenset({"parking_brake", "spoilers", "autothrottle", "autopilot"})


@dataclass
class Cockpit:
    """What the copilot sees: the aircraft (own-ship), its switches (``systems``, None until the sim sends them),
    its profile, and what ATC has cleared."""

    own: OwnshipState | None = None
    systems: AircraftSystems | None = None
    profile: Profile = Profile()
    cleared_altitude_ft: int | None = None
    assigned_squawk: str | None = None
    # The readings seen to move this flight ("flaps", "ap_altitude_sel", ...): only those are believed when they say a
    # thing is already so (an A350's flap reading sat at 0 with the flaps at 1: "flaps are already up"). And the
    # controls that never took from the copilot's side, twice: not reached for again.
    moved: set[str] = field(default_factory=set)
    dead: set[str] = field(default_factory=set)
    # The aircraft's own variables its profile reads (AircraftVars): the FSLabs's switch positions and windows.
    vars: dict[str, float] = field(default_factory=dict)

    def watch(self) -> None:
        """Note which readings changed since the last look (``own`` and ``systems`` just replaced)."""
        now = _readings(self)
        before = getattr(self, "_last", None)
        if before is not None:
            self.moved |= {k for k, v in now.items() if k in before and before[k] != v}
        self._last = now

    def trusts(self, reading: str) -> bool:
        return not reading or reading in self.moved

    def believes(self, reading: str) -> bool:
        """Whether this aircraft's ``reading`` can be taken at its word: one seen to move this flight, or any in an
        aircraft with a profile of its own (checked against it). The stock profile's readings have to show it first."""
        return self.trusts(reading) or self.profile.name != "stock"

    @property
    def airborne(self) -> bool:
        return self.own is not None and not self.own.on_ground

    @property
    def flaps_known(self) -> bool:
        """The sim's flap handle index means this aircraft's detents: not in an aircraft whose profile says it doesn't
        read them, nor when the sim counts other positions than the profile names (an add-on's handle, 0-8)."""
        p = self.profile
        if not p.reads_flaps:
            return False
        if p.detents and self.systems is not None and self.systems.flaps_positions:
            return self.systems.flaps_positions in (len(p.detents) - 1, len(p.detents))
        return True

    @property
    def flaps_index(self) -> int | None:
        """The flap handle's detent, None where it can't be told."""
        return self.own.flaps_index if self.own is not None and self.flaps_known else None

    @property
    def autopilot_known(self) -> bool:
        return self.profile.reads_autopilot

    @property
    def flap_positions(self) -> int:
        if self.systems is not None and self.systems.flaps_positions:
            return self.systems.flaps_positions
        return max(len(self.profile.detents) - 1, 0)


def as_read(ev: Any, profile: Profile, values: dict[str, float]) -> Any:
    """An OwnshipState or AircraftSystems with the fields this aircraft's profile reads from its own variables (the
    FSLabs's lights, levers and FCU windows) as they say."""
    if not profile.reads or not values:
        return ev
    changes: dict[str, Any] = {}
    for name, expr in profile.reads.items():
        if name in type(ev).__struct_fields__ and (value := read_var(expr, values)) is not None:
            kind = type(getattr(ev, name))
            changes[name] = bool(value) if kind is bool else int(round(value)) if kind is int else float(value)
    return replace_struct(ev, **changes) if changes else ev


def _readings(c: Cockpit) -> dict[str, object]:
    out: dict[str, object] = {}
    if c.own is not None:
        o = c.own
        out.update(flaps=o.flaps_index, gear=o.gear_down, parking_brake=o.parking_brake, squawk=o.squawk,
                   com1=round(o.com1_mhz, 3))
    if c.systems is not None:
        y = c.systems
        out.update(ap_master=y.ap_master, ap_heading_sel=y.ap_heading_sel, ap_altitude_sel=y.ap_altitude_sel,
                   ap_speed_sel=y.ap_speed_sel, ap_vs_sel=y.ap_vs_sel, ap_modes=(y.ap_heading, y.ap_nav, y.ap_approach,
                                                                                 y.ap_altitude, y.ap_vs, y.ap_flc),
                   athr=y.athr_armed, spoilers_armed=y.spoilers_armed, spoilers=round(y.spoilers_pct, -1),
                   autobrake=y.autobrake, com1_standby=round(y.com1_standby_mhz, 3),
                   **{f"light_{n}": getattr(y, f"light_{n}") for n in LIGHT_EVENTS})
    return out


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
    reads: str = ""  # the reading the check looks at (``Cockpit.moved``): believed once it's been seen to move
    check_s: float = 0.0  # how long the sim takes to show it (0: the copilot's usual)


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


def _clicks(custom: Write, key: str, target: float) -> tuple[SimCommand, ...]:
    """An add-on's clickspots turned until its variable reads ``target`` (each knob in turn), then any presses."""
    knobs = list(custom.knobs) or ([{"up": custom.click_up, "down": custom.click_down, "step": custom.step,
                                     "learn": custom.learn}] if custom.click_up or custom.click_down else [])
    out: list[SimCommand] = [
        TurnKnob(name=f"{key} {i + 1}" if len(knobs) > 1 else key, var=k.get("var", custom.var), unit="number",
                 target=float(k["target"]) if "target" in k else target,
                 step=float(k.get("step", 1.0)), wrap=360.0 if key == "heading" else 0.0, event=custom.click_event,
                 up=int(k.get("up", 0)), down=int(k.get("down", 0)), learn=bool(k.get("learn", False)),
                 div=float(k.get("div", 0.0)), mod=float(k.get("mod", 0.0)), burst=int(k.get("burst", 20)))
        for i, k in enumerate(knobs)]
    if custom.press:
        out.append(ClickSequence(name=key, codes=tuple(custom.press), event=custom.click_event))
    return tuple(out)


def _keys(custom: Write, key: str, text: str) -> tuple[SimCommand, ...]:
    """Typed on the aircraft's keypad: each character's key, then its release."""
    codes: list[int] = []
    for ch in text:
        code = custom.keys[ch]
        codes += [code, code + custom.release] if custom.release else [code]
    return (ClickSequence(name=key, codes=tuple(codes), event=custom.click_event),)


def _write(profile: Profile, key: str, default: SimCommand, value: float | None = None,
           on: bool | None = None) -> SimCommand | tuple[SimCommand, ...]:
    """The profile's own way for this action (an add-on's event, L:var or clickspots), else the standard event."""
    custom: Write | None = profile.actions.get(key)
    if custom is None:
        return default
    if custom.clicks:
        target = custom.on if on or (on is None and value is None) else custom.off if on is not None             else float(value) * custom.scale  # (a lever with no value, the gear: where ``on`` says)
        return _clicks(custom, key, target)
    if custom.knob:
        var, unit, wrap = KNOB_READS.get(key, ("", "number", 0.0))
        return TurnKnob(name=custom.knob, var=custom.var or var, unit=custom.var_unit or unit, target=float(value or 0.0),
                        step=custom.step, wrap=wrap)
    if custom.encoder and custom.lvar:
        clicks = max(0, -(-(float(value or 0.0) - custom.low) // custom.step))  # rounded up: from 100, a click is 1,000
        return NudgeVar(name=custom.lvar, deltas=(custom.stop, clicks))
    if custom.input:
        level = custom.on if on else custom.off if on is not None else (value or 0.0)
        return SetInputEvent(name=custom.input, value=float(level))
    if custom.lvar:
        level = custom.on if on else custom.off if on is not None else (value or 0.0)
        return SetSimVar(name=custom.lvar, unit=custom.unit, value=float(level))
    if custom.event:
        return SendSimEvent(name=custom.event, value=custom.value if custom.value is not None else int(value or 0))
    return default


def plan(cmd: Command, c: Cockpit) -> Plan | str:
    """What to send for ``cmd``, or why it can't be done on this aircraft ("unable ...")."""
    got = _plan(cmd, c)
    if not isinstance(got, Plan):
        return got
    if any(isinstance(w, tuple) for w in got.writes):  # an add-on's clickspots: several commands for one action
        got = replace(got, writes=tuple(x for w in got.writes for x in (w if isinstance(w, tuple) else (w,))))
    if any(isinstance(w, (SetSimVar, SetInputEvent)) and "," in w.name for w in got.writes):  # a switch each side: "A, B"
        got = replace(got, writes=tuple(x for w in got.writes for x in (
            [replace_struct(w, name=n.strip()) for n in w.name.split(",")]
            if isinstance(w, (SetSimVar, SetInputEvent)) else [w])))
    if got.reads and got.reads in c.profile.unread:
        got = replace(got, check=lambda _c: None, already="")
    if any(isinstance(w, (TurnKnob, ClickSequence)) for w in got.writes):
        return replace(got, check_s=KNOB_CHECK_S)
    return got


def _plan(cmd: Command, c: Cockpit) -> Plan | str:  # noqa: C901 - one branch per command, flat
    p = c.profile
    a, v = cmd.action, cmd.value
    if a == "gear":
        down = v == "down"
        event = SendSimEvent(name="GEAR_DOWN" if down else "GEAR_UP")
        return Plan(a, v, (_write(p, f"gear_{v}", event),), _own(lambda o: o.gear_down == down), f"Gear {v}.",
                    already=f"Gear's already {v}.", reads="gear")
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
        flaps_check: Check = lambda c2: None if not c2.flaps_known or c2.own is None else c2.own.flaps_index == index  # noqa: E731
        return Plan(a, name, (_write(p, f"flaps_{index}", event, index),), flaps_check,
                    said, spoken, already=f"Flaps are already {name.replace('+F', ' plus F')}.", reads="flaps")
    if a == "light":
        on = v == "on"
        event = SendSimEvent(name=LIGHT_EVENTS[cmd.target], value=int(on))
        what = LIGHT_NAMES[cmd.target]
        return Plan(a, f"{cmd.target} {v}", (_write(p, f"light_{cmd.target}", event, on=on),),
                    _sys(f"light_{cmd.target}", on), f"{what.capitalize()} {v}.", already=f"{what.capitalize()} already {v}.",
                    reads=f"light_{cmd.target}")
    if a == "spoilers":
        if v in ("arm", "disarm"):
            arm = v == "arm"
            event = SendSimEvent(name="SPOILERS_ARM_ON" if arm else "SPOILERS_ARM_OFF")
            return Plan(a, v, (_write(p, f"spoilers_{v}", event, on=arm),), _sys("spoilers_armed", arm),
                        "Spoilers armed." if arm else "Spoilers disarmed.",
                        already="Spoilers are already armed." if arm else "Spoilers aren't armed.", reads="spoilers_armed")
        out = v == "extend"
        event = SendSimEvent(name="SPOILERS_ON" if out else "SPOILERS_OFF")
        check: Check = lambda c2: None if c2.systems is None else (c2.systems.spoilers_pct > 40 if out else c2.systems.spoilers_pct < 5)  # noqa: E731
        return Plan(a, v, (_write(p, f"spoilers_{v}", event, on=out),), check,
                    "Speedbrakes extended." if out else "Speedbrakes retracted.")
    if a == "autopilot":
        on = v == "on"
        event = SendSimEvent(name="AUTOPILOT_ON" if on else "AUTOPILOT_OFF")
        return Plan(a, v, (_write(p, f"autopilot_{v}", event, on=on),), _sys("ap_master", on),
                    "Autopilot on." if on else "Autopilot off.", already=f"Autopilot's already {v}.", reads="ap_master")
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
                    f"{MODE_NAMES[mode].capitalize()}.", already=f"Already in {MODE_NAMES[mode]}.", reads="ap_modes")
    if a == "heading":
        deg = int(v)
        return Plan(a, v, (_write(p, "heading", SendSimEvent(name="HEADING_BUG_SET", value=deg), deg),),
                    _sys("ap_heading_sel", float(deg % 360)), f"Heading {deg:03d} set.",
                    f"heading {speech.heading(deg)} set", reads="ap_heading_sel")
    if a == "altitude":
        ft = int(v)
        first: tuple[SimCommand, ...] = ()
        if (w := p.actions.get("altitude")) is not None and w.fine_var and ft % 1000 \
                and c.vars.get(w.fine_var, w.fine_value) != w.fine_value:
            first = (ClickSequence(name="altitude 100s", codes=tuple(w.fine_codes), event=w.click_event),)
        return Plan(a, v, (*first, _write(p, "altitude", SendSimEvent(name="AP_ALT_VAR_SET_ENGLISH", value=ft), ft)),
                    _sys("ap_altitude_sel", float(ft)), f"{speech.altitude_display(ft)} set.",
                    f"{speech.altitude(ft)} set", reads="ap_altitude_sel")
    if a == "speed":
        kt = int(v)
        return Plan(a, v, (_write(p, "speed", SendSimEvent(name="AP_SPD_VAR_SET", value=kt), kt),),
                    _sys("ap_speed_sel", float(kt)), f"Speed {kt} set.", f"speed {speech.speed(kt)} set", reads="ap_speed_sel")
    if a == "vs":
        fpm = int(v)
        said = f"{'Minus' if fpm < 0 else 'Plus'} {abs(fpm):,} set."
        spoken = f"vertical speed {'minus' if fpm < 0 else 'plus'} {speech._feet(abs(fpm))} set" if abs(fpm) >= 100 else "vertical speed zero set"
        check_vs: Check = lambda c2: None if c2.systems is None else abs(c2.systems.ap_vs_sel - fpm) <= 50  # noqa: E731
        return Plan(a, v, (_write(p, "vs", SendSimEvent(name="AP_VS_VAR_SET_ENGLISH", value=fpm), fpm),), check_vs,
                    said, spoken, reads="ap_vs_sel")
    if a == "squawk":
        w = p.actions.get("squawk")
        typed = _keys(w, "squawk", v) if w is not None and w.keys else None
        return Plan(a, v, (typed or _write(p, "squawk", SendSimEvent(name="XPNDR_SET", value=int(v, 16))),),
                    _own(lambda o: o.squawk == v), f"Squawk {v} set.", f"squawk {speech.squawk(v)} set",
                    already=f"Squawk's already {v}.", reads="squawk")
    if a == "com_active":
        mhz = float(v)
        hz = channel_khz(mhz) * 1000
        return Plan(a, v, (_write(p, "com_active", SetComFrequency(hz=hz), hz / 1000),),
                    _own(lambda o: channel_khz(o.com1_mhz) == channel_khz(mhz)), f"{speech.frequency_display(mhz)} set.",
                    f"{speech.frequency(mhz)} set")
    if a == "com_standby":
        mhz = float(v)
        hz = channel_khz(mhz) * 1000
        check_stby: Check = lambda c2: None if c2.systems is None else channel_khz(c2.systems.com1_standby_mhz) == channel_khz(mhz)  # noqa: E731
        return Plan(a, v, (_write(p, "com_standby", SendSimEvent(name="COM_STBY_RADIO_SET_HZ", value=hz), hz / 1000),),
                    check_stby,
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
        w = p.actions.get("altimeter")
        if w is not None and w.clicks:
            writes = _baro(w, c, hpa, std=v == "29.92" and cmd.target != "hpa")
        else:
            writes = (_write(p, "altimeter", SendSimEvent(name="KOHLSMAN_SET", value=round(hpa * 16), index=2), hpa),)
        return Plan(a, v, writes, lambda c2: None, said.replace(" set.", " set on my side."), spoken + " on my side")
    if a == "parking_brake":
        on = v == "on"
        event = SendSimEvent(name="PARKING_BRAKE_SET", value=int(on))
        return Plan(a, v, (_write(p, f"parking_brake_{v}", event, on=on),), _own(lambda o: o.parking_brake == on),
                    "Parking brake set." if on else "Parking brake released.",
                    already="Parking brake's already set." if on else "Parking brake's already off.", reads="parking_brake")
    if a == "autobrake":
        return _autobrake(cmd, c)
    return f"Unable, I can't do {cmd} yet."


def _baro(w: Write, c: Cockpit, hpa: float, *, std: bool) -> tuple[SimCommand, ...]:
    """The first officer's baro knob (an add-on's): pulled to STD, or pushed back from it and turned to the QNH, in the
    window's own unit (``fine_var`` reads 1 in inches: hundredths of an inch; else hectopascals)."""
    is_std = c.vars.get(w.var.replace("_BARO", "_BARO_STD"), 0.0) >= 0.5
    if std:
        return () if is_std else (ClickSequence(name="altimeter std", codes=tuple(w.press), event=w.click_event),)
    inches = c.vars.get(w.fine_var, 1.0) >= 0.5 if w.fine_var else True
    target = round(hpa / 33.8639 * 100) if inches else round(hpa)
    push = (ClickSequence(name="altimeter qnh", codes=tuple(w.fine_codes), event=w.click_event),) if is_std else ()
    return (*push, TurnKnob(name="altimeter", var=w.var, unit="number", target=float(target), step=1.0,
                            event=w.click_event, up=w.click_up, down=w.click_down))


AUTOBRAKE_EVENTS = {"off": "AUTOBRAKE_DISARM", "low": "AUTOBRAKE_LO_SET", "medium": "AUTOBRAKE_MED_SET",
                    "max": "AUTOBRAKE_HI_SET"}


def _autobrake(cmd: Command, c: Cockpit) -> Plan | str:
    """The autobrake to a setting: the profile's own names where it has them ("low" is position 1), else the sim's
    standard events; a numbered setting (a 737's "3") by its position."""
    p, level = c.profile, cmd.value
    if not level:
        return "Which setting for the autobrake?"
    names = list(p.autobrake)
    index = names.index(level) if level in names else (int(level) if level.isdigit() and names and int(level) < len(names) else None)
    if level in AUTOBRAKE_EVENTS:
        event = SendSimEvent(name=AUTOBRAKE_EVENTS[level])
    elif level.isdigit():
        event = SendSimEvent(name="SET_AUTOBRAKE_CONTROL", value=int(level))
    else:
        return f"Unable, there's no autobrake {level} on this aircraft."
    check: Check = (lambda c2: None if c2.systems is None or c2.systems.autobrake < 0 else c2.systems.autobrake == index) \
        if index is not None else (lambda c2: None)
    said = "Autobrake off." if level == "off" else f"Autobrake {level}."
    return Plan("autobrake", level, (_write(p, f"autobrake_{level}", event),), check, said,
                already=f"Autobrake's already {level}.", reads="autobrake")


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
        current = c.flaps_index or 0
        if index is not None and index > current and c.airborne and (limit := p.vfe_for(index)) and ias > limit + SPEED_MARGIN_KT:
            name = p.detent_name(index, c.flap_positions)
            return Verdict("refuse", f"Unable, speed {ias:.0f}, flaps {name} limit {limit:.0f}.")
    if a == "spoilers" and v == "extend" and c.airborne and own is not None and own.alt_agl_ft < SPOILERS_MIN_AGL:
        return Verdict("refuse", "Negative, not below 1,000 feet.")
    if a == "parking_brake" and v == "on" and own is not None and own.gs_kt > MOVING_KT:
        return Verdict("refuse", "Negative, not while we're moving.")
    if a == "autobrake" and rolling:
        return Verdict("refuse", "Not on the roll.")
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
