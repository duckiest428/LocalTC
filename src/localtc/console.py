"""The flight console: what a pilot wants to see while flying, and nothing else.

ATC and pilot transmissions stand out, with ATC's words wrapped under the station name.
Frequency changes, ATIS, phase changes and alerts get one short line each. Everything else
(push-to-talk edges, language model calls, airport data, timing) goes to the log file. Use
``localtc run --events`` for the full event log.
"""

import logging
import os
import shutil
import sys
import textwrap
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO

from localtc.sim_api import (
    AtcAlert,
    AtcTransmission,
    AtisBroadcast,
    BusEvent,
    ConnectionStatus,
    PhaseChanged,
    RadioTuned,
    ReadbackEvaluated,
    SimLifecycle,
    Transcript,
)

CONSOLE = {"console": True}  # log.info(..., extra=CONSOLE): also show this INFO line on the console

PHASES = {
    "PARKED": "Parked", "TAXI_OUT": "Taxiing", "RUNWAY_HOLD": "Holding short", "TAKEOFF": "Takeoff roll",
    "DEPARTURE": "Airborne", "CRUISE": "Cruise", "ARRIVAL": "Arrival", "APPROACH": "Approach",
    "LANDING": "Final", "TAXI_IN": "Landed, taxiing in",
}
ALERTS = {
    "taxi_without_clearance": "Taxiing without a taxi clearance",
    "takeoff_without_clearance": "Takeoff roll without a takeoff clearance",
    "landed_without_clearance": "Landed without a landing clearance",
    "runway_incursion": "Runway entered without clearance",
    "readback_unresolved": "ATC gave up on a readback",
    "no_atc_on_frequency": "Nobody answers on this frequency",
    "emergency": "Emergency",
}


class Style:
    def __init__(self, color: bool) -> None:
        self.color = color

    def __call__(self, text: str, *codes: str) -> str:
        if not self.color or not codes:
            return text
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


BOLD, DIM, CYAN, GREEN, YELLOW, MAGENTA, RED = "1", "2", "36", "32", "33", "35", "31"


def use_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR") or not getattr(stream, "isatty", lambda: False)():
        return False
    if sys.platform == "win32":
        return _enable_windows_ansi()
    return True


def _enable_windows_ansi() -> bool:
    """Windows 10+ consoles understand ANSI colors once asked to."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except (AttributeError, OSError):
        return False


class FlightConsole:
    """Prints a flight as a radio log. Call it with every bus event."""

    def __init__(self, out: TextIO | None = None, *, color: bool | None = None, width: int | None = None) -> None:
        self.out = out or sys.stdout
        self.s = Style(use_color(self.out) if color is None else color)
        self.width = width
        self._station: str | None = None
        self._atis: tuple[str, str] | None = None

    def __call__(self, ev: BusEvent) -> None:
        for line in self.lines(ev):
            print(line, file=self.out, flush=True)

    def lines(self, ev: BusEvent) -> list[str]:
        s, when = self.s, self.s(_clock(ev.t), DIM)
        if isinstance(ev, AtcTransmission):
            head = f"{when}  {s('ATC', BOLD, CYAN)}  {s(ev.station, CYAN)} {s(f'{ev.frequency_mhz:.3f}', DIM)}"
            return [head, *self._wrap(ev.text, bold=True)]
        if isinstance(ev, Transcript):
            if ev.source == "copilot":
                return [f"{when}  {s('YOU', BOLD, GREEN)}  {ev.text}  {s('(copilot)', DIM)}"]
            if not ev.text:
                return [f"{when}  {s('YOU', BOLD, GREEN)}  {s('(nothing heard; check the microphone if you spoke)', DIM)}"]
            unsure = ev.confidence is not None and ev.confidence < 0.5
            return [f"{when}  {s('YOU', BOLD, GREEN)}  {ev.text}" + (s("  (unclear)", YELLOW) if unsure else "")]
        if isinstance(ev, ReadbackEvaluated):
            if ev.status == "correct":
                return [f"{' ' * 7}{s('   readback ok', DIM)}"]
            what = ", ".join([*ev.missing, *(f"{k} {v}" for k, v in ev.mismatched.items())])
            return [f"{' ' * 7}{s(f'   readback {ev.status}: {what}', YELLOW)}"]
        if isinstance(ev, RadioTuned):
            if ev.station == self._station:
                return []
            self._station = ev.station
            who = ev.station or "no ATC here"
            return [f"{when}  {s('COM' + str(ev.radio), BOLD)}  {ev.frequency_mhz:.3f}  {s(who, BOLD if ev.station else DIM)}"]
        if isinstance(ev, AtisBroadcast):
            key = (ev.station, ev.letter)
            if key == self._atis:
                return []
            self._atis = key
            head = f"{when}  {s('ATIS', BOLD, MAGENTA)} {s(f'{ev.station} information {ev.letter}', MAGENTA)}"
            return [head, *self._wrap(ev.text)]
        if isinstance(ev, PhaseChanged):
            return [f"{when}  {s('--', DIM)} {s(PHASES.get(ev.phase, ev.phase), DIM)}"]
        if isinstance(ev, AtcAlert):
            text = ALERTS.get(ev.kind, ev.kind.replace("_", " "))
            detail = f": {ev.detail}" if ev.kind == "emergency" and ev.detail else ""
            return [f"{when}  {s('!', BOLD, RED if ev.kind == 'emergency' else YELLOW)}  {s(text + detail, YELLOW)}"]
        if isinstance(ev, ConnectionStatus):
            return [f"{when}  {s('Sim connected' if ev.connected else 'Sim disconnected', BOLD if ev.connected else YELLOW)}"
                    + s(f"  {ev.detail}", DIM)]
        if isinstance(ev, SimLifecycle) and ev.kind in ("paused", "unpaused"):
            return [f"{when}  {s('-- sim ' + ev.kind, DIM)}"]
        return []

    def _wrap(self, text: str, *, bold: bool = False) -> list[str]:
        width = self.width or shutil.get_terminal_size((100, 24)).columns
        indent = " " * 12
        return [self.s(line, BOLD) if bold else line
                for line in textwrap.wrap(text, width=max(40, width - 1), initial_indent=indent, subsequent_indent=indent)]


def _clock(t: float) -> str:
    minutes, seconds = divmod(int(max(t, 0.0)), 60)
    return f"{minutes // 60:d}:{minutes % 60:02d}:{seconds:02d}" if minutes >= 60 else f"{minutes:02d}:{seconds:02d}"


class _ConsoleFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING or bool(getattr(record, "console", False))


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) / "LocalTC" / "logs") if base else Path.home() / ".cache" / "localtc" / "logs"


def setup_logging(*, verbose: bool = False, quiet_console: bool = False, log_file: Path | None = None) -> Path | None:
    """Warnings (and lines logged with ``extra=CONSOLE``) on the console; everything in the log file.
    ``verbose``: everything on the console too, at DEBUG. Returns the log file's path."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    console = logging.StreamHandler()
    if verbose:
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    else:
        console.setFormatter(logging.Formatter("%(message)s"))
        if quiet_console:
            console.addFilter(_ConsoleFilter())
    root.addHandler(console)
    if log_file is None:
        return None
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(handler)
    return log_file
