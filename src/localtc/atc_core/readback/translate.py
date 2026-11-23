"""What a pilot says in another language, put into the English the grammar reads.

"Montons niveau un deux zéro, cap deux sept zéro, Air France un deux trois" is read as "climb flight level 120
heading 270 air france 123": the numbers as figures, then each language's aviation words in English (its
``Tongue.lexicon``), so a readback in French is checked by the same rules as one in English, and a request is
understood the same way. Callsigns, fixes and station names are left as they were said.

The number words are worked out the way they're said on the radio: digits one by one are one number ("deux sept zéro"
is 270), a number said whole is its value ("trois mille cinq cents" is 3500, "quatre-vingt-dix" 90), and groups are
put together ("douze trente-quatre" is 1234).
"""

import re
from functools import lru_cache

from localtc.atc_core.phraseology.tongues import ICAO_LETTERS, Tongue, plain, tongue

JOIN = "+"  # a connector between two parts of a number ("vingt et un", "fünfundzwanzig")

# One word, a figure (with its decimals), or anything else: in that order.
PIECE = re.compile(r"(\d+(?:[.,]\d+)?)|([^\W\d_]+)|(\s+)|(.)", re.UNICODE)
# A single capital letter (or one with figures: "A1", "B12") that isn't the first word: a taxiway, a letter of the
# spelling alphabet. ("À" and the like are words, and don't match.)
LETTER = re.compile(r"(?<=[\s,;:(])([A-Z])(\d{0,2})(?=$|[\s,.;:)])")
# An approach's letters spelled ("I L S", "V O R D M E", "R-NAV"): one word, as written.
ACRONYM = re.compile(r"(?<![A-Za-z])(?:I L S|V O R(?: D M E)?|N D B(?: D M E)?|D M E|G P S|L D A|S D F|R-NAV|R NAV)(?![A-Za-z])")
FLIGHT_LEVEL = re.compile(r"(?i)(?<![a-z])fl ?(\d{2,3})(?!\d)")
ICAO_WORDS = frozenset(ICAO_LETTERS.values())
ZH_LETTER = re.compile(r"(?<![A-Za-z0-9])([A-Z])(\d{0,2})(?![A-Za-z0-9])")
THIN_SPACES ="[\u00a0\u202f\u2009]"  # between thousands: "3 000" (never between two numbers)
SPACED_THOUSANDS = re.compile(r"(?<!\d)(\d{1,3})(?:" + THIN_SPACES + r"(\d{3}))+(?!\d)")


def to_english(text: str, code: str) -> str:
    """``text`` said in ``code`` ("fr"), in English words for the grammar; as it is for English or a language
    LocalTC doesn't read."""
    tg = tongue(code)
    if tg is None or not text.strip():
        return text
    if tg.code == "zh":
        from localtc.atc_core.phraseology.tongues.zh import figures as zh_figures

        # (No spaces between words: a capital letter alone, not part of a name or a runway's "36L", is a letter.)
        said = ZH_LETTER.sub(lambda m: f" {ICAO_LETTERS[m.group(1)]} {m.group(2)} ",
                             ACRONYM.sub(lambda m: m.group(0).replace(" ", "").replace("-", ""), text))
        said = zh_figures(FLIGHT_LEVEL.sub(r"FL \1", said))
        return _words(_lexicon(tg.code), said.lower(), spaced=False)
    said = _letters(ACRONYM.sub(lambda m: m.group(0).replace(" ", "").replace("-", ""), text))
    said = FLIGHT_LEVEL.sub(r"FL \1", said)
    said = SPACED_THOUSANDS.sub(lambda m: re.sub(THIN_SPACES, "", m.group(0)), said)
    said = plain(said)
    said = re.sub(r"[\u2019'`\u00b4\-\u2013]", " ", said)
    said = _feet_thousands(said, plain(tg.feet))
    said = figures(said, tg)
    english = _words(_lexicon(tg.code), said, spaced=True)
    if tg.romanize is not None:  # a name written in the language's script: in Latin letters, as the callsigns are
        english = re.sub(r"[^\W\d_a-z]+", lambda m: tg.romanize(m.group(0)), english)
    return english


