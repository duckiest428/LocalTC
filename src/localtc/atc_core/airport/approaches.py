"""Which approach ATC gives you: what the airport publishes, what the weather allows, what the aircraft can fly.

**The approaches.** The sim's facility data lists an airport's instrument approaches (``Airport.approaches``):
ILS, localizer (LOC), LDA and SDF (offset and simplified localizers), localizer back course (reverse sensing),
RNAV (GPS) with its minima lines (LPV, LNAV/VNAV, LP, LNAV), RNAV (RNP) (RNP AR: authorization required),
VOR and VOR/DME, NDB and NDB/DME, and procedures with circling minima only (VOR-A). The sim doesn't say
whether a localizer approach needs DME, so they're all "LOC".

**Minima.** Each approach comes down to a height above the runway and a visibility: a decision altitude on a
glidepath (ILS, LPV, LNAV/VNAV, RNP) or a minimum descent altitude reached through step-down fixes (the rest).
The sim has no charts, so ``MINIMA`` holds typical published values. RNAV (GPS) minima depend on the aircraft:
LPV and LP need SBAS (WAAS), which the sim's GA navigators have and airliners mostly don't (they fly LNAV/VNAV
on baro-VNAV). An RNAV (RNP) approach is only for an aircraft that can fly RNP AR: an airliner.

**Choosing.** In the US and Canada an IFR arrival in good weather (ceiling and visibility for it, FAA JO
7110.65 7-4-3) is expected to fly the visual, the instrument approach to the same runway as the backup and for
anyone who asks. Otherwise it's the approach with the lowest minima the aircraft can fly, among those the
weather allows; ICAO regions keep the instrument approach. A glideslope out of service turns the ILS into
the localizer approach ("ILS or LOC RWY 16"); a localizer out takes every localizer approach to that runway
away. A runway without an instrument approach in weather too poor for a visual gets one to another runway,
then a circle to land (7110.65 4-8-6): "cleared VOR runway 16 approach, circle west of the airport for a left
downwind to runway 34". Circling minima and the area the aircraft may circle in grow with its approach
category (A to D).

Airport data recorded before approaches were read has none at all: those airports fall back to "ILS where the
runway has one, otherwise RNAV", which is what LocalTC did before.
"""

from dataclasses import dataclass

from localtc.atc_core.values import Approach
from localtc.sim_api import Airport

# The sim's approach types, as ATC names them.
KINDS = {"ils": "ILS", "localizer": "LOC", "lda": "LDA", "sdf": "SDF", "rnav": "RNAV", "gps": "RNAV", "vordme": "VOR/DME",
         "vor": "VOR", "ndbdme": "NDB/DME", "ndb": "NDB", "backcourse": "LOC BC"}
DISPLAY = KINDS  # (the old name)
LOCALIZER = frozenset({"ILS", "LOC", "LOC/DME", "LOC BC", "LDA", "SDF"})  # "until established on the localizer"
PRECISION = ("ILS", "LOC", "LDA", "RNAV", "RNP")  # (the old rule: in good weather, anything less and the visual is easier)
VISUAL = "VISUAL"
VISUAL_SM = 5.0  # visibility for a visual approach to be what's expected
VISUAL_CEILING_FT = 3000  # ... and a ceiling (above the field) comfortably over the vectoring altitudes, when it's known
RNAV_LINES = ("LPV", "LNAV/VNAV", "LP", "LNAV")


@dataclass(frozen=True)
class Minima:
    height_ft: int  # decision altitude (or MDA) above the touchdown zone
    visibility_sm: float
    glidepath: bool  # a decision altitude on a glidepath, or an MDA with step-down fixes

    def met(self, ceiling_ft: float | None, visibility_sm: float | None) -> bool:
        """Whether the weather is at or above these minima (unknown counts as good enough)."""
        return (ceiling_ft is None or ceiling_ft >= self.height_ft) and (visibility_sm is None or visibility_sm >= self.visibility_sm)


