"""Aviation vocabulary for Whisper: a prompt built from the flight, and fixes for common mishearings.

Whisper's ``initial_prompt`` is text it treats as what came before, so it picks up the words and
the writing style (digits, "06L", "5,000") from it. The prompt names the callsign, stations,
airports and every runway at the airports in play, but never the numbers ATC just gave: priming
Whisper with the expected squawk could make it hear the right number when the pilot said a wrong
one, and hide a readback error.
"""

import difflib
import re
from dataclasses import dataclass, field

# A style primer: standard phrasing with neutral numbers, written the way the parser reads best.
STYLE = (
    "Cleared to the airport as filed, climb and maintain 5,000, squawk 4521. Runway 34L, taxi via Alpha, "
    "Bravo 2, hold short runway 16R. Holding short, ready for departure. Line up and wait. Cleared for takeoff. "
    "Contact departure 124.675. With you at 3,000 climbing 7,000. Descend and maintain flight level 240. "
    "Cleared ILS runway 14R approach. Cleared to land. Wilco. Roger. Say again. Niner. Request higher. "
    "Request direct. Request deviation 20 degrees left for weather. Say the winds. What's the altimeter? "
    "Unable. Mayday, engine failure, 2 souls on board, 1 hour of fuel. Pan-pan."
)
MAX_PROMPT_CHARS = 700  # Whisper keeps the last 224 prompt tokens; stay well inside


@dataclass(frozen=True)
class VocabularyHints:
    callsign: str = ""  # as written, e.g. "DP69" or "N172LT"
    callsign_spoken: str = ""  # e.g. "Delta Papa 69", "Cessna 2 Lima Tango"
    stations: tuple[str, ...] = ()  # "Montreal Tower", "Quebec Ground", ...
    airports: tuple[str, ...] = ()  # "Montreal", "Quebec"
    runways: tuple[str, ...] = ()  # every runway end at the airports in play, e.g. "06L", "24R"
    taxiways: tuple[str, ...] = ()  # names at the current airport
    extra: tuple[str, ...] = field(default_factory=tuple)
    style: str = ""  # the primer, if not ATC's (the intercom's crew words)
    procedures: tuple[str, ...] = ()  # the flight's SID and STAR, as filed: "RADYR2", "LUCKI1"
    fixes: tuple[str, ...] = ()  # the filed route's fixes: "LYNDI"


# What's said to the crew in each language, to listen for: without it Whisper heard "checklist" as "Cake list", "Klappen" as "Captain".
CREW_STYLE_IN = {
    "de": "Fahrwerk ausfahren. Fahrwerk einfahren. Klappen eins, Klappen zwei, Klappen voll. Landescheinwerfer an. Parkbremse lösen. Autopilot ein. Kurs zwei sieben null. Höhe zehntausend Fuß. Flugfläche drei fünf null. Geschwindigkeit zweihundertfünfzig Knoten. Anflugcheckliste, Landecheckliste. Wie viel Treibstoff haben wir? Was hat die Flugsicherung gesagt? Rampe, Catering, GPU, Pushback, Nase links, Fluggastbrücke. Kabine, einen Kaffee bitte.",
    "fr": "Sors le train. Rentre le train. Volets un, volets deux, volets pleins. Allume les phares d'atterrissage. Desserre le frein de parking. Pilote automatique. Cap deux sept zéro. Altitude dix mille pieds. Niveau de vol trois cinq zéro. Vitesse deux cent cinquante nœuds. Checklist approche, checklist atterrissage. Combien de carburant avons-nous ? Qu'est-ce que le contrôle a dit ? Rampe, catering, GPU, repoussage, nez à gauche, passerelle. Cabine, un café s'il vous plaît.",
    "es": "Baja el tren. Sube el tren. Flaps uno, flaps dos, flaps completos. Enciende las luces de aterrizaje. Quita el freno de estacionamiento. Piloto automático. Rumbo dos siete cero. Altitud diez mil pies. Nivel de vuelo tres cinco cero. Velocidad doscientos cincuenta nudos. Lista de aproximación, lista de aterrizaje. ¿Cuánto combustible tenemos? ¿Qué ha dicho la torre? Rampa, catering, GPU, remolque, morro a la izquierda, pasarela. Cabina, un café por favor.",
    "it": "Carrello giù. Carrello su. Flap uno, flap due, flap tutti. Accendi le luci di atterraggio. Rilascia il freno di parcheggio. Pilota automatico. Prua due sette zero. Quota diecimila piedi. Livello di volo tre cinque zero. Velocità duecentocinquanta nodi. Checklist di avvicinamento, checklist di atterraggio. Quanto carburante abbiamo? Cosa ha detto il controllo? Rampa, catering, GPU, pushback, muso a sinistra, finger. Cabina, un caffè per favore.",
    "pt": "Baixa o trem. Recolhe o trem. Flaps um, flaps dois, flaps todos. Liga as luzes de aterragem. Solta o travão de estacionamento. Piloto automático. Rumo dois sete zero. Altitude dez mil pés. Nível de voo três cinco zero. Velocidade duzentos e cinquenta nós. Checklist de aproximação, checklist de aterragem. Quanto combustível temos? O que disse o controlo? Rampa, catering, GPU, pushback, nariz à esquerda, manga. Cabine, um café por favor.",
    "nl": "Landingsgestel neer. Landingsgestel op. Flaps één, flaps twee, flaps vol. Landingslichten aan. Parkeerrem los. Autopiloot aan. Koers twee zeven nul. Hoogte tienduizend voet. Vliegniveau drie vijf nul. Snelheid tweehonderdvijftig knopen. Naderingschecklist, landingschecklist. Hoeveel brandstof hebben we? Wat zei de verkeersleiding? Platform, catering, GPU, pushback, neus links, slurf. Cabine, een koffie alsjeblieft.",
    "pl": "Wypuść podwozie. Schowaj podwozie. Klapy jeden, klapy dwa, klapy pełne. Włącz światła do lądowania. Zwolnij hamulec postojowy. Autopilot. Kurs dwa siedem zero. Wysokość dziesięć tysięcy stóp. Poziom lotu trzy pięć zero. Prędkość dwieście pięćdziesiąt węzłów. Checklista podejścia, checklista lądowania. Ile mamy paliwa? Co powiedziała wieża? Rampa, catering, GPU, wypychanie, nos w lewo, rękaw. Kabina, kawę proszę.",
    "sv": "Fäll ut stället. Fäll in stället. Klaff ett, klaff två, klaff full. Landningsljus på. Släpp parkeringsbromsen. Autopilot på. Kurs två sju noll. Höjd tiotusen fot. Flygnivå tre fem noll. Fart tvåhundrafemtio knop. Inflygningschecklista, landningschecklista. Hur mycket bränsle har vi? Vad sa flygledningen? Ramp, catering, GPU, pushback, nosen vänster, flygbrygga. Kabin, en kaffe tack.",
    "ru": "Выпусти шасси. Убери шасси. Закрылки один, закрылки два, закрылки полностью. Включи посадочные фары. Сними стояночный тормоз. Автопилот. Курс два семь ноль. Высота десять тысяч футов. Эшелон три пять ноль. Скорость двести пятьдесят узлов. Карта перед заходом, карта перед посадкой. Сколько у нас топлива? Что сказал диспетчер? Перрон, кейтеринг, ГПУ, буксировка, нос влево, телетрап. Салон, кофе, пожалуйста.",
    "zh": "放下起落架。收起落架。襟翼一，襟翼二，襟翼全放。打开着陆灯。松开停留刹车。自动驾驶。航向二七零。高度一万英尺。飞行高度层三五零。速度二百五十节。进近检查单，着陆检查单。我们还有多少燃油？管制说了什么？地勤，配餐，地面电源，推出，机头向左，廊桥。客舱，来杯咖啡。",
}


