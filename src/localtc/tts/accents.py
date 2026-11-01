"""Accents: the English of a controller (or pilot) from anywhere in the world, made from Kokoro's English.

A line is turned into English phonemes by espeak (American or British, the English the region learns), and those are
moved onto the sounds of the speaker's own language, the way a speaker of it carries them into English: a French
controller drops the h, says "s" for "th" and a French r; a German says "v" for "w" and devoices the ends of words; a
Japanese says one r for r and l and ends a word on a vowel. The rules are the well-known traits of each first
language's English (L1 transfer), kept light: a controller is fluent, and every number has to come through the radio.
Each accent is then spoken by voices native to the language where Kokoro has them (French, Spanish, Italian,
Portuguese, Hindi, Japanese, Mandarin) and by the nearest ones elsewhere (``library.py``).

Measured with ``localtc tts accents``: Whisper base.en's word error through the radio effect (Kokoro's own American
English: 0.05), and Whisper small's guess of the language (how foreign it sounds).

Every locale of ``atc_core/region.py`` has an accent (``accent_for``): the English-speaking world its own English, the
rest grouped by first language into families (Arabic from Morocco to Oman, the Slavic languages, mainland Southeast
Asia...).
"""

import re
from dataclasses import dataclass

VOWEL = "aeiouæɑɒɔəɚɛɜɪʊʌøœɐɤɯyᵻ"
END = r"(?=[\s,.!?;:—]|$)"  # the end of a word
START = r"(?:^|(?<=[\s,.!?;:—]))"  # the start of one
STRESS = "[ˈˌ]?"
FULL = [("ɪ", "i"), ("ʊ", "u"), ("ᵻ", "i")]  # no lax vowels: the five or seven vowels of most languages
DEVOICE = [(r"b" + END, "p"), (r"d" + END, "t"), (r"ɡ" + END, "k"), (r"z" + END, "s"), (r"v" + END, "f"),
           (r"dʒ" + END, "tʃ")]
NO_TH = [("θ", "s"), ("ð", "z")]
TH_TD = [("θ", "t"), ("ð", "d")]
S_CLUSTER = START + r"(?=" + STRESS + r"s[ptkmnlw])"  # "speed": a vowel before it in Spanish, Persian, Arabic, Turkish


@dataclass(frozen=True)
class Accent:
    id: str
    name: str  # for the voice library's list ("French")
    english: str  # the English it's built on: "us" or "gb"
    rules: tuple[tuple[str, str], ...] = ()
    language: str = ""  # its first language (ISO 639-1), for the voices of it


def _a(id: str, name: str, english: str, rules: list[tuple[str, str]], language: str = "") -> Accent:
    return Accent(id, name, english, tuple(rules), language)


