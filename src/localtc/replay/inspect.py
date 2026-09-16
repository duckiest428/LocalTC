"""Human-readable summary of a recording, for debugging."""

from collections import Counter
from dataclasses import dataclass, field

from localtc.recorder.format import AUDIO_DIR
from localtc.replay.reader import Recording
from localtc.sim_api import (
    AircraftIdentity,
    AtcTransmission,
    ConnectionStatus,
    OwnshipState,
    PttReleased,
    SimLifecycle,
    TrafficSnapshot,
    Transcript,
    event_type,
)


@dataclass
class RecordingSummary:
    recording: Recording
    first_t: float = 0.0
    last_t: float = 0.0
    counts: Counter = field(default_factory=Counter)
    timeline: list[tuple[float, str]] = field(default_factory=list)
    max_traffic: int = 0
    audio_files: int = 0

    @property
    def duration_s(self) -> float:
        return self.last_t - self.first_t


def summarize(recording: Recording) -> RecordingSummary:
    s = RecordingSummary(recording)
    last_squawk = last_com1 = None
    first = True
    for ev in recording.events():
        if first:
            s.first_t = ev.t
            first = False
        s.last_t = max(s.last_t, ev.t)
        s.counts[event_type(ev)] += 1

        if isinstance(ev, OwnshipState):
            if ev.squawk != last_squawk:
                s.timeline.append((ev.t, f"squawk {ev.squawk} ({ev.xpdr_mode})"))
                last_squawk = ev.squawk
            if ev.com1_mhz != last_com1:
                s.timeline.append((ev.t, f"COM1 {ev.com1_mhz:.3f} {ev.com1_type} {ev.com1_ident}".rstrip()))
                last_com1 = ev.com1_mhz
        elif isinstance(ev, TrafficSnapshot):
            s.max_traffic = max(s.max_traffic, len(ev.targets))
        elif isinstance(ev, SimLifecycle):
            s.timeline.append((ev.t, f"sim {ev.kind} {ev.detail}".rstrip()))
        elif isinstance(ev, ConnectionStatus):
            s.timeline.append((ev.t, f"connection {'up' if ev.connected else 'down'}: {ev.detail}"))
        elif isinstance(ev, AircraftIdentity):
            s.timeline.append((ev.t, f"aircraft {ev.atc_id} {ev.atc_model} '{ev.title}'"))
        elif isinstance(ev, PttReleased):
            s.timeline.append((ev.t, f"PTT released COM{ev.radio} audio={ev.audio_ref}"))
        elif isinstance(ev, Transcript):
            s.timeline.append((ev.t, f'pilot: "{ev.text}"'))
        elif isinstance(ev, AtcTransmission):
            s.timeline.append((ev.t, f'{ev.station}: "{ev.text}"'))

    audio_dir = recording.root / AUDIO_DIR
    s.audio_files = len(list(audio_dir.glob("*.wav"))) if audio_dir.is_dir() else 0
    return s


def format_summary(s: RecordingSummary) -> str:
    h = s.recording.header
    lines = [
        f"Recording:  {s.recording.session_file}",
        f"Created:    {h.created}  (LocalTC {h.localtc_version}, schema {h.schema})",
        f"Source:     {h.session.source_kind}  {h.session.sim_product} {h.session.sim_version}".rstrip(),
        f"Duration:   {s.duration_s:.1f} s   events: {sum(s.counts.values())}"
        f"   skipped lines: {s.recording.skipped_lines}",
        f"Traffic:    up to {s.max_traffic} targets   audio files: {s.audio_files}",
        "",
        "Event counts:",
        *(f"  {name:<20} {count}" for name, count in sorted(s.counts.items())),
        "",
        "Timeline:",
        *(f"  [{t:8.2f}] {text}" for t, text in s.timeline),
    ]
    return "\n".join(lines)