def build_prompt(hints: VocabularyHints) -> str:
    parts = [hints.style or STYLE]
    if hints.stations:
        parts.append("Stations: " + ", ".join(dict.fromkeys(hints.stations)) + ".")
    if hints.airports:
        parts.append("Airports: " + ", ".join(dict.fromkeys(hints.airports)) + ".")
    if hints.runways:
        parts.append("Runways " + ", ".join(dict.fromkeys(hints.runways)) + ".")
    if hints.taxiways:
        parts.append("Taxiways " + ", ".join(list(dict.fromkeys(hints.taxiways))[:20]) + ".")
    parts += list(hints.extra)
    if hints.callsign:
        spoken = f" ({hints.callsign_spoken})" if hints.callsign_spoken and hints.callsign_spoken != hints.callsign else ""
        parts.append(f"This is {hints.callsign}{spoken}.")  # last: nearest to the audio, strongest effect
    prompt = " ".join(parts)
    return prompt[-MAX_PROMPT_CHARS:] if len(prompt) > MAX_PROMPT_CHARS else prompt


def without_prompt(text: str, hints: VocabularyHints) -> str:
    """``text`` without the prompt's last sentence, where Whisper heard it at the start ("This is Swiss 2027." from a
    moment of doubtful sound, the pilot having said nothing of the kind)."""
    if not hints.callsign:
        return text
    lead = re.compile(r"^\s*this is (?:%s)[.,]?\s*" % "|".join(
        re.escape(c) for c in (hints.callsign, hints.callsign_spoken) if c), re.I)
    return lead.sub("", text)


def hotwords(hints: VocabularyHints) -> str:
    """Words worth boosting on their own: the callsign and station names."""
    words = [hints.callsign, hints.callsign_spoken, *hints.stations]
    return " ".join(w for w in dict.fromkeys(words) if w)


