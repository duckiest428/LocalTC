"""The window's words, for translating: every piece of English text and tooltip the app's page shows.

    python tools/i18n.py extract        # src/localtc/ui/static/i18n/strings.json: the English, from the page's files
    python tools/i18n.py check          # each language's file against it: what's missing, what's left over
    python tools/i18n.py missing de     # the English not yet in de.json, as JSON (to translate)

The page writes its words in English (index.html, app.js, efb.js ...); ``static/i18n.js`` shows them in the language
chosen from ``i18n/<code>.json`` (English -> translation). A text with values in it is kept as a pattern: "Step {0} of
{1}" (each ``${...}`` of a template a ``{n}``); a translation may move them or leave one out. Found here:

- the page's text and its tooltips, placeholders and labels (index.html);
- in the scripts, the text of the HTML their templates build (between the tags, and those attributes), and the plain
  strings that read as words to a person (a space in them, or a capital first: "Not flying", "Start"; never an id, a
  class, a selector or a path);
- the words the app's own Python sends the page (the model catalog, the components, the personalities).
"""

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "localtc" / "ui" / "static"
OUT = STATIC / "i18n"
SCRIPTS = ("app.js", "efb.js", "atcmap.js", "flightsmap.js", "replayplayer.js", "wrapped.js", "sharecard.js", "cardmodel.js")
ATTRS = ("title", "placeholder", "aria-label")
HOLE = "\x00"  # where a template's ${...} was


# --- the scripts ---------------------------------------------------------------------------------------------------

REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^") | {"return", "typeof", "case", "do", "else", "in", "of", "void"}


def js_literals(src: str) -> list[tuple[str, str]]:
    """("plain" | "template", text) for each string literal in ``src``; a template's ${...} as HOLE (and the strings
    inside it found too)."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(src)
    last = ""  # the last significant token, to tell a regular expression from a division

    def template(j: int) -> int:
        """``src[j]`` is just past the opening backtick: the template's text to its end; its index past it."""
        parts: list[str] = []
        while j < n:
            c = src[j]
            if c == "\\":
                parts.append(src[j:j + 2])
                j += 2
            elif c == "`":
                out.append(("template", "".join(parts)))
                return j + 1
            elif c == "$" and src[j + 1:j + 2] == "{":
                parts.append(HOLE)
                j = expression(j + 2)
            else:
                parts.append(c)
                j += 1
        return j

    def expression(j: int) -> int:
        """Inside ${...}: to its closing brace, the literals in it found."""
        depth = 1
        nonlocal last
        while j < n and depth:
            c = src[j]
            if c in "\"'":
                j = quoted(j)
            elif c == "`":
                j = template(j + 1)
            elif c == "{":
                depth += 1
                j += 1
            elif c == "}":
                depth -= 1
                j += 1
            else:
                j += 1
        return j

    def quoted(j: int) -> int:
        q = src[j]
        k = j + 1
        buf = []
        while k < n and src[k] != q:
            if src[k] == "\\":
                buf.append(_unescape(src[k:k + 2]))
                k += 2
                continue
            if src[k] == "\n":
                break
            buf.append(src[k])
            k += 1
        out.append(("plain", "".join(buf)))
        return k + 1

    while i < n:
        c = src[i]
        if src.startswith("//", i):
            i = src.find("\n", i)
            i = n if i < 0 else i
        elif src.startswith("/*", i):
            i = src.find("*/", i + 2)
            i = n if i < 0 else i + 2
        elif c in "\"'":
            i = quoted(i)
            last = "str"
        elif c == "`":
            i = template(i + 1)
            last = "str"
        elif c == "/" and (last in REGEX_BEFORE or last == ""):
            # A regular expression: to its closing slash (not one inside a [class]).
            k, klass = i + 1, False
            while k < n and src[k] != "\n":
                if src[k] == "\\":
                    k += 2
                    continue
                if src[k] == "[":
                    klass = True
                elif src[k] == "]":
                    klass = False
                elif src[k] == "/" and not klass:
                    break
                k += 1
            i = k + 1
            while i < n and src[i].isalpha():
                i += 1
            last = "re"
        elif c.isspace():
            i += 1
        elif c.isalnum() or c in "_$":
            k = i
            while k < n and (src[k].isalnum() or src[k] in "_$"):
                k += 1
            last = src[i:k]
            i = k
        else:
            last = c
            i += 1
    return out


