"""Feeds sim events to the context builder and phase detector."""

from localtc.atc_core.phase.context import ContextBuilder, PositionContext
from localtc.atc_core.phase.detector import PhaseDetector, PhaseThresholds
from localtc.sim_api import AirportData, BusEvent, OwnshipState, PhaseChanged, SimLifecycle


class PhaseTracker:
    def __init__(
        self,
        *,
        destination: str | None = None,
        cruise_ft: float | None = None,
        thresholds: PhaseThresholds | None = None,
    ) -> None:
        th = thresholds or PhaseThresholds()
        self.context_builder = ContextBuilder(destination=destination)
        self.detector = PhaseDetector(th, cruise_ft=cruise_ft)
        self.context = PositionContext()
        self.ownship: OwnshipState | None = None
        self.paused = False

    @property
    def phase(self):
        return self.detector.phase

    def handle(self, event: BusEvent) -> PhaseChanged | None:
        if isinstance(event, AirportData):
            self.context_builder.add_airport(event.airport)
        elif isinstance(event, SimLifecycle):
            if event.kind in ("flight_loaded", "sim_stop"):
                self.detector.reset()
            elif event.kind == "paused":
                self.paused = True
            elif event.kind == "unpaused" and self.paused:
                self.paused = False
                self.detector.resume()
        elif isinstance(event, OwnshipState):
            self.ownship = event
            self.context = self.context_builder.build(event)
            if self.paused:
                return None  # values in the pause menu can be garbage (e.g. on-ground); don't judge the phase
            return self.detector.update(event, self.context)
        return None