def read_in(text: str, code: str) -> str:
    """ ``code`` when ``text`` (typed, so nobody heard which) reads as that language: two of its aviation words or
    more, and more of them than English ones; else "en"."""
    tg = tongue(code)
    if tg is None or not text.strip():
        return "en"
    said = plain(text) if tg.code != "zh" else text
    if tg.code == "zh":
        return code if re.search(r"[一-鿿]", text) else "en"
    own = sum(1 for pattern, english in _lexicon(tg.code) if english and pattern.search(said)
              and not re.fullmatch(english.replace("\\1", "").strip(), pattern.search(said).group(0)))
    english = len(re.findall(r"\b(?:runway|cleared|climb|descend|maintain|heading|contact|request|taxi|ready|roger"
                             r"|squawk|tower|ground|approach|departure|wilco|say again|with you)\b", text.lower()))
    return code if own >= 2 and own > english else "en"


ENGLISH_WORDS = re.compile(r"\b(?:runway|cleared|climb|descend|maintain|heading|contact|taxi|ready|roger|squawk|tower"
                           r"|ground|approach|departure|wilco|say again|expect|flight level|hold short|line up|via"
                           r"|frequency|altimeter|wind|traffic|unable|affirm|negative|knots|feet)\b", re.IGNORECASE)


def sounds_english(text: str, code: str) -> bool:
    """A reply that ought to be in ``code`` and reads as English: two of English's radio words or more, and none of
    that language's words found in it (a short reply in it, "cap 270", has hardly any of either)."""
    english = len(ENGLISH_WORDS.findall(text))
    if english < 2:
        return False
    if code == "zh":
        return not re.search(r"[\u4e00-\u9fff]", text)
    return to_english(text, code).lower() == re.sub(r"\s+", " ", plain(text)).strip(" ,.").lower()


def _letters(text: str) -> str:
    """Capital letters said alone as the spelling alphabet's words ("via A, B1" -> "via alpha, bravo 1"): the grammar
    reads them so, and the language's own short words ("a", "y", "e") are never taken for one."""
    return LETTER.sub(lambda m: ICAO_LETTERS[m.group(1)] + (" " + m.group(2) if m.group(2) else ""), " " + text)[1:]


def _feet_thousands(text: str, feet: str) -> str:
    """ "3 000 pieds" (a space between the thousands) is 3000 feet."""
    return re.sub(rf"(?<!\d)(\d{{1,2}}) (\d{{3}})(?= {re.escape(feet)}\b)", r"\1\2", text) if feet else text


# --- numbers --------------------------------------------------------------------------------------------------------


def figures(text: str, tg: Tongue) -> str:
    """Number words as figures: "niveau un deux zero" -> "niveau 120"."""
    pieces = [(m.group(0), _morphemes(m.group(2), tg) if m.group(2) else None) for m in PIECE.finditer(text)]
    out: list[str] = []
    i = 0
    while i < len(pieces):
        word, parts = pieces[i]
        if parts is None or _lone_article(pieces, i, tg):
            out.append(word)
            i += 1
            continue
        values: list[int | str] = []
        j = i
        while j < len(pieces):
            word_j, parts_j = pieces[j]
            if parts_j is not None and not _lone_article(pieces, j, tg):
                values += parts_j
                j += 1
            elif word_j.isspace() and j + 1 < len(pieces) and pieces[j + 1][1] is not None \
                    and not _lone_article(pieces, j + 1, tg):
                j += 1
            elif word_j.isspace() and j + 2 < len(pieces) and plain(pieces[j + 1][0]) in tg.connectors \
                    and pieces[j + 2][0].isspace() and j + 3 < len(pieces) and pieces[j + 3][1] is not None:
                values.append(JOIN)  # "vingt et un", "treinta y cinco"
                j += 2
            else:
                break
        out.append(joined(values, tg))
        i = j
    return "".join(out)


def _lone_article(pieces: list[tuple[str, list | None]], i: int, tg: Tongue) -> bool:
    """ "un", "ein", "een" before a word that isn't a number or a runway's side: the article ("un Boeing", "une
    approche"), not one. Before a figure, a number word, a side, a comma or the end it's one ("piste un gauche",
    "décimal un", "BAYST un,")."""
    if plain(pieces[i][0]) not in tg.articles:
        return False
    k = i + 1
    while k < len(pieces) and pieces[k][0].isspace():
        k += 1
    if k >= len(pieces) or not pieces[k][0][:1].isalpha():
        return False  # the end, a figure or a comma: a number
    if plain(pieces[k][0]) in {plain(w) for w in tg.sides.values()}:
        return False
    k = i - 1
    while k >= 0 and pieces[k][0].isspace():
        k -= 1
    if k >= 0 and (plain(pieces[k][0]) in tg.takes_numbers or plain(pieces[k][0]) in ICAO_WORDS):
        return False  # "piste un via ...", "mike un"

    def numeric(k: int) -> bool:
        while 0 <= k < len(pieces) and pieces[k][0].isspace():
            k += 1 if k > i else -1
        return 0 <= k < len(pieces) and (pieces[k][1] is not None or pieces[k][0][:1].isdigit()
                                         or plain(pieces[k][0]) in tg.connectors)

    return not (numeric(i - 1) or numeric(i + 1))


