"""The checks run against the live sim (localtc.simcheck, ``localtc debug ...``), here against a fake sim: what's sent,
how the sim's answer is read, what's put back, and what the report says. The live run is tests/test_sim_live.py."""

import asyncio
import json

import msgspec
import pytest

from localtc import simcheck
from localtc.sim_api import (
    TurnKnob,
    AircraftIdentity,
    AircraftInputEvents,
    AircraftSystems,
    AiObjectAssigned,
    EnumerateModels,
    ModelList,
    OwnshipState,
    RemoveAiAircraft,
    SendSimEvent,
    SessionInfo,
    SetComFrequency,
    SpawnAiAircraft,
    TrafficIdentity,
    TrafficSnapshot,
    TrafficTarget,
)

LIGHTS = {"LANDING_LIGHTS_SET": "light_landing", "TAXI_LIGHTS_SET": "light_taxi", "STROBES_SET": "light_strobe",
          "BEACON_LIGHTS_SET": "light_beacon", "NAV_LIGHTS_SET": "light_nav", "LOGO_LIGHTS_SET": "light_logo"}


class FakeSim:
    """A parked aircraft that does what it's told, except the events in ``deaf`` (an aircraft whose panel ignores
    them, as some MSFS 2024 aircraft ignore the old key events)."""

    def __init__(self, *, deaf=(), airborne=False, input_events=("LIGHTING_LANDING_1", "LIGHTING_TAXI_1")):
        self.deaf = set(deaf)
        self.own = OwnshipState(t=0, lat=47.45, lon=-122.31, alt_msl_ft=430, alt_indicated_ft=430,
                                alt_agl_ft=0 if not airborne else 3000, altimeter_inhg=29.92, hdg_mag=160, hdg_true=176,
                                ias_kt=0 if not airborne else 200, gs_kt=0 if not airborne else 200, vs_fpm=0,
                                on_ground=not airborne, squawk="1200", xpdr_mode="alt", com1_mhz=121.7, com2_mhz=121.5,
                                parking_brake=True, altimeter_setting_inhg=29.92)
        self.sys = AircraftSystems(t=0, flaps_positions=4, light_nav=True, light_beacon=False, ap_heading_sel=160,
                                   ap_altitude_sel=5000, ap_speed_sel=0, com1_standby_mhz=118.0, engines_running=0)
        self.input_events = input_events
        self.sent = []
        self.ai = {}
        self.queue = asyncio.Queue()
        self.stopped = False

    async def start(self):
        for ev in (self.own, self.sys, AircraftIdentity(t=0, title="Airbus A320neo Asobo", atc_model="A20N"),
                   AircraftInputEvents(t=0, names=self.input_events)):
            self.queue.put_nowait(ev)
        self.queue.put_nowait(self.snapshot())
        self.queue.put_nowait(self.snapshot())
        self.queue.put_nowait(TrafficIdentity(t=0, object_id=7, title="Boeing 737-800", destination="KPDX", state="sleep"))
        return SessionInfo(source_kind="live", sim_product="FakeSim", sim_version="1")

    async def stop(self):
        self.stopped = True

    async def events(self):
        while True:
            yield await self.queue.get()

    def snapshot(self):
        still = {"alt_ft": 430.0, "hdg_true": 0.0, "gs_kt": 0.0, "on_ground": True}
        targets = [TrafficTarget(object_id=7, atc_id="N1", lat=47.46, lon=-122.30, **still)]
        targets += [TrafficTarget(object_id=oid, atc_id=s.tail, lat=s.lat, lon=s.lon, **still) for oid, s in self.ai.items()]
        return TrafficSnapshot(t=0, targets=tuple(targets))

    async def send(self, cmd):
        self.sent.append(cmd)
        own, sys_ = {}, {}
        if isinstance(cmd, TurnKnob) and cmd.name not in self.deaf:  # turned all the way at once
            sys_[{"AUTOPILOT HEADING LOCK DIR": "ap_heading_sel", "AUTOPILOT ALTITUDE LOCK VAR": "ap_altitude_sel",
                  "AUTOPILOT AIRSPEED HOLD VAR": "ap_speed_sel", "AUTOPILOT VERTICAL HOLD VAR": "ap_vs_sel"}[cmd.var]] = cmd.target
        if isinstance(cmd, SendSimEvent) and cmd.name not in self.deaf:
            n, v = cmd.name, cmd.value
            if n in LIGHTS:
                sys_[LIGHTS[n]] = bool(v)
            elif n in ("FLAPS_UP", "FLAPS_1", "FLAPS_2", "FLAPS_3", "FLAPS_DOWN"):
                own["flaps_index"] = ("FLAPS_UP", "FLAPS_1", "FLAPS_2", "FLAPS_3", "FLAPS_DOWN").index(n)
            elif n in ("SPOILERS_ARM_ON", "SPOILERS_ARM_OFF"):
                sys_["spoilers_armed"] = n.endswith("ON")
            elif n == "HEADING_BUG_SET":
                sys_["ap_heading_sel"] = float(v)
            elif n == "AP_ALT_VAR_SET_ENGLISH":
                sys_["ap_altitude_sel"] = float(v)
            elif n == "AP_SPD_VAR_SET":
                sys_["ap_speed_sel"] = float(v)
            elif n == "AP_VS_VAR_SET_ENGLISH":
                sys_["ap_vs_sel"] = float(v)
            elif n == "XPNDR_SET":
                own["squawk"] = f"{v:04x}"
            elif n == "COM_STBY_RADIO_SET_HZ":
                sys_["com1_standby_mhz"] = v / 1e6
            elif n == "PARKING_BRAKE_SET":
                own["parking_brake"] = bool(v)
            elif n in ("AUTOPILOT_ON", "AUTOPILOT_OFF"):
                sys_["ap_master"] = n.endswith("ON")
        elif isinstance(cmd, SetComFrequency):
            own["com1_mhz"] = cmd.hz / 1e6
        elif isinstance(cmd, SpawnAiAircraft):
            oid = 500 + len(self.ai)
            self.ai[oid] = cmd
            self.queue.put_nowait(AiObjectAssigned(t=0, request_id=cmd.request_id, object_id=oid))
        elif isinstance(cmd, RemoveAiAircraft):
            self.ai.pop(cmd.object_id, None)
        elif isinstance(cmd, EnumerateModels):
            self.queue.put_nowait(ModelList(t=0, models=(("Airbus A320neo Asobo", ""), ("FSLTL_B738_SWA", "Southwest"))))
        if own:
            self.own = msgspec.structs.replace(self.own, **own)
            self.queue.put_nowait(self.own)
        if sys_:
            self.sys = msgspec.structs.replace(self.sys, **sys_)
            self.queue.put_nowait(self.sys)
        self.queue.put_nowait(self.snapshot())


