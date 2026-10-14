"""How big an aircraft is, by its ICAO type designator: its wingspan, and from that its ICAO aerodrome reference code
letter (A-F), the stand it fits and the taxiway width it needs.

Wingspans from the manufacturers' published airport planning data (winglets and sharklets included). An unknown
type is matched by its family (the first three characters, then the maker's model words in a sim's title), and
else left unknown: nothing is held against it.
"""

import re

WINGSPAN_M: dict[str, float] = {
    # Airbus
    "A318": 34.1, "A319": 35.8, "A320": 35.8, "A321": 35.8, "A19N": 35.8, "A20N": 35.8, "A21N": 35.8,
    "A306": 44.8, "A30B": 44.8, "A310": 43.9, "A332": 60.3, "A333": 60.3, "A337": 60.3, "A338": 64.0, "A339": 64.0,
    "A342": 60.3, "A343": 60.3, "A345": 63.5, "A346": 63.5, "A359": 64.8, "A35K": 64.8, "A388": 79.8, "A3ST": 44.8,
    "BCS1": 35.1, "BCS3": 35.1, "A400": 42.4,
    # Boeing
    "B712": 28.4, "B732": 28.4, "B733": 28.9, "B734": 28.9, "B735": 28.9, "B736": 34.3, "B737": 35.8, "B738": 35.8,
    "B739": 35.8, "B37M": 35.9, "B38M": 35.9, "B39M": 35.9, "B3XM": 35.9, "B741": 59.6, "B742": 59.6, "B743": 59.6,
    "B744": 64.4, "B74F": 64.4, "B748": 68.4, "B752": 41.1, "B753": 41.1, "B762": 47.6, "B763": 50.9, "B764": 51.9,
    "B772": 60.9, "B77L": 64.8, "B77W": 64.8, "B778": 64.8, "B779": 64.8, "B788": 60.1, "B789": 60.1, "B78X": 60.1,
    "C17": 51.8, "MD11": 51.7, "DC10": 50.4, "MD82": 32.9, "MD83": 32.9, "MD88": 32.9, "MD90": 32.9,
    # Regional and turboprops
    "E170": 26.0, "E75L": 26.0, "E75S": 26.0, "E190": 28.7, "E195": 28.7, "E290": 33.7, "E295": 35.1,
    "CRJ2": 21.2, "CRJ7": 23.2, "CRJ9": 24.9, "CRJX": 26.2, "DH8A": 25.9, "DH8C": 27.4, "DH8D": 28.4,
    "AT43": 24.6, "AT45": 24.6, "AT72": 27.1, "AT75": 27.1, "AT76": 27.1, "SF34": 21.4, "SB20": 24.8,
    "F70": 28.1, "F100": 28.1, "RJ85": 26.3, "B461": 26.3, "SU95": 27.8, "C919": 35.8, "ARJ": 27.3,
    # Business and general aviation
    "C172": 11.0, "C152": 10.1, "C182": 11.0, "C208": 15.9, "PA28": 10.7, "PA46": 13.1, "SR22": 11.7, "DA40": 11.9,
    "DA42": 13.4, "DA62": 14.6, "BE20": 16.6, "BE36": 10.2, "BE58": 11.5, "PC12": 16.3, "TBM9": 12.8, "TBM8": 12.8,
    "C25A": 15.5, "C25B": 15.5, "C510": 13.2, "C56X": 17.2, "C680": 19.4, "C700": 22.0, "CL30": 19.5, "CL35": 21.0,
    "CL60": 19.6, "GLEX": 28.6, "GL7T": 31.7, "GLF5": 28.5, "GLF6": 30.4, "F900": 19.3, "FA7X": 26.2, "FA8X": 26.3,
    "E55P": 16.2, "E50P": 12.3, "HDJT": 12.1, "LJ45": 14.6, "LJ75": 15.5, "SF50": 11.8,
}
# A type not listed, by its family (the A330s, the 777s).
FAMILY_M = {"A33": 60.3, "A34": 63.5, "A35": 64.8, "A38": 79.8, "A32": 35.8, "A31": 35.8, "B73": 35.8, "B74": 64.4,
            "B75": 41.1, "B76": 50.9, "B77": 64.8, "B78": 60.1, "E17": 26.0, "E19": 28.7, "CRJ": 24.9}