# Mishearings seen in aviation speech-to-text, as (pattern, replacement), whole words, case-insensitive.
FIXUPS: list[tuple[str, str]] = [
    (r"\bwhole short\b|\bhold shirt\b|\bhole short\b", "hold short"),
    (r"\bsquak\b|\bsquaw\b|\bsquat\b|\bsquack\b|\bskwak\b|\bsquawks\b", "squawk"),
    (r"\bdecent and maintain\b|\bdescent and maintain\b", "descend and maintain"),
    (r"\b(request|requesting) (?:the )?(?:sent|set|decent|dissent)\b", r"\1 descent"),  # "request the sent via CHINS5"
    (r"\bclimate and maintain\b|\bclimate maintain\b|\bclimbing maintain\b|\bline and maintain\b", "climb and maintain"),
    (r"\bline up and weight\b|\bline up and wade\b|\blineup and wait\b", "line up and wait"),
    (r"\bwill co\b|\bwilko\b|\bwill comply\b", "wilco"),
    (r"\bmay day\b", "mayday"),
    (r"\bpan pan\b", "pan-pan"),
    (r"\brun way\b|\brunaway\b", "runway"),
    (r"\btake-off\b", "takeoff"),
    (r"\bfl ?(\d{2,3})\b", r"flight level \1"),
    # "expect 37022 minutes after departure": the level and the minutes run together into one number.
    (r"\b(expect(?:ing)? )(flight level )?(\d\d[05])(\d{1,2}) minutes", r"\1\2\3 \4 minutes"),  # (a level ends in 0 or 5)
    (r"\b(expect(?:ing)? )(?:flight level |fl ?)(\d\d)(\d{2}) minutes", r"\1flight level \2 \3 minutes"),  # FL60, 10
    (r"^(?:to|toe|tell|till) (left|right)\b", r"tail \1"),  # "To left, EDW87": the pushback's turn read back
    (r"\bfo(?:ck|x)?(?:s)?trap\b|\bfox ?trot\b|\bfoxtrop\b|\bfoxstrot\b|\bfox ?tr(?:uck|ucks|ock)\b", "foxtrot"),
    (r"\bcleared for the takeoff\b", "cleared for takeoff"),
    (r"\bpoppa\b|\bpopa\b|\bpopper\b", "papa"),  # "information Poppa"
    (r"\b([0-3]),? ([0-9]),? (left|right|cent(?:er|re))\b", r"\1\2 \3"),  # "2, 6, left": runway 26L
    (r"\bi\.l\.s\.?", "ILS"),
    (r"\bready for departures\b", "ready for departure"),
    (r"\b(\d{1,2}),000,000 minutes\b", r"\1,000, 10 minutes"),  # "expect 12,000 one zero minutes" run together
    (r"(\d)er\b", r"\1"),  # "6-9er"
    (r"\b(\d),? (\d),?000\b", r"\1\g<2>000"),  # "level 1 2000", "1, 2000": one two thousand
    (r"\b([1-9]),? (\d),([1-9])00\b", r"\1\2,\g<3>00"),  # "1 4,500": one four thousand five hundred
    (r"\bshort (?:file|finale|fine|find)\b", "short final"),
    (r"\b(?:r-?naf|r-?nav|rnf|rna|(?:are|our|r) nav)\b", "RNAV"),  # ("are nav": R-NAV said as it's spelled)
    # "Runway 1, 5", "runway 2, 8 at Kilo": a runway's two digits said one by one, a comma between them
    (r"\b(runway) ([0-3]),? ([0-9])\b(?![.,]?\d)(?![.,]? (?:mile|miles|nm|nautical)\b)", r"\1 \2\3"),  # (not "3 mile final")
    (r"\btime (?:and )?maintain\b", "climb and maintain"),
    (r"\btail ?(?:rite|write|wright)\b|\btill right\b|\btale right\b", "tail right"),  # a pushback turn
    (r"\btail ?lift\b|\btill left\b|\btale left\b", "tail left"),
    (r"^(?:a|uh) (?:from|firm)\b|\ba firm\b", "affirm"),  # "affirm" said quickly: two words, one of them wrong
    (r"\bholding (?:port|pint|pointe)\b", "holding point"),
    (r"\bon my discussion\b|\bat my discussion\b|\bat your discussion\b", "at my discretion"),
    (r"\b(?:factors|vectoring|vector's|victors)\b(?= (?:for|to|onto|as filed)\b)|\bsome (?:factors|victors)\b", "vectors"),
    (r"\b(?:taxi ?viate|taxi via it|taxiv[ie]a)\b", "taxi via"),
    (r"\b(\d{3})[.,]? (?:decimal|point)[.,]? (\d{1,3})\b", r"\1.\2"),  # "119. Decimal 7" is 119.7
    (r"\b(?:crime|climate|clime|climbed)(?: and| to)? maintain\b", "climb and maintain"),
    (r"\bclimate(?= (?:\d|flight level))", "climb"),  # "Climate 17,000"
    (r"^comment in(?= \d)", "climb and maintain"),  # "Comment in 230"
    (r"\b(\d{3})\.(\d{4})\b", r"\1, \2"),  # "flight level 370.2321": the level, then the flight number; a frequency's never four decimals
    (r"\btengo\b", "tango"),
    (r"\b(?:inflammation|in formation|informations)\b", "information"),  # "We have Inflammation Golf"
    (r"\b(via|then) (?:mic|mic's|mics|mikes)\b", r"\1 Mike"),  # "via mic, Sierra Hotel ..."
    (r"(?<=, )(?:mic|mics|mikes)\b(?=,)", "Mike"),
    # Readbacks misheard around their numbers: "squad 4521", "220 nots", "hitting 270", "flight lever 350"
    (r"\bsquad\b(?=,? \d)", "squawk"),
    (r"(\d),? (?:nots|notes|knot's|nuts)\b", r"\1 knots"),
    (r"\b(?:hitting|heating|heeding|heading's)(?=,? \d{3}\b)", "heading"),
    (r"\b(?:flight lever|fly level|flightlevel|flight-level|fight level)\b", "flight level"),
    (r"\bthe (?:sent|send|scent) and maintain\b", "descend and maintain"),
    (r"\b(climb|descend)(?:ed)? (?:in|under|an|end|ad) maintain\b", r"\1 and maintain"),  # "climb under maintain"
    # "direct" in an accent: "Dehect Opail", "Dishect Bubin", "Deact Keeper", "They hacked Archie", "Directs Semper"
    (r"\b(?:de-?h[ae](?:ct|ck|kt)|di-?shect|deact|dee-?rect|directs)\b(?=[ ,-]+[A-Za-z])", "direct"),
    (r"(?:^|(?<=[.,] ))(?:they|the) hacked\b(?= [A-Za-z])", "direct"),
    (r"\bdirect\.(?=[a-z])", "direct "),  # "direct.gun"
    # A level in an accent: "flight level 1 under" (one hundred), "flight level 1 to 0" (one two zero)
    (r"\b(flight level) ([1-4]) (?:under|onder|hunder|hundred)(?: the)?\b", r"\1 \g<2>00"),
    (r"\b(flight level|heading|inbound) ([0-4]) (?:to|too) ([0-9])\b", r"\1 \g<2>2\3"),
    (r"\b(?:es|is|e)(?:quok|quark|quawk|quak|kwak|quack)\b", "squawk"),  # (an "s" before a consonant: "Esquark", "Isquark")
    (r"\bis (?=squawk\b)", ""),  # "is squawk 4521"
    (r"\bfly,? level\b", "flight level"),  # "Down to fly, level 120"
    # "5350 in 10": five thousand, then the level expected (three five zero) in ten minutes, run together
    (r"\b([1-9]|1\d)([1-5]\d0) (?:in|and) (\d{1,2})\b(?! ?(?:knots|degrees|miles))", r"\1,000, \2 in \3"),
    # "zero" said "tsero": "flight level 12-Z-Hoe", "inbound 27Z"
    (r"(\d)-?Z-(?:A-)?Hoe\b", r"\g<1>0"),
    (r"\b(flight level|heading|inbound) (\d{1,2})Z\b", r"\1 \g<2>0"),
    (r"\b(?:dennis|then is|than as|den as|then us|ten as|then has|than is)(?=,? filed\b)", "then as"),  # "Dennis filed"
    # "Tower 118, 3": a frequency after the station's name, its decimal point heard as a pause
    (r"\b(tower|ground|departure|approach|cent(?:er|re)|clearance|delivery|radio|director|control)(,? (?:on )?)"
     r"(1[1-3]\d)[.,] (\d{1,3})\b(?![,.]?\d)", r"\1\2\3.\4"),
    (r"\b(?:runway|taxiway) (\d{1,2})[.,]? (r|l|c)(?:ight|eft|enter)\b[?,.]?", lambda m: f"runway {m[1]}{m[2].upper()}"),  # "Runway 10, right?"
]
_COMPILED = [(re.compile(p, re.IGNORECASE), r) for p, r in FIXUPS]


