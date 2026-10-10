"""Which installed model an aircraft is drawn with: FSLTL's for its type and airline when FSLTL is installed, else
FSLTL's unpainted one for the type (its "ZZZZ"), else one of the sim's own of the same size.

FSLTL names its models a few ways: "FSLTL_A359_DAL-Delta", "FSLTL_FAIB_B738_UAL-United_NC" (a painter's prefix),
"FSLTL_CRJ7_SKW_DAL" (SkyWest flying for Delta), "FSLTL A321 DAL Delta"; "-STUB" ones are placeholders its own
injector fills in, never used here.
"""

import re
from dataclasses import dataclass, field

# Types the sim's names and FSLTL's treat as one ("A20N" is drawn as an A320 if that's all there is).
FAMILY = {"A19N": "A319", "A20N": "A320", "A21N": "A321", "B38M": "B738", "B39M": "B739", "B37M": "B737",
          "B3XM": "B739", "A359": "A350", "A35K": "A350", "B788": "B787", "B789": "B787", "B78X": "B787",
          "B77W": "B777", "B77L": "B777", "B772": "B777", "B773": "B777", "B744": "B747", "B748": "B747",
          "A332": "A330", "A333": "A330", "A339": "A330", "A343": "A340", "A346": "A340", "E75L": "E170",
          "E75S": "E170", "E175": "E170", "E170": "E170", "E190": "E190", "E195": "E190", "E290": "E190",
          "CRJ9": "CRJ7", "CRJ7": "CRJ7", "CRJ2": "CRJ7", "BCS1": "BCS3", "BCS3": "BCS3", "DH8D": "DH8D",
          "AT76": "AT76", "AT75": "AT76", "AT45": "AT76", "B752": "B757", "B753": "B757", "B762": "B767",
          "B763": "B767", "B764": "B767"}
# When the type isn't known: by its ADS-B category (A1 light ... A5 heavy).
BY_CATEGORY = {"A1": "C172", "A2": "CRJ9", "A3": "A320", "A4": "B752", "A5": "B77W", "A7": "R44"}
# The sim's own aircraft, by size, when FSLTL has nothing of the type.
STOCK = (("A320", ("A320neo V2",)), ("A321", ("A321",)), ("B738", ("737 Max 8 Passengers",)),
         ("B777", ("777-300ER",)), ("B747", ("747-8i",)), ("A350", ("A350-900 (No Cabin)", "A350-900 (Default Cabin)")),
         ("A330", ("A330-300 (GE)",)), ("C172", ("Cessna 172 Skyhawk", "Cessna Skyhawk G1000 Asobo")))
VENDORS = {"FSLTL", "FAIB", "TFS", "FSPXAI", "AIG", "GA"}
TYPE = re.compile(r"[A-Z][A-Z0-9]{2,3}")


def family(code: str) -> str:
    code = (code or "").upper()
    return FAMILY.get(code, code)


def size_of(code: str) -> str:
    """"heavy", "medium" or "small" (a parking spot's size)."""
    f = family(code)
    if f in ("B777", "B747", "A350", "A330", "A340", "A380", "A388", "B787", "B767", "MD11", "A306", "A30B", "B757"):
        return "heavy"
    if f in ("A320", "A321", "A319", "B738", "B739", "B737", "B733", "B734", "BCS3", "E190", "B752"):
        return "medium"
    return "small"


@dataclass(frozen=True)
class Model:
    title: str
    livery: str
    type: str  # FSLTL's type code ("B738"); "" for the sim's own
    airlines: tuple[str, ...]  # the ICAO codes in its name ("SKW", "DAL")
    generic: bool = False  # FSLTL's unpainted one ("ZZZZ")


def parse(title: str, livery: str = "") -> Model | None:
    """An FSLTL model's type and airlines from its name; None for anything else (and for its stubs)."""
    both = f"{title} {livery}"
    if "FSLTL" not in both.upper() or "STUB" in both.upper():
        return None
    tokens = [t for t in re.split(r"[_\s]+", title) if t]
    kind = next((t.upper() for t in tokens if t.upper() not in VENDORS and TYPE.fullmatch(t.upper())
                 and re.search(r"\d", t)), "")
    if not kind:
        return None
    after = tokens[[t.upper() for t in tokens].index(kind) + 1:]
    codes = tuple(c for t in after if (c := t.split("-")[0]).isupper() and re.fullmatch(r"[A-Z]{3}", c))
    generic = any(t.upper().startswith("ZZZ") for t in after)
    return Model(title, livery, kind, codes, generic)


@dataclass
class ModelPicker:
    """The installed models (EnumerateSimObjectsAndLiveries), and the best one for a type and airline."""

    models: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._fsltl = [m for t, liv in self.models if (m := parse(t, liv)) is not None]
        self._titles = {t for t, _ in self.models}
        self._cache: dict[tuple[str, str], tuple[str, str] | None] = {}

    @property
    def fsltl(self) -> bool:
        return bool(self._fsltl)

    def pick(self, type_code: str, airline: str = "", category: str = "") -> tuple[str, str] | None:
        """(title, livery) to create it with, or None when nothing installed fits."""
        code = (type_code or BY_CATEGORY.get(category, "")).upper()
        key = (code, (airline or "").upper())
        if key not in self._cache:
            self._cache[key] = self._pick(*key)
        return self._cache[key]

    def _pick(self, code: str, airline: str) -> tuple[str, str] | None:
        if not code:
            return None
        best: tuple[float, Model] | None = None
        for m in self._fsltl:
            same, kin = m.type == code, family(m.type) == family(code)
            if not kin:
                continue
            if airline and airline in m.airlines:
                score = 4.0 + same + (m.airlines[0] == airline) * 0.5  # its own colours (operator first: SkyWest's)
            elif m.generic:
                score = 2.0 + same
            elif not airline:
                score = 1.0 + same  # no airline known (a GA or a private jet): any of the type
            else:
                continue  # another airline's colours would be another flight
            if best is None or score > best[0]:
                best = (score, m)
        if best is not None:
            return best[1].title, best[1].livery
        for kind, titles in STOCK:
            if family(kind) == family(code) or size_of(kind) == size_of(code) and kind != "C172" and size_of(code) != "small":
                for title in titles:
                    if title in self._titles:
                        return title, ""
        return None