MINIMA = {
    "ILS": Minima(200, 0.5, True), "RNP": Minima(250, 0.75, True), "LPV": Minima(250, 0.75, True),
    "LNAV/VNAV": Minima(350, 1.0, True), "LP": Minima(400, 1.0, False), "LNAV": Minima(450, 1.0, False),
    "LOC": Minima(450, 1.0, False), "LOC/DME": Minima(400, 1.0, False), "LDA": Minima(500, 1.0, False),
    "SDF": Minima(500, 1.0, False), "LOC BC": Minima(500, 1.0, False), "VOR/DME": Minima(450, 1.0, False),
    "VOR": Minima(550, 1.0, False), "NDB/DME": Minima(550, 1.0, False), "NDB": Minima(650, 1.25, False),
    "GPS": Minima(450, 1.0, False),
}
# Circling, by approach category: higher and farther than any straight-in, with the area the aircraft may circle in
# (the radius from each runway threshold, TERPS's expanded circling areas).
CIRCLING = {"A": Minima(600, 1.0, False), "B": Minima(600, 1.0, False), "C": Minima(700, 1.75, False),
            "D": Minima(800, 2.25, False)}
CIRCLING_RADIUS_NM = {"A": 1.3, "B": 1.7, "C": 2.7, "D": 3.6}
APPROACH_LIGHTS_OUT_SM = 0.5  # approach lights out of service: the visibility minimum goes up

# Aircraft with no instrument approach capability. Matched against the sim's model name, lowercased.
VISUAL_ONLY = ("cub", "champ", "stearman", "glider", "sailplane", "balloon", "blimp", "ask 21", "ask21", "dg-1001",
               "dg 1001", "jenny", "waco", "trike", "paramotor", "ultralight", "gyrocopter", "biplane")
HEAVY = ("747", "b74", "777", "b77", "787", "b78", "a33", "a330", "a34", "a340", "a35", "a350", "a38", "a380", "md11",
         "md-11", "dc10", "dc-10", "767", "b76", "an-124", "an225", "c-5", "c-17", "kc-")
JETS = ("737", "b73", "b38", "b39", "a31", "a32", "a318", "a319", "a320", "a321", "a22", "a220", "bcs", "e17", "e19",
        "e-jet", "erj", "crj", "embraer", "757", "b75", "md8", "md-8", "md9", "717", "citation", "c25", "c56", "c68",
        "c700", "longitude", "phenom", "e50", "e55", "learjet", "lj", "gulfstream", "g650", "g-v", "global", "challenger",
        "falcon", "hondajet", "sf50", "vision jet", "pc-24", "pc24", *HEAVY)
TURBOPROPS = ("king air", "be20", "be35", "b350", "tbm", "pc-12", "pc12", "kodiak", "caravan", "c208", "atr", "at7",
              "q400", "dh8", "dash 8", "saab", "twin otter", "dhc-6", "conquest", "baron", "be58", "da62", "da42", "seneca")


def instrument_capable(aircraft_type: str) -> bool:
    name = (aircraft_type or "").lower()
    return not any(word in name for word in VISUAL_ONLY)


def _is(aircraft_type: str, names: tuple[str, ...]) -> bool:
    name = (aircraft_type or "").lower()
    return any(n in name for n in names)


def category(aircraft_type: str, airline: bool = False) -> str:
    """Approach category (by speed over the threshold): A light singles, B twins and turboprops, C jets, D heavies."""
    if _is(aircraft_type, HEAVY):
        return "D"
    if _is(aircraft_type, JETS) or airline:
        return "C"
    if _is(aircraft_type, TURBOPROPS):
        return "B"
    return "A"


def sbas(aircraft_type: str, airline: bool = False) -> bool:
    """LPV and LP minima need SBAS (WAAS). The sim's GA navigators have it; airliners fly LNAV/VNAV."""
    return not airline and not _is(aircraft_type, JETS)


