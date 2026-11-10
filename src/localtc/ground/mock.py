"""A stand-in for GSX, for working without a sim (the Mac) and for the tests: services that take their time and a menu with
the rows and questions recorded from the real one (Zurich, GSX Pro, MSFS 2024). What it hasn't seen of the real one (the
push's pages) comes from GSX's manual, and is marked.
"""

from collections.abc import Callable

from localtc.ground.api import ACTIVE, ADDITIONAL, SERVICES, GroundState, Menu, Result, Service

TOP = "Activate Services at LSZH/Zurich"
ADDITIONAL_TITLE = "Additional Services"
IN_PROGRESS = "Service in progress"
BACK = "↑ Back"
# Rows when it can be called, and when it's running, as the real menu has them.
REQUEST_ROW = {"Deboarding": "Request Deboarding", "Catering": "Request Catering service", "Refueling": "Request Refueling",
               "Boarding": "Request Boarding", "Departure": "Prepare for Push-back and Departure", "OperateJetways": "Operate Jetway",
               "OperateStairs": "Operate Stairs", "GPU": "Request Ground Power Unit", "DeIce": "Request DeIce",
               "Lavatory": "Request Lavatory service", "Water": "Request Water service", "Cleaning": "Request Cleaning service"}
RUNNING_ROW = {"Catering": "Catering vehicles are on their way", "Refueling": "Refueling service requested",
               "GPU": "Dismiss Ground Power Unit", "Water": "Dismiss Water service", "Cleaning": "Cleaning crew on the way...",
               "Lavatory": "Lavatory service requested", "Departure": "Pushback in progress", "Deboarding": "Deboarding in progress",
               "Boarding": "Boarding in progress"}
HANDLERS = ["Swissport International [GSX choice]", "Airline Assitance Switzerland", "DNATA", "Back"]
CATERERS = ["Bijoux [GSX choice]", "First Catering", "Gate Gourmet", "LSG Sky Chefs", "Back"]
NEEDS_HANDLER = ("Catering", "Boarding", "Deboarding")
# The push's pages: from GSX's manual, not recorded yet.
DIRECTION_TITLE = "Select pushback direction"
DIRECTION_ROWS = ["Nose Right/Tail Left (LEFT)", "Nose Left/Tail Right (RIGHT)", "Straight Pushback", "Pull Straight", "Back"]
ENGINE_TITLE = "Confirm Good Engine Start"
ENGINE_ROWS = ["Good Engine Start", "Complete without waiting", "Abort service"]
# As the real one asks them: the fuel truck's operator, and once it's in position how much.
FUEL_OPERATOR = ["Jetaviation [GSX choice]", "Back"]
FUEL_TITLE = "Select refueling level"
FUEL_ROWS = [" 35% - 14700 USGAL / 98490 lb", " 50% - 21000 USGAL / 140700 lb", " 60% - 25200 USGAL / 168840 lb", " 70% - 29400 USGAL / 196980 lb",
             " 80% - 33600 USGAL / 225120 lb", " 90% - 37800 USGAL / 253260 lb", "100% - 42000 USGAL / 281400 lb", "Custom refueling using default Fuel menu"]
DURATIONS = {"GPU": 8.0, "OperateJetways": 6.0, "OperateStairs": 6.0, "Deboarding": 30.0, "Boarding": 30.0, "Cleaning": 20.0,
             "Catering": 25.0, "Refueling": 25.0, "Water": 15.0, "Lavatory": 15.0, "DeIce": 20.0, "Departure": 30.0}


ARRIVES_S = 5.0  # called, and in position this long after