def _unescape(pair: str) -> str:
    return {"\\n": "\n", "\\t": " ", "\\'": "'", '\\"': '"', "\\\\": "\\", "\\`": "`"}.get(pair, pair[1:])


ENTITY = re.compile(r"&(#\d+|#x[0-9a-f]+|[a-z]+);", re.I)


class Fragment(HTMLParser):
    """An HTML fragment's text and its people-facing attributes (``convert_charrefs``: entities as characters)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.texts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "code", "pre"):
            self.skip += 1
        if any(k == "translate" and v == "no" for k, v in attrs):
            return
        for k, v in attrs:
            if k in ATTRS and v:
                self.texts.append(v)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "code", "pre") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.texts.append(data)


def html_texts(fragment: str) -> list[str]:
    p = Fragment()
    p.feed(fragment)
    p.close()
    return p.texts


def holes(text: str) -> str:
    """HOLE as {0}, {1} ... in order."""
    count = iter(range(100))
    return re.sub(HOLE, lambda m: "{%d}" % next(count), text)


WORDS = re.compile(r"[A-Za-z]{2,}")
NOT_WORDS = re.compile(
    r"^(?:[#.\[:&]|https?:|/|\w+\.(?:js|css|json|svg|png|onnx|toml)$|data-|aria-|\(prefers-)|=>|(?:[{;]|(?<!\{\d)\})\s*$|^\w+[:=]\w|"
    r"^[\w-]+\s*:\s*[\w#.%-]+;?$|\\|monospace|sans-serif|\brgba?\(|\bhsla?\(|\d+px\b|\$\d|\w=\{")


def html_unescape(text: str) -> str:
    import html

    return html.unescape(text)


# Texts the page puts together from pieces found elsewhere: "Cruise · 12 nm, 5 min" (the phase, then where it is).
EXTRA = ("{0} · {1} nm, {2} min", "{0}: hears you faster and more accurately.", "✓ ready", "✓ {0} lines",
         # The components' notes when they're missing (components.status: not on a PC that has them all).
         "Without it LocalTC understands you with its grammar only, or the cloud model.",
         "Without it LocalTC opens in your browser.", "Install it from ollama.com.",
         # The logbook's totals (lower-case words in a plain string), and the updates' messages (ui/updates.py).
         "flights", "airports", "LocalTC {0} is the latest version.", "LocalTC {0} is out (you have {1}).",
         "Downloading LocalTC {0} ...", "The download failed: {0}",
         "LocalTC {0} is ready: install it now, or it goes in the next time LocalTC closes.",
         "LocalTC {0} is ready to install.", "Restarting to install the update ...",
         "LocalTC {0} is downloaded and ready to install.",
         "This is a git checkout: update it with git pull (or GitHub Desktop).",
         "This copy wasn't set up by the installer; run the latest LocalTC-Setup.exe instead.",
         "Updates install themselves on Windows. Here, update with git pull.")
# Strings that look like words and are code: key names, CSS, a class list, an event list, an SVG path.
JUNK = re.compile(r"^(?:M[\d.]|Arrow(?:Left|Right|Up|Down)$|Escape$|Content-Type$|use strict$|JetBrains Mono$|Inter$|"
                  r"translate\(|width \{0\}ms|line atc thinking$|small (?:error|green|\{0\})$|zoomend |joystick:\d|"
                  r"\d+ \{0\}px|(?:center|line) \{0\}$)")


def people(text: str, *, plain: bool) -> bool:
    """Words a person reads (not code)."""
    t = text.strip()
    if JUNK.search(t):
        return False
    letters = len(re.findall(r"[A-Za-z]", t.replace("{", "").replace("}", "")))
    if letters < 2 or not WORDS.search(re.sub(r"\{\d\}", "", t)):
        return False
    if re.fullmatch(r"(?:\{\d\}[\s,.:;·\-–—/()%°×]*)+", t):
        return False
    if plain:
        if NOT_WORDS.search(t) or "\x00" in t:
            return False
        if " " not in t and not (t[0].isupper() and t[1:2].islower()):
            return False  # an id, a class, a key: one word in lower case ("installed" is in a template's HTML instead)
        if re.fullmatch(r"[A-Z0-9_]+", t):
            return False  # a constant, a code ("KSEA", "ATC" as a key)
        if re.search(r"\$\{|\bfunction\b|\breturn\b|\bconst\b|document\.|\bnew \w+\(", t):
            return False
    if re.fullmatch(r"[\w-]+(?:\s+[\w-]+)*", t) and plain and t == t.lower() and " " not in t:
        return False
    return True


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def from_script(src: str) -> set[str]:
    found: set[str] = set()
    for kind, text in js_literals(src):
        if "<" not in text:  # no HTML in it: a string like any other (a template's values as {n})
            kind, text = "plain", html_unescape(holes(text))
        if kind == "template" or "<" in text and ">" in text:
            for piece in html_texts(text):
                for line in piece.split("\n") if "<" not in text else [piece]:
                    s = norm(holes(line))
                    if people(s, plain=kind == "plain" and "<" not in text):
                        found.add(s)
        else:
            for line in text.split("\n"):
                s = norm(line)
                if people(s, plain=True):
                    found.add(s)
    return found


def from_page(html: str) -> set[str]:
    return {s for t in html_texts(html) if people(s := norm(t), plain=False)}


# --- what the app's Python sends the page ----------------------------------------------------------------------------

def from_python() -> set[str]:
    """The model catalog's names and descriptions, the components', the personalities' manner."""
    sys.path.insert(0, str(ROOT / "src"))
    from localtc import models
    from localtc.crew.personalities import PERSONALITIES

    found: set[str] = set()
    cat = models.catalog()
    for kind in ("llm", "whisper", "voice"):
        for o in cat.get(kind, []):
            found.update(x for x in (o.get("label"), o.get("speed"), o.get("quality"), o.get("note")) if x)
    for p in cat.get("profiles", []):
        found.update(x for x in (p.get("label"), p.get("description")) if x)
    for p in PERSONALITIES:
        found.update(x for x in (p.style, p.brief) if x)
    from localtc.console import ALERTS, PHASES

    found.update(PHASES.values())  # the flight's phase, under the radios
    found.update(ALERTS.values())
    found.update(EXTRA)
    from localtc import components
    from localtc.llm.cloud import PROVIDERS

    for platform, gpu in (("win32", ""), ("win32", "GPU"), ("darwin", "")):
        for c in components.status(platform=platform, gpu=gpu):
            found.update(x for x in (c.label, c.detail) if x and "GPU:" not in x)
    found.update(p.note for p in PROVIDERS if p.note)
    return {norm(s) for s in found if people(norm(s), plain=False)}