def run(sim, check, **kw):
    async def go():
        async with simcheck.Probe(sim, readback_s=0.3) as probe:
            return await check(probe, **kw)
    return asyncio.run(go())


def by_name(report):
    return {s.name: s for s in report.steps}


def test_hands_moves_each_control_reads_it_back_and_puts_it_back():
    sim = FakeSim()
    report = run(sim, simcheck.check_hands)
    steps = by_name(report)
    assert report.aircraft == "Airbus A320neo Asobo" and report.profile == "Airbus A320neo"
    assert steps["landing light on"].result == "pass" and steps["landing light on"].restored
    assert steps["nav light off"].result == "pass"  # it was on: turned off, then on again
    assert steps["flaps 1"].result == "pass" and "event FLAPS_1 0" in steps["flaps 1"].sent
    assert steps["squawk"].result == "pass" and sim.own.squawk == "1200"  # put back
    assert steps["COM1 active"].result == "pass" and abs(sim.own.com1_mhz - 121.7) < 0.001
    assert steps["altimeter (first officer's)"].result == "sent"  # the sim can't show the first officer's
    assert steps["parking brake release"].result == "pass" and sim.own.parking_brake  # engines off: tried, and set again
    assert steps["autopilot on"].result == "skip"  # only when asked
    assert not report.failed
    assert not any(isinstance(c, SendSimEvent) and c.name.startswith("GEAR") for c in sim.sent)  # never the gear
    assert sim.stopped
    assert sim.sys.light_nav and not sim.sys.light_landing  # as they were