DIGIT_RUN = re.compile(r"\b(\d+)-(\d+)\b")


def fixup(text: str) -> str:
    """Correct well-known mishearings; everything else is left for the parser and the model."""
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)
    while DIGIT_RUN.search(text):  # Whisper hyphenates digits said one by one: "1-2000", "0-6", "5-0-1-5"
        text = DIGIT_RUN.sub(r"\1\2", text)
    for pattern, replacement in _AFTER_DIGITS:
        text = pattern.sub(replacement, text)
    return _written(" ".join(text.split()))


# Once the hyphenated digits are joined ("expect 1-7,010 minutes")
_AFTER_DIGITS = [(re.compile(p, re.IGNORECASE), r) for p, r in [
    # "expect 14,010 minutes after": one four thousand, one zero minutes, run together
    (r"\b(expect(?:ing)? )(\d{1,2}),0(\d{2}) minutes\b", r"\g<1>\2,000, \3 minutes"),
    # "expect 6010 minutes": six thousand, one zero minutes (601 is no flight level)
    (r"\b(expect(?:ing)? )([1-9])0([1-9]\d) minutes\b", r"\g<1>\2,000, \3 minutes"),
    (r"\b(expect(?:ing)? )(?:flight level |fl ?)(\d\d)(\d{2}) minutes", r"\1flight level \2 \3 minutes"),  # "8-0-1-0"
]]


PHONETIC = {"alpha": "A", "alfa": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E", "foxtrot": "F", "golf": "G",
            "hotel": "H", "india": "I", "juliet": "J", "juliett": "J", "kilo": "K", "lima": "L", "mike": "M", "november": "N",
            "oscar": "O", "papa": "P", "quebec": "Q", "romeo": "R", "sierra": "S", "tango": "T", "uniform": "U", "victor": "V",
            "whiskey": "W", "x-ray": "X", "xray": "X", "yankee": "Y", "zulu": "Z"}
_NAME = "|".join(sorted(PHONETIC, key=len, reverse=True))  # ("juliett" before "juliet": "Jt" was left of it)
# One name of a route: "Tango", "Hotel 4", "H4". Not a callsign's first word ("Delta 2321"): a taxiway has two digits at most.
_ITEM = rf"(?:(?:{_NAME})(?:[ -]\d{{1,2}}(?![\d.,]\d))?(?![ -]?\d)|[a-z]\d{{0,2}}(?![a-z\d]))"
_ROUTE = re.compile(rf"\b(via) ({_ITEM}(?:(?:[.,]? and|[.,])? {_ITEM})*)", re.IGNORECASE)  # ("via A. A3 Bravo")
_ONE = re.compile(rf"\b({_NAME})(?:[ -](\d{{1,2}}))?\b", re.IGNORECASE)
_STAND = re.compile(rf"\b(gate|stand) ({_NAME})[ -]?(\d{{1,3}})\b", re.IGNORECASE)


