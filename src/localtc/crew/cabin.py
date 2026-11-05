"""The cabin crew ([cabin]): a cabin manager who looks after the passengers and works with you through the flight.

They follow the flight as a real crew does, from what the aircraft does rather than a clock:

- **At the gate**: boarding begins when GSX says so, or without GSX when the door opens for the jetway or the stairs:
  the music, and the welcome while people board. Boarding complete on the interphone (with how many are aboard) when
  GSX's boarding is over or the door shuts as the jetway leaves (at the latest with the beacon, an engine or the push).
  Then "doors to automatic and cross-check", the music stops, and the welcome (the airline, the flight, the flight
  time, a word for the loyalty programme's members, who's looking after the cabin). No clock in any of it.
- **The push and the taxi out**: the exit rows and the safety demonstration when the push begins (this aircraft's
  exits, what a briefing has to say by regulation), the cabin lights dimmed at night, and "cabin secure for takeoff" on
  the interphone once taxiing.
- **In the air**: the seatbelt sign switched off is announced the moment it's off (an aircraft that has never shown
  the sign on gets it once climbing through ten thousand feet); a call to the flight deck before the service (what
  would you like?), then the trolley; the sign back on mid-flight is the turbulence announcement.
- **Down again**: the descent (the destination's local time and weather, and a word about the city), the approach,
  "cabin secure for landing" on the interphone; applause for a soft landing; the welcome to the city; "doors to
  manual" at the gate, the goodbye, and everyone off.

You can talk to them on the intercom by starting with "cabin", "cabin crew", "purser" or their name: a coffee
("cabin, could I get a coffee, white, one sugar?"), how the cabin is, how many are on board, "prepare the cabin for
landing", turbulence ahead, a delay ("we've got a twenty minute delay"), something to tell the passengers ("tell the
passengers we're number two for departure"). "Ladies and gentlemen ..." on the intercom is your own announcement:
nobody answers it. While they've asked you something (what you'd like to drink), your answer goes to them too.

It is pure and event-driven like the copilot: feed it every event with ``observe`` and it returns what the cabin says
and plays (``CabinSpeech``, ``CabinSound``); ``claims`` says whether words on the intercom are for the cabin, and
``heard`` answers them.
"""

import difflib
import json
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import msgspec

from localtc.atc_core.llm.backend import LlmRequest
from localtc.atc_core.region import accent, airline_accent
from localtc.crew.cabin_data import (
    Airline,
    aircraft_facts,
    airline_of,
    city_of,
    crc,
    crew_name,
    describe,
    exits,
    flight_number,
    seats,
)
from localtc.crew.commands import parse as copilot_command
from localtc.ground import words as ramp_words
from localtc.crew.turnaround import off_block_in
from localtc.crew import cabin_words
from localtc.crew.cabin_lines import BY_ID, FOREIGN, LANDING_WORDS, LINES, PEOPLE, REPLIES, Person
from localtc.sim_api import (
    AircraftIdentity,
    AircraftSystems,
    AircraftVars,
    CabinSound,
    CabinSpeech,
    FlightArrived,
    GroundStatus,
    IntercomHeard,
    LlmExchange,
    OwnshipState,
    PhaseChanged,
    SimLifecycle,
    WeatherReport,
)
from localtc.sim_api.geo import haversine_nm


@dataclass(frozen=True)
class CabinSettings:
    crew: str = "auto"  # who leads the cabin: one of cabin_lines.PEOPLE, or "auto" (a different one each flight)
    announcements: bool = True
    safety_demo: bool = True
    service: bool = True
    chime: bool = True
    boarding_music: bool = True
    applause: bool = True
    english_too: bool = True  # a crew whose language isn't English says each announcement in English too
    interphone: bool = True  # the crew call the flight deck (boarding done, cabin secure, the service)
    captain: str = ""  # the pilot's name, for the welcome ("Captain Reyes"); "" the captain


@dataclass(frozen=True)
class Facts:
    """What the crew are told about the flight (the flight plan's): callsign, airports, aircraft, the planned time."""

    callsign: str = ""
    origin: str = ""
    destination: str = ""
    aircraft: str = ""  # ICAO type ("A20N")
    dest_lat: float | None = None
    dest_lon: float | None = None
    origin_lat: float | None = None
    origin_lon: float | None = None
    planned_s: float = 0.0  # the planned flight time (0: not known)
    cruise_ft: int = 0
    sched_out_s: int = 0  # the plan's off-block time (Unix seconds; 0: not planned)
    pax: int = 0  # the plan's passengers (0: not known)


# What says where the boarding is: GSX's own state where it's running (its manual's L:vars: 1 available, 3 skipped,
# 4 requested, 5 in progress, 6 done), else the door the jetway or the stairs come to.
GSX_BOARDING, GSX_DEBOARDING, GSX_DEPARTURE = ("L:FSDT_GSX_BOARDING_STATE", "L:FSDT_GSX_DEBOARDING_STATE",
                                               "L:FSDT_GSX_DEPARTURE_STATE")
DOORS = ("INTERACTIVE POINT OPEN:0", "INTERACTIVE POINT OPEN:1")
WATCHED = (GSX_BOARDING, GSX_DEBOARDING, GSX_DEPARTURE, *DOORS)
# "Can we get boarding started?", "start boarding", "board them", "go ahead with the deboarding". Not "has boarding started?".
START_BOARDING = re.compile(r"\b(?:start|begin|commence)\s+(?:the\s+)?(de-?\s?)?boarding\b|\b(de-?\s?)?board\s+(?:them|the\s+passengers)\b"
                            r"|\b(de-?\s?)?boarding\s+(?:started|going|under\s?way)\b"
                            r"|\b(?:go\s+ahead\s+with|proceed\s+with|get\s+on\s+with|time\s+for|ready\s+for)\s+(?:the\s+)?(de-?\s?)?boarding\b")
ASKS_ABOUT = re.compile(r"\W*(?:\w+\W+)?(?:has|have|did|is|are|how|when|what|was|were)\b")
GSX_PAX = re.compile(r"\bpax \d+/(\d+)")
BOARDING_PA_S = 25.0  # after boarding begins: the boarding welcome
ROLLING_S = 3.0  # moving for this long: moving
# The announcements the language model may put in the crew member's own words, when it's a cloud one (never the safety
# ones, whose words are what the rules ask for, nor an order to the crew).
OWN_WORDS = ("boarding", "welcome", "aircraft_talk", "service", "descent", "landing", "after_landing", "goodbye", "seatbelt_off")
ALWAYS_SCRIPTED = ("safety", "exit_row", "doors", "turbulence")  # (Fully LLM too: what these begin with)
NUMBER = re.compile(r"\d[\d:,.]*|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|"
                    r"forty|fifty|sixty|hour|hours|minutes?)\b", re.I)
AIRCRAFT_TALK_S = 600.0  # after the seatbelt sign is off: a word about the aircraft
GAP_S = 1.5  # between one thing said and the next
WELCOME_AFTER_DOORS_S = 20.0
SECURE_AFTER_SAFETY_S = 25.0
LISTENING_S = 30.0  # after their call to the flight deck: the pilot's answer is theirs (unless it's a command)
SERVICE_CALL_S = 150.0  # after the seatbelt sign is off: the call to the flight deck before the service
SERVICE_PA_S = 70.0  # ... and the trolley this long after the call
DRINK_S = (100.0, 170.0)  # a drink asked for arrives this long after
ASKING_S = 40.0  # a question to the flight deck waits this long for its answer
NO_BELT_CLIMB_S = 120.0  # an aircraft without a seatbelt sign: free to move about this long after 10,000 ft
LANDING_SECURE_S = 100.0  # after the approach announcement: the cabin secure for landing
AFTER_LANDING_S = 35.0  # after touchdown at the latest: the welcome to the city
GOODBYE_AFTER_DOORS_S = 8.0
DEBOARDED_S = 120.0
SOFT_FPM = 200.0  # a touchdown gentler than this is applauded
FIRM_FPM = 450.0
MIN_SERVICE_S = 25 * 60  # a flight shorter than this has no service
BOARDING_S_PER_PAX = 7.6  # GSX's pace, about (130 on an A220 took 16 and a half minutes)
NOT_BOARDING_S = 25 * 60  # this long before off-block with nobody on board: said
READY_LATE_S = 5 * 60  # this late, boarded, and not pushing: said
LATE_PA_S = (15 * 60, 30 * 60)  # this late: the passengers are told (ten minutes was thought too soon for it)
SPEECH_S_PER_WORD = 0.42  # how long an announcement takes to say, about
FACTS_EVERY_S = 10.0  # the flight's details asked for again this often