ACCENTS: dict[str, Accent] = {a.id: a for a in (
    # The English-speaking world.
    _a("us", "American", "us", []),
    _a("ca", "Canadian", "us", [(r"aʊ(?=[ptkfsθʃ])", "ʌʊ"), (r"aɪ(?=[ptkfsθʃ])", "ʌɪ")]),
    _a("gb", "British", "gb", []),
    _a("ie", "Irish", "us", [*TH_TD, ("eɪ", "eː"), ("oʊ", "oː"), ("aɪ", "ɒɪ"), ("ɾ", "t")]),
    _a("au", "Australian", "gb", [("eɪ", "æɪ"), ("aɪ", "ɒɪ"), ("iː", "əi"), ("əʊ", "ɐʊ"), ("ɑː", "aː"),
                                  ("aʊ", "æʊ"), (r"(?<=[" + VOWEL + r"ː])t(?=[ˈˌ]?[" + VOWEL + "])", "ɾ")]),
    _a("za", "South African", "gb", [("ɹ", "r"), ("æ", "ɛ"), ("ɪ", "ə"), ("eɪ", "eː"), ("əʊ", "œː")]),
    _a("in", "Indian", "us", [("θ", "tʰ"), ("ð", "d"), ("w", "ʋ"), ("v", "ʋ"), ("t", "ʈ"), ("d", "ɖ"),
                              ("ɹ", "ɾ"), ("ɚ", "əɾ"), ("ɜː", "əɾ"), ("æ", "ɛ"), ("oʊ", "oː"), ("eɪ", "eː")], "hi"),
    _a("sg", "Singaporean and Malaysian", "gb", [*TH_TD, ("æ", "ɛ"), *FULL[:2], ("ː", "")], "ms"),
    _a("ph", "Filipino", "us", [("v", "b"), *TH_TD, ("z", "s"), ("ɹ", "ɾ"), ("æ", "a"), *FULL[:2]], "tl"),
    _a("eaf", "East African", "gb", [("ɹ", "r"), ("æ", "a"), ("ʌ", "a"), ("ɜː", "ɛ"), ("ə" + END, "a"), *FULL,
                                     ("ː", "")], "sw"),
    _a("waf", "West African", "gb", [*TH_TD, ("ɹ", "r"), ("ɜː", "ɛ"), ("ʌ", "ɔ"), ("æ", "a"), ("ə" + END, "a"),
                                     *FULL, ("ː", "")], "yo"),
    # Europe.
    _a("fr", "French", "gb", [("h", ""), *NO_TH, ("ɹ", "ʁ"), *FULL[:2], ("ˌ", "")], "fr"),
    _a("de", "German", "gb", [("w", "v"), *NO_TH, ("dʒ", "tʃ"), ("ɹ", "ʁ"), *DEVOICE,
                              (START + r"s(?=" + STRESS + "[" + VOWEL + "])", "z"), ("æ", "ɛ")], "de"),
    _a("nl", "Dutch", "gb", [*TH_TD, ("ɹ", "r"), ("w", "ʋ"), ("ɡ", "ɣ"), *DEVOICE, ("æ", "ɛ")], "nl"),
    _a("nordic", "Scandinavian", "gb", [("z", "s"), ("dʒ", "j"), ("w", "v"), ("ð", "d"), ("ɹ", "r"),
                                        ("ʃ", "ʂ")], "sv"),
    _a("fi", "Finnish", "gb", [("b", "p"), ("ɡ", "k"), *TH_TD, ("z", "s"), ("ʃ", "s"), ("ɹ", "r"), ("w", "v"),
                               ("dʒ", "j"), *FULL], "fi"),
    _a("es", "Spanish", "gb", [*TH_TD, ("v", "b"), ("z", "s"), ("ɹ", "ɾ"), *FULL, ("æ", "a"), ("ʌ", "a"),
                               ("h", "x"), (S_CLUSTER, "e")], "es"),
    _a("latam", "Latin American", "us", [*TH_TD, ("v", "b"), ("z", "s"), ("ɹ", "ɾ"), ("ɚ", "eɾ"), *FULL,
                                         ("æ", "a"), ("ʌ", "a"), (S_CLUSTER, "e")], "es"),
    _a("pt", "Portuguese", "gb", [*TH_TD, ("ɹ", "ʁ"), *FULL[:2], ("æ", "ɛ")], "pt"),
    _a("br", "Brazilian", "us", [*TH_TD, (START + r"([ˈˌ]?)ɹ", r"\1h"), ("ɹ", "ɾ"), *FULL[:2], ("æ", "ɛ")], "pt"),
    _a("it", "Italian", "gb", [("h", ""), *TH_TD, ("ɹ", "r"), *FULL[:2], ("æ", "ɛ"), (r"([dɡ])" + END, r"\1ə")],
       "it"),
    _a("ru", "Russian", "gb", [("w", "v"), *NO_TH, ("h", "x"), ("ɹ", "r"), ("æ", "ɛ"), ("ʌ", "a"), *FULL,
                               (r"ŋ" + END, "nk"), *DEVOICE, (r"([tdnl])(?=i)", r"\1ʲ")], "ru"),
    _a("cee", "Central and Eastern European", "gb", [("w", "v"), ("θ", "t"), ("ð", "d"), ("h", "x"), ("ɹ", "r"),
                                                     ("æ", "ɛ"), *FULL, (r"ŋ" + END, "nk"), *DEVOICE], "pl"),
    _a("gr", "Greek", "gb", [("ɹ", "ɾ"), ("h", "x"), *FULL, ("æ", "e"), ("ʌ", "a"), ("ʃ", "s"), ("ʒ", "z"),
                             ("dʒ", "dz"), ("tʃ", "ts"), ("ː", "")], "el"),
    _a("tr", "Turkish", "gb", [("w", "v"), *TH_TD, ("æ", "e"), ("ɹ", "ɾ"), *FULL, (S_CLUSTER, "i"),
                               (r"ŋ" + END, "nɡ")], "tr"),
    # The Middle East and North Africa.
    _a("me", "Arabic", "gb", [("p", "b"), ("v", "f"), ("ɹ", "r"), ("æ", "a"), ("ʌ", "a"), *FULL, ("eɪ", "eː"),
                              ("əʊ", "oː"), (r"ŋ" + END, "nɡ"), (S_CLUSTER, "i")], "ar"),
    _a("fa", "Persian", "gb", [("w", "v"), *NO_TH, ("ɹ", "ɾ"), ("æ", "a"), *FULL, (S_CLUSTER, "e")], "fa"),
    _a("il", "Israeli", "gb", [("ɹ", "ʁ"), *TH_TD, ("w", "v"), *FULL[:2], ("æ", "ɛ")], "he"),
    # Asia.
    # (Kokoro's Mandarin voices have a Chinese accent of their own: the rules only finish it.)
    _a("zh", "Chinese", "us", [*NO_TH[:1], ("ð", "d"), ("v", "w")], "zh"),
    _a("ja", "Japanese", "us", [("θ", "s"), ("ð", "z"), ("v", "b"), ("ɹ", "ɾ"), ("ɪ", "i"), ("æ", "a"),
                                (r"d" + END, "do")], "ja"),
    _a("ko", "Korean", "us", [("f", "p"), ("v", "b"), *NO_TH[:1], ("ð", "d"), ("ɹ", "ɾ"), *FULL[:2], ("æ", "ɛ"),
                              (r"(ʃ|tʃ|dʒ)" + END, r"\1ɯ")], "ko"),
    _a("sea", "Southeast Asian", "gb", [*TH_TD, ("v", "w"), ("z", "s"), *FULL[:2], ("ː", "")], "th"),
    _a("id", "Indonesian", "gb", [*TH_TD, ("z", "s"), ("ɹ", "r"), *FULL[:2], ("æ", "e"), ("ː", "")], "id"),
)}

