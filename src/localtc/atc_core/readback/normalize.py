"""Turn a transcript (Whisper-style text or typed input) into tokens with numbers and letters resolved.

    normalize("Climb and maintain 5,000, departure one twenty-four point six seven five, squawk 4521")
    -> climb and maintain <5000> departure <124.675> squawk <4521>

Handles written digits, number words, ICAO/FAA digit words (niner, tree, fife),
group forms ("one twenty point two"), thousands/hundreds, phonetic letters and
filler words.
"""

import re
from dataclasses import dataclass

DIGIT_WORDS = {
    "zero": 0, "oh": 0, "one": 1, "won": 1, "two": 2, "three": 3, "tree": 3, "four": 4, "fower": 4, "five": 5,
    "fife": 5, "six": 6, "seven": 7, "eight": 8, "ait": 8, "nine": 9, "niner": 9,
}
TEEN_WORDS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS_WORDS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
MULTIPLIERS = {"hundred": 100, "thousand": 1000}
POINT_WORDS = {"point", "decimal", "."}
PHONETIC = {
    "alpha": "a", "alfa": "a", "bravo": "b", "charlie": "c", "delta": "d", "echo": "e", "foxtrot": "f", "golf": "g",
    "hotel": "h", "india": "i", "juliet": "j", "juliett": "j", "kilo": "k", "lima": "l", "mike": "m",
    "november": "n", "oscar": "o", "papa": "p", "quebec": "q", "romeo": "r", "sierra": "s", "tango": "t",
    "uniform": "u", "victor": "v", "whiskey": "w", "whisky": "w", "xray": "x", "x-ray": "x", "yankee": "y", "zulu": "z",
}
FILLERS = {"uh", "um", "er", "erm", "ah", "the", "uhh", "umm"}
TOKEN_RE = re.compile(r"\d+(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|x-ray|[a-z]+|[.,;:!?]")
BREAKS = {",", ";", ":", "!", "?"}


@dataclass(frozen=True)
class Token:
    kind: str  # "word", "number", "letter"
    text: str

    def __str__(self) -> str:
        return f"<{self.text}>" if self.kind == "number" else (self.text.upper() if self.kind == "letter" else self.text)


def normalize(text: str) -> list[Token]:
    lowered = text.lower().replace("-", " ").replace("x ray", "x-ray")
    matches = list(TOKEN_RE.finditer(lowered))
    raw = [m.group() for m in matches]
    # Digits glued to letters ("2LT", "EXP69") belong to a word; they're never one of a list of digits.
    glued = {i for i in range(len(matches) - 1) if matches[i].end() == matches[i + 1].start() and raw[i][0].isdigit()
             and raw[i + 1][0].isalpha()}
    tokens: list[Token] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            tokens.extend(Token("number", n) for n in _numbers(run))
            run.clear()

    for i, word in enumerate(raw):
        numeric = (
            word[0].isdigit()
            or word in DIGIT_WORDS or word in TEEN_WORDS or word in TENS_WORDS
            or (word in MULTIPLIERS and run)
            or (word in POINT_WORDS and run and i + 1 < len(raw) and _is_digitish(raw[i + 1]))
        )
        if word == "oh" and not run:
            numeric = False  # "oh" only counts as zero inside a number
        if word == "," and i + 1 not in glued and _digit_list(run, raw, i):
            continue  # Whisper writes digits said one by one as "3, 2, 0, 0": that's 3200
        if word == "and" and run and run[-1] == "hundred" and i + 1 < len(raw) and (
            raw[i + 1] in TENS_WORDS or raw[i + 1] in TEEN_WORDS or raw[i + 1] in DIGIT_WORDS
        ):
            continue  # "one hundred and fifty": the "and" is part of the number
        if numeric:
            run.append(word)
            continue
        flush()
        if word in FILLERS or word == "." or word in BREAKS:
            continue
        if word in PHONETIC:
            tokens.append(Token("letter", PHONETIC[word]))
        else:
            tokens.append(Token("word", word))
    flush()
    return tokens


def _single_digit(word: str) -> bool:
    return (len(word) == 1 and word.isdigit()) or word in DIGIT_WORDS


