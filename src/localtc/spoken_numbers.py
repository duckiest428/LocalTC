"""Numbers said as words, in every language the crew talk in: "zehntausend" is 10000, "deux cent cinquante" 250, "двести
пятьдесят" 250. Digits said one by one are left as they are ("zwei sieben null"), and so is "und" between two things ("Klappen
zwei und Fahrwerk raus"). Works on text already made ``plain``: lower case, accents off.
"""

import re
import unicodedata
from dataclasses import dataclass


def plain(text: str) -> str:
    """Lowercase, accents off (not in Russian or Chinese, where the marks are letters), apostrophes and dashes as spaces."""
    text = text.lower().replace("ё", "е")
    if not re.search(r"[Ѐ-ӿ一-鿿]", text):
        text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
        text = text.replace("ł", "l").replace("ß", "ss").replace("œ", "oe")
    text = re.sub(r"[’'`´\-]", " ", text)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.,;]", " ", text)).strip()


@dataclass(frozen=True)
class Words:
    values: dict[str, int]
    hundred: tuple[str, ...] = ()  # multiplies what's before it ("zwei" "hundert")
    thousand: tuple[str, ...] = ()
    joiner: str = ""  # "und", said inside a number
    glued: bool = False  # written as one word ("zweihundertfunfzig")
    articles: tuple[str, ...] = ()  # "a", "an": on their own, a word and not the number 1
    not_numbers: tuple[str, ...] = ()  # other words of the language spelled the same ("sei": you are)


def _table(text: str) -> dict[str, int]:
    return {word: int(value) for value, words in (part.split(":") for part in text.split()) for word in words.split("/")}


_ONE = {"de": ("ein", "eine", "einen", "einem", "einer"), "fr": ("un", "une"), "es": ("un", "una", "uno"), "it": ("un", "una", "uno"),
        "pt": ("um", "uma"), "nl": ("een",), "pl": (), "sv": ("en", "ett"), "ru": ("один", "одна", "одно")}

