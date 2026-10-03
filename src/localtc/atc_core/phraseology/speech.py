"""FAA radiotelephony formatting: numbers, letters, runways, altitudes, frequencies, callsigns.

Everything returns the spoken form as plain words for speech synthesis, e.g.
``runway("34L") == "three four left"``.
"""

from localtc.atc_core.region import CURRENT
from localtc.atc_core.values import Approach, Callsign, Wind

DIGITS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "niner")
PHONETIC = {
    "A": "alpha", "B": "bravo", "C": "charlie", "D": "delta", "E": "echo", "F": "foxtrot", "G": "golf",
    "H": "hotel", "I": "india", "J": "juliett", "K": "kilo", "L": "lima", "M": "mike", "N": "november",
    "O": "oscar", "P": "papa", "Q": "quebec", "R": "romeo", "S": "sierra", "T": "tango", "U": "uniform",
    "V": "victor", "W": "whiskey", "X": "x-ray", "Y": "yankee", "Z": "zulu",
}
ONES = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
TEENS = ("ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
SIDES = {"L": "left", "R": "right", "C": "center"}


def digits(text: str) -> str:
    """Each digit separately; letters phonetically; '.' as 'point'."""
    words = []
    for ch in str(text).upper():
        if ch.isdigit():
            words.append(DIGITS[int(ch)])
        elif ch == ".":
            words.append("point")
        elif ch in PHONETIC:
            words.append(PHONETIC[ch])
    return " ".join(words)


def number_words(n: int) -> str:
    """0-99 as words ("twenty-three")."""
    if n < 10:
        return ONES[n]
    if n < 20:
        return TEENS[n - 10]
    tens, ones = divmod(n, 10)
    return TENS[tens] + (f"-{ONES[ones]}" if ones else "")


def miles(n: int) -> str:
    """A distance: 38 -> "thirty-eight", 120 -> "one hundred twenty"."""
    n = max(0, int(n))
    if n < 100:
        return number_words(n)
    hundreds, rest = divmod(n, 100)
    head = f"{number_words(hundreds)} hundred"
    return f"{head} {number_words(rest)}" if rest else head


def feet(n: int) -> str:
    """A runway length, to the nearest hundred: 13300 -> "one three thousand three hundred"."""
    return _feet(int(round(n / 100.0) * 100))


def group_form(number: str) -> str:
    """Airline flight numbers: 123 -> "one twenty-three", 1234 -> "twelve thirty-four", 1200 -> "twelve hundred"."""
    num = number.lstrip("0") or "0"
    suffix = ""
    if num[-1].isalpha():
        num, suffix = num[:-1], " " + PHONETIC[num[-1].upper()]
    n = int(num)
    if n < 100:
        text = number_words(n)
    elif n < 1000:
        head, tail = divmod(n, 100)
        text = f"{ONES[head]} hundred" if tail == 0 else f"{ONES[head]} {number_words(tail) if tail >= 10 else 'zero ' + ONES[tail]}"
    else:
        head, tail = divmod(n, 100)
        text = f"{number_words(head)} hundred" if tail == 0 else f"{number_words(head)} {number_words(tail) if tail >= 10 else 'zero ' + ONES[tail]}"
    return text + suffix


def runway(ident: str) -> str:
    """ "34L" -> "three four left", "04" -> "four"."""
    num = "".join(c for c in ident if c.isdigit()).lstrip("0") or "0"
    side = ident[len(ident.rstrip("LRC")):]
    return " ".join([digits(num), *(SIDES[s] for s in side)])


def frequency(mhz: float) -> str:
    """121.8 -> "one two one point eight", 124.675 -> "one two four point six seven five". ICAO says "decimal"."""
    text = f"{mhz:.3f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    spoken = digits(text)
    return spoken.replace(" point ", " decimal ") if CURRENT.get().icao else spoken


def frequency_display(mhz: float) -> str:
    text = f"{mhz:.3f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def altitude(feet: int) -> str:
    """5000 -> "five thousand", 3500 -> "three thousand five hundred", 12000 -> "one two thousand"; at and
    above the region's transition altitude, a flight level. ICAO says "feet" below it: "six thousand feet"."""
    feet = int(round(feet / 100.0) * 100)
    region = CURRENT.get()
    if feet >= region.transition_ft:
        return "flight level " + digits(str(feet // 100))
    if region.icao:
        return _feet(feet) + " feet"
    return _feet(feet)


def _feet(feet: int) -> str:
    thousands, hundreds = divmod(feet, 1000)
    parts = []
    if thousands:
        parts.append(f"{digits(str(thousands))} thousand")
    if hundreds:
        parts.append(f"{ONES[hundreds // 100]} hundred")
    return " ".join(parts) or "zero"


def altitude_display(feet: int) -> str:
    feet = int(round(feet / 100.0) * 100)
    region = CURRENT.get()
    if feet >= region.transition_ft:
        return f"FL{feet // 100:03d}"
    return f"{feet:,} feet" if region.icao else f"{feet:,}"


def heading(degrees: int) -> str:
    return digits(f"{int(degrees) % 360 or 360:03d}")


def speed(knots: int) -> str:
    """ "210" -> "two one zero": an airspeed assignment is read digit by digit."""
    return digits(str(int(knots)))


def squawk(code: str) -> str:
    return digits(code)


def hpa(inhg: float) -> int:
    return round(inhg * 33.8639)


def altimeter(inhg: float) -> str:
    """ "two niner niner two"; ICAO gives QNH in hectopascals: "one zero one three"."""
    if CURRENT.get().icao:
        return digits(str(hpa(inhg)))
    return digits(f"{inhg:.2f}".replace(".", ""))


def altimeter_display(inhg: float) -> str:
    return str(hpa(inhg)) if CURRENT.get().icao else f"{inhg:.2f}"


def letter(ch: str) -> str:
    return PHONETIC[ch.upper()]


def taxiway(name: str) -> str:
    """ "A1" -> "alpha one", "C" -> "charlie", "TWY B" -> "bravo"."""
    name = name.upper().replace("TWY", "").strip()
    return " ".join(PHONETIC[c] if c.isalpha() else DIGITS[int(c)] for c in name if c.isalnum())


def procedure(name: str) -> str:
    """A SID or STAR ident as it is said: "MONTN2" -> "montn two", "XIBI3A" -> "xibi three alpha".

    The published spoken name ("MONTANA TWO") isn't in the sim's data, so the letters are left as a
    word and only the digits and any trailing letter are spelled out, the way a controller reads them.
    """
    said, word = [], ""
    for char in name.upper():
        if char.isdigit():
            if word:
                said.append(word.lower())
                word = ""
            said.append(DIGITS[int(char)])
        elif char.isalnum():
            if said and not word:  # a letter after the number is spelled: "xibi three alpha"
                said.append(PHONETIC[char])
            else:
                word += char
    if word:
        said.append(word.lower())
    return " ".join(said)


def gate(display: str) -> str:
    """ "Gate B25" -> "gate bravo two five", "Parking 3" -> "parking three"."""
    word, _, label = display.partition(" ")
    return f"{word.lower()} {taxiway(label)}".strip()


def taxi_route(names: tuple[str, ...]) -> str:
    return ", ".join(taxiway(n) for n in names)


def wind(value: Wind) -> str:
    """ "two seven zero at one zero"; ICAO: "two seven zero degrees one zero knots"."""
    if value.speed_kt < 3:
        return "calm"
    direction, speed_ = digits(f"{value.direction_mag % 360 or 360:03d}"), digits(str(value.speed_kt))
    return f"{direction} degrees {speed_} knots" if CURRENT.get().icao else f"{direction} at {speed_}"


def wind_display(value: Wind) -> str:
    if value.speed_kt < 3:
        return "calm"
    direction = f"{value.direction_mag % 360 or 360:03d}"
    return f"{direction} degrees {value.speed_kt} knots" if CURRENT.get().icao else f"{direction} at {value.speed_kt}"


APPROACH_SPOKEN = {"ILS": "I L S", "RNAV": "R-NAV", "RNP": "R-NAV", "GPS": "G P S", "LOC": "localizer",
                   "LOC/DME": "localizer D M E", "LOC BC": "localizer back course", "LDA": "L D A", "SDF": "S D F",
                   "VOR": "V O R", "VOR/DME": "V O R D M E", "NDB": "N D B", "NDB/DME": "N D B D M E", "VISUAL": "visual"}


def approach(value: Approach) -> str:
    """ "I L S zulu runway three four right", "localizer back course runway two six", "V O R alpha"."""
    kind = APPROACH_SPOKEN.get(value.kind, value.kind.lower())
    suffix = f" {PHONETIC[value.suffix.upper()]}" if value.suffix and value.suffix.upper() in PHONETIC else ""
    if not value.runway:
        return f"{kind}{suffix or ' alpha'}"
    return f"{kind}{suffix} runway {runway(value.runway)}"


def circling(value: Approach) -> tuple[str, str]:
    """What follows "cleared ... approach" on a circle-to-land (FAA JO 7110.65 4-8-6): "circle to runway 34", or
    "circle west of the airport for a left downwind to runway 34". (display, spoken); empty when not circling."""
    if not value.circle_to:
        return "", ""
    if value.circle_side and value.circle_pattern:
        where = f"circle {value.circle_side} of the airport for a {value.circle_pattern} downwind to runway "
        return where + value.circle_to, where + runway(value.circle_to)
    return f"circle to runway {value.circle_to}", f"circle to runway {runway(value.circle_to)}"


def callsign(value: Callsign) -> str:
    if value.is_airline:
        return f"{value.telephony} {group_form(value.flight_number)}"
    if value.abbreviated:
        prefix = value.type_name or PHONETIC.get(value.ident[:1], "")
        return f"{prefix} {digits(value.suffix)}".strip()
    return digits(value.ident.replace("-", ""))


def callsign_display(value: Callsign) -> str:
    if value.is_airline:
        return f"{value.telephony} {value.flight_number}"
    if value.abbreviated:
        return f"{value.type_name} {value.suffix}".strip() if value.type_name else value.suffix
    return value.ident


ABBREVIATIONS = {
    "FLD": "Field", "INTL": "International", "CO": "County", "MUNI": "Municipal", "RGNL": "Regional",
    "MEM": "Memorial", "ARPT": "Airport", "EXEC": "Executive", "NATL": "National", "ST": "Saint",
    # Military fields keep their letters: "Yuma MCAS", "El Centro NAF".
    "MCAS": "MCAS", "NAS": "NAS", "NAF": "NAF", "AFB": "AFB", "AAF": "AAF", "ANGB": "ANGB", "ARB": "ARB",
}


def airport_name(name: str, icao: str) -> str:
    """ "SNOHOMISH CO (PAINE FLD)" -> "Paine Field", "BOEING FLD/KING CO INTL" -> "Boeing Field"."""
    if "(" in name and ")" in name:
        name = name[name.index("(") + 1 : name.index(")")]
    name = name.split("/")[0].strip()
    if not name:
        return digits(icao)
    return " ".join(ABBREVIATIONS.get(w.upper(), _capitalized(w)) for w in name.split())


def _capitalized(word: str) -> str:
    """ "SEATTLE-TACOMA" -> "Seattle-Tacoma"."""
    return "-".join(part.capitalize() for part in word.split("-"))


def station_name(name: str) -> str:
    """Frequency names like "PAINE TOWER" -> "Paine Tower"."""
    return " ".join(ABBREVIATIONS.get(w.upper(), w.capitalize()) for w in name.split())


def model_number(n: int) -> str:
    """An aircraft model's number as it's said: 737 "seven thirty-seven", 320 "three twenty", 170 "one seventy",
    90 "ninety"."""
    if n < 100:
        return number_words(n)
    head, rest = divmod(n, 100)
    if head >= 10:
        return digits(str(n))
    return f"{ONES[head]} " + ("hundred" if rest == 0 else f"oh {ONES[rest]}" if rest < 10 else number_words(rest))


# ICAO type designators as controllers say them ("give way to the Boeing 737"): maker and model, never the letters
# spelled out ("bravo seven three seven"). A family's variants are one model to a controller. Boeing, Airbus and
# Embraer airliners are worked out from the designator (``aircraft_type``); the rest are named here.
TYPE_NAMES: tuple[tuple[str, str, str], ...] = (  # (designator pattern, display, spoken)
    (r"BCS[13]|A22\d", "Airbus A220", "Airbus two twenty"),
    (r"E1[34]5|ERJ", "Embraer regional jet", "Embraer regional jet"),
    (r"CRJ", "CRJ", "regional jet"),
    (r"CRJ[1-9X]", "CRJ", "regional jet"),
    (r"DH8[A-D]", "Dash 8", "Dash eight"),
    (r"AT[47]\d", "ATR", "A T R"),
    (r"C1[78]\d", "Cessna", "Cessna"),
    (r"C208", "Caravan", "Caravan"),
    (r"C25[ABC]|C5\d[0-9A-Z]|C68A|C700|C750", "Citation", "Citation"),
    (r"PC12", "Pilatus PC-12", "Pilatus"),
    (r"PC24", "Pilatus PC-24", "Pilatus"),
    (r"MD1[01]", "MD-11", "M D eleven"),
    (r"MD8\d|MD90", "MD-80", "M D eighty"),
    (r"B190", "Beech 1900", "Beech nineteen hundred"),
    (r"BE\d\d|B350", "Beechcraft", "Beechcraft"),
    (r"P28[A-Z]|PA\d\d", "Piper", "Piper"),
    (r"SR2[02]", "Cirrus", "Cirrus"),
    (r"TBM\d", "TBM", "T B M"),
    (r"GLF\d|G[56]\d\d|GLEX", "Gulfstream", "Gulfstream"),
    (r"CL\d\d", "Challenger", "Challenger"),
)


def _airliner(code: str) -> tuple[str, int] | None:
    """("Boeing", 737) for "B738", "B38M" or "737"; ("Airbus", 321) for "A321" or "A21N"; ("Embraer", 175) for
    "E75L"; None for anything else."""
    import re

    if m := re.fullmatch(r"B?7([0-8])[0-9LWXR]", code):
        return "Boeing", 707 + 10 * int(m.group(1))
    if re.fullmatch(r"B3[789]M", code):
        return "Boeing", 737  # the 737 MAX
    if m := re.fullmatch(r"A3(\d)(\d)|A3(\d)[NK]", code):
        n = int(f"3{m.group(1)}{m.group(2)}") if m.group(1) else 300 + 10 * int(m.group(3))
        return "Airbus", n if 318 <= n <= 321 else n // 10 * 10  # A332, A333: the A330
    if m := re.fullmatch(r"A(19|20|21)N", code):
        return "Airbus", 300 + int(m.group(1))  # the neo
    if re.fullmatch(r"E75[LS]", code):
        return "Embraer", 175
    if m := re.fullmatch(r"E[12]([79])(\d)", code):
        return "Embraer", 100 + 10 * int(m.group(1)) + int(m.group(2))  # E290: the E190-E2
    return None


def aircraft_type(model: str) -> tuple[str, str]:
    """(display, spoken) for an aircraft type as the sim names it ("B738", "A21N", "E170", "737", "Airbus"):
    "Boeing 737" / "Boeing seven thirty-seven", "Airbus A321" / "Airbus three twenty-one", "Embraer 170" /
    "Embraer one seventy". A name the table doesn't know is said as written."""
    import re

    code = model.strip().upper().replace("-", "")
    if not code:
        return "", ""
    if (airliner := _airliner(code)) is not None:
        maker, n = airliner
        return f"{maker} {'A' if maker == 'Airbus' else ''}{n}", f"{maker} {model_number(n)}"
    for pattern, display, spoken in TYPE_NAMES:
        if re.fullmatch(pattern, code):
            return display, spoken
    if re.fullmatch(r"[A-Za-z ]+", model.strip()):
        return model.strip(), model.strip()  # "Airbus", "Citation": a name already
    return model, digits(model)
