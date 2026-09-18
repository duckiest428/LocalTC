"""``localtc`` command line: run, record, replay, inspect."""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

import msgspec
from typing import TextIO

from localtc import __version__
from localtc.airports import AirportCache, dump_airport, load_airport_dir
from localtc.atc_core.phase import PhaseThresholds, PhaseTracker
from localtc.app import debug_airport, run_session
from localtc.config import ConfigError, load_config
from localtc.replay import Recording, RecordingFormatError
from localtc.replay.inspect import format_summary, summarize
from localtc.sim_api import (
    AircraftIdentity,
    AirportData,
    AtcAlert,
    PhaseChanged,
    RadioTuned,
    ReadbackEvaluated,
    AtcTransmission,
    BusEvent,
    ConnectionStatus,
    LlmExchange,
    OwnshipState,
    PttPressed,
    PttReleased,
    SimLifecycle,
    SourceUnavailable,
    TrafficSnapshot,
    Transcript,
)


def format_event(ev: BusEvent) -> str:
    if isinstance(ev, OwnshipState):
        flags = " GND" if ev.on_ground else ""
        flags += " TX" if ev.com1_tx else ""
        body = (
            f"OWN   {ev.lat:9.5f},{ev.lon:10.5f}  ALT {ev.alt_indicated_ft:6.0f}ft  "
            f"HDG {ev.hdg_mag:03.0f}  IAS {ev.ias_kt:3.0f}kt  VS {ev.vs_fpm:+5.0f}  "
            f"SQ {ev.squawk} {ev.xpdr_mode}  COM1 {ev.com1_mhz:.3f}{flags}"
        )
    elif isinstance(ev, AircraftIdentity):
        from localtc.atc_core.values import clean_sim_name

        body = f"ACFT  {ev.atc_id} {clean_sim_name(ev.atc_type)} {clean_sim_name(ev.atc_model)} '{ev.title}'"
    elif isinstance(ev, TrafficSnapshot):
        names = ", ".join(tgt.atc_id or str(tgt.object_id) for tgt in ev.targets[:6])
        body = f"TFC   {len(ev.targets)} targets" + (f": {names}" if names else "")
    elif isinstance(ev, SimLifecycle):
        body = f"SIM   {ev.kind} {ev.detail}".rstrip()
    elif isinstance(ev, ConnectionStatus):
        body = f"CONN  {'up' if ev.connected else 'down'}: {ev.detail}"
    elif isinstance(ev, PttPressed):
        body = f"PTT   down COM{ev.radio}"
    elif isinstance(ev, PttReleased):
        body = f"PTT   up COM{ev.radio} audio={ev.audio_ref}"
    elif isinstance(ev, Transcript):
        body = f'PILOT "{ev.text}"'
    elif isinstance(ev, AtcTransmission):
        body = f'ATC   {ev.station} {ev.frequency_mhz:.3f}: "{ev.text}"'
    elif isinstance(ev, PhaseChanged):
        body = f"PHASE {ev.previous or '-'} -> {ev.phase} ({ev.reason})"
    elif isinstance(ev, ReadbackEvaluated):
        extra = f" missing={','.join(ev.missing)}" if ev.missing else ""
        extra += f" heard={ev.mismatched}" if ev.mismatched else ""
        body = f"RDBK  {ev.instruction_id}: {ev.status}{extra}"
    elif isinstance(ev, RadioTuned):
        who = ev.station or "no ATC on this frequency"
        body = f"TUNE  COM{ev.radio} {ev.frequency_mhz:.3f} -> {who}"
    elif isinstance(ev, AtcAlert):
        body = f"ALERT {ev.kind}: {ev.detail}"
    elif isinstance(ev, AirportData):
        body = f"APT   {ev.airport.icao} {ev.airport.name}"
    elif isinstance(ev, LlmExchange):
        detail = f" ({ev.detail})" if ev.detail else ""
        body = f"LLM   {ev.purpose} {ev.outcome} {ev.latency_ms:.0f} ms{detail}: {ev.response}"
    else:
        body = repr(ev)
    return f"[{ev.t:8.2f}] {body}"