TABLES: dict[str, Words] = {
    "de": Words(_table("0:null 1:ein/eins/eine 2:zwei/zwo 3:drei 4:vier 5:funf 6:sechs 7:sieben 8:acht 9:neun 10:zehn 11:elf 12:zwolf "
                       "13:dreizehn 14:vierzehn 15:funfzehn 16:sechzehn 17:siebzehn 18:achtzehn 19:neunzehn 20:zwanzig "
                       "30:dreissig 40:vierzig 50:funfzig 60:sechzig 70:siebzig 80:achtzig 90:neunzig"),
                ("hundert",), ("tausend",), "und", True, _ONE["de"]),
    "fr": Words(_table("0:zero 1:un/une 2:deux 3:trois 4:quatre 5:cinq 6:six 7:sept 8:huit 9:neuf 10:dix 11:onze 12:douze 13:treize "
                       "14:quatorze 15:quinze 16:seize 17:dixsept 18:dixhuit 19:dixneuf 20:vingt/vingts 30:trente 40:quarante 50:cinquante "
                       "60:soixante 80:quatrevingt"),
                ("cent", "cents"), ("mille",), "et", False, _ONE["fr"]),
    "es": Words(_table("0:cero 1:un/uno/una 2:dos 3:tres 4:cuatro 5:cinco 6:seis 7:siete 8:ocho 9:nueve 10:diez 11:once 12:doce 13:trece "
                       "14:catorce 15:quince 16:dieciseis 17:diecisiete 18:dieciocho 19:diecinueve 20:veinte 21:veintiuno "
                       "22:veintidos 23:veintitres 24:veinticuatro 25:veinticinco 26:veintiseis 27:veintisiete 28:veintiocho "
                       "29:veintinueve 30:treinta 40:cuarenta 50:cincuenta 60:sesenta 70:setenta 80:ochenta 90:noventa "
                       "100:cien/ciento 200:doscientos/doscientas 300:trescientos/trescientas 400:cuatrocientos/cuatrocientas "
                       "500:quinientos/quinientas 600:seiscientos/seiscientas 700:setecientos/setecientas "
                       "800:ochocientos/ochocientas 900:novecientos/novecientas"),
                (), ("mil",), "y", False, _ONE["es"]),
    "it": Words(_table("0:zero 1:un/uno/una 2:due 3:tre 4:quattro 5:cinque 6:sei 7:sette 8:otto 9:nove 10:dieci 11:undici 12:dodici "
                       "13:tredici 14:quattordici 15:quindici 16:sedici 17:diciassette 18:diciotto 19:diciannove 20:venti/vent "
                       "30:trenta/trent 40:quaranta/quarant 50:cinquanta/cinquant 60:sessanta/sessant 70:settanta/settant "
                       "80:ottanta/ottant 90:novanta/novant"),
                ("cento", "cent"), ("mila", "mille"), "e", True, _ONE["it"], ("sei",)),
    "pt": Words(_table("0:zero 1:um/uma 2:dois/duas 3:tres 4:quatro 5:cinco 6:seis 7:sete 8:oito 9:nove 10:dez 11:onze 12:doze 13:treze "
                       "14:catorze/quatorze 15:quinze 16:dezasseis/dezesseis 17:dezassete/dezessete 18:dezoito 19:dezanove/dezenove "
                       "20:vinte 30:trinta 40:quarenta 50:cinquenta 60:sessenta 70:setenta 80:oitenta 90:noventa 100:cem/cento "
                       "200:duzentos/duzentas 300:trezentos/trezentas 400:quatrocentos/quatrocentas 500:quinhentos/quinhentas "
                       "600:seiscentos/seiscentas 700:setecentos/setecentas 800:oitocentos/oitocentas 900:novecentos/novecentas"),
                (), ("mil",), "e", False, _ONE["pt"]),
    "nl": Words(_table("0:nul 1:een/eén 2:twee 3:drie 4:vier 5:vijf 6:zes 7:zeven 8:acht 9:negen 10:tien 11:elf 12:twaalf 13:dertien "
                       "14:veertien 15:vijftien 16:zestien 17:zeventien 18:achttien 19:negentien 20:twintig 30:dertig 40:veertig "
                       "50:vijftig 60:zestig 70:zeventig 80:tachtig 90:negentig"),
                ("honderd",), ("duizend",), "en", True, _ONE["nl"]),
    "pl": Words(_table("0:zero 1:jeden/jedna/jedno 2:dwa/dwie 3:trzy 4:cztery 5:piec 6:szesc 7:siedem 8:osiem 9:dziewiec 10:dziesiec "
                       "11:jedenascie 12:dwanascie 13:trzynascie 14:czternascie 15:pietnascie 16:szesnascie 17:siedemnascie "
                       "18:osiemnascie 19:dziewietnascie 20:dwadziescia 30:trzydziesci 40:czterdziesci 50:piecdziesiat "
                       "60:szescdziesiat 70:siedemdziesiat 80:osiemdziesiat 90:dziewiecdziesiat 100:sto 200:dwiescie 300:trzysta "
                       "400:czterysta 500:piecset 600:szescset 700:siedemset 800:osiemset 900:dziewiecset"),
                (), ("tysiac", "tysiace", "tysiecy"), "", False, ("jeden", "jedna", "jedno")),
    "sv": Words(_table("0:noll 1:en/ett/et 2:tva 3:tre 4:fyra 5:fem 6:sex 7:sju 8:atta 9:nio 10:tio 11:elva 12:tolv 13:tretton 14:fjorton "
                       "15:femton 16:sexton 17:sjutton 18:arton 19:nitton 20:tjugo 30:trettio 40:fyrtio 50:femtio 60:sextio "
                       "70:sjuttio 80:attio 90:nittio"),
                ("hundra",), ("tusen",), "", True, _ONE["sv"]),
    "ru": Words(_table("0:ноль/нуль 1:один/одна/одно 2:два/две 3:три 4:четыре 5:пять 6:шесть 7:семь 8:восемь 9:девять 10:десять 11:одиннадцать "
                       "12:двенадцать 13:тринадцать 14:четырнадцать 15:пятнадцать 16:шестнадцать 17:семнадцать 18:восемнадцать "
                       "19:девятнадцать 20:двадцать 30:тридцать 40:сорок 50:пятьдесят 60:шестьдесят 70:семьдесят 80:восемьдесят "
                       "90:девяносто 100:сто 200:двести 300:триста 400:четыреста 500:пятьсот 600:шестьсот 700:семьсот "
                       "800:восемьсот 900:девятьсот"),
                (), ("тысяча", "тысячи", "тысяч", "тысячу"), "", False, _ONE["ru"]),
}
_CACHE: dict[str, dict[str, tuple[str, ...]]] = {}
_FR_TENS = re.compile(r"\bquatre vingts?\b")
_FR_TEENS = re.compile(r"\bdix (sept|huit|neuf)\b")


