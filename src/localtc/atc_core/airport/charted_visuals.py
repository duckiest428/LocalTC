"""Charted visual flight procedures: the named visual approaches the FAA publishes (its d-TPP index, cycle 2611), by
airport and the runways each is to. ATC offers one to an arrival expecting the visual to a runway that has one, in the
weather the chart asks for, and clears it by name ("cleared River Visual runway 19 approach"); the pilot can ask for one
by name too. Only the names and runways are here: the route is the chart's, flown by the pilot looking out.
"""

import zlib

from localtc.atc_core.values import Approach

# (airport, the chart's name before "VISUAL", its runways)
_CHARTED = (
    ("KACK", "Great Point", ("24",)), ("KACK", "Tuckernuck", ("06",)),
    ("KASE", "Roaring Fork", ("15",)),
    ("KBFI", "Harbor", ("14R",)),
    ("KBJC", "Red Rocks", ("12L", "12R")),
    ("KBOS", "Light", ("33L",)),
    ("KBUR", "Four Stacks", ("15",)),
    ("KDCA", "Mount Vernon", ("01",)), ("KDCA", "River", ("19",)),
    ("KEWR", "Bridge", ("29",)), ("KEWR", "Stadium", ("29",)),
    ("KGEG", "Riddle Hill", ("08",)),
    ("KHFD", "River", ("02",)),
    ("KHPN", "Sound", ("34",)),
    ("KHXD", "Broad Creek", ("03",)),
    ("KJFK", "Belmont", ("22L",)), ("KJFK", "Parkway", ("13L", "13R")),
    ("KLGA", "Park", ("31",)), ("KLGA", "River", ("13",)),
    ("KLGB", "Arsenal", ("30",)), ("KLGB", "LA River", ("12",)),
    ("KLSV", "Sin City", ("03L", "03R")),
    ("KMMU", "Route 80", ("23",)),
    ("KMRY", "Raceway", ("28L",)),
    ("KNUQ", "Dumbarton", ("14L",)),
    ("KNZY", "Hotel", ("29",)),
    ("KORF", "River", ("05",)),
    ("KPDX", "Columbia", ("10L", "10R")), ("KPDX", "Mill", ("28L", "28R")),
    ("KPHL", "Freedom", ("09L",)), ("KPHL", "Liberty", ("27L",)), ("KPHL", "River", ("09L", "09R")),
    ("KPIE", "North Bay", ("18",)),
    ("KPWM", "Harbor", ("29",)),
    ("KRDM", "Juniper", ("23",)),
    ("KSAN", "Sweetwater", ("27",)),
    ("KSEA", "Bay", ("16R", "16C", "16L")), ("KSEA", "Husky", ("16R", "16C", "16L")), ("KSEA", "Mall", ("34R", "34C", "34L")),
    ("KSFO", "Quiet Bridge", ("28R",)), ("KSFO", "Tipp Toe", ("28L", "28R")),
    ("KSJC", "Fairgrounds", ("30L", "30R")),
    ("PANC", "Highway", ("25R",)), ("PANC", "Seward", ("25L",)),
    ("PHNL", "Kahe Power Plant", ("22L",)), ("PHNL", "Waialae Golf Course", ("22L",)),
    ("PHOG", "Smoke Stack", ("02",)),
    ("TJSJ", "Bridge", ("10",)), ("TJSJ", "Lagoon", ("08",)),
)

CHARTED: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
for _icao, _name, _runways in _CHARTED:
    CHARTED.setdefault(_icao, []).append((_name, _runways))

# A charted visual wants more than the basic visual: the landmarks seen from the start (most charts ask a ceiling
# around 3,000 to 4,000 and five miles; the chart's own figure isn't here, so the common one).
CEILING_FT = 3000.0
VISIBILITY_SM = 5.0


def charted_for(icao: str | None, runway: str) -> list[str]:
    """The named visuals to ``runway`` at ``icao`` ("River", "Mount Vernon"), the chart's order."""
    return [name for name, runways in CHARTED.get((icao or "").upper(), []) if runway in runways]


def count() -> tuple[int, int]:
    """(procedures, airports)."""
    return len(_CHARTED), len(CHARTED)


def choose(icao: str | None, runway: str, seed: str, *, ceiling_ft: float | None, visibility_sm: float | None,
           in_cloud: bool) -> Approach | None:
    """The named visual ATC gives for ``runway`` in this weather, or None: none, or the weather's not for one. Of
    several, the same one for the same flight (``seed``)."""
    names = charted_for(icao, runway)
    if not names or in_cloud or (ceiling_ft is not None and ceiling_ft < CEILING_FT) \
            or (visibility_sm is not None and visibility_sm < VISIBILITY_SM):
        return None
    return Approach("VISUAL", runway, charted=names[zlib.crc32(seed.encode()) % len(names)])


def requested(icao: str | None, runway: str | None, text: str) -> Approach | None:
    """The named visual the pilot asked for ("request the River Visual"), or None."""
    low = (text or "").lower()
    for name, runways in CHARTED.get((icao or "").upper(), []):
        if f"{name.lower()} visual" in low:
            return Approach("VISUAL", runway if runway in runways else runways[0], charted=name)
    return None