class EventPrinter:
    """Prints events, limiting own-ship lines to one per ``ownship_every_s`` of session time."""

    def __init__(self, ownship_every_s: float = 1.0, out: TextIO | None = None, *, skip_traffic: bool = False) -> None:
        self._every = ownship_every_s
        self._skip_traffic = skip_traffic
        self._out = out
        self._last_own: float | None = None

    def __call__(self, ev: BusEvent) -> None:
        if self._skip_traffic and isinstance(ev, (TrafficSnapshot, OwnshipState)):
            return
        if isinstance(ev, OwnshipState):
            if self._last_own is not None and ev.t - self._last_own < self._every:
                return
            self._last_own = ev.t
        print(format_event(ev), file=self._out or sys.stdout, flush=True)


def _cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if args.source:
        cfg.source.kind = args.source
    record = cfg.recorder.enabled and not args.no_record
    if args.destination:
        cfg.flight.destination = args.destination
    if args.cruise_ft:
        cfg.flight.cruise_ft = args.cruise_ft
    if args.callsign:
        cfg.flight.callsign = args.callsign
    if args.no_atc:
        cfg.atc.enabled = False
    if args.no_llm:
        cfg.llm.enabled = False
    if args.llm_model:
        cfg.llm.model = args.llm_model
    if args.copilot:
        cfg.copilot.mode = args.copilot
    show = args.print or args.type or cfg.copilot.mode != "off"
    printer = EventPrinter(skip_traffic=True) if show else None
    return _run(cfg, record, printer, typed_input=args.type)