def _pieces(word: str, table: Words) -> list[str] | None:
    """A word cut into the parts of a number ("zweihundertfunfzig": zwei, hundert, funfzig); None if it isn't one."""
    if not table.glued:
        return [word] if word in table.values or word in table.hundred or word in table.thousand else None
    known = sorted({*table.values, *table.hundred, *table.thousand, *((table.joiner,) if table.joiner else ())}, key=len, reverse=True)

    def cut(rest: str) -> list[str] | None:
        if not rest:
            return []
        for piece in known:
            if rest.startswith(piece) and (after := cut(rest[len(piece):])) is not None:
                return [piece, *after]
        return None
    found = cut(word)
    return found if found and found != [table.joiner] else None


def _value(parts: list[str], table: Words) -> int:
    total = current = 0
    for part in parts:
        if part.isdigit():
            current += int(part)
        elif part in table.thousand:
            total, current = total + max(current, 1) * 1000, 0
        elif part in table.hundred:
            # (what's before "hundert" is what's since the last hundred or thousand: all of "current", or just its units)
            current = max(current, 1) * 100
        elif part != table.joiner:
            current += table.values[part]
    return total + current


def _whole(parts: list[str], table: Words) -> bool:
    """Said as one number, not digit by digit: it has a tens, a hundred or a thousand in it."""
    return any(p in table.thousand or p in table.hundred or table.values.get(p, 0) > 10 for p in parts)


def _runs(text: str, table: Words) -> list[tuple[int, int, list[str]]]:
    """The runs of number words in ``text``: (first word, past the last word, their parts)."""
    words = text.split(" ")
    runs, i = [], 0
    while i < len(words):
        parts: list[str] = []
        j = i
        while j < len(words):
            word = words[j].strip(".,;")
            found = [word] if word.isdigit() else _pieces(word, table)
            if found is not None:
                parts += found
                j += 1
                if words[j - 1] != words[j - 1].rstrip(".,;"):
                    break  # the end of a sentence or a clause
            elif word == table.joiner and parts and j + 1 < len(words) and _joins(parts[-1], words[j + 1], table):
                parts.append(word)
                j += 1
            else:
                break
        if parts:
            runs.append((i, j, parts))
            i = j
        else:
            i += 1
    return runs


def _joins(before: str, word: str, table: Words) -> bool:
    """Whether "and" here is inside a number: "treinta y cinco", "doze mil e quinhentos", "funf und dreissig". Not "flaps dos y
    tres": from units to units it's two things said."""
    word = word.strip(".,;")
    if (found := _pieces(word, table)) is None:
        return False
    big = before in table.thousand or before in table.hundred or table.values.get(before, 0) >= 20
    return big or any(table.values.get(p, 0) >= 20 for p in found)


def _prepared(text: str, code: str) -> str:
    if code == "fr":  # the two words that make one number once the dash has gone
        text = _FR_TENS.sub("quatrevingt", text)
        text = _FR_TEENS.sub(lambda m: "dix" + m[1], text)
    return text


