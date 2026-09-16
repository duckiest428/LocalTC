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

    @property
    def phase(self):
        return self.detector.phase

    def handle(self, event: BusEvent) -> PhaseChanged | None:
        if isinstance(event, AirportData):
            self.context_builder.add_airport(event.airport)
        elif isinstance(event, SimLifecycle) and event.kind == "flight_loaded":
            self.detector.reset()
        elif isinstance(event, OwnshipState):
            self.ownship = event
            self.context = self.context_builder.build(event)
            return self.detector.update(event, self.context)
        return None