def _digit_list(run: list[str], raw: list[str], comma: int) -> bool:
    """True if the comma at ``raw[comma]`` sits inside digits said one at a time and written with
    commas between them: "3, 2, 0, 0" or "0, 8". Not "8, 3 miles" (two numbers), and not
    "one two three four, two lima tango" (a group, then a new number)."""
    if not run or not _single_digit(raw[comma - 1]) or comma + 1 >= len(raw) or not _single_digit(raw[comma + 1]):
        return False
    count, k = 0, comma - 1  # digits before, each separated by a comma
    while k >= 0 and _single_digit(raw[k]):
        count += 1
        if k >= 1 and _single_digit(raw[k - 1]):
            return False  # "... three four, two": the comma ends a spoken group
        if k >= 2 and raw[k - 1] == ",":
            k -= 2
        else:
            break
    j = comma + 1  # and after
    while j < len(raw) and _single_digit(raw[j]):
        count += 1
        if j + 2 < len(raw) and raw[j + 1] == ",":
            j += 2
        else:
            break
    return count >= 3 or raw[comma - 1] in ("0", "zero", "oh")


def _is_digitish(word: str) -> bool:
    return word[0].isdigit() or word in DIGIT_WORDS


def _numbers(run: list[str]) -> list[str]:
    """Split a run of numeric words into one or more numbers and resolve each."""
    numbers: list[list[str]] = [[]]
    for i, word in enumerate(run):
        current = numbers[-1]
        # After "thousand"/"hundred", more digits start a new number unless they lead into "hundred"
        # ("five thousand five hundred" vs "maintain five thousand, one two four point six").
        if current and any(w in MULTIPLIERS for w in current) and word not in MULTIPLIERS and word not in POINT_WORDS:
            leads_to_hundred = i + 1 < len(run) and run[i + 1] == "hundred" and "hundred" not in current
            # "one hundred fifty" is 150; after "thousand", words start a new number ("5,000, one two four ...").
            after_hundred = (current[-1] == "hundred" and (word in TENS_WORDS or word in TEEN_WORDS)) or (
                len(current) >= 2 and current[-2] == "hundred" and current[-1] in TENS_WORDS
                and DIGIT_WORDS.get(word, 0) > 0  # "one hundred fifty five"
            )
            if not leads_to_hundred and not after_hundred:
                numbers.append([])
                current = numbers[-1]
        # A written multi-digit number after another written number starts a new number
        # ("runway 34 120.2"), but short written groups merge ("squawk 45 21").
        if word[0].isdigit() and current and current[-1][0].isdigit():
            prev = current[-1].replace(",", "")
            one_by_one = len(word) == 1 and all(len(w) == 1 and w.isdigit() for w in current)  # "3 2 0 0"
            if not one_by_one and ("." in prev or "." in word or len(prev) > 2 or len(word.replace(",", "")) > 2):
                numbers.append([])
        # Digits spoken after a written number are a new number too
        # ("expect 7,000 one zero minutes after" is 7000 then 10, not 700010).
        elif current and current[-1][0].isdigit() and word not in MULTIPLIERS and word not in POINT_WORDS:
            if len(current[-1].replace(",", "").split(".")[0]) > 2:
                numbers.append([])
        numbers[-1].append(word)
    return [value for part in numbers if part and (value := _resolve(part))]


def _resolve(words: list[str]) -> str:
    if any(w in POINT_WORDS for w in words):
        index = next(i for i, w in enumerate(words) if w in POINT_WORDS)
        whole, fraction = _resolve(words[:index]), "".join(_digit_string(w) for w in words[index + 1 :])
        return f"{whole}.{fraction}" if fraction else whole
    total = 0
    hundreds = 0
    digits = ""
    used_multiplier = False
    i = 0
    while i < len(words):
        word = words[i].replace(",", "")
        if word in MULTIPLIERS:
            base = int(digits or "1")
            digits = ""
            used_multiplier = True
            if word == "hundred":
                hundreds += base * 100
            else:
                total = (total + hundreds + base) * 1000
                hundreds = 0
        elif word in TENS_WORDS:
            value = TENS_WORDS[word]
            if i + 1 < len(words) and words[i + 1] in DIGIT_WORDS and DIGIT_WORDS[words[i + 1]] > 0:
                value += DIGIT_WORDS[words[i + 1]]
                i += 1
            digits += str(value)
        elif word in TEEN_WORDS:
            digits += str(TEEN_WORDS[word])
        elif word in DIGIT_WORDS:
            digits += str(DIGIT_WORDS[word])
        elif word[0].isdigit():
            if "." in word:
                return word
            digits += word
        i += 1
    if used_multiplier:
        return str(total + hundreds + int(digits or "0"))
    return digits


def _digit_string(word: str) -> str:
    if word[0].isdigit():
        return word
    return str(DIGIT_WORDS.get(word, TEEN_WORDS.get(word, TENS_WORDS.get(word, ""))))


def render(tokens: list[Token]) -> str:
    """Debug view: ``climb and maintain <5000> squawk <4521>``."""
    return " ".join(str(t) for t in tokens)