def _cmd_record(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    cfg.source.kind = "live"
    return _run(cfg, True, EventPrinter() if args.print else None)


def _cmd_replay(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    cfg.source.kind = "replay"
    cfg.replay.path = args.path
    cfg.replay.speed = args.speed
    cfg.replay.start_at = args.start_at
    cfg.replay.end_at = args.end_at
    cfg.replay.loop = args.loop
    cfg.replay.include_radio = not args.no_radio
    cfg.atc.enabled = args.atc
    if args.destination:
        cfg.flight.destination = args.destination
    if args.cruise_ft:
        cfg.flight.cruise_ft = args.cruise_ft
    if args.callsign:
        cfg.flight.callsign = args.callsign
    cfg.llm.replay = args.llm
    printer = None if args.quiet else EventPrinter(args.ownship_every, skip_traffic=args.atc)
    return _run(cfg, args.record, printer, typed_input=args.type)


def _cmd_phases(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    destination = (args.destination or cfg.flight.destination or "").upper() or None
    cruise_ft = args.cruise_ft if args.cruise_ft is not None else (cfg.flight.cruise_ft or None)
    tracker = PhaseTracker(
        destination=destination, cruise_ft=cruise_ft, thresholds=msgspec.convert(cfg.atc.phase, PhaseThresholds)
    )
    for directory in [*cfg.atc.airport_dirs, *(args.airports or [])]:
        for airport in load_airport_dir(directory):
            tracker.context_builder.add_airport(airport)
    if destination and destination not in tracker.context_builder.airports:
        if (cached := AirportCache().get(destination)) is not None:
            tracker.context_builder.add_airport(cached)
    recording = Recording(args.path)
    count = 0
    for event in recording.events():
        if isinstance(event, AirportData):
            print(f"[{event.t:8.1f}] airport data: {event.airport.icao}")
        if (change := tracker.handle(event)) is not None:
            count += 1
            print(f"[{change.t:8.1f}] {change.previous or '-':>11} -> {change.phase:<11} {change.reason}")
    if destination and destination not in tracker.context_builder.airports:
        print(f"note: no airport data for destination {destination}; arrival phases use telemetry only")
    return 0 if count else 1


def _cmd_inspect(args: argparse.Namespace) -> int:
    print(format_summary(summarize(Recording(args.path))))
    return 0


def _cmd_debug_airport(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    report = asyncio.run(debug_airport(cfg, args.icao, raw_path=args.raw, timeout=args.timeout))
    print(f"Sim: {report.session.sim_product} {report.session.sim_version}")
    print("Facility messages (id, Type field, bytes after 40-byte header): count")
    for key, count in sorted(report.messages.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0, kv[0][2])):
        print(f"  {key}: {count}")
    airport = report.airport
    if airport is None:
        print(f"No airport data received for {args.icao or 'nearest airport'} within {args.timeout:.0f} s")
        return 1
    print(format_airport(airport))
    if args.json:
        dump_airport(airport, args.json)
        print(f"Wrote {args.json}")
    if args.raw:
        print(f"Raw messages in {args.raw}")
    return 0


def format_airport(airport) -> str:
    hold_shorts = sum(p.is_hold_short for p in airport.taxi_points)
    names = sorted({p.name for p in airport.taxi_paths if p.name})
    lines = [
        f"{airport.icao} {airport.name} ({airport.region})  {airport.lat:.5f},{airport.lon:.5f}  "
        f"elev {airport.elev_ft:.0f} ft  magvar {airport.magvar:+.1f}",
        "Runways:",
        *(f"  {r.name:<9} hdg {r.heading_true:5.1f}T  {r.length_m:5.0f} x {r.width_m:3.0f} m  "
          f"ILS {r.primary.ils_ident or '-'}/{r.secondary.ils_ident or '-'}" for r in airport.runways),
        "Frequencies:",
        *(f"  {f.kind:<12} {f.mhz:7.3f}  {f.name}" for f in airport.frequencies),
        f"Taxi points: {len(airport.taxi_points)} ({hold_shorts} hold-short)   paths: {len(airport.taxi_paths)}   "
        f"parking: {len(airport.parking)}",
        f"Taxiway names: {', '.join(names) or '(none)'}",
    ]
    return "\n".join(lines)


def _cmd_atc(args: argparse.Namespace) -> int:
    from localtc.app import build_engine, ollama_backend
    from localtc.config import FlightConfig
    from localtc.scenario import Scenario, ScenarioMeta, load_scenario, run

    cfg = load_config(args.config)
    if args.scenario:
        scenario, base = load_scenario(args.scenario), Path(args.scenario).parent
    elif args.path and args.copilot:
        scenario = Scenario(scenario=ScenarioMeta(recording=str(Path(args.path).resolve()), airports=[]),
                            flight=FlightConfig(), atc=cfg.atc)
        base = Path.cwd()
    else:
        print("localtc atc: give --scenario, or a recording and --copilot", file=sys.stderr)
        return 2
    if args.destination:
        scenario.flight.destination = args.destination
    if args.cruise_ft:
        scenario.flight.cruise_ft = args.cruise_ft
    if args.callsign:
        scenario.flight.callsign = args.callsign
    scenario.scenario.airports = [*scenario.scenario.airports, *(str(Path(a).resolve()) for a in args.airports or [])]
    interpreter = phraser = None
    if args.llm == "live":
        backend = ollama_backend(cfg.llm)
        if not backend.status().reachable:
            print(f"localtc atc: Ollama isn't running at {cfg.llm.base_url}", file=sys.stderr)
            return 2
        engine = build_engine(cfg, backend)
        interpreter, phraser = engine.interpreter, engine.phraser
    result = run(scenario, base, recording=args.path, interpreter=interpreter, phraser=phraser, copilot=args.copilot)
    sys.stdout.write(result.transcript)
    if args.snapshot:
        print(msgspec.json.format(msgspec.json.encode(result.engine.snapshot()), indent=2).decode())
    return 0


def _cmd_llm_check(args: argparse.Namespace) -> int:
    from localtc.app import ollama_backend, warm_up
    from localtc.atc_core.llm import LlmInterpreter
    from localtc.atc_core.readback import InterpretContext

    cfg = load_config(args.config)
    if args.model:
        cfg.llm.model = args.model
    backend = ollama_backend(cfg.llm)
    status = backend.status()
    if not status.reachable:
        print(f"Ollama isn't running at {cfg.llm.base_url} ({status.error}). Start the Ollama app and try again.")
        return 1
    print(f"Ollama {status.version} at {cfg.llm.base_url}; models: {', '.join(status.models) or '(none)'}")
    if not status.has(cfg.llm.model):
        print(f"Model {cfg.llm.model} isn't installed. Run: ollama pull {cfg.llm.model}")
        return 1
    print(f"Loading {cfg.llm.model} ...")
    seconds = warm_up(backend)
    if seconds is None:
        print("The model didn't answer.")
        return 1
    print(f"Loaded and answered in {seconds:.1f} s (first call; later ones reuse the loaded model)")
    interpreter = LlmInterpreter(backend, timeout_s=30.0, budget_s=60.0)
    for phase, station, text in (("RUNWAY_HOLD", "Montreal Tower", "tower DP69 holding short zero six left"),
                                 ("CRUISE", "Montreal Center", "what's the altimeter in quebec"),
                                 ("CRUISE", "Montreal Center", "request flight level two four zero, DP69")):
        result = interpreter.interpret(text, None, InterpretContext(phase=phase, station=station))
        took = sum(e.latency_ms for e in result.exchanges)
        print(f"  {took:6.0f} ms  {text!r} -> {result.intent} {dict(result.values)}")
    print(f"Timeout per call is {cfg.llm.timeout_s} s ([llm] timeout_s). Run 'localtc llm eval' for the full test.")
    return 0


def _cmd_llm_eval(args: argparse.Namespace) -> int:
    from localtc.app import ollama_backend
    from localtc.atc_core.llm import LlmInterpreter
    from localtc.llm.eval import format_results, load_cases, run_cases

    cfg = load_config(args.config)
    if args.model:
        cfg.llm.model = args.model
    backend = ollama_backend(cfg.llm)
    if not backend.status().reachable:
        print(f"Ollama isn't running at {cfg.llm.base_url}. Start the Ollama app and try again.")
        return 1
    cases = load_cases(args.cases)
    print(f"Running {len(cases)} cases against {cfg.llm.model} (timeout {cfg.llm.timeout_s} s per call) ...")
    interpreter = LlmInterpreter(backend, mode="primary", timeout_s=cfg.llm.timeout_s, budget_s=cfg.llm.budget_s,
                                 max_attempts=cfg.llm.max_attempts)
    results = run_cases(interpreter, cases)
    print(format_results(results))
    return 0 if all(r.passed for r in results) else 1


def _run(cfg, record: bool, printer: EventPrinter | None, *, typed_input: bool = False) -> int:
    started = time.monotonic()
    out_dir = asyncio.run(run_session(cfg, record=record, on_event=printer, typed_input=typed_input))
    if out_dir:
        print(f"Recording saved to {out_dir} ({time.monotonic() - started:.0f} s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="localtc", description="Offline ATC for MSFS 2024")
    parser.add_argument("--version", action="version", version=f"localtc {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_config(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--config", help="config TOML (default: $LOCALTC_CONFIG or config/localtc.toml)")
        return p

    run = with_config(sub.add_parser("run", help="run with the configured source"))
    run.add_argument("--source", choices=["live", "replay"], help="override source.kind")
    run.add_argument("--no-record", action="store_true", help="don't record this session")
    run.add_argument("--print", action="store_true", help="print events")
    run.add_argument("--type", action="store_true", help="type pilot transmissions on stdin (implies --print)")
    run.add_argument("--destination", help="destination ICAO (overrides [flight])")
    run.add_argument("--cruise-ft", type=int, help="planned cruise altitude (overrides [flight])")
    run.add_argument("--callsign", help="callsign to use instead of the sim's (e.g. N738B on an airline livery)")
    run.add_argument("--no-atc", action="store_true", help="don't run the ATC engine")
    run.add_argument("--copilot", choices=["assist", "full"],
                     help="copilot works the radio: assist = readbacks + frequency changes, full = every call")
    run.add_argument("--no-llm", action="store_true", help="don't use the language model (grammar only)")
    run.add_argument("--llm-model", help="Ollama model (overrides [llm] model)")
    run.set_defaults(func=_cmd_run)

    record = with_config(sub.add_parser("record", help="record a live MSFS 2024 session (Windows)"))
    record.add_argument("--print", action="store_true", help="print events while recording")
    record.set_defaults(func=_cmd_record)

    replay = with_config(sub.add_parser("replay", help="play back a recording"))
    replay.add_argument("path", help="recording directory or session.jsonl[.gz]")
    replay.add_argument("--speed", type=float, default=1.0, help="playback speed; 0 = as fast as possible")
    replay.add_argument("--start-at", type=float, default=0.0, metavar="SECONDS")
    replay.add_argument("--end-at", type=float, default=None, metavar="SECONDS")
    replay.add_argument("--loop", action="store_true")
    replay.add_argument("--no-radio", action="store_true", help="skip recorded PTT/transcript/ATC events")
    replay.add_argument("--record", action="store_true", help="also record the replayed session")
    replay.add_argument("--quiet", action="store_true", help="don't print events")
    replay.add_argument("--atc", action="store_true", help="run the ATC engine on the replayed flight")
    replay.add_argument("--type", action="store_true", help="type pilot transmissions on stdin (use with --atc)")
    replay.add_argument("--destination", help="destination ICAO for --atc")
    replay.add_argument("--cruise-ft", type=int, help="planned cruise altitude for --atc")
    replay.add_argument("--callsign", help="callsign to use instead of the sim's")
    replay.add_argument("--llm", choices=["recorded", "live", "off"], default="recorded",
                        help="language model answers: as recorded (default), from the live model, or none")
    replay.add_argument("--ownship-every", type=float, default=1.0, metavar="SECONDS",
                        help="print at most one own-ship line per this much session time")
    replay.set_defaults(func=_cmd_replay)

    debug = sub.add_parser("debug", help="live SimConnect diagnostics (Windows)")
    debug_sub = debug.add_subparsers(dest="debug_command", required=True)
    airport = with_config(debug_sub.add_parser("airport", help="fetch an airport's layout and show raw message stats"))
    airport.add_argument("icao", nargs="?", help="airport ICAO; omit for the nearest airport")
    airport.add_argument("--json", type=Path, help="write the parsed airport as JSON")
    airport.add_argument("--raw", type=Path, help="write raw facility messages (length-prefixed) to this file")
    airport.add_argument("--timeout", type=float, default=60.0)
    airport.set_defaults(func=_cmd_debug_airport)

    phases = with_config(sub.add_parser("phases", help="print the flight phase timeline of a recording"))
    phases.add_argument("path", help="recording directory or session.jsonl[.gz]")
    phases.add_argument("--destination", help="destination ICAO (default: [flight] destination)")
    phases.add_argument("--cruise-ft", type=float, help="planned cruise altitude (default: [flight] cruise_ft)")
    phases.add_argument("--airports", action="append", help="folder of <ICAO>.json airport files (repeatable)")
    phases.set_defaults(func=_cmd_phases)

    atc = with_config(sub.add_parser("atc", help="run ATC offline on a recording: a scripted scenario or the copilot"))
    atc.add_argument("path", nargs="?", help="recording (default: the scenario's recording)")
    atc.add_argument("--scenario", help="scenario TOML (see localtc/scenario.py)")
    atc.add_argument("--copilot", choices=["assist", "full"], help="let the copilot work the radio")
    atc.add_argument("--destination", help="destination ICAO")
    atc.add_argument("--cruise-ft", type=int, help="planned cruise altitude")
    atc.add_argument("--callsign", help="callsign to use instead of the sim's")
    atc.add_argument("--airports", action="append", help="folder of <ICAO>.json airport files (repeatable)")
    atc.add_argument("--llm", choices=["off", "live"], default="off", help="use the live language model (Ollama)")
    atc.add_argument("--snapshot", action="store_true", help="print the final session snapshot as JSON")
    atc.set_defaults(func=_cmd_atc)

    llm = sub.add_parser("llm", help="the local language model (Ollama)")
    llm_sub = llm.add_subparsers(dest="llm_command", required=True)
    check = with_config(llm_sub.add_parser("check", help="is Ollama running, is the model there, how fast is it"))
    check.add_argument("--model", help="model to check (default: [llm] model)")
    check.set_defaults(func=_cmd_llm_check)
    evaluate = with_config(llm_sub.add_parser("eval", help="run the seeded edge cases against the model"))
    evaluate.add_argument("--model", help="model to test (default: [llm] model)")
    evaluate.add_argument("--cases", help="cases TOML (default: the built-in set)")
    evaluate.set_defaults(func=_cmd_llm_eval)

    inspect = sub.add_parser("inspect", help="summarize a recording")
    inspect.add_argument("path", help="recording directory or session.jsonl[.gz]")
    inspect.set_defaults(func=_cmd_inspect)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except (ConfigError, RecordingFormatError, FileNotFoundError, SourceUnavailable) as exc:
        print(f"localtc: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
