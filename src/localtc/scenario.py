"""Offline ATC scenarios: a recording, a flight intent, and a scripted pilot, run through ``AtcEngine``.

    localtc atc tests/fixtures/ifr_kpae_kbfi --scenario tests/scenarios/ifr_happy_path.toml

A scenario TOML::

    [scenario]
    recording = "../fixtures/ifr_kpae_kbfi"     # relative to the scenario file
    airports = ["../fixtures/airports"]

    [flight]
    destination = "KBFI"
    cruise_ft = 5000

    [[pilot]]
    when = { phase = "PARKED", after_s = 10 }   # or on = "<instruction id>" to react to ATC
    tune = "clearance"                          # controller name, a frequency, or "handoff"
    say = "Paine Clearance, {callsign}, IFR to Boeing Field, ready to copy"

Placeholders in ``say``: {callsign}, {callsign_short}, {readback} (the ideal
readback of the instruction that triggered the rule), {alt}, {departure_runway},
{arrival_runway}, plus anything in ``[scenario] vars``.

``[scenario] copilot = "full"`` (or "assist") lets ``localtc.copilot`` work the radio
instead of, or alongside, the scripted ``[[pilot]]`` rules.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import msgspec

from localtc.airports import load_airport_dir
from localtc.atc_core.engine import AtcEngine
from localtc.atc_core.llm import LlmPhraser
from localtc.atc_core.readback import Interpreter
from localtc.atc_core.phraseology import speech
from localtc.config import AtcConfig, FlightConfig
from localtc.replay import Recording
from localtc.sim_api import (
    SIM_EVENT_TYPES,
    AirportData,
    AtcAlert,
    AtcTransmission,
    BusEvent,
    OwnshipState,
    PhaseChanged,
    LlmExchange,
    ReadbackEvaluated,
    Transcript,
)


class When(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    phase: str | None = None
    after_s: float = 0.0  # seconds since entering ``phase``
    min_t: float | None = None
    final_nm_below: float | None = None
    agl_above: float | None = None
    cleared: str | None = None  # clearance kind whose readback must be correct, e.g. "ifr"


class PilotRule(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    say: str
    when: When | None = None  # alone: fires when true; with ``on``: an extra condition
    on: str | None = None  # AtcTransmission.instruction_id that triggers this rule
    tune: str | float | None = None
    delay_s: float = 2.0
    repeat: bool = False


class ScenarioMeta(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    name: str = ""
    recording: str
    airports: list[str] = []
    end_t: float | None = None
    tail_s: float = 30.0
    vars: dict[str, str] = {}
    copilot: str = ""  # "", "assist" or "full"


class Scenario(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    scenario: ScenarioMeta
    flight: FlightConfig = msgspec.field(default_factory=FlightConfig)
    atc: AtcConfig = msgspec.field(default_factory=AtcConfig)
    pilot: list[PilotRule] = []


def load_scenario(path: str | Path) -> Scenario:
    return msgspec.convert(tomllib.loads(Path(path).read_text(encoding="utf-8")), Scenario)


@dataclass
class ScenarioResult:
    lines: list[str] = field(default_factory=list)
    outputs: list[BusEvent] = field(default_factory=list)
    engine: AtcEngine | None = None

    @property
    def transcript(self) -> str:
        return "\n".join(self.lines) + "\n"


def format_output(event: BusEvent) -> str | None:
    if isinstance(event, AtcTransmission):
        return f"[{event.t:8.1f}] ATC       {event.station} {speech.frequency_display(event.frequency_mhz)}: {event.text}"
    if isinstance(event, PhaseChanged):
        return f"[{event.t:8.1f}] PHASE     {event.previous or '-'} -> {event.phase} ({event.reason})"
    if isinstance(event, ReadbackEvaluated):
        detail = ""
        if event.missing:
            detail += f" missing={','.join(event.missing)}"
        if event.mismatched:
            detail += " heard=" + ",".join(f"{k}:{v}" for k, v in event.mismatched.items())
        return f"[{event.t:8.1f}] READBACK  {event.instruction_id} {event.status}{detail}"
    if isinstance(event, AtcAlert):
        return f"[{event.t:8.1f}] ALERT     {event.kind}: {event.detail}"
    if isinstance(event, LlmExchange):
        return f"[{event.t:8.1f}] LLM       {event.purpose} {event.outcome}: {event.response}" + (
            f" ({event.detail})" if event.detail else "")
    return None


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def run_scenario(
    path: str | Path,
    *,
    recording: str | Path | None = None,
    interpreter: Interpreter | None = None,
    phraser: LlmPhraser | None = None,
    copilot: str | None = None,
) -> ScenarioResult:
    path = Path(path)
    return run(load_scenario(path), path.parent, recording=recording, interpreter=interpreter, phraser=phraser,
               copilot=copilot)


def run(
    scenario: Scenario,
    base: Path,
    *,
    recording: str | Path | None = None,
    interpreter: Interpreter | None = None,
    phraser: LlmPhraser | None = None,
    copilot: str | None = None,
) -> ScenarioResult:
    """Run a scenario; ``base`` is the folder its relative paths start from."""
    from localtc.app import engine_config
    from localtc.copilot import Copilot, Note, Say, Tune

    engine = AtcEngine(engine_config(scenario.flight, scenario.atc), interpreter=interpreter, phraser=phraser)
    result = ScenarioResult(engine=engine)
    mode = copilot if copilot is not None else scenario.scenario.copilot
    pilot = Copilot(engine, mode=mode) if mode else None
    for directory in scenario.scenario.airports:
        for airport in load_airport_dir(base / directory):
            engine.handle(AirportData(t=0.0, airport=airport))

    queue: list[tuple[float, int, PilotRule, dict[str, Any]]] = []
    fired: set[int] = set()
    state = {"com1": None, "last_own": None}
    counter = 0

    def schedule(rule_index: int, rule: PilotRule, due: float, context: dict[str, Any]) -> None:
        nonlocal counter
        if not rule.repeat:
            fired.add(rule_index)
        counter += 1
        queue.append((due, counter, rule, context))
        queue.sort(key=lambda item: (item[0], item[1]))

    def feed(event: BusEvent) -> None:
        if pilot is not None:
            pilot.observe(event)
        for output in engine.handle(event):
            result.outputs.append(output)
            if pilot is not None:
                pilot.observe(output)
            if (line := format_output(output)) is not None:
                result.lines.append(line)
            if isinstance(output, AtcTransmission):
                issued = engine.state.issued.get(output.instruction_id or "")
                for index, rule in enumerate(scenario.pilot):
                    if rule.on == output.instruction_id and index not in fired and (
                        rule.when is None or _when_matches(rule.when, engine, state["last_own"])
                    ):
                        schedule(index, rule, output.t + rule.delay_s, {"instruction": output.instruction_id, "issued": issued})

    def speak(rule: PilotRule, at: float, context: dict[str, Any]) -> None:
        own: OwnshipState | None = state["last_own"]
        if rule.tune is not None:
            state["com1"] = _resolve_tune(rule.tune, engine, context)
            if own is not None:
                own = msgspec.structs.replace(own, t=at, com1_mhz=state["com1"])
                state["last_own"] = own
                feed(own)
        text = rule.say.format_map(_SafeDict(_placeholders(engine, own, context, scenario.scenario.vars)))
        mhz = state["com1"] if state["com1"] is not None else (own.com1_mhz if own else 0.0)
        result.lines.append(f"[{at:8.1f}] PILOT     {speech.frequency_display(mhz)}: {text}")
        feed(Transcript(t=at, text=text))

    def run_due(until: float) -> None:
        while queue and queue[0][0] <= until:
            due, _, rule, context = queue.pop(0)
            speak(rule, due, context)

    def copilot_acts(at: float) -> None:
        if pilot is None:
            return
        for action in pilot.due(at):
            if isinstance(action, Tune):
                state["com1"] = action.hz / 1e6
                result.lines.append(f"[{at:8.1f}] TUNE      COM1 {speech.frequency_display(state['com1'])}")
                if state["last_own"] is not None:
                    state["last_own"] = msgspec.structs.replace(state["last_own"], t=at, com1_mhz=state["com1"])
                    feed(state["last_own"])
            elif isinstance(action, Say):
                mhz = state["com1"] if state["com1"] is not None else (state["last_own"].com1_mhz if state["last_own"] else 0.0)
                result.lines.append(f"[{at:8.1f}] PILOT     {speech.frequency_display(mhz)}: {action.text}")
                feed(Transcript(t=at, text=action.text))
            elif isinstance(action, Note):
                result.lines.append(f"[{at:8.1f}] NOTE      {action.text}")

    def check_when(own: OwnshipState) -> None:
        for index, rule in enumerate(scenario.pilot):
            if rule.when is None or rule.on is not None or index in fired or not _when_matches(rule.when, engine, own):
                continue
            schedule(index, rule, own.t + rule.delay_s, {})

    events = (e for e in Recording(recording or base / scenario.scenario.recording).events() if isinstance(e, SIM_EVENT_TYPES))
    for event in events:
        if scenario.scenario.end_t is not None and event.t > scenario.scenario.end_t:
            break
        run_due(event.t)
        if isinstance(event, OwnshipState):
            if state["com1"] is not None:
                event = msgspec.structs.replace(event, com1_mhz=state["com1"])
            state["last_own"] = event
        feed(event)
        if isinstance(event, OwnshipState):
            check_when(event)
            copilot_acts(event.t)

    last: OwnshipState | None = state["last_own"]
    if last is not None:
        for step in range(1, int(scenario.scenario.tail_s) + 1):
            tick = msgspec.structs.replace(last, t=last.t + step, com1_mhz=state["com1"] or last.com1_mhz)
            run_due(tick.t)
            state["last_own"] = tick
            feed(tick)
            copilot_acts(tick.t)
    return result


def _resolve_tune(tune: str | float, engine: AtcEngine, context: dict[str, Any]) -> float:
    if isinstance(tune, (int, float)):
        return float(tune)
    if tune == "handoff":
        issued = context.get("issued")
        if issued is None or "frequency" not in issued.slots:
            raise ValueError("tune = 'handoff' needs a triggering instruction with a frequency")
        return float(issued.slots["frequency"])
    facility = engine.facility(tune)
    if facility is None:
        raise ValueError(f"no {tune!r} facility known")
    return facility.mhz


def _placeholders(engine: AtcEngine, own: OwnshipState | None, context: dict[str, Any], extra: dict[str, str]) -> dict[str, str]:
    callsign = engine.state.flight.callsign
    values: dict[str, str] = dict(extra)
    if callsign is not None:
        values["callsign"] = speech.callsign(callsign)
        values["callsign_short"] = speech.callsign(callsign.short)
    if own is not None:
        values["alt"] = f"{int(round(own.alt_indicated_ft / 100.0) * 100):,}"
    a = engine.state.assignments
    values["departure_runway"] = a.departure_runway or ""
    values["arrival_runway"] = a.arrival_runway or ""
    if a.squawk:
        values["squawk"] = speech.squawk(a.squawk)
    issued = context.get("issued")
    if issued is not None and engine.library.get(issued.instruction_id).pilot_readback:
        values["readback"] = engine.library.pilot_readback(issued.instruction_id, issued.slots)
    return values


def _when_matches(when: When, engine: AtcEngine, own: OwnshipState) -> bool:
    st = engine.state
    if when.phase is not None and (st.phase != when.phase or own.t - st.phase_since_t < when.after_s):
        return False
    if when.min_t is not None and own.t < when.min_t:
        return False
    if when.agl_above is not None and own.alt_agl_ft <= when.agl_above:
        return False
    if when.final_nm_below is not None:
        final = engine.tracker.context.final
        if final is None or final.distance_nm > when.final_nm_below:
            return False
    if when.cleared is not None:
        clearance = st.clearances.get(when.cleared)
        if clearance is None or clearance.readback != "correct":
            return False
    return True