def strings() -> list[str]:
    found = from_page((STATIC / "index.html").read_text(encoding="utf-8"))
    for name in SCRIPTS:
        found |= from_script((STATIC / name).read_text(encoding="utf-8"))
    found |= from_python()
    return sorted(found)


def placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{\d\}", text))


def main(argv: list[str]) -> int:
    what = argv[0] if argv else "check"
    if what == "extract":
        OUT.mkdir(exist_ok=True)
        found = strings()
        (OUT / "strings.json").write_text(json.dumps(found, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
        print(f"{len(found)} strings, {sum(len(s) for s in found):,} characters")
        return 0
    english = json.loads((OUT / "strings.json").read_text(encoding="utf-8"))
    if what == "missing":
        have = json.loads((OUT / f"{argv[1]}.json").read_text(encoding="utf-8")) if (OUT / f"{argv[1]}.json").exists() else {}
        print(json.dumps([s for s in english if s not in have], ensure_ascii=False, indent=0))
        return 0
    bad = 0
    for path in sorted(OUT.glob("*.json")):
        if path.name == "strings.json":
            continue
        have = json.loads(path.read_text(encoding="utf-8"))
        missing = [s for s in english if s not in have]
        extra = [s for s in have if s not in english]
        wrong = [s for s, t in have.items() if not placeholders(t) <= placeholders(s)]
        print(f"{path.stem}: {len(have)} translated, {len(missing)} missing, {len(extra)} left over, {len(wrong)} with "
              f"values that aren't in the English")
        bad += len(missing) + len(wrong)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