def _written(text: str) -> str:
    """A taxi route and a gate as they're written: "via Tango, Charlie, H4" is "via T, C, H4", "gate Delta 1" is "gate D1"."""
    def route(m: re.Match) -> str:
        return f"{m[1]} " + _ONE.sub(lambda n: PHONETIC[n[1].lower()] + (n[2] or ""), m[2])

    return _STAND.sub(lambda m: f"{m[1]} {PHONETIC[m[2].lower()]}{m[3]}", _ROUTE.sub(route, text))


# --- the flight's own names ------------------------------------------------------------------------------------------
# Whisper writes what it hears: "Raider 2" for RADYR2, "from to 1469" for Frontier 1469. Put right only where it can only
# be the flight's own name, sounding the same; a wrong procedure or a wrong number doesn't sound the same, and stays
# wrong, for the readback to catch.
PHONETIC = {"alpha": "A", "alfa": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E", "foxtrot": "F",
            "golf": "G", "hotel": "H", "india": "I", "juliet": "J", "juliett": "J", "kilo": "K", "lima": "L", "mike": "M",
            "november": "N", "oscar": "O", "papa": "P", "quebec": "Q", "romeo": "R", "sierra": "S", "tango": "T",
            "uniform": "U", "victor": "V", "whiskey": "W", "x-ray": "X", "xray": "X", "yankee": "Y", "zulu": "Z"}
_LETTER = "|".join(sorted(map(re.escape, PHONETIC), key=len, reverse=True))
SPELLED = re.compile(r"\b[A-Za-z0-9]{1,3}(?:-[A-Za-z0-9]{1,3}){1,6}\b")
# (Not a number's first digit: "maintain 2,000" was MONTN2,000 with the MONTN2 departure filed.)
PROCEDURE_SAID = re.compile(r"\b([A-Za-z]{3,})[ -]?(\d)\b(?![.,]?\d)")
# ATC's own words, never a procedure or a fix however they sound: "maintain" is MONTN, "heading" HDNG.
NEVER_A_NAME = {"maintain", "maintaining", "climb", "climbing", "descend", "descending", "heading", "runway", "squawk",
                "contact", "cleared", "expect", "flight", "level", "turn", "left", "right", "taxi", "via", "hold",
                "holding", "direct", "approach", "departure", "arrival", "tower", "ground", "center", "centre", "speed",
                "knots", "thousand", "hundred", "decimal", "point", "until", "established", "after", "minutes", "then",
                "filed", "cross", "inbound", "outbound", "radial", "frequency", "altitude", "wind", "information"}
# A procedure's name heard in pieces, its number said or written: "F falls three" FOLZZ3, "FOL ZZ 3", "this NAPR 2" SNAPR2.
DIGIT_WORDS = {"one": "1", "two": "2", "three": "3", "tree": "3", "four": "4", "five": "5", "fife": "5", "six": "6",
               "seven": "7", "eight": "8", "nine": "9", "niner": "9"}
# (A word's "'s" is part of it: "Holt's niner" HOLTZ9; Europe's letter after the number: "Lazet one alpha" LAZE1A.)
_WORD = r"[A-Za-z]+(?:'[A-Za-z]+)?"
PROCEDURE_PIECES = re.compile(
    rf"\b((?:{_WORD}[ -]+){{0,3}}{_WORD})[ -]+(\d|one|two|three|tree|four|five|fife|six|seven|eight|nine|niner)\b(?![.,]?\d)"
    rf"((?:,?[ -]+(?:{_LETTER})\b)?)", re.I)
MIN_PIECES_SOUND = 3  # a name put together from pieces sounds like this many consonants at least
# A procedure's number heard as the word it sounds like, where only a procedure can follow: "the Angel for arrival" (ANJLL4),
# "the Raider to departure", "Lazy won alpha departure".
SOUNDS_LIKE_DIGIT = {"won": "1", "to": "2", "too": "2", "for": "4", "fore": "4", "free": "3", "tree": "3", "ate": "8",
                     "sex": "6"}
PROCEDURE_HOMOPHONE = re.compile(
    rf"\b((?:{_WORD}[ -]+){{0,3}}{_WORD})[ -]+(won|to|too|for|fore|free|tree|ate|sex)"
    rf"((?:[ -]+(?:{_LETTER}))?)(?=[ ,.]+(?:departure|arrival|transition)s?\b)", re.I)
FIX_SAID = re.compile(r"\b(direct(?: to)?|via|at|to|hold(?:ing)?(?: at)?)\s+([A-Za-z]{4,})\b", re.I)
LETTER_AND_NUMBER = re.compile(rf"\b({_LETTER})[ -]?(\d{{1,2}}[LRC]?)\b(?![.,]?\d)", re.I)
INFORMATION = re.compile(rf"\b(information)([.,]?\s+)({_LETTER})\b", re.I)
VIA_LIST = re.compile(rf"\b(via\s+)((?:(?:{_LETTER}|[A-Z]\d{{0,2}})(?:,?\s+|$|(?=[.,])))+)", re.I)
MIN_CALLSIGN_LIKE = 0.5
# "RNAV to JETTI" heard as "on F2 jet T": the RNAV, the "to", and the fix in pieces.
RNAV_TO = re.compile(r"\b(?:(?:on|an|are|in) )?(?:r ?nav|r-?nav|[fv])[ -]?(?:2|to|two)[ -]+([A-Za-z]+(?:[ -][A-Za-z]{1,4}){0,2})\b", re.I)


