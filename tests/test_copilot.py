"""The copilot: readbacks, frequency changes and (in full mode) every call.

Whole-flight behaviour is pinned by tests/scenarios/copilot_*.golden.txt.
"""

import asyncio
from pathlib import Path

import msgspec

from localtc.airports import load_airport_dir
from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.service import AtcService
from localtc.bus import EventBus
from localtc.copilot import Copilot, Note, Say, Tune
from localtc.replay import Recording
from localtc.scenario import Scenario, run
from localtc.sim_api import (
    AirportData,
    AtcAlert,
    AtcTransmission,
    OwnshipState,
    SetComFrequency,
    SIM_EVENT_TYPES,
    Transcript,
)

FIXTURES = Path(__file__).parent / "fixtures"
FLIGHT = FIXTURES / "ifr_kpae_kbfi"


def test_tune_expands_25khz_channel_names():
    assert Tune(0.0, 120.42).hz == 120_425_000
    assert Tune(0.0, 119.3).hz == 119_300_000
    assert Tune(0.0, 120.43).hz == 120_430_000  # an 8.33 kHz channel stays itself


def test_assist_mode_reads_back_and_changes_frequency_but_makes_no_calls():
    scenario = msgspec.convert({
        "scenario": {"recording": str(FLIGHT), "airports": [str(FIXTURES / "airports")], "end_t": 400},
        "flight": {"destination": "KBFI", "cruise_ft": 5000, "callsign": "N172LT"},
        "atc": {"seed": 7},
        "pilot": [
            {"when": {"phase": "PARKED", "after_s": 5}, "tune": "clearance",
             "say": "Paine Clearance, {callsign}, IFR to Boeing Field, ready to copy"},
            {"when": {"phase": "PARKED", "cleared": "ifr"}, "tune": "ground", "delay_s": 5,
             "say": "Paine Ground, {callsign_short}, ready to taxi"},
        ],
    }, Scenario)
    lines = run(scenario, Path.cwd(), copilot="assist").lines
    pilot = [line.split(": ", 1)[1] for line in lines if " PILOT " in line]
    assert pilot == [
        "Paine Clearance, november one seven two lima tango, IFR to Boeing Field, ready to copy",
        "Cleared to Boeing Field as filed, climb and maintain five thousand, expect five thousand one zero minutes "
        "after, departure one two four point six seven five, squawk three two six zero, Cessna two lima tango.",
        "Paine Ground, Cessna two lima tango, ready to taxi",
        "Runway three four left, taxi via charlie, alpha, alpha one, Cessna two lima tango.",
        "Paine Tower one two zero point two, Cessna two lima tango.",  # the handoff at the hold short line
    ]
    assert any("TUNE      COM1 120.2" in line for line in lines)  # tuned tower for the pilot
    assert not any("ready for departure" in line for line in lines)  # but the call to tower is the pilot's


def test_the_copilot_stops_repeating_itself():
    engine = AtcEngine(EngineConfig(callsign="N172LT"))
    copilot = Copilot(engine, mode="assist", delay_s=(1.0, 1.0))
    copilot._last_said = "Paine Tower one two zero point two, Cessna two lima tango."
    said = []
    for n in range(4):
        copilot.observe(AtcTransmission(t=10.0 * n, station="Paine Tower", frequency_mhz=120.2, text="say again",
                                        instruction_id="common.say_again"))
        said += copilot.due(10.0 * n + 5)
    assert [type(a) for a in said] == [Say, Say, Note, Note]


def test_the_service_carries_out_the_copilots_actions():
    """Live wiring: Say becomes a Transcript on the bus, Tune becomes a SetComFrequency sim command."""

    class Source:
        def __init__(self) -> None:
            self.commands: list = []

        async def send(self, command) -> None:
            self.commands.append(command)

    async def main():
        bus = EventBus()
        engine = AtcEngine(EngineConfig(destination="KBFI", cruise_ft=5000, callsign="N172LT", seed=7, unscripted=False))
        for airport in load_airport_dir(FIXTURES / "airports"):
            engine.handle(AirportData(t=0.0, airport=airport))
        source = Source()
        service = AtcService(engine, bus, source, copilot=Copilot(engine, mode="full"))
        heard = bus.subscribe(Transcript, AtcTransmission, AtcAlert)
        task = asyncio.create_task(service.run())
        com1 = None
        for event in Recording(FLIGHT).events():
            if not isinstance(event, SIM_EVENT_TYPES) or event.t > 40:
                continue
            if isinstance(event, OwnshipState):
                tuned = [c for c in source.commands if isinstance(c, SetComFrequency)]
                com1 = tuned[-1].hz / 1e6 if tuned else com1  # the sim follows the tune command
                event = msgspec.structs.replace(event, com1_mhz=com1 or event.com1_mhz)
            bus.publish(event)
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        bus.close()
        await task
        return source.commands, [e async for e in heard]

    commands, events = asyncio.run(main())
    assert [c.hz for c in commands][:2] == [127_250_000, 121_800_000]  # clearance delivery, then ground
    pilot = [e.text for e in events if isinstance(e, Transcript)]
    assert pilot[0] == "Paine Clearance, N172LT, IFR to Boeing Field, ready to copy"
    assert pilot[1].startswith("Cleared to Boeing Field as filed")
    assert any(isinstance(e, AtcTransmission) and e.instruction_id == "ground.taxi_out" for e in events)
