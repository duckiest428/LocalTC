"""A flight to rewatch: its track, its radio transcript and the moments that matter, from its recording.

A recording holds everything (the sim at 4 Hz, every radio event, the model's calls, the pilot's voice). A
replay keeps what it takes to watch the flight again, small enough to upload: the aircraft's path thinned
to a point every few seconds (and wherever it turns, climbs or lands), the radio log exactly as the app
showed it, and marks for the timeline (phases, handoffs, takeoff and landing, alerts). No audio, no model
calls, no dev notes. See docs/replay-format.md.

Times in a replay are seconds from its start; ``flight.started_at`` says when that was.
"""

import bisect
import gzip
import itertools
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from localtc.atc_core.facilities import sector_name
from localtc.radiolog import radio_line
from localtc.replay.reader import Recording
from localtc.sim_api import (
    AircraftIdentity,
    AirportData,
    AtcAlert,
    AtcTransmission,
    OwnshipState,
    PhaseChanged,
    ReadbackEvaluated,
    Transcript,
)

VERSION = 1
CACHE_FILE = "replay.json.gz"
LEAD_S = 60.0  # kept before the first call or movement, and after the last
MOVING_KT = 3.0
EVERY_AIR_S = 5.0
EVERY_GROUND_S = 2.0
TURN_DEG = 4.0
CLIMB_FT = 150.0
RADIO_KINDS = ("atc", "pilot", "copilot", "atis", "tuned", "alert", "phase")
SKIP = ("traffic_snapshot", "llm_exchange", "nearby_airports", "session_note", "ptt_pressed", "ptt_released")
HANDOFF_TO = re.compile(r"contact ([A-Z][\w'-]*(?: [A-Z][\w'-]*)*)")


def build_replay(recording: Recording, record: Any = None) -> dict[str, Any]:
    """The replay of a recording. ``record``: its logbook line (``FlightRecord``), for the names it knows."""
    own: list[OwnshipState] = []
    events: list[Any] = []
    airports: dict[str, dict[str, Any]] = {}
    identity: AircraftIdentity | None = None
    for ev in recording.events(skip=SKIP):
        if isinstance(ev, OwnshipState):
            own.append(ev)
        elif isinstance(ev, AirportData):
            a = ev.airport
            airports[a.icao] = {"lat": round(a.lat, 4), "lon": round(a.lon, 4), "elev": round(a.elev_ft)}
            if a.name:  # the place, for a shared card: "Seattle" under KSEA
                airports[a.icao]["name"] = sector_name(a)
        elif isinstance(ev, AircraftIdentity):
            identity = identity or ev
        else:
            events.append(ev)
    if not own:
        raise ValueError("the recording has no aircraft positions")

    start, end = _window(own, events)
    start = next(o.t for o in own if o.t >= start)  # the replay starts on a position: the map has somewhere to be
    flight_cfg = recording.header.config.get("flight", {}) or {}
    track = _track([o for o in own if start <= o.t <= end], start)
    radio = _radio([e for e in events if start <= e.t <= end], start)
    marks = _marks(own, events, start, end)
    wanted = {getattr(record, "origin", "") or flight_cfg.get("origin", ""),
              getattr(record, "destination", "") or flight_cfg.get("destination", ""),
              flight_cfg.get("alternate", "")} - {""}
    places = {icao: airports[icao] for icao in sorted(wanted) if icao in airports}
    for prefix in ("origin", "destination"):  # the logbook knows them even when the recording didn't fetch them
        icao, lat = getattr(record, prefix, ""), getattr(record, f"{prefix}_lat", None)
        if icao and icao not in places and lat is not None:
            places[icao] = {"lat": lat, "lon": getattr(record, f"{prefix}_lon"), "elev": 0}

    created = datetime.fromisoformat(recording.header.created)
    started = (created + timedelta(seconds=start)).astimezone(UTC).replace(microsecond=0)
    first = next((o for o in own if o.t >= start), own[0])

    def field(name: str, fallback: str = "") -> str:
        return (getattr(record, name, "") or fallback or "")[:64]

    return {
        "v": VERSION,
        "flight": {
            "id": field("id"),
            "callsign": field("callsign", flight_cfg.get("callsign") or (identity.atc_id if identity else "")),
            "aircraft": field("aircraft", identity.atc_model if identity else ""),
            "livery": (identity.title.strip() if identity else "")[:64],  # the sim's title: "FlyByWire A320neo (Delta)"
            "origin": field("origin", flight_cfg.get("origin", "")),
            "destination": field("destination", flight_cfg.get("destination", "")),
            "departure_runway": field("departure_runway"),
            "arrival_runway": field("arrival_runway"),
            "departure_gate": field("departure_gate"),
            "arrival_gate": field("arrival_gate"),
            "started_at": started.isoformat().replace("+00:00", "Z"),
            "zulu0": round(first.zulu_s) if first.zulu_s is not None else None,
            "duration_s": round(end - start, 1),
        },
        "airports": places,
        "route": [{"ident": str(f.get("ident", ""))[:12], "lat": round(f["lat"], 4), "lon": round(f["lon"], 4)}
                  for f in flight_cfg.get("fixes", []) or [] if "lat" in f and "lon" in f][:400],
        "track": track,
        "radio": radio,
        "marks": marks,
    }