ADDRESS = re.compile(
    r"^\s*(?:(?:hey|hi|hello|okay|ok|yeah|so)[,.]?\s+)?(?:the\s+)?"
    r"(?:cabin(?:\s+crew)?|purser|cabin\s+manager|flight\s+attendants?|crew\s+in\s+the\s+back|back\s+there|{name})"
    r"(?:\s+(?:here|this\s+is\s+the\s+(?:captain|flight\s+deck)))?\b[,.!?]?\s*", re.I)
CAPTAIN_PA = re.compile(r"^\s*(?:ladies\s+and\s+gentlemen|good\s+(?:morning|afternoon|evening)\s+(?:ladies|everyone|folks)|"
                        r"(?:hello\s+)?(?:folks|everyone),?\s+this\s+is\s+(?:your|the)\s+captain|this\s+is\s+(?:your|the)\s+captain)\b",
                        re.I)
DRINKS = (
    (r"hot\s+chocolate|cocoa", "hot chocolate", True), (r"cappuccino", "cappuccino", True),
    (r"espresso", "espresso", True), (r"lattes?", "latte", True), (r"coffees?", "coffee", True),
    (r"teas?\b", "tea", True), (r"orange\s+juice|o\.?j\.?", "orange juice", False),
    (r"apple\s+juice", "apple juice", False), (r"juices?", "orange juice", False),
    (r"sparkling\s+water", "sparkling water", False), (r"waters?\b", "water", False),
    (r"coke|cola|pepsi", "cola", False), (r"sprite|lemonade|7\s*up", "lemonade", False),
    (r"ginger\s+ale", "ginger ale", False), (r"soda|soft\s+drink", "soda", False),
)
NO_THANKS = re.compile(r"\b(?:no(?:pe)?,?\s+thank(?:s|\s+you)|i'?m\s+(?:fine|good|ok(?:ay)?|all\s+set)|nothing|not\s+(?:right\s+)?now|"
                       r"we'?re\s+(?:fine|good|ok(?:ay)?)|no\s+thanks?|maybe\s+later|all\s+good)\b", re.I)
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45, "forty-five": 45,
                "sixty": 60, "an hour": 60, "half an hour": 30, "ninety": 90}

ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
        "seventeen eighteen nineteen").split()
TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")