def rnp_ar(aircraft_type: str, airline: bool = False) -> bool:
    """Can fly an RNAV (RNP) approach: RNP AR takes an airliner's FMS (and the crew's authorization)."""
    return airline or _is(aircraft_type, JETS)


@dataclass(frozen=True)
class Aircraft:
    type: str = ""
    airline: bool = False

    @property
    def category(self) -> str:
        return category(self.type, self.airline)


@dataclass(frozen=True)
class Outages:
    """What's out of service at an airport (the ATIS's notices): runway idents."""

    localizer: frozenset[str] = frozenset()  # the ILS (localizer and glideslope) out
    glideslope: frozenset[str] = frozenset()  # the glideslope only: the ILS is flown as a localizer approach
    approach_lights: frozenset[str] = frozenset()
    navaids: frozenset[str] = frozenset()  # approach kinds whose navaid is out ("VOR", "VOR/DME")


# What a pilot asking for one gets, best first: "the VOR" where there's a VOR/DME, "the RNAV" where it's RNP.
ALIASES = {"VOR": ("VOR", "VOR/DME"), "NDB": ("NDB", "NDB/DME"), "LOC": ("LOC", "LOC/DME"), "RNAV": ("RNAV", "RNP"),
           "GPS": ("RNAV", "RNP"), "VOR/DME": ("VOR/DME", "VOR"), "NDB/DME": ("NDB/DME", "NDB")}
NO_OUTAGES = Outages()
ANY_AIRCRAFT = Aircraft()


def published(airport: Airport | None, runway: str) -> tuple[str, ...]:
    """The approaches published to this runway, best first, by name: ``("ILS Z", "RNAV (GPS) Y", "VOR")``."""
    if airport is None or not airport.approaches:
        return ()
    names = []
    for proc in sorted(airport.approaches_to(runway), key=_rank):
        kind = "RNAV (RNP)" if proc.rnp_ar else "RNAV (GPS)" if KINDS.get(proc.kind) == "RNAV" else KINDS.get(proc.kind)
        if kind is None:
            continue
        name = f"{kind} {proc.suffix}" if proc.suffix else kind
        if proc.lines:
            name += f" ({', '.join(line.upper() for line in proc.lines)})"
        names.append(name)
    return tuple(dict.fromkeys(names))


def _rank(proc) -> tuple[int, str]:
    kind = "RNP" if proc.rnp_ar else KINDS.get(proc.kind, "")
    minima = MINIMA.get(_best_line(proc) if kind == "RNAV" else kind)
    return (minima.height_ft if minima else 9999, proc.suffix or "")


def _best_line(proc) -> str:
    lines = [line.upper() for line in proc.lines] or ["LNAV/VNAV"]
    return next((line for line in RNAV_LINES if line in lines), "LNAV")


def rnav_line(lines: tuple[str, ...], aircraft: Aircraft) -> str | None:
    """The RNAV (GPS) minima line this aircraft flies: the lowest published it can use. Data without the lines
    (older airport files) is taken as LNAV/VNAV, which nearly every RNAV approach has."""
    offered = [line.upper() for line in lines] or ["LNAV/VNAV", "LNAV"]
    usable = RNAV_LINES if sbas(aircraft.type, aircraft.airline) else ("LNAV/VNAV", "LNAV")
    return next((line for line in usable if line in offered), None)


@dataclass(frozen=True)
class Option:
    approach: Approach
    minima: Minima