class MockGsx:
    name = "mock"

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self.clock = clock or (lambda: 0.0)
        self.running = True
        self.services: dict[str, Service] = {s: Service(id=s, name=s, operator="") for s in SERVICES}
        self._done_at: dict[str, float] = {}
        self._page: Menu | None = None
        self._pending: list[tuple[str, list[str]]] = []  # questions still to come for the service asked for
        self._asking_for = ""
        self._row: str = ""  # the service whose "in progress" page is up
        self._on_change: Callable[[], None] = lambda: None
        self._handled = False
        self.message = ""
        self.parking = "Gate B 37"
        self.sent: list[tuple[str, object]] = []  # everything asked of it

    @property
    def state(self) -> GroundState:
        return GroundState(connected=self.running, running=self.running, capabilities=frozenset(("state", "services", "menu", "prompts")),
                           services=dict(self.services) if self.running else {}, menu=self._page if self.running else None,
                           message=self.message, parking=self.parking, airport="LSZH")

    async def start(self, on_change: Callable[[], None]) -> None:
        self._on_change = on_change

    async def stop(self) -> None:
        self.running = False

    def _changed(self) -> None:
        self._on_change()

    def advance(self, now: float) -> None:
        """Services finish on their own. The push stops at its engine question until that's answered."""
        for service, at in list(self._done_at.items()):
            svc = self.services[service]
            if now >= at - DURATIONS[service] + ARRIVES_S and svc.state == "requested":
                # (GSX's own words for the push: the tug on the nose wheel, then moving)
                status = "pushing back" if service == "Departure" else "in position"
                self.services[service] = Service(id=service, name=service, state="performing", status=status, can_trigger=False)
                self._changed()
            if now < at:
                continue
            del self._done_at[service]
            if service == "Departure" and self._page is None:
                self.services[service] = Service(id=service, name=service, state="completing",
                                                 status="waiting for engine-start confirmation", can_trigger=False)
                self._page = Menu(ENGINE_TITLE, tuple(ENGINE_ROWS), (False,) * len(ENGINE_ROWS))
            elif service in ("GPU",):
                self.services[service] = Service(id=service, name=service, state="performing", status="connected\nconnected", can_trigger=False)
            else:
                status = "undocked - 1 not docked" if service == "OperateJetways" and self.services[service].status == "undocking" else ""
                self.services[service] = Service(id=service, name=service, state="completed", status=status, can_trigger=True)
            self._changed()

    def _begin(self, service: str) -> None:
        status = "on the way, ETA 2 mins"
        if service == "OperateJetways":
            status = "docking"
        self.services[service] = Service(id=service, name=service, state="requested", status=status, can_trigger=False)
        self._done_at[service] = self.clock() + DURATIONS[service]
        self.message = f"{service} service requested"
        self._page = None

    def _questions(self, service: str) -> list[tuple[str, list[str]]]:
        out: list[tuple[str, list[str]]] = []
        if service in NEEDS_HANDLER and not self._handled:
            out.append(("Select handling operator", HANDLERS))
        if service == "Catering":
            out.append(("Select catering operator", CATERERS))
        if service == "Refueling":
            out.append(("Select fueltruck operator", FUEL_OPERATOR))
            out.append((FUEL_TITLE, FUEL_ROWS))
        if service == "Departure":
            out.append((DIRECTION_TITLE, DIRECTION_ROWS))
        return out

    def _next_question(self) -> None:
        if self._pending:
            title, rows = self._pending.pop(0)
            self._page = Menu(title, tuple(rows), (False,) * len(rows))
        else:
            self._begin(self._asking_for)
            self._asking_for = ""
        self._changed()

    async def select_gate(self, gate: str) -> Result:
        return Result(False, "no_gate")

    async def speed_up(self, service: str) -> Result:
        return Result(False, "no_setting")

    async def slow_down(self, service: str) -> Result:
        return Result(True)

    async def trigger(self, service: str) -> Result:
        self.sent.append(("trigger", service))
        if not self.running:
            return Result(False, "not_connected")
        svc = self.services.get(service)
        if svc is None or not svc.can_trigger and svc.state != "completed":
            self.message = f"Cannot request {service} now"
            self._changed()
            return Result(True)
        self._asking_for = service
        self._pending = self._questions(service)
        self._next_question()
        return Result(True)

    def _top(self) -> Menu:
        rows = [self._row_of(s) for s in REQUEST_ROW if s not in ADDITIONAL]
        rows = [*rows, "Additional Services ▶", "Reposition Aircraft"]
        return Menu(TOP, tuple(rows), (False,) * len(rows))

    def _additional(self) -> Menu:
        rows = [self._row_of(s) for s in ("GPU", "DeIce", "Lavatory", "Water", "Cleaning")]
        rows = [*rows, "Next Page ▶", BACK]
        return Menu(ADDITIONAL_TITLE, tuple(rows), (False,) * len(rows))

    def _row_of(self, service: str) -> str:
        return RUNNING_ROW.get(service, REQUEST_ROW[service]) if self.services[service].state in ACTIVE else REQUEST_ROW[service]

    async def open_menu(self) -> Result:
        self.sent.append(("open", None))
        if self.running and self._page is None:
            self._page = self._top()
            self._changed()
        return Result(self.running, "" if self.running else "not_connected")

    async def close_menu(self) -> Result:
        self.sent.append(("close", None))
        self._page, self._pending, self._asking_for = None, [], ""
        self._changed()
        return Result(True)

    async def pick(self, index: int) -> Result:
        page = self._page
        self.sent.append(("pick", page.entries[index] if page is not None and index < len(page.entries) else index))
        if page is None or index >= len(page.entries):
            return Result(False, "bad_args")
        if index < len(page.disabled) and page.disabled[index]:
            return Result(False, "disabled")
        self._pick(page, page.entries[index])
        self._changed()
        return Result(True)

    def _pick(self, page: Menu, label: str) -> None:
        if page.title == TOP or page.title == ADDITIONAL_TITLE:
            if label.startswith("Additional"):
                self._page = self._additional()
                return
            service = next((s for s in SERVICES if label in (REQUEST_ROW[s], RUNNING_ROW.get(s))), "")
            if not service:
                self._page = None
            elif label.startswith("Dismiss"):
                self._done_at.pop(service, None)
                self.services[service] = Service(id=service, name=service)
                self._page = None
            elif self.services[service].state in ACTIVE:
                self._row = service
                self._page = Menu(IN_PROGRESS, ("Complete now", "Abort service", "Back"), (False,) * 3)
            else:
                self._asking_for, self._pending = service, self._questions(service)
                self._next_question()
        elif page.title == IN_PROGRESS:
            if label == "Abort service":
                self._done_at.pop(self._row, None)
                self.services[self._row] = Service(id=self._row, name=self._row)
            elif label == "Complete now":
                self._done_at[self._row] = self.clock()
            self._page = None
        elif page.title == ENGINE_TITLE:
            self.services["Departure"] = Service(id="Departure", name="Departure", state="completed")
            self._page = None
        elif label == "Back":
            self._page, self._pending, self._asking_for = None, [], ""
        else:
            if "operator" in page.title:
                self._handled = True
            self._next_question()

    async def answer_prompt(self, gen: int, text: str) -> Result:
        self.sent.append(("prompt", text))
        return Result(True)