def words(n: int) -> str:
    """0-9999 in English words ("one hundred and seventy-four")."""
    if n < 0:
        return "minus " + words(-n)
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (f"-{ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        return f"{ONES[n // 100]} hundred" + (f" and {words(n % 100)}" if n % 100 else "")
    return f"{words(n // 1000)} thousand" + ((" and " if n % 1000 < 100 else " ") + words(n % 1000) if n % 1000 else "")


def flight_words(number: str) -> str:
    """A flight number as it's announced: "123" one twenty-three, "1234" twelve thirty-four, "1005" ten oh five."""
    digits = number.lstrip("0") or "0"
    if not digits.isdigit():
        return number

    def pair(two: str) -> str:
        n = int(two)
        return "hundred" if n == 0 else f"oh {ONES[n]}" if two[0] == "0" else words(n)

    if len(digits) <= 2:
        return words(int(digits))
    if len(digits) == 3:
        return f"{ONES[int(digits[0])]} {pair(digits[1:])}"
    if len(digits) == 4:
        return f"{words(int(digits[:2]))} {pair(digits[2:])}"
    return " ".join(ONES[int(d)] for d in digits)


def duration_words(seconds: float) -> str:
    minutes = max(5, int(round(seconds / 60 / 5)) * 5)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append("one hour" if hours == 1 else f"{words(hours)} hours")
    if minutes:
        parts.append(f"{words(minutes)} minutes")
    return " and ".join(parts)


def duration_in(language: str, seconds: float) -> str:
    """The flight time in an announcement's language ("1 Stunde 20 Minuten")."""
    minutes = max(5, int(round(seconds / 60 / 5)) * 5)
    h, m = divmod(minutes, 60)

    def slavic(n: int, one: str, few: str, many: str) -> str:
        return one if n == 1 else few if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else many

    forms = {
        "fr": (lambda: f"{h} heure{'s' if h > 1 else ''}" + (f" {m}" if m else "") if h else f"{m} minutes"),
        "de": (lambda: (f"{h} Stunde{'n' if h > 1 else ''}" + (f" {m} Minuten" if m else "")) if h else f"{m} Minuten"),
        "es": (lambda: (f"{h} hora{'s' if h > 1 else ''}" + (f" y {m} minutos" if m else "")) if h else f"{m} minutos"),
        "it": (lambda: (f"{h} or{'e' if h > 1 else 'a'}" + (f" e {m} minuti" if m else "")) if h else f"{m} minuti"),
        "pt": (lambda: (f"{h} hora{'s' if h > 1 else ''}" + (f" e {m} minutos" if m else "")) if h else f"{m} minutos"),
        "nl": (lambda: (f"{h} uur" + (f" en {m} minuten" if m else "")) if h else f"{m} minuten"),
        "sv": (lambda: (f"{h} timm{'ar' if h > 1 else 'e'}" + (f" och {m} minuter" if m else "")) if h else f"{m} minuter"),
        "pl": (lambda: (f"{h} {slavic(h, 'godzina', 'godziny', 'godzin')}" + (f" i {m} {slavic(m, 'minuta', 'minuty', 'minut')}" if m else ""))
               if h else f"{m} {slavic(m, 'minuta', 'minuty', 'minut')}"),
        "ru": (lambda: (f"{h} {slavic(h, 'час', 'часа', 'часов')}" + (f" {m} {slavic(m, 'минута', 'минуты', 'минут')}" if m else ""))
               if h else f"{m} {slavic(m, 'минута', 'минуты', 'минут')}"),
        "zh": (lambda: (f"{h}小时" + (f"{m}分钟" if m else "")) if h else f"{m}分钟"),
    }
    return forms[language]() if language in forms else duration_words(seconds)


def clock(seconds_of_day: float, *, twelve: bool) -> tuple[str, str]:
    """The time of day as text ("16:35", or "4:35 pm") and as said ("four thirty-five in the afternoon")."""
    total = int(seconds_of_day // 60) % (24 * 60)
    h, m = divmod(total, 60)
    h12 = h % 12 or 12
    part = "in the morning" if h < 12 else "in the afternoon" if h < 18 else "in the evening"
    said_m = "o'clock" if m == 0 else f"oh {ONES[m]}" if m < 10 else words(m)
    said = f"{words(h12)} {said_m} {part}"
    text = f"{h12}:{m:02d} {'am' if h < 12 else 'pm'}" if twelve else f"{h:02d}:{m:02d}"
    return text, said


def sky(raw: str, night: bool) -> str:
    """The weather in a METAR, as a passenger would say it ("with some rain"); "" where there's nothing to say."""
    tokens = raw.upper().split()
    have = lambda *codes: any(any(c in t for c in codes) for t in tokens[2:])  # noqa: E731
    if have("TS"):
        return " with thunderstorms around"
    if have("SN"):
        return " with some snow"
    if have("RA", "DZ", "SH"):
        return " with some rain"
    if any(t in ("FG", "BR") for t in tokens):
        return " and a little misty"
    if have("OVC", "BKN"):
        return " under cloudy skies"
    if have("CAVOK", "CLR", "SKC", "NSC", "FEW"):
        return " with clear skies" if night else " and sunny"
    return ""


def fahrenheit(c: float) -> int:
    return int(round(c * 9 / 5 + 32))


@dataclass
class _Later:
    due: float
    what: str
    data: dict = field(default_factory=dict)


class CabinCrew:
    def __init__(self, settings: CabinSettings | None = None, facts: Callable[[], Facts] | None = None, *,
                 seed: int = 1) -> None:
        self.settings = settings or CabinSettings()
        self.facts_of = facts or (lambda: Facts())
        self.seed = seed
        self.talks_in = ""  # [crew] language: the captain's language, which they answer in ("" English)
        self.language_name = ""  # ... its name, for the language model
        self._gsx_pax = 0  # the passengers GSX has for this flight
        self.ground_call: Any = None  # the ground crew, to start boarding and deboarding (ground.crew.RampCrew.called_by_cabin)
        self.backend: Any = None  # the cloud language model (None: the script's words)
        self.llm_mode = "mostly_llm"  # how much it's asked ([cabin] llm)
        self._to_word: set[str] = set()  # the announcements it may word
        self._rolling_since: float | None = None
        self.model_wait_s = 10.0
        self._new_flight()

    # ---- the flight ----

    def _new_flight(self) -> None:
        self._rng = random.Random(self.seed)
        self.done: set[str] = set()
        self._at: dict[str, float] = {}  # when each moment happened
        self._t = 0.0
        self._start: float | None = None
        self._own: OwnshipState | None = None
        self._sys: AircraftSystems | None = None
        self._identity: AircraftIdentity | None = None
        self._airborne = False
        self._was_airborne = False  # been in the air this flight
        self._landed_t: float | None = None
        self._touchdown_fpm = 0.0
        self._last_vs = 0.0
        self._belt: bool | None = None
        self._belt_seen_on = False
        self._above_10k_t: float | None = None
        self._phase = ""
        self._weather: dict[str, WeatherReport] = {}
        self._later: list[_Later] = []
        self._asking: tuple[str, float] | None = None  # the question put to the flight deck, and when
        self._called_t = -1e9  # their last call to the flight deck
        self._listening_until = -1.0  # called with nothing more ("cabin?"): the next words are for them too
        self._service_paused = False
        self._crew: tuple[Person, str, str, str] | None = None  # the person, their name, English, sex
        self._crew_for = ""
        self._facts: Facts | None = None
        self._facts_t = 0.0
        self._vars: dict[str, float] = {}
        self._queue: list[CabinSpeech] = []  # said one at a time
        self._free_at = 0.0

    # ---- who's on board ----

    @property
    def facts(self) -> Facts:
        if self._facts is None or self._t - self._facts_t >= FACTS_EVERY_S:  # (the plan changes rarely)
            f = self.facts_of()
            self._facts_t = self._t
            if f.callsign or self._facts is None:
                self._facts = f
        if not self._facts.callsign and self._identity is not None:
            ident = self._identity
            callsign = ident.atc_id if re.fullmatch(r"[A-Z]{3}\d+[A-Z]?", ident.atc_id or "") else ""
            self._facts = Facts(**{**self._facts.__dict__, "callsign": callsign})
        return self._facts

    def airline(self) -> tuple[str, Airline | None]:
        return airline_of(self.facts.callsign)

    @property
    def active(self) -> bool:
        """A flight with a cabin crew: an airline's callsign, or an airliner LocalTC knows."""
        code, _ = self.airline()
        return bool(flight_number(self.facts.callsign) and len(code) == 3) or seats(self.facts.aircraft) >= 50

    def locale(self) -> str:
        code, airline = self.airline()
        if airline is not None:
            return accent(airline.home) or "en-US"
        return airline_accent(self.facts.callsign) or accent(self.facts.origin) or "en-US"

    def crew(self) -> tuple[Person, str, str, str]:
        """The cabin manager: the person ([cabin] crew, or one for this flight), their name where the airline's from,
        their English, and their sex."""
        if self._crew is None or self._crew_for != self.facts.callsign:  # (cast once the callsign is known)
            self._crew_for = self.facts.callsign
            chosen = BY_ID.get(self.settings.crew)
            if chosen is None:
                chosen = PEOPLE[crc(f"{self.facts.callsign}|{self.seed}") % len(PEOPLE)]
            locale = self.locale()
            english_names = locale.lower().startswith("en") and locale.lower() not in ("en-in", "en-sg", "en-hk")
            name = chosen.name if english_names else crew_name(locale, chosen.sex, f"{self.facts.callsign}|{chosen.id}")
            self._crew = (chosen, name, locale, chosen.sex)
        return self._crew

    def languages(self) -> tuple[str, ...]:
        """The languages the announcements are made in, the first first."""
        _, airline = self.airline()
        langs = tuple(airline.languages) if airline is not None else ("en",)
        if not self.settings.english_too and langs[0] != "en":
            langs = tuple(lang for lang in langs if lang != "en")
        return langs or ("en",)

    def passengers(self) -> int:
        """How many are on board: what GSX is boarding, else the flight plan's, else about what the aircraft would carry."""
        if self._gsx_pax or self.facts.pax:
            return self._gsx_pax or self.facts.pax
        capacity = seats(self.facts.aircraft) or 150
        load = 0.72 + 0.26 * ((crc(f"load|{self.facts.callsign}|{self.seed}") % 1000) / 999)
        return max(1, int(capacity * load))

    # ---- what they say ----

    def _night(self) -> bool:
        local = self._local_s()
        if local is None:
            return False
        hour = local / 3600 % 24
        return hour >= 19.5 or hour < 6

    def _local_s(self) -> float | None:
        own = self._own
        if own is None or own.zulu_s is None:
            return None
        if own.local_s is not None:
            return own.local_s
        return (own.zulu_s + own.lon / 15 * 3600) % 86400  # no local time from the sim: by the sun

    def _planned_s(self) -> float:
        f = self.facts
        if f.planned_s:
            return f.planned_s
        if None not in (f.dest_lat, f.dest_lon, f.origin_lat, f.origin_lon):
            nm = haversine_nm(f.origin_lat, f.origin_lon, f.dest_lat, f.dest_lon)
            return nm / 430 * 3600 + 20 * 60
        return 0.0

    def _remaining_s(self) -> float:
        f, own = self.facts, self._own
        if own is None or f.dest_lat is None or f.dest_lon is None:
            return self._planned_s()
        return haversine_nm(own.lat, own.lon, f.dest_lat, f.dest_lon) / max(own.gs_kt, 250.0) * 3600

    def _city(self, landed: bool):
        own, f = self._own, self.facts
        if landed and own is not None:
            return city_of(own.lat, own.lon)
        if f.dest_lat is not None and f.dest_lon is not None:
            return city_of(f.dest_lat, f.dest_lon)
        return None

    def _values(self, *, landed: bool = False) -> tuple[dict[str, str], dict[str, str]]:
        """What the templates are filled with: as shown, and as said."""
        f = self.facts
        code, airline = self.airline()
        person, name, english, _ = self.crew()
        city = self._city(landed)
        number = flight_number(f.callsign)
        text: dict[str, str] = {"name": name, "airline": airline.name if airline else (code or "LocalTC Air")}
        if number:
            text["flight"] = number
        if city is not None:
            text["dest"] = city.name
            text["dest_about"] = describe(city)
        planned = self._planned_s()
        if planned:
            text["time"] = duration_words(planned)
        captain = self.settings.captain.strip()
        text["captain"] = f"Captain {captain}" if captain else "the captain"
        text["Captain"] = f"Captain {captain}" if captain else "The captain"
        text["pax"] = str(self.passengers())
        local = self._local_s()
        hour = (local or 12 * 3600) / 3600 % 24
        text["greeting"] = "Good morning" if 4 <= hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        type_facts = aircraft_facts(f.aircraft)
        if type_facts is not None:
            text["aircraft"], text["aircraft_fact"] = type_facts.name, type_facts.fact
            if type_facts.overwing:
                text["overwing"] = ("If you are sitting beside an exit over the wing, please read the card in your seat "
                                    "pocket: you would open it. ")
        count, where = exits(f.aircraft)
        text["exits"] = (f"{words(count)} emergency exits: {where}" if count else
                         f"emergency exits {where}" if where else "emergency exits at the front and the rear")
        if f.cruise_ft:
            text["cruise"] = (f"flight level {f.cruise_ft // 100}" if f.cruise_ft >= 18000 else f"{f.cruise_ft:,} feet")
        program = airline.program if airline else ""
        if program:
            text["program"] = program
            text["alliance_part"] = f", and to members of {airline.alliance}" if airline.alliance else ""
        said = dict(text)
        if number:
            said["flight"] = flight_words(number)
        said["pax"] = words(self.passengers())
        if f.cruise_ft:
            said["cruise"] = (f"flight level {flight_words(str(f.cruise_ft // 100))}" if f.cruise_ft >= 18000
                              else f"{words(f.cruise_ft)} feet")
        twelve = self.locale().lower() in ("en-us", "en-ca", "en-au", "en-nz", "en-ph", "en-in")
        if local is not None and (landed or self._near_destination()):
            shown, spoken = clock(local, twelve=twelve)
            text["local"], said["local"] = f"The local time is {shown}. ", f"The local time is {spoken}. "
        temp, raw = self._destination_weather(landed)
        if temp is not None:
            look = sky(raw, self._night()) if raw else ""
            c = int(round(temp))
            if self.locale().lower() == "en-us" or (f.destination or "").upper().startswith("K"):
                text["weather"] = f"It's {fahrenheit(temp)} degrees Fahrenheit, {c} Celsius{look}. "
                said["weather"] = f"It's {words(fahrenheit(temp))} degrees Fahrenheit, {words(c)} Celsius{look}. "
            else:
                text["weather"] = f"It's {c} degrees{look}. "
                said["weather"] = f"It's {words(c)} degrees{look}. "
        return text, said

    def _near_destination(self) -> bool:
        return self._remaining_s() < 3600

    def _destination_weather(self, landed: bool) -> tuple[float | None, str]:
        dest = (self.facts.destination or "").upper()
        report = self._weather.get(dest)
        if landed and self._own is not None and self._own.temperature_c is not None:
            return self._own.temperature_c, report.raw if report else ""
        if report is not None and report.temperature_c is not None:
            return report.temperature_c, report.raw
        return None, ""

    def _wording(self, key: str, values: dict[str, str]) -> str | None:
        """``key``'s line in this crew member's manner, filled in; None when no wording can be filled."""
        person = self.crew()[0]
        lines = LINES.get(key, {})
        options = [*lines.get(person.manner, ()), *lines.get("polished", ())]
        rng = random.Random(crc(f"{key}|{self.facts.callsign}|{self.seed}"))
        first = options[: len(lines.get(person.manner, ()))] or options
        for pool in (first, options):
            ok = [o for o in pool if fill(o, values) is not None]
            if ok:
                return fill(rng.choice(ok), values)
        return None

    def _english(self, key: str, *, landed: bool = False, extra: dict[str, str] | None = None) -> tuple[str, str] | None:
        text_values, said_values = self._values(landed=landed)
        text_values.update(extra or {})
        said_values.update(extra or {})
        for part in ("members", "members_bye"):  # (a sentence of its own inside the line: its space kept)
            text_values[part] = ((self._wording(part, text_values) or "") + " ").lstrip()
            said_values[part] = ((self._wording(part, said_values) or "") + " ").lstrip()
        # The same wording for both (chosen by key and flight): the text is what was said.
        text, said = self._wording(key, text_values), self._wording(key, said_values)
        if text is None or said is None:
            return None
        return text, said

    def _speech(self, text: str, spoken: str, channel: str, *, chime: str = "", language: str = "") -> CabinSpeech:
        person, name, english, sex = self.crew()
        return CabinSpeech(t=self._t, text=text, spoken=spoken if spoken != text else "", speaker=name,
                           channel=channel, chime=chime if self.settings.chime else "", sex=sex, locale=english,
                           language="" if language == "en" else language)

    def announce(self, key: str, *, landed: bool = False, chime: bool = True, first_only: bool = False,
                 extra: dict[str, str] | None = None) -> list[Any]:
        """An announcement on the address system, in each of the airline's languages (the chime before the first;
        ``first_only``: an order to the crew, given once)."""
        if not self.settings.announcements:
            return []
        out: list[Any] = []
        for lang in self.languages()[:1] if first_only else self.languages():
            if lang == "en":
                said = self._english(key, landed=landed, extra=extra)
                if said is None:
                    continue
                text, spoken = said
            else:
                template = FOREIGN.get(lang, {}).get(key)
                values = self._foreign_values(lang, landed)
                text = fill(template, values) if template else None
                if text is None:
                    continue
                spoken = text
            out.append(self._speech(text, spoken, "pa", chime="pa" if chime and not out else "", language=lang))
            if lang == "en" and self._worded(key):
                self._to_word.add(text)
        return out

    def _foreign_values(self, lang: str, landed: bool) -> dict[str, str]:
        values, _ = self._values(landed=landed)
        planned = self._planned_s()
        if planned:
            values["ltime"] = duration_in(lang, planned)
        return values

    def call(self, key: str, *, extra: dict[str, str] | None = None, chime: str = "", channel: str = "interphone"
             ) -> list[Any]:
        """A call of the crew's own to the flight deck (``LINES``): in English, on the interphone."""
        said = self._english(key, extra=extra)
        if said is not None and channel == "interphone":
            self._called_t = self._t
        return [self._speech(said[0], said[1], channel, chime=chime)] if said is not None else []

    def reply(self, key: str, extra: dict[str, str] | None = None, *, channel: str = "interphone") -> list[Any]:
        """An answer to the pilot (``REPLIES``), on the interphone (or in person: ``channel`` "deck")."""
        extra = extra or {}
        if (own := cabin_words.REPLIES.get(self.talks_in, {}).get(key)) is not None:
            # In the captain's language. (Their drink too: "coffee, black" said back in English isn't what was asked for.)
            text_values, _ = self._values()
            text_values.update({k: cabin_words.drink_said(v, self.talks_in) if k == "drink" else v for k, v in extra.items()})
            words = fill(own, text_values)
            if words is not None:
                return [self._speech(words, words, channel, language=self.talks_in)]
        said = self._reply(key, extra)
        return [self._speech(said[0], said[1], channel)] if said is not None else []

    def _reply(self, key: str, extra: dict[str, str]) -> tuple[str, str] | None:
        text_values, said_values = self._values()
        text_values.update(extra)
        said_values.update(extra)
        options = REPLIES.get(key, ())
        ok = [o for o in options if fill(o, text_values) is not None]
        if not ok:
            return None
        choice = self._rng.choice(ok)
        return fill(choice, text_values), fill(choice, said_values)

    # ---- the events ----

    def observe(self, ev: Any) -> list[Any]:
        """Everything that happens; what the cabin says and plays in return, one line at a time."""
        return self._paced(self._happened(ev), ev.t)

    def _paced(self, out: list[Any], t: float) -> list[Any]:
        """What's to be said waits its turn behind what's being said (shown and heard one at a time, not three
        languages of an announcement at once); the sounds don't wait."""
        self._queue += [o for o in out if isinstance(o, CabinSpeech)]
        out = [o for o in out if not isinstance(o, CabinSpeech)]
        if self._queue and t >= self._free_at:
            line = msgspec.structs.replace(self._queue.pop(0), t=t)
            self._free_at = t + _speaking_s(line) + GAP_S
            out.append(line)
        return out

    def _happened(self, ev: Any) -> list[Any]:
        self._t = max(self._t, ev.t)
        out: list[Any] = []
        if isinstance(ev, SimLifecycle) and ev.kind in ("flight_loaded", "aircraft_loaded"):
            self._new_flight()
            self._t = ev.t
            return []
        if isinstance(ev, AircraftIdentity):
            self._identity = ev
        elif isinstance(ev, WeatherReport):
            self._weather[ev.icao.upper()] = ev
        elif isinstance(ev, PhaseChanged):
            self._phase = ev.phase
        elif isinstance(ev, AircraftVars):
            self._vars.update(ev.values)
        elif isinstance(ev, GroundStatus):
            for service in ev.services:  # "pax 23/133"
                if service.id in ("Boarding", "Deboarding") and (m := GSX_PAX.search(service.status)) and int(m[1]):
                    self._gsx_pax = int(m[1])
        elif isinstance(ev, AircraftSystems):
            out += self._systems(ev)
        elif isinstance(ev, OwnshipState):
            out += self._ownship(ev)
        elif isinstance(ev, FlightArrived):
            self._at.setdefault("arrived", ev.t)
        if self._own is not None and self.active:
            out += self._moments(ev.t)
            out += self._due(ev.t)
        return out

    def _systems(self, ev: AircraftSystems) -> list[Any]:
        out: list[Any] = []
        before, self._sys = self._belt, ev
        self._belt = ev.seatbelt
        if ev.seatbelt:
            self._belt_seen_on = True
        if before is not None and before != ev.seatbelt and self.active and self.settings.chime:
            out.append(CabinSound(t=ev.t, sound="chime", detail="seatbelt"))
        if before is True and not ev.seatbelt and self._airborne and self.active:
            out += self._belt_off(ev.t)
        elif before is False and ev.seatbelt and self._airborne and self.active and "seatbelt_off" in self.done \
                and "descent" not in self.done:
            out += self._belt_on(ev.t)
        return out

    def _belt_off(self, t: float) -> list[Any]:
        if "seatbelt_off" not in self.done:
            if self._own is None or self._own.alt_agl_ft < 3000 or self._phase in ("APPROACH", "LANDING"):
                return []
            self._mark("seatbelt_off", t)
            return self.announce("seatbelt_off", chime=False)
        if "descent" in self.done or not self._service_paused:
            return []
        self._service_paused = False
        return self.announce("seatbelt_again_off", chime=False)

    def _belt_on(self, t: float) -> list[Any]:
        self._mark("turbulence", t)
        out = self.announce("turbulence", chime=False)
        if "service" in self.done or "service_call" in self.done:
            self._service_paused = True
            out += self.announce("turbulence_service", chime=False)
        return out

    def _ownship(self, own: OwnshipState) -> list[Any]:
        out: list[Any] = []
        if self._start is None:
            self._start = own.t
        if not own.on_ground:
            self._last_vs = own.vs_fpm
        # (Parking brake set off the ground: the pilot moving the aircraft in slew, not a flight.)
        airborne = not own.on_ground and own.alt_agl_ft > 50 and not own.parking_brake
        if airborne and not self._airborne:
            self._was_airborne = True
            self._mark("airborne", own.t)
            self._queue.clear()  # what was still to be said on the ground
        if own.on_ground and self._airborne:  # touchdown
            self._landed_t = own.t
            self._touchdown_fpm = abs(self._last_vs)
            self._mark("touchdown", own.t)
            if self.active:
                out += self._touchdown(own.t)
        self._airborne = airborne if airborne or own.on_ground or own.parking_brake else self._airborne
        if airborne and own.alt_msl_ft >= 10000 and self._above_10k_t is None:
            self._above_10k_t = own.t
        self._own = own
        return out

    def _touchdown(self, t: float) -> list[Any]:
        if self.settings.applause and self._touchdown_fpm < SOFT_FPM:
            chance = 0.85 if self._touchdown_fpm < 120 else 0.6
            if random.Random(crc(f"clap|{self.facts.callsign}|{self.seed}")).random() < chance:
                self._mark("applause", t)
                return [CabinSound(t=t, sound="applause", seed=crc(self.facts.callsign) % 1000)]
        return []

    def _mark(self, key: str, t: float) -> None:
        self.done.add(key)
        self._at.setdefault(key, t)

    def _var(self, name: str) -> float:
        return self._vars.get(name, 0.0)

    def _door_open(self) -> bool:
        return any(self._var(d) > 0.5 for d in DOORS)

    def _length(self, said: list[Any]) -> float:
        return sum(_speaking_s(s) + GAP_S for s in said if isinstance(s, CabinSpeech))

    def _moment(self, key: str, t: float, said: list[Any]) -> list[Any]:
        """``key`` done now; it takes as long as its words to say."""
        self._mark(key, t)
        self._at[key + "_end"] = t + self._length(said)
        return said

    def _rolling(self, own: Any, t: float) -> bool:
        """On the ground and moving for a few seconds: one reading of 2.5 knots as GSX's jetway came up was "everyone's
        on" with nobody on board."""
        if not own.on_ground or own.gs_kt <= 2.0:
            self._rolling_since = None
            return False
        self._rolling_since = t if self._rolling_since is None else self._rolling_since
        return t - self._rolling_since >= ROLLING_S

    def _moments(self, t: float) -> list[Any]:  # noqa: C901 - the flight, in order
        own, sys = self._own, self._sys
        if own is None:
            return []
        out: list[Any] = []
        s = self.settings
        engines = (sys.engines_running if sys is not None else 0) or (1 if own.engine_running else 0)
        beacon = sys.light_beacon if sys is not None else False
        moving = self._rolling(own, t)
        before_departure = not self._was_airborne
        if before_departure and own.on_ground:
            board, departure = self._var(GSX_BOARDING), self._var(GSX_DEPARTURE)
            gsx = board >= 1 and board != 3  # GSX is looking after the boarding (3: skipped in it)
            door = self._door_open()
            begun = board in (4, 5, 6) if gsx else door
            finished = board == 6 if gsx else "door_seen" in self.done and not door
            # (GSX still boarding: nobody's "all on" for the beacon or an engine, only once the flight really goes)
            boarding = gsx and board in (4, 5)
            ready = (not boarding and (beacon or engines > 0)) or moving or departure >= 4 \
                or self._phase in ("PUSHBACK", "TAXI_OUT", "RUNWAY_HOLD")
            pushing = moving or departure == 5 or self._phase in ("PUSHBACK", "TAXI_OUT", "RUNWAY_HOLD")
            if door:
                self._mark("door_seen", t)
            if begun and "begun" not in self.done:
                self._mark("begun", t)
                if s.boarding_music and not ready:
                    self._mark("music", t)
                    out.append(CabinSound(t=t, sound="music", seed=crc(self.facts.callsign) % 1000))
            # (GSX's "requested" is the music; the welcome's for when somebody's walking on. It was said three minutes before anybody did.)
            if "boarding" not in self.done and "begun" in self.done and not ready and not finished \
                    and (not gsx or board == 5) and t - self._at["begun"] >= BOARDING_PA_S:
                out += self._moment("boarding", t, self.announce("boarding"))
            if pushing:
                self.done.add("pushed")
            if not pushing and "pushed" not in self.done:  # (off blocks is off blocks: stopped to start up, nobody's "running behind")
                out += self._schedule(t, gsx=gsx)
            if "boarded" not in self.done and (finished or ready):
                self._mark("boarded", t)
                if s.interphone:
                    out += self.call("boarded")
            # The doors armed with the door shut (GSX's boarding over, the door may yet be open: at the beacon).
            if "doors_arm" not in self.done and "boarded" in self.done and (ready or (not gsx and not door)):
                if "music" in self.done and "music_stop" not in self.done:
                    self._mark("music_stop", t)
                    out.append(CabinSound(t=t, sound="music_stop"))
                key = "doors_arm_us" if self.locale().lower() == "en-us" else "doors_arm"
                out += self._moment("doors_arm", t, self.announce(key, first_only=True))
            if "welcome" not in self.done and "doors_arm" in self.done \
                    and t >= self._at.get("doors_arm_end", 0) + WELCOME_AFTER_DOORS_S:
                out += self._moment("welcome", t, self.announce("welcome"))
            if "safety" not in self.done and "welcome" in self.done and s.safety_demo and pushing \
                    and t >= self._at.get("welcome_end", 0):  # with the push, as the crew stand in the aisles
                out += self._moment("safety", t, self.announce("exit_row") + self.announce("safety") + self.announce("safety_more"))
            at_runway = own.on_runway or self._phase == "RUNWAY_HOLD"
            done_talking = t >= max(self._at.get("safety_end", 0), self._at.get("welcome_end", 0)) + SECURE_AFTER_SAFETY_S
            taxiing = engines > 0 and (moving or self._phase == "TAXI_OUT")
            if "secure_takeoff" not in self.done and "welcome" in self.done \
                    and ("safety" in self.done or not s.safety_demo) and ((done_talking and taxiing) or at_runway):
                if self._night() and "night" not in self.done:
                    self._mark("night", t)
                    out += self.announce("night")
                self._mark("secure_takeoff", t)
                if s.interphone:
                    out += self.call("secure_takeoff")
            return out
        if self._airborne:
            out += self._cruise(t)
            return out
        if self._landed_t is not None and own.on_ground:
            out += self._arrival(t)
        return out

    def _cruise(self, t: float) -> list[Any]:
        own, s = self._own, self.settings
        out: list[Any] = []
        assert own is not None
        # An aircraft whose seatbelt sign has never shown on (none modelled): free to move about after the climb.
        if "seatbelt_off" not in self.done and not self._belt_seen_on and self._above_10k_t is not None \
                and t - self._above_10k_t >= NO_BELT_CLIMB_S and own.vs_fpm > -300:
            self._mark("seatbelt_off", t)
            out += self.announce("seatbelt_off_generic")
        descending = "descent" not in self.done and "seatbelt_off" in self.done and (
            self._phase == "ARRIVAL" or (own.vs_fpm < -700 and self._remaining_s() < 45 * 60
                                         and own.alt_msl_ft > 8000))
        if "aircraft_talk" not in self.done and "seatbelt_off" in self.done and not descending and own.vs_fpm > -300 \
                and t - self._at["seatbelt_off"] >= AIRCRAFT_TALK_S and self._remaining_s() >= MIN_SERVICE_S:
            self._mark("aircraft_talk", t)
            out += self.announce("aircraft_talk")
        if "service_call" not in self.done and "seatbelt_off" in self.done and s.service and not descending \
                and t - self._at["seatbelt_off"] >= SERVICE_CALL_S and self._remaining_s() >= MIN_SERVICE_S \
                and not self._service_paused:
            self._mark("service_call", t)
            if s.interphone:
                out += self.call("service_call")
                self._asking = ("drink", t)
            self._later.append(_Later(t + SERVICE_PA_S, "service"))
        if descending:
            self._mark("descent", t)
            self._service_paused = False
            out += self.announce("descent")
        if "descent" in self.done and "landing" not in self.done and own.alt_msl_ft < 10000 and own.vs_fpm < -200 \
                and own.alt_agl_ft > 1500:
            out += self._moment("landing", t, self.announce("landing"))
            self._later.append(_Later(t + LANDING_SECURE_S, "secure_landing"))
        return out

    def _arrival(self, t: float) -> list[Any]:
        own, sys, s = self._own, self._sys, self.settings
        assert own is not None and self._landed_t is not None
        out: list[Any] = []
        if "after_landing" not in self.done and (
                (own.gs_kt < 30 and not own.on_runway) or t - self._landed_t >= AFTER_LANDING_S + 60) \
                and t - self._landed_t >= 8:
            out += self._moment("after_landing", t, self.announce("after_landing", landed=True))
        engines = (sys.engines_running if sys is not None else 0) or (1 if own.engine_running else 0)
        parked = own.gs_kt < 0.5 and (own.parking_brake or "arrived" in self._at) and \
            (engines == 0 or "arrived" in self._at or (sys is not None and not sys.light_beacon))
        if "doors_disarm" not in self.done and "after_landing" in self.done and parked:
            key = "doors_disarm_us" if self.locale().lower() == "en-us" else "doors_disarm"
            out += self._moment("doors_disarm", t, self.announce(key))
        if "goodbye" not in self.done and "doors_disarm" in self.done \
                and t >= self._at.get("doors_disarm_end", 0) + GOODBYE_AFTER_DOORS_S:
            out += self._moment("goodbye", t, self.announce("goodbye", landed=True))
            if s.boarding_music:
                out.append(CabinSound(t=t, sound="music", seed=crc(self.facts.callsign) % 1000 + 1))
                self._at["music2"] = t
        off = self._var(GSX_DEBOARDING) == 6 if self._var(GSX_DEBOARDING) >= 1 and self._var(GSX_DEBOARDING) != 3 \
            else t - self._at.get("goodbye", t) >= DEBOARDED_S  # GSX's deboarding over, else a little while
        if "deboarded" not in self.done and "goodbye" in self.done and off:
            self._mark("deboarded", t)
            if "music2" in self._at:
                out.append(CabinSound(t=t, sound="music_stop"))
            if s.interphone:
                verdict = self._landing_words()
                said = self.call("deboarded")
                if said and verdict:
                    first = said[0]
                    said = [CabinSpeech(**{**_fields(first), "text": f"{first.text} {verdict}",
                                           "spoken": f"{first.spoken or first.text} {verdict}"})]
                out += said
        return out

    def _landing_words(self) -> str:
        if self._landed_t is None:
            return ""
        kind = "soft" if self._touchdown_fpm < SOFT_FPM else "firm" if self._touchdown_fpm > FIRM_FPM else "normal"
        if kind == "soft" and "applause" in self.done:
            kind = "applauded"
        return random.Random(crc(f"verdict|{self.seed}")).choice(LANDING_WORDS[kind])

    def _due(self, t: float) -> list[Any]:
        out: list[Any] = []
        for later in [x for x in self._later if x.due <= t]:
            if not self._may_come_in(later.what):
                continue  # held until it's a good moment (not below 10,000 ft)
            self._later.remove(later)
            out += self._do_later(later)
        if self._asking is not None and t - self._asking[1] > ASKING_S:
            self._asking = None
        return out

    def _may_come_in(self, what: str) -> bool:
        """A crew member coming into the flight deck, or calling it: never in the critical phases."""
        own = self._own
        if own is None:
            return True
        if what in ("service", "secure_landing"):
            return True
        if own.on_ground:
            return own.gs_kt < 2.0 or what != "delivered"
        return own.alt_msl_ft >= 10000

    def _do_later(self, later: _Later) -> list[Any]:
        t = later.due
        if later.what == "service":
            if self._service_paused or "descent" in self.done:
                return []
            self._mark("service", self._t)
            _, airline = self.airline()
            return self.announce("service_sale" if airline is not None and airline.low_cost else "service")
        if later.what == "secure_landing":
            own = self._own
            if "secure_landing" in self.done or own is None or own.on_ground or own.alt_agl_ft < 1000:
                return []
            self._mark("secure_landing", t)
            return self.call("secure_landing") if self.settings.interphone else []
        if later.what == "delivered":
            hot = later.data.get("hot") == "1"
            key = "meal_delivered" if later.data.get("meal") else "drink_delivered" if hot else "drink_delivered_cold"
            return self.reply(key, {"drink": later.data.get("drink", "")}, channel="deck")
        if later.what == "announce":
            return self.announce(later.data["key"])
        if later.what == "relay":
            text = f"Ladies and gentlemen, a message from the flight deck: {later.data['message']}"
            return [self._speech(text, text, "pa", chime="pa")]
        if later.what == "say":
            return [self._speech(later.data["message"], later.data["message"], "pa", chime="pa")]
        if later.what == "secure_takeoff":
            return self.call("secure_takeoff") if self.settings.interphone else []
        return []

    # ---- the pilot on the intercom ----

    def claims(self, text: str, t: float, to: str = "") -> bool:
        """Whether the pilot's words are for the cabin (or the passengers): said on the cabin's own key (``to``),
        addressed to them, an announcement of the pilot's own, or the answer to the question they've asked."""
        if not self.active or not text.strip():
            return False
        if to == "cabin":
            return True
        if RAMP_CALL.match(ramp_words.as_english(text, self.talks_in)):
            return False  # for the ground crew, even with the cabin still listening
        text = cabin_words.as_english(text, self.talks_in)
        if self._address(text) is not None or CAPTAIN_PA.match(text):
            return True
        if t <= self._listening_until:
            return True
        if t - self._called_t <= LISTENING_S and not copilot_command(text) and "checklist" not in text.lower():
            return True  # they've just called: the answer's theirs
        if self._asking is not None and t - self._asking[1] <= ASKING_S:
            return bool(_drink(text) or NO_THANKS.search(text) or re.search(r"\b(?:meal|food|something\s+to\s+eat)\b", text, re.I))
        return False

    def _address(self, text: str) -> str | None:
        """The words after "cabin, ...", or None when they're not addressed to the cabin."""
        name = self.crew()[1].split()[0] if self.active else "cabin"
        m = re.compile(ADDRESS.pattern.replace("{name}", re.escape(name)), re.I).match(text)
        if m:
            return text[m.end():].strip()
        # Whisper's spelling of their name ("Stephan", "Steven") or of the cabin ("Kevin").
        first = re.match(r"\s*(?:(?:hey|hi|hello|okay|ok)[,.]?\s+(?:there[,.]?\s+)?)?([^\W\d_]+)[,.!?]?\s*", text)
        if first and (difflib.SequenceMatcher(None, first.group(1).lower(), name.lower()).ratio() >= 0.7
                      or first.group(1).lower() in ("kevin", "cabins", "cabin's")):
            return text[first.end():].strip()
        return None

    def heard(self, ev: IntercomHeard) -> list[Any]:
        """The cabin's answer to the pilot's words (``claims`` said they're the cabin's)."""
        self._t = max(self._t, ev.t)
        text = cabin_words.as_english(ev.text.strip(), self.talks_in)
        if CAPTAIN_PA.match(text):
            return []  # the captain's own announcement
        body = self._address(text)
        listening = ev.t <= self._listening_until
        self._called_t = ev.t  # (a conversation: the pilot's next words are theirs too, unless a command)
        self._listening_until = -1.0
        if body is None:
            body = text
        if not body.strip(" .,!?"):
            self._listening_until = ev.t + 20.0
            return self.reply("hello")
        return self._answer(body, ev.t, asked=self._asking is not None or listening)

    def word_announcement(self, draft: str, t: float) -> tuple[str, list[LlmExchange]]:
        """An announcement in the crew member's own words, different each flight, from the language model; ``draft``, the
        script's, if it can't do it or loses a fact (every number and time in it has to be there still)."""
        self._to_word.discard(draft)
        if self.backend is None:
            return draft, []
        person, name, _, _ = self.crew()
        system = (f"You are {name}, the cabin manager on this flight, making an announcement to the passengers. Your manner: "
                  f"{person.brief}. Say the announcement in your own fresh words, the way you'd say it today: about the "
                  "same length, spoken, plain, no stage directions. Keep every fact, name, number and instruction in it and "
                  'add no fact of your own. Reply as JSON: {"reply": "..."}')
        request = LlmRequest("crew", system, (("user", f'The announcement: "{draft}"'),),
                             {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]},
                             max_tokens=240)
        result = self.backend.complete(request, timeout_s=self.model_wait_s)
        reply = ""
        if result.text:
            try:
                reply = str(json.loads(result.text).get("reply", "")).strip()
            except (ValueError, AttributeError):
                reply = ""
        wanted = {n.lower().strip(".,") for n in NUMBER.findall(draft)}
        kept = {n.lower().strip(".,") for n in NUMBER.findall(reply)}
        good = bool(reply) and wanted <= kept and 0.5 * len(draft) <= len(reply) <= 1.6 * len(draft)
        model = getattr(self.backend, "model", "")
        record = LlmExchange(t=t, purpose="cabin", model=model, key=request.key(model), prompt=draft,
                             response=result.text or "", outcome="used" if good else "rejected",
                             detail=result.error or ("" if good else "a fact lost, or the wrong length: the script's words"),
                             latency_ms=result.latency_ms)
        return (reply if good else draft), [record]

    def _worded(self, key: str) -> bool:
        """Whether this announcement goes to the model, by ``llm_mode``."""
        if self.backend is None:
            return False
        if self.llm_mode == "llm":
            return not key.startswith(ALWAYS_SCRIPTED)
        return self.llm_mode == "mostly_llm" and key in OWN_WORDS

    def answers_by_model(self, draft: str) -> bool:
        """Whether this answer to the pilot goes to the model: Semi, only what the script has no answer to."""
        if self.backend is None or self.llm_mode == "scripted":
            return False
        return self.llm_mode != "semi" or self._unclear(draft)

    def _unclear(self, draft: str) -> bool:
        return draft in [fill(o, {}) for o in REPLIES["unclear"]] or draft == cabin_words.REPLIES.get(self.talks_in, {}).get("unclear")

    def wants_words(self, text: str) -> bool:
        return text in self._to_word

    def word(self, heard: str, draft: str, t: float) -> tuple[str, list[LlmExchange]]:
        """The answer in the crew member's own words, from the language model (``backend``), knowing what they're doing
        about it (``draft``: what the script would say, and what's said when the model can't answer)."""
        if self.backend is None:
            return draft, []
        person, name, _, _ = self.crew()
        facts = self._conversation_facts()
        system = (f"You are {name}, the cabin manager on this flight, on the interphone with the captain. Your manner: "
                  f"{person.brief}. Answer as a real flight attendant talks: one or two short spoken sentences, plain "
                  "words, no stage directions. Use only the facts given and never make up a number, a time or a gate. "
                  "Anything about flying the aircraft is the flight deck's, not yours. "
                  + (f"Answer in {self.language_name}. " if self.language_name else "") +
                  'Reply as JSON: {"reply": "..."}')
        ask = ("Answer as best you can from the facts; if it isn't something you'd know, say so." if self._unclear(draft) else
               f'What you would say, to put in your own words (keep what it promises): "{draft}"')
        prompt = ("Facts:\n" + "\n".join(f"{k}: {v}" for k, v in facts.items()) + f'\n\nCaptain: "{heard}"\n\n{ask}')
        request = LlmRequest("crew", system, (("user", prompt),),
                             {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]},
                             max_tokens=120)
        result = self.backend.complete(request, timeout_s=self.model_wait_s)
        model = getattr(self.backend, "model", "")
        reply = ""
        if result.text:
            try:
                reply = str(json.loads(result.text).get("reply", "")).strip()
            except (ValueError, AttributeError):
                reply = ""
        record = LlmExchange(t=t, purpose="cabin", model=model, key=request.key(model), prompt=prompt,
                             response=result.text or "", outcome="used" if reply else "error",
                             detail=result.error or "", latency_ms=result.latency_ms)
        return reply or draft, [record]

    def _where_we_are(self, t: float) -> tuple[str, dict[str, str]]:
        """The answer to "are we on time?", "how long for the boarding?": the reply's key and its values."""
        left = self._boarding_left_s(t)
        to_go = self._to_off_block()
        if left is not None:
            return "boarding_left", {"left": words(max(1, round(left / 60)))}
        if to_go is None:
            return "no_schedule", {}
        return ("on_time", {}) if to_go >= 0 else ("behind", {"minutes": words(max(1, round(-to_go / 60)))})

    def _conversation_facts(self) -> dict[str, str]:
        values, _ = self._values()
        own = self._own
        facts = {k: values[v] for k, v in (("airline", "airline"), ("flight", "flight"), ("destination", "dest"),
                                           ("flight time", "time"), ("passengers on board", "pax"), ("aircraft", "aircraft"),
                                           ("about the aircraft", "aircraft_fact"), ("exits", "exits"),
                                           ("loyalty programme", "program"), ("cruising", "cruise")) if v in values}
        if own is not None:
            facts["where"] = "on the ground" if own.on_ground else f"in the air at {round(own.alt_msl_ft, -2):,.0f} feet"
        done = [k for k in ("boarding", "boarded", "doors_arm", "welcome", "safety", "secure_takeoff", "seatbelt_off",
                            "service_call", "descent", "landing", "secure_landing", "after_landing") if k in self.done]
        facts["done so far in the cabin"] = ", ".join(done) or "nothing yet"
        to_go = self._to_off_block()
        if to_go is not None and not self._was_airborne:
            facts["off-block time (the plan)"] = f"{abs(round(to_go / 60))} minutes {'away' if to_go >= 0 else 'ago'}"
        left = self._boarding_left_s(self._t)
        if left is not None:
            facts["boarding to go, about"] = f"{round(left / 60)} minutes"
        facts["boarding"] = {4: "requested", 5: "in progress", 6: "complete"}.get(int(self._var(GSX_BOARDING)),
                                                                                  "complete" if "boarded" in self.done else "not reported")
        return facts

    # ---- the schedule ----

    def _to_off_block(self) -> float | None:
        """Seconds to the plan's off-block time (negative: late), None without a time that fits the sim's clock."""
        return off_block_in(self.facts.sched_out_s, self._own.zulu_s if self._own is not None else None)

    def _boarding_left_s(self, t: float) -> float | None:
        """How long the boarding has to go at GSX's pace, None when it isn't going (or isn't GSX's)."""
        board = self._var(GSX_BOARDING)
        pax = self.passengers()
        if board not in (4, 5) or not pax:
            return None
        return max(60.0, pax * BOARDING_S_PER_PAX - (t - self._at.get("boarding_going", t) if board == 5 else 0.0))

    def _schedule(self, t: float, *, gsx: bool) -> list[Any]:
        """A word when the flight is behind the plan: to the flight deck with what they know, and to the passengers
        once it's a real delay. Nothing's made up: what's said is where the boarding is and what the clock says."""
        if self._var(GSX_BOARDING) == 5:
            self._at.setdefault("boarding_going", t)
        to_go = self._to_off_block()
        if to_go is None:
            return []
        out: list[Any] = []
        begun, boarded = "begun" in self.done, "boarded" in self.done
        phone = self.settings.interphone
        if gsx and not begun and 0 < to_go <= NOT_BOARDING_S and "late_not_boarding" not in self.done:
            self._mark("late_not_boarding", t)
            if phone:
                out += self.call("late_not_boarding", extra={"minutes": words(max(1, round(to_go / 60)))})
        left = self._boarding_left_s(t)
        if to_go <= 0 and begun and not boarded and "late_boarding" not in self.done:
            self._mark("late_boarding", t)
            if phone:
                out += self.call("late_boarding", extra={"left": words(max(1, round(left / 60)))} if left else {"plain": ""})
        if to_go <= -READY_LATE_S and boarded and "late_ready" not in self.done:
            self._mark("late_ready", t)
            if phone:
                out += self.call("late_ready", extra={"minutes": words(round(-to_go / 60))})
        for i, late in enumerate(LATE_PA_S):
            if begun and to_go <= -late and f"late_pa{i}" not in self.done:
                self.done.update(f"late_pa{j}" for j in range(i + 1))
                reason = {"reason": "we're just finishing the boarding"} if not boarded else {"plain": ""}
                out += self.announce("delay", extra=reason)
                break
        return out

    def ground_relay(self, what: str, t: float) -> list[Any] | None:
        """The ground crew passing on boarding or deboarding: what's said about it. Not on this flight: None, it's theirs to
        say. (When it's over they say so at their own moment, whoever started it.)"""
        if not self.active:
            return None
        self._t = t
        if what == "boarding_start":
            return self.reply("ground_boarding")
        if what == "deboarding_start":
            return self.reply("ground_deboarding")
        return []

    def _answer(self, body: str, t: float, *, asked: bool) -> list[Any]:  # noqa: C901 - one reading per kind of call
        low = body.lower()
        reply = self.reply
        if self.ground_call is not None and not ASKS_ABOUT.match(low) and (m := START_BOARDING.search(low)):
            service = "Deboarding" if any(m.groups()) else "Boarding"
            if not self.ground_call(service, t):
                return reply("ground_not_yet")
            return reply("ground_deboarding" if service == "Deboarding" else "ground_boarding")
        drink = _drink(body)
        if drink is not None:
            self._asking = None
            name, hot, both = drink
            delay = self._rng.uniform(*DRINK_S)
            self._later.append(_Later(t + delay, "delivered", {"drink": name if not both else f"{name}s",
                                                               "hot": "1" if hot else ""}))
            return reply("drink_both" if both else "drink", {"drink": name})
        if re.search(r"\b(?:meals?|food|something\s+to\s+eat|sandwich|lunch|dinner|breakfast|snacks?)\b", low):
            self._asking = None
            self._later.append(_Later(t + self._rng.uniform(*DRINK_S) + 60, "delivered", {"meal": "1"}))
            return reply("meal")
        if asked and NO_THANKS.search(body):
            self._asking = None
            return reply("no_drink")
        if m := re.search(r"\b(?:tell|let)\s+(?:the\s+)?(?:passengers|everyone|folks|cabin)\s+(?:know\s+)?(?:that\s+)?(.+)", body, re.I) \
                or re.search(r"\b(?:make|do)\s+an?\s+announcement\s*(?:that|saying|:|,)?\s*(.+)", body, re.I) \
                or re.search(r"\bannounce\s+(?:that\s+)?(.+)", body, re.I):
            message = m.group(1).strip().rstrip(".") + "."
            message = message[:1].upper() + message[1:]
            self._later.append(_Later(t + 6.0, "relay", {"message": message}))
            return reply("relay")
        if re.search(r"\bdelay", low) or re.search(r"\b(?:waiting\s+for|holding\s+for)\s+(?:a\s+|our\s+)?(?:slot|de-?icing|gate|stand|fuel|catering|bags|baggage)", low):
            minutes = _minutes(low)
            msg = ("Ladies and gentlemen, the captain has let us know we have a short delay" +
                   (f" of about {words(minutes)} minutes" if minutes else "") +
                   ". We'll keep you updated, and thank you for your patience.")
            self._later.append(_Later(t + 6.0, "say", {"message": msg}))
            return reply("delay")
        if re.search(r"turbulen|bump|chop|rough\s+air|be\s+seated|seat\s*belts?\s+on|stop\s+the\s+service", low):
            self._service_paused = "service_call" in self.done
            self._later.append(_Later(t + 5.0, "announce", {"key": "turbulence"}))
            return reply("turbulence")
        if re.search(r"(?:prepare|secure|ready)\s+(?:the\s+)?(?:cabin\s+)?for\s+landing|seats?\s+for\s+landing|landing\s+in\s+\w+\s+minutes", low):
            if "secure_landing" in self.done:
                return reply("already_secure")
            if "landing" not in self.done:
                self._mark("landing", t)
                self._later.append(_Later(t + 5.0, "announce", {"key": "landing"}))
                self._later.append(_Later(t + LANDING_SECURE_S * 0.6, "secure_landing"))
            return reply("prepare_landing")
        if re.search(r"(?:prepare|secure|ready)\s+(?:the\s+)?(?:cabin\s+)?for\s+(?:take\s*off|departure)|seats?\s+for\s+take\s*off", low):
            if "secure_takeoff" in self.done:
                return reply("already_secure")
            self._mark("secure_takeoff", t)
            self._later.append(_Later(t + 30.0, "secure_takeoff"))
            return reply("prepare_takeoff")
        if re.search(r"how\s+many\s+(?:passengers|people|pax|souls)|passenger\s+count|head\s*count|pax\s+count", low):
            return reply("count")
        if re.search(r"\bon\s+time\b|\brunning\s+(?:late|behind)\b|\bare\s+we\s+late\b|how\s+long\b.*\bboard|\bboarding\b.*\b(?:done|finished|complete|long)\b", low):
            return reply(*self._where_we_are(t))
        if re.search(r"how\s+was\s+(?:the|my|that)\s+landing", low):
            verdict = self._landing_words() or "Ask me again once we've landed!"
            return reply("landing_feedback", {"verdict": verdict})
        if re.search(r"how(?:'s|\s+is|\s+are)\s+(?:the\s+cabin|everything|everyone|things|it\s+going|the\s+passengers)|everything\s+(?:ok|okay|alright|all\s+right|fine)|all\s+good\s+back", low):
            return reply("status")
        if re.search(r"(?:start|begin)\s+(?:the\s+)?service", low):
            if "service" not in self.done:
                self._later.append(_Later(t + 20.0, "service"))
            return reply("service_start")
        if re.search(r"safety\s+(?:demo|briefing|demonstration)", low):
            if "safety" in self.done:
                return reply("safety_done")
            self._mark("safety", t)
            self._later.append(_Later(t + 5.0, "announce", {"key": "safety"}))
            return reply("relay")
        if re.search(r"who(?:'s|\s+is)\s+(?:this|that|on|there)|what(?:'s|\s+is)\s+your\s+name", low):
            return reply("who")
        if re.search(r"\bthank|\bcheers\b|appreciate", low):
            return reply("thanks")
        return reply("unclear")


def _speaking_s(line: CabinSpeech) -> float:
    words = line.spoken or line.text
    if line.language == "zh":
        return len(words) * 0.24
    return len(words.split()) * SPEECH_S_PER_WORD


def _fields(speech: CabinSpeech) -> dict:
    return {f: getattr(speech, f) for f in speech.__struct_fields__}


def fill(template: str, values: dict[str, str]) -> str | None:
    """``template`` with its ``{names}`` filled; None when one isn't known (the optional parts are "" when not)."""
    optional = {"members", "members_bye", "local", "weather", "alliance_part", "overwing"}
    missing = [n for n in re.findall(r"\{(\w+)\}", template) if n not in values and n not in optional]
    if missing:
        return None
    out = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), ""), template)
    return re.sub(r"\s{2,}", " ", out).strip()