def _sound(word: str) -> str:
    """A word's consonants, as it sounds: RADYR and "Raider", LUCKI and "Lucky" come out the same."""
    # (w goes with the vowels and z is an s: "Fonz" for FAWNS; a g before e, i or y is a j: "Angel" for ANJLL; x is ks,
    # q a k: "Kugex" and "Kugeks")
    word = word.lower().replace("ck", "k").replace("ph", "f").replace("c", "k").replace("z", "s").replace("x", "ks")
    word = re.sub(r"g(?=[eiy])", "j", word).replace("q", "k")
    head, rest = word[:1], re.sub(r"[aeiouyhw]", "", word[1:])
    return re.sub(r"(.)\1+", r"\1", head + rest)


# How alike a heard word sounds to a name, past what one spelling of a sound or an accent changes: the letters as
# they're said (ph f, a soft c s, a soft g j, x ks, X at the start z), voiced and unvoiced alike (b p, d t, g k, v f,
# s z), any vowel a vowel. "Diamond" for DYAMD, "Lobster" LBSTA, "Itloaf" ITLOV, "Timber" TIMBA, "Ocean" OOSHN.
_SAID_AS = (("tch", "ch"), ("ck", "k"), ("ph", "f"), ("gh", ""), ("sh", "s"), ("ch", "k"), ("qu", "kw"), ("q", "k"),
            ("x", "ks"), ("wr", "r"), ("kn", "n"), ("ee", "i"), ("ea", "i"), ("iew", "u"), ("oo", "u"), ("ou", "u"),
            ("ew", "u"), ("ie", "i"), ("ay", "e"), ("ai", "e"), ("ey", "i"))
_ALIKE = str.maketrans({"b": "p", "d": "t", "g": "k", "v": "f", "z": "s", "j": "s", "y": "i", "w": "u"})
SOUNDS_LIKE = 0.6  # this alike, and clearly more than any other of the flight's names: it's that one
SOUNDS_CLEARER = 0.08  # ... by this much (TIMBER: TIMBA 0.83, the next 0.38)


def _said_key(word: str, vowels: bool = True) -> str:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    w = re.sub(r"^x", "z", w)
    for spelled, said in _SAID_AS:
        w = w.replace(spelled, said)
    w = re.sub(r"g(?=[eiy])", "j", re.sub(r"c(?=[eiy])", "s", w)).replace("c", "k").translate(_ALIKE)
    w = w[0] + w[1:].replace("h", "")
    w = re.sub(r"(.)\1+", r"\1", w)
    return w if vowels else w[0] + re.sub(r"[aeiou]", "", w[1:])


def sounds_like(said: str, name: str) -> float:
    """0 to 1: how alike ``said`` (as written by speech-to-text) and ``name`` (a fix or a procedure's) sound."""
    k1, k2 = _said_key(said), _said_key(name)
    if not k1 or not k2:
        return 0.0
    c1, c2 = _said_key(said, False), _said_key(name, False)
    squeeze = lambda s: re.sub(r"(.)\1+", r"\1", re.sub(r"[^A-Z]", "", s.upper()))  # noqa: E731
    score = (0.4 * difflib.SequenceMatcher(None, k1, k2).ratio() + 0.4 * difflib.SequenceMatcher(None, c1, c2).ratio()
             + 0.2 * difflib.SequenceMatcher(None, squeeze(said), squeeze(name)).ratio())
    if k1[0] == k2[0] or (k1[0] in "aeiou" and k2[0] in "aeiou"):
        score += 0.05  # it starts the same
    return score


def _sounds_most_like(said: str, names: tuple[str, ...], also: dict[str, tuple[str, ...]] | None = None) -> str | None:
    """The one of ``names`` that ``said`` sounds clearly most like, or None. ``also``: other ways a name is said (a
    procedure's whole fix: VUTE3E's VUTEB)."""
    if len(re.sub(r"[^A-Za-z]", "", said)) < 2:
        return None
    scored = sorted(((max(sounds_like(said, form) for form in (name, *(also or {}).get(name, ()))), name)
                     for name in dict.fromkeys(names)), reverse=True)
    if not scored or scored[0][0] < SOUNDS_LIKE:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < SOUNDS_CLEARER:
        return None  # two of them alike: which it was isn't known
    return scored[0][1]


def _named(said: str, names: tuple[str, ...], stem: bool = False) -> str | None:
    """The one of ``names`` that ``said`` sounds like. ``stem``: the names are cut short, as Europe's procedures are
    (LAZE1A is the LAZET one alpha, VUTE3E the VUTEB three echo): said, the whole fix is heard, a sound more."""
    key = _sound(said)
    found = {n for n in names if _sound(n) == key} if len(key) >= 2 else set()
    if not found and stem and len(key) >= 3:
        found = {n for n in names if len(_sound(n)) >= 2 and key.startswith(_sound(n)) and len(key) - len(_sound(n)) == 1}
    return found.pop() if len(found) == 1 else None  # (two names that sound alike: which it was isn't known)


NOT_IN_A_NAME = {"the", "a", "an", "by", "as", "via", "cleared", "clear", "to", "and", "then", "on", "of", "is", "are"}