def whole_numbers(text: str, code: str) -> str:
    """``text`` (plain) with the numbers said whole as digits. "dos siete cero" stays: it's digit by digit."""
    table = TABLES.get(code)
    if table is None:
        return text
    words = _prepared(text, code).split(" ")
    for first, last, parts in reversed(_runs(" ".join(words), table)):
        if _whole(parts, table) or (any(p.isdigit() for p in parts) and len(parts) > 1 and _has_scale(parts, table)):
            end = words[last - 1][len(words[last - 1].rstrip(".,;")):]
            words[first:last] = [str(_value(parts, table)) + end]
    return " ".join(words)


def _has_scale(parts: list[str], table: Words) -> bool:
    return any(p in table.thousand or p in table.hundred for p in parts)


# "двадцать девять целых девяносто два": 29.92 said as words.
_DECIMAL = re.compile(r"(\d+) (?:целых|целая|целые|запятая|przecinek|komma|virgule|coma|virgola|virgula) (\d+)")
# "час двадцать" is 1 hour 20, with no 1 said: a word that stands for the number 1 as a lone "a" does.
IMPLICIT_ONE = {"ru": ("час", "минуту", "минута"), "pl": ("godzine", "godzina", "minute", "minuta")}


def in_digits(text: str, code: str) -> str:
    """A line with every number it spells out in digits, for checking them: a model asked for digits often writes "cinco mil
    pies" all the same, and a correct translation was thrown away and the line said in English. A lone "una" ("una hora") or "ein"
    isn't a number, nor is a word that only looks like one ("sei": you are)."""
    table = TABLES.get(code)
    if table is None:
        return text
    words = whole_numbers(plain(text), code).split(" ")
    for first, last, parts in reversed(_runs(" ".join(words), table)):
        if all(p.isdigit() for p in parts):
            continue
        if len(parts) == 1 and (parts[0] in table.articles or parts[0] in table.not_numbers):
            continue
        end = words[last - 1][len(words[last - 1].rstrip(".,;")):]
        words[first:last] = ["".join(str(table.values.get(p, p)) for p in parts if p != table.joiner) + end]
    return _DECIMAL.sub(r"\1.\2", " ".join(words))


def lone_articles(text: str, code: str) -> int:
    """How many words of ``text`` may stand for the number 1 without being a digit ("una hora" for "1 hour")."""
    table = TABLES.get(code)
    if table is None:
        return 0
    stands_for_one = (*table.articles, *IMPLICIT_ONE.get(code, ()))
    return sum(word.strip(".,;") in stands_for_one for word in plain(text).split(" "))


# Mandarin: read by place ("一万二千", "二百五十") or digit by digit, with the radio's own digits ("洞" zero, "拐" seven, "幺" one).
ZH_DIGITS = {c: i for i, c in enumerate("零一二三四五六七八九")} | {"〇": 0, "两": 2, "洞": 0, "幺": 1, "拐": 7}
ZH_PLACES = {"十": 10, "百": 100, "千": 1000, "万": 10000}
ZH_RUN = re.compile(r"[零〇一二两三四五六七八九十百千万洞幺拐]+")


def zh_value(run: str) -> str:
    """One run of Chinese numerals as digits."""
    if not any(c in ZH_PLACES for c in run):
        return "".join(str(ZH_DIGITS[c]) for c in run)
    total = section = digit = 0
    for c in run:
        if c in ZH_DIGITS:
            digit = ZH_DIGITS[c]
        elif c == "万":
            total, section, digit = (total + section + digit) * 10000, 0, 0
        else:
            section += (digit or 1) * ZH_PLACES[c]
            digit = 0
    return str(total + section + digit)


def zh_whole_numbers(text: str) -> str:
    """Numbers said by place as digits ("一万": 10000); those said digit by digit stay, one at a time."""
    return ZH_RUN.sub(lambda m: zh_value(m[0]) if any(c in ZH_PLACES for c in m[0]) and m[0] != "十" else m[0], text)
