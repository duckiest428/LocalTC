"""The radio log: one line per event the pilot would want to read back later.

The app's ATC tab shows these as the flight goes, and a replay (``localtc.replay.rewatch``) keeps them as the
flight's transcript, so the two always read the same.
"""

from localtc.console import ALERTS, PHASES
from localtc.sim_api import (
    AtcAlert,
    AtcTransmission,
    AtisBroadcast,
    BusEvent,
    ConnectionStatus,
    LlmExchange,
    PhaseChanged,
    RadioTuned,
    ReadbackEvaluated,
    SessionNote,
    SimLifecycle,
    Transcript,
)


def radio_line(ev: BusEvent) -> dict | None:
    """One line of the ATC tab's radio log, or None for events it doesn't show."""
    t = round(ev.t, 1)
    if isinstance(ev, AtcTransmission):
        return {"kind": "atc", "t": t, "station": ev.station, "mhz": ev.frequency_mhz, "text": ev.text}
    if isinstance(ev, Transcript):
        who = "copilot" if ev.source == "copilot" else "pilot"
        if not ev.text:
            return {"kind": "system", "t": t, "text": "Nothing heard: check the microphone if you spoke", "level": "warn"}
        return {"kind": who, "t": t, "text": ev.text, "unclear": ev.confidence is not None and ev.confidence < 0.5}
    if isinstance(ev, ReadbackEvaluated):
        if ev.status == "correct":
            return {"kind": "readback", "t": t, "ok": True, "text": "readback ok"}
        what = ", ".join([*ev.missing, *(f"{k} {v}" for k, v in ev.mismatched.items())])
        return {"kind": "readback", "t": t, "ok": False, "text": f"readback {ev.status}" + (f": {what}" if what else "")}
    if isinstance(ev, AtisBroadcast):
        return {"kind": "atis", "t": t, "station": f"{ev.station} information {ev.letter}", "mhz": ev.frequency_mhz,
                "text": ev.text}
    if isinstance(ev, PhaseChanged):
        return {"kind": "phase", "t": t, "text": PHASES.get(ev.phase, ev.phase)}
    if isinstance(ev, RadioTuned):
        return {"kind": "tuned", "t": t, "mhz": ev.frequency_mhz, "radio": ev.radio,
                "text": ev.station or "no ATC on this frequency"}
    if isinstance(ev, AtcAlert):
        text = ALERTS.get(ev.kind, ev.kind.replace("_", " "))
        if ev.detail and ev.kind in ("emergency", "copilot"):
            text = f"{text}: {ev.detail}" if ev.kind == "emergency" else f"Copilot: {ev.detail}"
        return {"kind": "alert", "t": t, "text": text, "level": "error" if ev.kind == "emergency" else "warn"}
    if isinstance(ev, ConnectionStatus):
        return {"kind": "system", "t": t, "text": ("Sim connected " if ev.connected else "Sim disconnected ") + ev.detail,
                "level": "info" if ev.connected else "warn"}
    if isinstance(ev, SimLifecycle) and ev.kind in ("paused", "unpaused"):
        return {"kind": "phase", "t": t, "text": f"sim {ev.kind}"}
    if isinstance(ev, SessionNote):
        return {"kind": "note", "t": t, "text": ev.text}
    if isinstance(ev, LlmExchange):
        return None
    return None