def _window(own: list[OwnshipState], events: list[Any]) -> tuple[float, float]:
    """From a minute before the first call or movement to a minute after the last: not the half hour parked
    with the sim open before the flight."""
    said = [e.t for e in events if isinstance(e, AtcTransmission | Transcript) and getattr(e, "text", "")]
    moving = [o.t for o in own if o.gs_kt > MOVING_KT or not o.on_ground]
    busy = said + moving
    if not busy:
        return own[0].t, own[-1].t
    return max(own[0].t, min(busy) - LEAD_S), min(own[-1].t, max(busy) + LEAD_S)


def _track(own: list[OwnshipState], start: float) -> dict[str, list]:
    cols: dict[str, list] = {k: [] for k in ("t", "lat", "lon", "alt", "gs", "hdg", "vs", "gnd")}
    last: OwnshipState | None = None

    def keep(o: OwnshipState) -> None:
        nonlocal last
        cols["t"].append(round(o.t - start, 1))
        cols["lat"].append(round(o.lat, 5))
        cols["lon"].append(round(o.lon, 5))
        cols["alt"].append(round(o.alt_indicated_ft / 10) * 10)
        cols["gs"].append(round(o.gs_kt))
        cols["hdg"].append(round(o.hdg_true) % 360)
        cols["vs"].append(round(o.vs_fpm / 10) * 10)
        cols["gnd"].append(1 if o.on_ground else 0)
        last = o

    for i, o in enumerate(own):
        if last is None or i == len(own) - 1:
            keep(o)
            continue
        if o.on_ground != last.on_ground:  # takeoff or touchdown: the samples either side
            if own[i - 1] is not last:
                keep(own[i - 1])
            keep(o)
            continue
        turned = abs((o.hdg_true - last.hdg_true + 540) % 360 - 180)
        every = EVERY_GROUND_S if o.on_ground else EVERY_AIR_S
        if (o.t - last.t >= every or turned > TURN_DEG or abs(o.alt_indicated_ft - last.alt_indicated_ft) > CLIMB_FT):
            keep(o)
    return cols


def _radio(events: list[Any], start: float) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for ev in events:
        if isinstance(ev, ReadbackEvaluated):  # onto the pilot's line it judged
            said = next((line for line in reversed(lines) if line["kind"] in ("pilot", "copilot")), None)
            if said is not None and "ok" not in said:
                said["ok"] = ev.status == "correct"
                if not said["ok"]:
                    said["readback"] = radio_line(ev)["text"]
            continue
        line = radio_line(ev)
        if line is None or line["kind"] not in RADIO_KINDS:
            continue
        line = {k: v for k, v in line.items() if k in ("kind", "t", "station", "mhz", "text", "unclear")}
        line["t"] = round(ev.t - start, 1)
        if not line.get("unclear"):
            line.pop("unclear", None)
        lines.append(line)
    return lines


def _marks(own: list[OwnshipState], events: list[Any], start: float, end: float) -> list[dict[str, Any]]:
    marks: list[dict[str, Any]] = []

    def mark(t: float, kind: str, text: str) -> None:
        if start <= t <= end:
            marks.append({"t": round(t - start, 1), "kind": kind, "text": text})

    for ev in events:
        if isinstance(ev, PhaseChanged) and ev.previous is not None:
            mark(ev.t, "phase", radio_line(ev)["text"])
        elif isinstance(ev, AtcAlert):
            mark(ev.t, "alert", radio_line(ev)["text"])
        elif isinstance(ev, AtcTransmission) and "handoff" in (ev.instruction_id or ""):
            to = HANDOFF_TO.search(ev.text)
            mark(ev.t, "handoff", f"{ev.station} to {to.group(1)}" if to else f"{ev.station} hands off")
    for prev, o in itertools.pairwise(own):
        if prev.on_ground and not o.on_ground:
            mark(o.t, "takeoff", "Takeoff")
        elif not prev.on_ground and o.on_ground:
            mark(o.t, "landing", f"Touchdown, {round(prev.vs_fpm)} fpm")
    marks.sort(key=lambda m: m["t"])
    return marks


def encode(replay: dict[str, Any]) -> bytes:
    return gzip.compress(json.dumps(replay, separators=(",", ":")).encode(), mtime=0)


def decode(data: bytes) -> dict[str, Any]:
    return json.loads(gzip.decompress(data))


def replay_for(recording_dir: str | Path, record: Any = None) -> bytes:
    """The replay of a recording, gzipped: built once, then kept next to it (``replay.json.gz``)."""
    root = Path(recording_dir)
    recording = Recording(root)
    cache = root / CACHE_FILE
    if cache.is_file() and cache.stat().st_mtime >= recording.session_file.stat().st_mtime:
        data = cache.read_bytes()
        try:
            cached = decode(data)
            if cached.get("v") == VERSION and "livery" in cached.get("flight", {}):  # made before liveries: again
                return data
        except (OSError, ValueError):
            pass
    data = encode(build_replay(recording, record))
    try:
        cache.write_bytes(data)
    except OSError:
        pass  # a read-only recording still replays, it's just built again next time
    return data


def at(track: dict[str, list], t: float) -> int:
    """The index of the last track point at or before ``t``."""
    return max(0, bisect.bisect_right(track["t"], t) - 1)


__all__ = ["CACHE_FILE", "VERSION", "at", "build_replay", "decode", "encode", "replay_for"]