RAMP_CALL = re.compile(r"^\W*(?:ramp|ground\s+crew|chief|headset|tug)\b", re.I)


def _drink(text: str) -> tuple[str, bool, bool] | None:
    """The drink asked for, whether it's hot, and whether it's two of them ("coffee, white, one sugar")."""
    low = text.lower()
    for pattern, name, hot in DRINKS:
        if re.search(r"\b(?:" + pattern + r")", low):
            how = []
            if hot and name in ("coffee", "tea"):
                if re.search(r"\bblack\b", low):
                    how.append("black")
                elif re.search(r"\b(?:white|with\s+milk|milk)\b", low):
                    how.append("white")
                if m := re.search(r"\b(no|one|two|three|1|2|3)\s+sugars?\b", low):
                    n = {"1": "one", "2": "two", "3": "three"}.get(m.group(1), m.group(1))
                    how.append("no sugar" if n == "no" else f"{n} sugar" if n == "one" else f"{n} sugars")
                elif re.search(r"\bwith\s+sugar\b", low):
                    how.append("with sugar")
            drink = name + (", " + " ".join(how) if how else "")
            both = bool(re.search(r"\b(?:two|both\s+of\s+us|one\s+for\s+(?:the\s+)?(?:first\s+officer|f\.?o\.?|copilot|him|her))\b", low)) \
                and not re.search(r"\btwo\s+sugars?\b", low)
            return drink.replace("  ", " "), hot, both
    return None


def _minutes(text: str) -> int:
    if m := re.search(r"(\d+)\s*(?:min|minute)", text):
        return int(m.group(1))
    for words_, n in sorted(NUMBER_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{words_}\s+(?:min|minute)", text) or (words_ in ("an hour", "half an hour") and words_ in text):
            return n
    return 0