def options(airport: Airport | None, runway: str, aircraft: Aircraft = ANY_AIRCRAFT, outages: Outages = NO_OUTAGES
            ) -> list[Option]:
    """The instrument approaches to ``runway`` this aircraft can fly with what's working, lowest minima first."""
    if airport is None:
        return []
    found: list[Option] = []
    for proc in airport.approaches_to(runway):
        kind = KINDS.get(proc.kind)
        if kind is None:
            continue
        line = ""
        if kind == "RNAV" and proc.rnp_ar:
            if not rnp_ar(aircraft.type, aircraft.airline):
                continue  # RNP AR: not for this aircraft
            kind = "RNP"
        elif kind == "RNAV":
            line = rnav_line(proc.lines, aircraft) or ""
            if not line:
                continue
        if kind in outages.navaids:
            continue
        if kind in LOCALIZER and kind != "LOC BC" and runway in outages.localizer:
            continue
        if kind == "LOC BC" and (_opposite(airport, runway) or "") in outages.localizer:
            continue  # the back course is the opposite runway's localizer
        if kind == "ILS" and runway in outages.glideslope:
            kind = "LOC"  # "ILS or LOC": without the glideslope, the localizer approach
        minima = MINIMA[line or kind]
        if runway in outages.approach_lights and minima.visibility_sm < 2:
            minima = Minima(minima.height_ft, minima.visibility_sm + APPROACH_LIGHTS_OUT_SM, minima.glidepath)
        found.append(Option(Approach(kind, runway, proc.suffix, minima=line), minima))
    order = ("ILS", "RNP", "RNAV", "LOC", "LOC/DME", "LDA", "SDF", "LOC BC", "VOR/DME", "VOR", "NDB/DME", "NDB", "GPS")
    found.sort(key=lambda o: (o.minima.height_ft, o.minima.visibility_sm, order.index(o.approach.kind), o.approach.suffix))
    unique: dict[str, Option] = {}
    for option in found:
        unique.setdefault(option.approach.kind, option)  # "ILS Z" and "ILS Y": the first (Z is the usual one)
    return list(unique.values())


def _opposite(airport: Airport, runway: str) -> str | None:
    for rw in airport.runways:
        if rw.primary.ident == runway:
            return rw.secondary.ident
        if rw.secondary.ident == runway:
            return rw.primary.ident
    return None


def choose_approach(
    airport: Airport | None,
    runway: str,
    *,
    has_ils: bool = False,
    visibility_sm: float | None = None,
    in_cloud: bool = False,
    ceiling_ft: float | None = None,
    aircraft_type: str = "",
    airline: bool = False,
    requested: str | None = None,
    override: str = "auto",
    visual_first: bool = False,
    precip: str = "",
    outages: Outages = NO_OUTAGES,
) -> Approach:
    """The approach to expect for landing on ``runway``."""
    aircraft = Aircraft(aircraft_type, airline)
    choices = options(airport, runway, aircraft, outages)
    kinds = [o.approach.kind for o in choices]
    listed = airport is not None and bool(airport.approaches)  # False: airport data from before approaches were read
    visual_weather = not in_cloud and (visibility_sm is None or visibility_sm >= 3.0) \
        and (ceiling_ft is None or ceiling_ft >= 1000)

    def pick(kind: str) -> Approach | None:
        if kind == VISUAL:
            return Approach(VISUAL, runway) if visual_weather else None
        if kind == "LOC" and "LOC" not in kinds and "ILS" in kinds:
            return Approach("LOC", runway, choices[kinds.index("ILS")].approach.suffix)  # the ILS flown without the glideslope
        for alias in ALIASES.get(kind, (kind,)):
            if alias in kinds:
                return choices[kinds.index(alias)].approach
        if not listed and (kind != "ILS" or has_ils) and kind in ("ILS", "RNAV", "LOC"):
            return Approach(kind, runway)  # an airport with no approach data: take the pilot's word for it
        return None

    for wanted in ((override.upper(),) if override != "auto" else ()) + ((requested.upper(),) if requested else ()):
        if (chosen := pick(wanted)) is not None:
            return chosen
    if not instrument_capable(aircraft_type):
        return Approach(VISUAL, runway)  # no instrument approach capability: visual whatever the field has
    if not choices:
        if not listed:
            if has_ils and runway not in outages.localizer:
                return Approach("LOC" if runway in outages.glideslope else "ILS", runway)
            return Approach("RNAV", runway)
        if visual_weather:
            return Approach(VISUAL, runway)  # the airport publishes approaches, just none to this runway
        return circle(airport, runway, aircraft, outages, ceiling_ft, visibility_sm) or Approach(VISUAL, runway)
    good = [o for o in choices if o.minima.met(ceiling_ft, visibility_sm)]
    best = (good or choices)[0].approach
    if not visual_weather:
        return best
    if visual_first and not precip and visibility_sm is not None and visibility_sm >= VISUAL_SM \
            and (ceiling_ft is None or ceiling_ft >= VISUAL_CEILING_FT):
        return Approach(VISUAL, runway)  # the US and Canada in good weather: "expect the visual", the ILS if asked for
    # Good weather and nothing better than a non-precision approach: the visual is simpler for everyone.
    return best if best.kind in PRECISION else Approach(VISUAL, runway)