def _pieces(m: re.Match, procedures: tuple[str, ...], digit: str = "", suffix: str = "",
            fixes: tuple[str, ...] = ()) -> str:
    """The words before a number, as few as make a name, read as the flight's own procedure: one word, or two to four
    joined ("F falls": FOLZZ's sound), or the last with the end of the one before ("this NAPR": SNAPR). Only where
    exactly one of its procedures sounds so and has that number (and ``suffix``, its letter: "1A")."""
    words = [w.replace("'", "") for w in re.split(r"[ -]+", m.group(1))]
    digit = digit or DIGIT_WORDS.get(m.group(2).lower(), m.group(2))
    end = digit + suffix
    wanted = tuple(p[:-len(end)] for p in procedures if p.endswith(end) and p[:-len(end)].isalpha())
    if not wanted:
        return m.group(0)
    tries: list[tuple[int, str]] = [(n, "".join(words[-n:])) for n in range(min(4, len(words)), 1, -1)]  # most first
    if len(words) >= 2:
        tries += [(2, words[-2][-k:] + words[-1]) for k in (1, 2) if len(words[-2]) > k]
    tries.append((1, words[-1]))
    for n, said in tries:
        if len(said) < (3 if n == 1 else 4) or n > 1 and len(_sound(said)) < MIN_PIECES_SOUND:
            continue
        if any(w.lower() in NEVER_A_NAME or w.lower() in DIGIT_WORDS for w in words[-n:]):
            continue  # never across ATC's words or a number said: "maintain one two thousand" was MONTN2 thousand
        if (named := _named(said, wanted, stem=bool(suffix))) is not None:
            kept = " ".join(words[:-n])
            return (kept + " " if kept else "") + f"{named}{end}"
    # No spelling of the sound the same: the word or two before the number that sound most like it ("the Ocean 5" for
    # OOSHN5, "a full 3" FOLZZ3, "Jurcer 1 Yankee" GERS1Y), Europe's said as their whole fix (VUTE3E, "Viewedab 3 Echo").
    full = {stem: tuple(f for f in fixes if f.startswith(stem) and len(f) > len(stem)) for stem in wanted}
    for n in (1, 2):
        if len(words) < n or any(w.lower() in NEVER_A_NAME or w.lower() in DIGIT_WORDS or w.lower() in NOT_IN_A_NAME
                                 for w in words[-n:]):
            break
        said = "".join(words[-n:])
        if len(said) >= 3 and (named := _sounds_most_like(said, wanted, full)) is not None:
            kept = " ".join(words[:-n])
            return (kept + " " if kept else "") + f"{named}{end}"
    return m.group(0)


def _homophone(m: re.Match, procedures: tuple[str, ...], fixes: tuple[str, ...] = ()) -> str:
    """ "the Angel for arrival": the flight's ANJLL4, its number heard as a word; else left as heard."""
    letter = m.group(3).strip(" -").lower()
    suffix = PHONETIC[letter] if letter else ""
    named = _pieces(m, procedures, SOUNDS_LIKE_DIGIT[m.group(2).lower()], suffix, fixes)
    return named if named != m.group(0) else m.group(0)


# Where only a fix's name can follow, however it's heard: "direct", "hold at" (heard "hold a", "hold it", "holt at",
# "older"), "cross". Then the next word or two, the callsign's own words never among them.
FIX_SOUNDS = re.compile(
    r"\b(direct(?: to)?|proceed(?:ing)? direct(?: to)?|hold(?:ing)? (?:at|over)|hold (?:a|it|ad|that)|holt at|halt at|"
    r"older|halter(?:ed)?|halted(?: at)?|holder|cross(?:ing)?)[ ,-]+((?:the )?([A-Za-z][A-Za-z'-]*)(?:[ -]([A-Za-z][A-Za-z']*))?)", re.I)
# Where "direct" goes that isn't a fix: "direct to the airport", "proceed direct on course".
NOT_A_FIX = {"airport", "field", "destination", "course", "navigation", "own", "waypoint", "fix", "next", "present",
             "position", "final", "localizer", "outer", "marker", "vor", "ndb", "please", "request", "requesting",
             "now", "when", "if", "for", "we", "i", "is", "it", "that", "this", "there", "here"}
HOLD_HEARD = re.compile(r"^(?:hold (?:a|it|ad|that)|holt at|halt at|older|halter(?:ed)?|halted(?: at)?|holder)$", re.I)


def _fix_sounding(m: re.Match, hints: VocabularyHints) -> str:
    """ "direct Diamond" DYAMD, "hold a carob" HAROB, "direct sea view" SEAVU: the flight's own fix that the word, or
    the two together, sound clearly most like. A word that's a fix already, or ATC's, or the callsign's, is left."""
    first, second = m.group(3), m.group(4)
    own = {w.lower() for w in hints.callsign_spoken.split()} | {hints.callsign.lower()}
    own |= {w.lower() for place in (*hints.airports, *hints.stations) for w in place.split()}  # "direct Boston": the airport
    lead = "hold at" if HOLD_HEARD.match(m.group(1)) else m.group(1)
    if first.upper() in hints.fixes:  # the fix already, "hold" heard as "holt at"
        return lead + m.group(0)[len(m.group(1)):]
    if first.lower() in NEVER_A_NAME or first.lower() in own or first.lower() in NOT_A_FIX:
        return m.group(0)
    tries = [(first, m.group(0)[:m.start(3) - m.start()] + first)]
    if second and second.lower() not in NEVER_A_NAME and second.lower() not in own and second.lower() not in PHONETIC \
            and second.lower() not in ("inbound", "at", "and", "then", "climb", "descend", "maintain"):
        tries.insert(0, (first + second, m.group(0)))
    best = None
    for said, span in tries:
        named = _sounds_most_like(said, hints.fixes)
        score = sounds_like(said, named) if named else 0.0
        if named and (best is None or score > best[0] + 0.05):
            best = (score, named, span)
    if best is None:
        return m.group(0)
    return f"{lead} {best[1]}" + m.group(0)[len(best[2]):]


