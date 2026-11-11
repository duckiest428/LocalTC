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
    (r"\bexpect (flight level )?(\d{3})(\d{1,2}) minutes", r"expect \1\2 \3 minutes"),
    (r"^(?:to|toe|tell|till) (left|right)\b", r"tail \1"),  # "To left, EDW87": the pushback's turn read back
    (r"\bfo(?:ck|x)?(?:s)?trap\b|\bfox ?trot\b|\bfoxtrop\b|\bfoxstrot\b|\bfox ?tr(?:uck|ucks|ock)\b", "foxtrot"),
    (r"\bcleared for the takeoff\b", "cleared for takeoff"),
    (r"\bi\.l\.s\.?", "ILS"),
    (r"\bready for departures\b", "ready for departure"),
    (r"\b(\d{1,2}),000,000 minutes\b", r"\1,000, 10 minutes"),  # "expect 12,000 one zero minutes" run together
    (r"(\d)er\b", r"\1"),  # "6-9er"
    (r"\b(\d),? (\d),?000\b", r"\1\g<2>000"),  # "level 1 2000", "1, 2000": one two thousand
    (r"\bshort (?:file|finale|fine|find)\b", "short final"),
    (r"\b(?:r-?naf|r-?nav|rnf|rna)\b", "RNAV"),
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
    return _written(" ".join(text.split()))


PHONETIC = {"alpha": "A", "alfa": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E", "foxtrot": "F", "golf": "G",
            "hotel": "H", "india": "I", "juliet": "J", "juliett": "J", "kilo": "K", "lima": "L", "mike": "M", "november": "N",
            "oscar": "O", "papa": "P", "quebec": "Q", "romeo": "R", "sierra": "S", "tango": "T", "uniform": "U", "victor": "V",
            "whiskey": "W", "x-ray": "X", "xray": "X", "yankee": "Y", "zulu": "Z"}
_NAME = "|".join(PHONETIC)
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
PROCEDURE_SAID = re.compile(r"\b([A-Za-z]{3,})[ -]?(\d)\b")
FIX_SAID = re.compile(r"\b(direct(?: to)?|via|at|to|hold(?:ing)?(?: at)?)\s+([A-Za-z]{4,})\b", re.I)
LETTER_AND_NUMBER = re.compile(rf"\b({_LETTER})[ -]?(\d{{1,2}}[LRC]?)\b(?![.,]?\d)", re.I)
INFORMATION = re.compile(rf"\b(information)([.,]?\s+)({_LETTER})\b", re.I)
VIA_LIST = re.compile(rf"\b(via\s+)((?:(?:{_LETTER}|[A-Z]\d{{0,2}})(?:,?\s+|$|(?=[.,])))+)", re.I)
MIN_CALLSIGN_LIKE = 0.5
# "RNAV to JETTI" heard as "on F2 jet T": the RNAV, the "to", and the fix in pieces.
RNAV_TO = re.compile(r"\b(?:(?:on|an|are|in) )?(?:r ?nav|r-?nav|[fv])[ -]?(?:2|to|two)[ -]+([A-Za-z]+(?:[ -][A-Za-z]{1,4}){0,2})\b", re.I)


def _sound(word: str) -> str:
    """A word's consonants, as it sounds: RADYR and "Raider", LUCKI and "Lucky" come out the same."""
    # (w goes with the vowels and z is an s: "Fonz" for FAWNS)
    word = word.lower().replace("ck", "k").replace("ph", "f").replace("c", "k").replace("z", "s")
    head, rest = word[:1], re.sub(r"[aeiouyhw]", "", word[1:])
    return re.sub(r"(.)\1+", r"\1", head + rest)


def _named(said: str, names: tuple[str, ...]) -> str | None:
    key = _sound(said)
    found = {n for n in names if _sound(n) == key} if len(key) >= 2 else set()
    return found.pop() if len(found) == 1 else None  # (two names that sound alike: which it was isn't known)


def _letters(text: str, hints: VocabularyHints) -> str:
    """Taxiways, gates and the ATIS as they're written: "Echo 10" E10, "information Mike" M, "via Bravo, Papa" B, P. The
    callsign's own word is left alone ("Delta 12")."""
    own = (hints.callsign_spoken.split() or [""])[0].lower()

    def number(m: re.Match) -> str:
        return m.group(0) if m.group(1).lower() == own else PHONETIC[m.group(1).lower()] + m.group(2).upper()

    def listed(m: re.Match) -> str:
        words = re.findall(r"[A-Za-z-]+\d*[LRC]?", m.group(2))
        return m.group(1) + ", ".join(PHONETIC.get(w.lower(), w) for w in words) + (" " if m.group(2)[-1:].isspace() else "")

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
        wanted = [p for p in hints.procedures if p[-1:] == m.group(2)]
        return _named(m.group(1), tuple(p[:-1] for p in wanted)) and \
            f"{_named(m.group(1), tuple(p[:-1] for p in wanted))}{m.group(2)}" or m.group(0)

    text = PROCEDURE_SAID.sub(procedure, text)

    def fix(m: re.Match) -> str:
        named = _named(m.group(2), hints.fixes)
        return f"{m.group(1)} {named}" if named else m.group(0)

    text = FIX_SAID.sub(fix, text)

    def rnav(m: re.Match) -> str:
        named = _named("".join(re.split(r"[ -]", m.group(1))), tuple(f for f in hints.fixes if len(f) >= 4))
        return f"RNAV to {named}" if named else m.group(0)

    text = RNAV_TO.sub(rnav, text)
    return _letters(_callsign(text, hints), hints)
