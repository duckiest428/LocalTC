"""Which approach ATC gives you: what the airport publishes, what the weather allows, what the aircraft can fly.

The sim's facility data lists an airport's instrument approaches (``Airport.approaches``). A small field
with nothing but a VOR approach can't offer an RNAV, and a Cub can't fly either, so ATC says "expect the
visual". Airport data recorded before approaches were read has none at all: those airports fall back to
"ILS where the runway has one, otherwise RNAV", which is what LocalTC did before.
"""

from localtc.sim_api import Airport

# Best first: what a controller would offer when several are published for the same runway.
ORDER = ("ils", "localizer", "lda", "sdf", "rnav", "gps", "vordme", "vor", "ndbdme", "ndb", "backcourse")
DISPLAY = {"ils": "ILS", "localizer": "LOC", "lda": "LDA", "sdf": "SDF", "rnav": "RNAV", "gps": "RNAV",
           "vordme": "VOR", "vor": "VOR", "ndbdme": "NDB", "ndb": "NDB", "backcourse": "LOC BC"}
PRECISION = ("ILS", "LOC", "LDA", "RNAV")  # the rest are non-precision: in good weather, a visual is easier
VISUAL = "VISUAL"

# Aircraft with no instrument approach capability. Matched against the sim's model name, lowercased.
VISUAL_ONLY = ("cub", "champ", "stearman", "glider", "sailplane", "balloon", "blimp", "ask 21", "ask21", "dg-1001",
               "dg 1001", "jenny", "waco", "trike", "paramotor", "ultralight", "gyrocopter", "biplane")


def instrument_capable(aircraft_type: str) -> bool:
    name = (aircraft_type or "").lower()
    return not any(word in name for word in VISUAL_ONLY)


def published(airport: Airport | None, runway: str) -> tuple[str, ...]:
    """The approach kinds published to this runway, best first: ``("ILS", "RNAV")``."""
    if airport is None or not airport.approaches:
        return ()
    kinds = {a.kind for a in airport.approaches_to(runway)}
    return tuple(dict.fromkeys(DISPLAY[k] for k in ORDER if k in kinds))


def select_approach(
    airport: Airport | None,
    runway: str,
    *,
    has_ils: bool = False,
    visibility_sm: float | None = None,
    in_cloud: bool = False,
    aircraft_type: str = "",
    requested: str | None = None,
    override: str = "auto",
) -> str:
    """The approach to expect for ``runway``: an approach kind, or "VISUAL"."""
    choices = published(airport, runway)
    listed = airport is not None and bool(airport.approaches)  # False: airport data from before approaches were read
    instrument = instrument_capable(aircraft_type)
    visual_weather = not in_cloud and (visibility_sm is None or visibility_sm >= 3.0)

    if override != "auto":
        wanted = override.upper()
        if wanted == VISUAL and visual_weather:
            return VISUAL
        if wanted in choices or (not listed and (wanted != "ILS" or has_ils)):
            return wanted
    if requested is not None:
        if requested == VISUAL and visual_weather:
            return VISUAL
        if requested in choices or (not listed and (requested != "ILS" or has_ils)):
            return requested  # an airport with no approach data: take the pilot's word for it
    if not instrument:
        return VISUAL  # no instrument approach capability: visual whatever the field has
    if not choices:
        if listed:
            return VISUAL  # the airport publishes approaches, just none to this runway
        return "ILS" if has_ils else "RNAV"  # airport data without approaches (older recordings)
    if not visual_weather:
        return choices[0]
    # Good weather and nothing better than a non-precision approach: the visual is simpler for everyone.
    return choices[0] if choices[0] in PRECISION else VISUAL
