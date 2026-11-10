"""Without GSX: the sim's own ground services, by the key events it documents. There's no menu, no operators, and no telling
how far along they are: a call goes out and that's all that's known. The push is the sim's own tug, straight back; which way
it turns is left out until it has been tried in the sim (stock tugs are ignored or blocked by many add-on aircraft).
"""

from collections.abc import Callable
from typing import Any

from localtc.ground.api import GroundState, Result, Service
from localtc.sim_api import SendSimEvent

EVENTS = {"OperateJetways": "TOGGLE_JETWAY", "OperateStairs": "TOGGLE_RAMPTRUCK", "Departure": "TOGGLE_PUSHBACK",
          "Refueling": "REQUEST_FUEL_KEY", "Catering": "REQUEST_CATERING", "GPU": "REQUEST_POWER_SUPPLY"}
CALLED_S = 8.0  # a call's shown as under way this long, then done: the sim says no more than that


class SimServices:
    name = "standalone"

    def __init__(self, send: Callable[[Any], None], clock: Callable[[], float]) -> None:
        self.send, self.clock = send, clock
        self._called: dict[str, float] = {}

    @property
    def state(self) -> GroundState:
        now = self.clock()
        services = {}
        for service in EVENTS:
            at = self._called.get(service)
            state = "available" if at is None else "performing" if now - at < CALLED_S else "completed"
            services[service] = Service(id=service, name=service, state=state, can_trigger=True)
        return GroundState(connected=True, running=True, services=services)

    async def start(self, on_change: Callable[[], None]) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def select_gate(self, gate: str) -> Result:
        return Result(False, "no_gate")

    async def speed_up(self, service: str) -> Result:
        return Result(False, "no_setting")

    async def slow_down(self, service: str) -> Result:
        return Result(True)

    async def trigger(self, service: str) -> Result:
        event = EVENTS.get(service)
        if event is None:
            return Result(False, "not_available")
        self.send(SendSimEvent(name=event))
        self._called[service] = self.clock()
        return Result(True)

    async def open_menu(self) -> Result:
        return Result(False, "not_available")

    async def pick(self, index: int) -> Result:
        return Result(False, "not_available")

    async def close_menu(self) -> Result:
        return Result(True)

    async def answer_prompt(self, gen: int, text: str) -> Result:
        return Result(False, "not_available")