def _morphemes(word: str, tg: Tongue) -> list[int | str] | None:
    """A word made of number words only ("vingt", "funfundzwanzig", "tremilacinquecento"): their values; else None."""
    return _split(plain(word), tuple(sorted({*tg.numbers, *tg.connectors}, key=len, reverse=True)), tg)


@lru_cache(maxsize=4096)
def _split_cached(word: str, keys: tuple[str, ...], code: str) -> tuple | None:
    tg = tongue(code)
    if not word:
        return ()
    for key in keys:
        if word.startswith(key):
            rest = _split_cached(word[len(key):], keys, code)
            if rest is not None:
                if key in tg.connectors:
                    if not rest or word == key:
                        continue  # a connector alone isn't a number ("et"), nor at the end of one
                    return (JOIN, *rest)
                return (tg.numbers[key], *rest)
    return None


def _split(word: str, keys: tuple[str, ...], tg: Tongue) -> list[int | str] | None:
    found = _split_cached(word, keys, tg.code)
    if not found or found[0] == JOIN:
        return None
    return list(found)


def joined(values: list[int | str], tg: Tongue) -> str:
    """The figures said: one by one ("2 7 0" -> "270"), whole ("3 1000 5 100" -> "3500"), or in groups ("12 30 4" ->
    "1234")."""
    chunks: list[str] = []
    big, small = 0, None
    join = False

    def close() -> None:
        nonlocal big, small
        if small is not None or big:
            chunks.append(str(big + (small or 0)))
        big, small = 0, None

    for v in values:
        if v == JOIN:
            join = True
            continue
        assert isinstance(v, int)
        if v == 0 and not join:
            close()
            chunks.append("0")
        elif v == 1000:
            big += (small or 1) * 1000
            small = None
        elif v == 100:
            if small is None:
                small = 100
            elif small < 100:
                small *= 100  # "trois cents", "dix-neuf cents"
            else:
                close()
                small = 100
        elif v == 10000:  # Chinese 万
            big = (big + (small or 1)) * 10000
            small = None
        elif small is None:
            small = v
        elif join or (small % 100 == 0 and small > 0 and v < 100) or (small % 10 == 0 and small % 100 and v < 10):
            small += v
        elif tg.code == "fr" and small % 100 in (60, 80) and 10 <= v < 20:
            small += v  # soixante-dix, quatre-vingt-dix
        elif tg.code == "fr" and v == 20 and small % 100 == 4:
            small += 76  # quatre-vingt(s)
        elif tg.code in ("de", "nl") and small % 10 and small < 10 and v % 10 == 0 and 20 <= v < 100:
            small += v  # "fünf zwanzig" written apart: 25
        else:
            close()
            small = v
        join = False
    close()
    return "".join(chunks)


# --- the words ------------------------------------------------------------------------------------------------------


@lru_cache(maxsize=16)
def _lexicon(code: str) -> tuple[tuple[re.Pattern, str], ...]:
    tg = tongue(code)
    assert tg is not None
    spaced = tg.code != "zh"
    end = r"(?![^\W_])" if spaced else ""
    return tuple((re.compile(f"(?:{plain(pattern)}){end}" if spaced else pattern), english) for pattern, english in tg.lexicon)


def _words(rules: tuple[tuple[re.Pattern, str], ...], text: str, *, spaced: bool) -> str:
    """Each of the language's phrases in English, read left to right: the first rule that fits at a word's start
    is taken, and what it gives isn't read again."""
    out: list[str] = []
    i = 0
    while i < len(text):
        if spaced and i > 0 and (text[i - 1].isalnum() or text[i - 1] == "_"):
            out.append(text[i])
            i += 1
            continue
        for pattern, english in rules:
            m = pattern.match(text, i)
            if m is not None and m.end() > i:
                out.append(" " + m.expand(english) + " " if english else " ")
                i = m.end()
                break
        else:
            out.append(text[i])
            i += 1
    return re.sub(r"\s+", " ", re.sub(r"\s+([,.;:!?])", r"\1", "".join(out))).strip(" ,")
