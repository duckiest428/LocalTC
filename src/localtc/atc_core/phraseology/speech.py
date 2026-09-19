"""FAA radiotelephony formatting: numbers, letters, runways, altitudes, frequencies, callsigns.

Everything returns the spoken form as plain words for speech synthesis, e.g.
``runway("34L") == "three four left"``.
"""

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
    """121.8 -> "one two one point eight", 124.675 -> "one two four point six seven five"."""
    text = f"{mhz:.3f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    return digits(text)


def frequency_display(mhz: float) -> str:
    text = f"{mhz:.3f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def altitude(feet: int) -> str:
    """5000 -> "five thousand", 3500 -> "three thousand five hundred", 12000 -> "one two thousand", FL180+ -> flight level."""
    feet = int(round(feet / 100.0) * 100)
    if feet >= 18000:
        return "flight level " + digits(str(feet // 100))
    thousands, hundreds = divmod(feet, 1000)
    parts = []
    if thousands:
        parts.append(f"{digits(str(thousands))} thousand")
    if hundreds:
        parts.append(f"{ONES[hundreds // 100]} hundred")
    return " ".join(parts) or "zero"


def altitude_display(feet: int) -> str:
    feet = int(round(feet / 100.0) * 100)
    return f"FL{feet // 100:03d}" if feet >= 18000 else f"{feet:,}"


def heading(degrees: int) -> str:
    return digits(f"{int(degrees) % 360 or 360:03d}")


def squawk(code: str) -> str:
    return digits(code)


def altimeter(inhg: float) -> str:
    return digits(f"{inhg:.2f}".replace(".", ""))


def letter(ch: str) -> str:
    return PHONETIC[ch.upper()]


def taxiway(name: str) -> str:
    """ "A1" -> "alpha one", "C" -> "charlie", "TWY B" -> "bravo"."""
    name = name.upper().replace("TWY", "").strip()
    return " ".join(PHONETIC[c] if c.isalpha() else DIGITS[int(c)] for c in name if c.isalnum())


def taxi_route(names: tuple[str, ...]) -> str:
    return ", ".join(taxiway(n) for n in names)


def wind(value: Wind) -> str:
    if value.speed_kt < 3:
        return "calm"
    return f"{digits(f'{value.direction_mag % 360 or 360:03d}')} at {digits(str(value.speed_kt))}"


def wind_display(value: Wind) -> str:
    return "calm" if value.speed_kt < 3 else f"{value.direction_mag % 360 or 360:03d} at {value.speed_kt}"


def approach(value: Approach) -> str:
    kind = {"ILS": "I L S", "RNAV": "R-NAV", "VISUAL": "visual"}.get(value.kind, value.kind.lower())
    return f"{kind} runway {runway(value.runway)}"


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
}


def airport_name(name: str, icao: str) -> str:
    """ "SNOHOMISH CO (PAINE FLD)" -> "Paine Field", "BOEING FLD/KING CO INTL" -> "Boeing Field"."""
    if "(" in name and ")" in name:
        name = name[name.index("(") + 1 : name.index(")")]
    name = name.split("/")[0].strip()
    if not name:
        return digits(icao)
    return " ".join(ABBREVIATIONS.get(w, w.capitalize()) for w in name.split())


def station_name(name: str) -> str:
    """Frequency names like "PAINE TOWER" -> "Paine Tower"."""
    return " ".join(ABBREVIATIONS.get(w, w.capitalize()) for w in name.split())
