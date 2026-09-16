"""``localtc`` command line: run, record, replay, inspect."""

import argparse
import asyncio
import logging
import sys
import time
from typing import TextIO

from localtc import __version__
from localtc.app import run_session
from localtc.config import ConfigError, load_config
from localtc.replay import Recording, RecordingFormatError
from localtc.replay.inspect import format_summary, summarize
from localtc.sim_api import (
    AircraftIdentity,
    AtcTransmission,
    BusEvent,
    ConnectionStatus,
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
        body = f"ACFT  {ev.atc_id} {ev.atc_type} {ev.atc_model} '{ev.title}'"
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
    else:
        body = repr(ev)
    return f"[{ev.t:8.2f}] {body}"


class EventPrinter:
    """Prints events, limiting own-ship lines to one per ``ownship_every_s`` of session time."""

    def __init__(self, ownship_every_s: float = 1.0, out: TextIO | None = None) -> None:
        self._every = ownship_every_s
        self._out = out
        self._last_own: float | None = None

    def __call__(self, ev: BusEvent) -> None:
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
    return _run(cfg, record, EventPrinter() if args.print else None)


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
    return _run(cfg, args.record, None if args.quiet else EventPrinter(args.ownship_every))


def _cmd_inspect(args: argparse.Namespace) -> int:
    print(format_summary(summarize(Recording(args.path))))
    return 0


def _run(cfg, record: bool, printer: EventPrinter | None) -> int:
    started = time.monotonic()
    out_dir = asyncio.run(run_session(cfg, record=record, on_event=printer))
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
    replay.add_argument("--ownship-every", type=float, default=1.0, metavar="SECONDS",
                        help="print at most one own-ship line per this much session time")
    replay.set_defaults(func=_cmd_replay)

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