# A locale's language (or an English locale) to its accent. Those not here: the region's English as before.
BY_LOCALE = {
    "en-us": "us", "en-ca": "ca", "en-gb": "gb", "en-ie": "ie", "en-au": "au", "en-nz": "au", "en-za": "za",
    "en-in": "in", "en-sg": "sg", "en-hk": "gb", "en-ph": "ph", "en-ke": "eaf", "en-tz": "eaf", "en-ng": "waf",
    "pt-br": "br", "fr-ca": "fr",
}
BY_LANGUAGE = {
    "fr": "fr", "de": "de", "nl": "nl", "da": "nordic", "sv": "nordic", "nb": "nordic", "no": "nordic",
    "is": "nordic", "fi": "fi", "it": "it", "pt": "pt", "ru": "ru", "uk": "ru", "be": "ru", "kk": "ru", "uz": "ru",
    "az": "ru", "ka": "ru", "hy": "ru", "pl": "cee", "cs": "cee", "sk": "cee", "sl": "cee", "hr": "cee",
    "sr": "cee", "bs": "cee", "mk": "cee", "bg": "cee", "lt": "cee", "lv": "cee", "et": "cee", "hu": "cee",
    "ro": "cee", "sq": "cee", "el": "gr", "tr": "tr", "ar": "me", "fa": "fa", "he": "il", "ur": "in", "bn": "in",
    "ne": "in", "si": "in", "hi": "in", "zh": "zh", "ja": "ja", "ko": "ko", "th": "sea", "vi": "sea", "km": "sea",
    "lo": "sea", "my": "sea", "ms": "sg", "id": "id", "tl": "ph", "am": "eaf", "sw": "eaf",
}


def accent_for(locale: str) -> Accent | None:
    """The accent of ``locale`` ("de-DE", "en-AU", "es-MX"): None where there's no region in particular."""
    low = (locale or "").lower()
    if not low:
        return None
    if low in BY_LOCALE:
        return ACCENTS[BY_LOCALE[low]]
    lang = low.split("-")[0]
    if lang == "es":
        return ACCENTS["es" if low in ("es", "es-es") else "latam"]
    if lang == "en":
        return ACCENTS["gb"]
    return ACCENTS.get(BY_LANGUAGE.get(lang, ""))


def accented(phonemes: str, accent: Accent | None) -> str:
    """English phonemes (espeak's, of the accent's English) as the accent says them."""
    if accent is None:
        return phonemes
    for pattern, replacement in accent.rules:
        phonemes = re.sub(pattern, replacement, phonemes)
    return phonemes