# A sim's title or model words ("A330-300", "Boeing 777-300ER") to a designator.
TITLE_TYPES = ((r"A330-?9|A339", "A339"), (r"A330-?8|A338", "A338"), (r"A330-?3|A333", "A333"),
               (r"A330-?2|A332", "A332"), (r"A350-?10|A35K", "A35K"), (r"A350|A359", "A359"), (r"A380|A388", "A388"),
               (r"A340-?6|A346", "A346"), (r"A340-?5|A345", "A345"), (r"A340|A34\d", "A343"),
               (r"A321|A21N", "A321"), (r"A320|A20N", "A320"), (r"A319|A19N", "A319"), (r"A220|BCS", "BCS3"),
               (r"777-?3|77W", "B77W"), (r"777-?2|77L", "B77L"), (r"777-?[89]", "B779"), (r"787-?10|78X", "B78X"),
               (r"787", "B789"), (r"747-?8", "B748"), (r"747", "B744"), (r"737 ?MAX|B3[789]M", "B38M"), (r"737", "B738"),
               (r"757", "B752"), (r"767", "B763"), (r"MD-?11", "MD11"), (r"E-?1[79]\d|EMB", "E190"),
               (r"CRJ", "CRJ9"), (r"ATR", "AT76"), (r"DASH ?8|Q400|DH8", "DH8D"), (r"172", "C172"), (r"208", "C208"))

# ICAO Annex 14: the code letter by wingspan, and the taxiway width each needs.
CODE_LETTERS = ((15.0, "A"), (24.0, "B"), (36.0, "C"), (52.0, "D"), (65.0, "E"), (80.0, "F"))
TAXIWAY_WIDTH_M = {"A": 7.5, "B": 10.5, "C": 15.0, "D": 18.0, "E": 23.0, "F": 25.0}


def designator(text: str) -> str:
    """The ICAO type designator in an aircraft type or title ("A333", "A330-300 (RR)" -> "A333"), or ""."""
    word = (text or "").strip().upper()
    if word in WINGSPAN_M:
        return word
    for pattern, code in TITLE_TYPES:
        if re.search(pattern, word):
            return code
    return ""


def wingspan_m(aircraft_type: str) -> float:
    """The wingspan in metres (0: not known)."""
    code = designator(aircraft_type)
    if code:
        return WINGSPAN_M[code]
    word = (aircraft_type or "").strip().upper()
    return FAMILY_M.get(word[:3], 0.0)


def code_letter(span_m: float) -> str:
    """The ICAO code letter for a wingspan ("" when not known)."""
    if span_m <= 0:
        return ""
    return next((letter for limit, letter in CODE_LETTERS if span_m < limit), "F")


def taxiway_width_m(span_m: float) -> float:
    """The taxiway width an aircraft of this span needs (0: not known)."""
    letter = code_letter(span_m)
    return TAXIWAY_WIDTH_M.get(letter, 0.0)


def fits_stand(radius_m: float, span_m: float) -> bool:
    """A parking spot of this radius takes the aircraft (a radius or span not known: it does). The sim's radius is
    half the largest span it takes, give or take a metre."""
    return radius_m <= 0 or span_m <= 0 or span_m / 2 <= radius_m + 1.0


# ICAO wake turbulence categories (Doc 8643): J super, H heavy (136 t or more), M medium, L light (7 t or less).
SUPER = {"A388", "A225"}
HEAVY_FAMILIES = ("A30", "A31", "A33", "A34", "A35", "B74", "B76", "B77", "B78")
HEAVY_TYPES = {"MD11", "DC10", "C17", "A400", "IL76", "A124", "C5", "C5M", "K35R", "KC10"}
HEAVY_EXCEPT = {"A318", "A319", "A31N", "A19N"}  # A31x: the A310 is heavy, the A318/A319 aren't


def wake_category(aircraft_type: str) -> str:
    """The wake category, "J", "H", "M" or "L", of a type designator or a sim's title ("" when not known)."""
    code = designator(aircraft_type) or (aircraft_type or "").strip().upper()
    if not code:
        return ""
    if code in SUPER:
        return "J"
    if code in HEAVY_TYPES or (code not in HEAVY_EXCEPT and code.startswith(HEAVY_FAMILIES)):
        return "H"
    span = WINGSPAN_M.get(code, 0.0)
    if span:
        return "L" if span < 15.0 and code[:1] in ("C", "P", "S", "D", "B", "T", "E", "H", "L") and code not in (
            "C25A", "C25B", "C56X", "C680", "C700", "CL30", "CL35", "CL60", "E55P", "BE20", "PC12") else "M"
    return ""


def heavier(leader: str, follower: str) -> bool:
    """Whether ``leader``'s wake is one ``follower`` is separated from (a heavier category: J > H > M > L)."""
    order = {"J": 3, "H": 2, "M": 1, "L": 0}
    return leader in order and follower in order and order[leader] > order[follower] and order[leader] >= 2