def select_approach(airport: Airport | None, runway: str, **kw) -> str:
    """The kind of approach to expect ("ILS", "VISUAL"): ``choose_approach``'s, for callers that only need that."""
    return choose_approach(airport, runway, **kw).kind


COMPASS = ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")


def circle(airport: Airport | None, runway: str, aircraft: Aircraft, outages: Outages = NO_OUTAGES,
           ceiling_ft: float | None = None, visibility_sm: float | None = None) -> Approach | None:
    """An approach to another runway (or a circling-only one, VOR-A) and a circle to land on ``runway``: the best
    one the aircraft can fly, lowest minima first. With the landing more than a right angle off the approach
    course, ATC keeps the circling to the side of the runway's traffic pattern (a left one, unless the field
    says otherwise): "circle west of the airport for a left downwind to runway 34"."""
    if airport is None:
        return None
    runways = {e.ident for rw in airport.runways for e in (rw.primary, rw.secondary) if e.ident}
    candidates: list[Option] = []
    for other in sorted(runways - {runway}):
        candidates += options(airport, other, aircraft, outages)
    for proc in airport.approaches:
        if not proc.runway and (kind := KINDS.get(proc.kind)) is not None:  # VOR-A, NDB-B: circling only
            candidates.append(Option(Approach(kind, "", proc.suffix or "A"), MINIMA.get(kind, MINIMA["VOR"])))
    if not candidates:
        return None
    candidates.sort(key=lambda o: (o.minima.height_ft, o.minima.visibility_sm))
    return circle_to(airport, candidates[0].approach, runway)


def circle_to(airport: Airport | None, via: Approach, runway: str) -> Approach:
    """``via`` flown, then a circle to land on ``runway``; restricted to the side of the runway's traffic pattern
    when the landing is more than a right angle off the approach course."""
    side, pattern = "", ""
    course = _heading(airport, via.runway) if airport is not None else None
    landing = _heading(airport, runway) if airport is not None else None
    if course is not None and landing is not None and abs((landing - course + 540) % 360 - 180) > 90:
        pattern = "left"
        side = COMPASS[round(((landing - 90) % 360) / 45) % 8]  # a left pattern lies to the left of the landing
    return Approach(via.kind, via.runway, via.suffix, circle_to=runway, circle_side=side, circle_pattern=pattern,
                    minima=via.minima)


def circling_minima(aircraft: Aircraft) -> Minima:
    return CIRCLING[aircraft.category]


def circling_radius_nm(aircraft: Aircraft) -> float:
    return CIRCLING_RADIUS_NM[aircraft.category]


def minima_for(approach: Approach, aircraft: Aircraft = ANY_AIRCRAFT) -> Minima | None:
    """The minima of an approach as given (circling ones for a circle to land); None for a visual."""
    if approach.kind == VISUAL:
        return None
    if approach.circling:
        return circling_minima(aircraft)
    return MINIMA.get(approach.minima or approach.kind)


def _heading(airport: Airport, ident: str) -> float | None:
    for rw in airport.runways:
        if rw.primary.ident == ident:
            return rw.heading_true % 360
        if rw.secondary.ident == ident:
            return (rw.heading_true + 180) % 360
    return None

