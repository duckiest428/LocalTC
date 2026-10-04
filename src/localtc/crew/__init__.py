"""The copilot as Pilot Monitoring, on the intercom: hears the pilot (``IntercomHeard``), works the aircraft through
SimConnect, says what it did (``CrewSpeech``) and keeps a record (``CrewAction``).

Pure and event-driven like ``localtc.copilot``: ``PilotMonitoring.observe(event)`` takes every bus event and
returns what to say and what to send to the sim, so a recording replays through it exactly. ``CrewService``
puts it on the bus.
"""

from localtc.crew.pm import PilotMonitoring  # noqa: E402

__all__ = ["PilotMonitoring"]