def _letters(text: str, hints: VocabularyHints) -> str:
    """Taxiways, gates and the ATIS as they're written: "Echo 10" E10, "information Mike" M, "via Bravo, Papa" B, P. The
    callsign's own word is left alone ("Delta 12")."""
    own = (hints.callsign_spoken.split() or [""])[0].lower()

    def number(m: re.Match) -> str:
        return m.group(0) if m.group(1).lower() == own else PHONETIC[m.group(1).lower()] + m.group(2).upper()

    taxiways = {w.upper() for w in hints.taxiways}

    def listed(m: re.Match) -> str:
        found = list(re.finditer(r"[A-Za-z-]+\d*[LRC]?", m.group(2)))
        names: list[str] = []
        for k, w in enumerate(found):
            letter = PHONETIC.get(w.group(0).lower(), w.group(0))
            gap = m.group(2)[found[k - 1].end():w.start()] if k else ","
            if names and "," not in gap and (names[-1] + letter).upper() in taxiways and len(names[-1]) == 1:
                names[-1] += letter  # "Bravo Tango" is taxiway BT at Fiumicino, said without a pause
            else:
                names.append(letter)
        # (the comma after the last kept: "via E7, 2LT" ran into taxiway E72)
        tail = re.search(r"[,\s]*$", m.group(2)).group(0)
        return m.group(1) + ", ".join(names) + tail

    text = LETTER_AND_NUMBER.sub(number, text)
    text = INFORMATION.sub(lambda m: f"{m.group(1)} {PHONETIC[m.group(3).lower()]}", text)
    # "... gate Echo 10. Mike.": a letter on its own at the end
    text = re.sub(rf"(?<=[.,] )({_LETTER})\.?$", lambda m: PHONETIC[m.group(1).lower()] + ".", text, flags=re.I)
    return VIA_LIST.sub(listed, text)


def _callsign(text: str, hints: VocabularyHints) -> str:
    """ "from to 1469", "Tier 1469", "for 201469": the flight's own number after words that sound like its name."""
    spoken = hints.callsign_spoken.split()
    if len(spoken) != 2 or not spoken[1].isdigit() or hints.callsign_spoken.lower() in text.lower():
        return text
    name, number = spoken
    found = re.search(rf"((?:\b[A-Za-z]+[ -]+){{0,2}})\b\d{{0,2}}{number}\b", text)
    if found is None:
        return text
    before = found.group(1).split()
    for count in (2, 1):
        said = "".join(before[-count:]).lower() if len(before) >= count else ""
        if said and difflib.SequenceMatcher(None, said, name.lower()).ratio() >= MIN_CALLSIGN_LIKE:
            kept = " ".join(before[:-count])
            return text[:found.start()] + (kept + " " if kept else "") + hints.callsign_spoken + text[found.end():]
    return text


def match_names(text: str, hints: VocabularyHints) -> str:
    """``text`` with the flight's own names as they're written, where what was heard can only be them."""
    if not text:
        return text
    names = (*hints.procedures, *hints.fixes)

    def spelled(m: re.Match) -> str:
        joined = m.group(0).replace("-", "").upper()
        parts = m.group(0).split("-")
        if joined in names or all(len(part) == 1 and part.isalpha() for part in parts):
            return joined
        wanted = tuple(p[:-1] for p in hints.procedures if p[-1:] == joined[-1:])  # "LU-CK-1": LUCKI1
        named = _named(joined[:-1], wanted) if joined[-1:].isdigit() else None
        return f"{named}{joined[-1]}" if named else m.group(0)

    text = SPELLED.sub(spelled, text)

    def procedure(m: re.Match) -> str:
        if m.group(1).lower() in NEVER_A_NAME:
            return m.group(0)
        wanted = [p for p in hints.procedures if p[-1:] == m.group(2)]
        return _named(m.group(1), tuple(p[:-1] for p in wanted)) and \
            f"{_named(m.group(1), tuple(p[:-1] for p in wanted))}{m.group(2)}" or m.group(0)

    text = PROCEDURE_SAID.sub(procedure, text)
    def pieces(m: re.Match) -> str:
        letter = m.group(3).strip(" ,-").lower()
        if letter and (named := _pieces(m, hints.procedures, suffix=PHONETIC[letter], fixes=hints.fixes)) != m.group(0):
            return named  # "Lazet one alpha": LAZE1A
        # (else the letter's another word: "Stick 5, Delta 1123")
        named = _pieces(m, hints.procedures, fixes=hints.fixes)
        return named + m.group(3) if named != m.group(0) else m.group(0)

    text = PROCEDURE_PIECES.sub(pieces, text)
    text = PROCEDURE_HOMOPHONE.sub(lambda m: _homophone(m, hints.procedures, hints.fixes), text)

    def fix(m: re.Match) -> str:
        named = _named(m.group(2), hints.fixes) if m.group(2).lower() not in NEVER_A_NAME else None
        return f"{m.group(1)} {named}" if named else m.group(0)

    text = FIX_SAID.sub(fix, text)
    if hints.fixes:
        text = FIX_SOUNDS.sub(lambda m: _fix_sounding(m, hints), text)

    def rnav(m: re.Match) -> str:
        named = _named("".join(re.split(r"[ -]", m.group(1))), tuple(f for f in hints.fixes if len(f) >= 4))
        return f"RNAV to {named}" if named else m.group(0)

    text = RNAV_TO.sub(rnav, text)
    return _letters(_callsign(text, hints), hints)