def test_a_control_the_aircraft_ignores_is_a_failure_with_what_was_sent():
    report = run(FakeSim(deaf={"LOGO_LIGHTS_SET", "BEACON_LIGHTS_SET"}), simcheck.check_hands)
    failed = {s.name: s for s in report.failed}
    assert set(failed) == {"logo light on", "beacon light on"}
    assert failed["logo light on"].sent[0] == "event LOGO_LIGHTS_SET 1" and failed["logo light on"].detail
    text = report.text()
    assert "FAIL  logo light on  <- event LOGO_LIGHTS_SET 1" in text and "2 fail" in text
    data = json.loads(report.to_json())
    assert data["check"] == "hands" and any(s["result"] == "fail" for s in data["steps"])


def test_only_parked():
    report = run(FakeSim(airborne=True), simcheck.check_hands)
    assert [s.name for s in report.steps] == ["parked"] and report.failed


def test_only_some_controls():
    report = run(FakeSim(), simcheck.check_hands, only=("light",))
    assert report.steps and all("light" in s.name for s in report.steps)


def test_the_aircraft_check_lists_its_input_events_and_checks_the_profiles(monkeypatch):
    from localtc.crew import profiles

    a320 = next(p for p in profiles.load_all(None) if p.match and "A20N" in p.match)
    from dataclasses import replace

    with_inputs = replace(a320, actions={"light_landing": profiles.Write(input="LIGHTING_LANDING_1"),
                                         "light_logo": profiles.Write(input="LIGHTING_LOGO_1")})
    monkeypatch.setattr(profiles, "load_all", lambda user_dir=None: [with_inputs, profiles.Profile()])
    report = run(FakeSim(), simcheck.check_aircraft, wait_s=1.0)
    steps = by_name(report)
    assert report.input_events == ["LIGHTING_LANDING_1", "LIGHTING_TAXI_1"]
    assert steps["profile light_landing: input LIGHTING_LANDING_1"].result == "pass"
    assert steps["profile light_logo: input LIGHTING_LOGO_1"].result == "fail"


def test_hands_go_through_the_profiles_input_event(monkeypatch):
    from dataclasses import replace

    from localtc.crew import profiles

    a320 = next(p for p in profiles.load_all(None) if p.match and "A20N" in p.match)
    with_inputs = replace(a320, actions={"light_landing": profiles.Write(input="LIGHTING_LANDING_1")})
    monkeypatch.setattr(profiles, "load_all", lambda user_dir=None: [with_inputs, profiles.Profile()])
    report = run(FakeSim(), simcheck.check_hands, only=("landing",))
    [step] = report.steps
    assert step.sent[0] == "input LIGHTING_LANDING_1 = 1" and step.result == "fail"  # the fake has no input events


def test_traffic_sees_the_ai_creates_one_and_takes_it_out():
    sim = FakeSim()
    report = run(sim, simcheck.check_traffic)
    steps = by_name(report)
    assert steps["traffic snapshots"].result == "pass"
    assert steps["traffic identity"].result == "pass" and "sleep" in steps["traffic identity"].detail
    assert steps["installed models"].detail == "2 models, 1 FSLTL"
    assert steps["create a parked aircraft"].result == "pass" and steps["create a parked aircraft"].restored
    assert steps["... seen in the traffic"].result == "pass" and steps["... removed"].result == "pass"
    [spawn] = [c for c in sim.sent if isinstance(c, SpawnAiAircraft)]
    assert spawn.title == "Airbus A320neo Asobo" and spawn.on_ground and not sim.ai
    assert simcheck._nm(spawn.lat, spawn.lon, 47.45, -122.31) == pytest.approx(120 / 1852, abs=0.01)


def test_a_report_is_written(tmp_path):
    report = simcheck.Report("hands", aircraft="X")
    report.add(simcheck.Step("a", "pass"))
    path = simcheck.write(report, tmp_path / "r.json")
    assert json.loads(path.read_text())["steps"][0]["name"] == "a"


def test_the_command_lines_entry_runs_a_check_on_any_source():
    from localtc.app import sim_check
    from localtc.config import Config

    report = asyncio.run(sim_check(Config(), "hands", source=FakeSim(), only=("squawk",)))
    assert [s.name for s in report.steps] == ["squawk"] and report.steps[0].result == "pass"
