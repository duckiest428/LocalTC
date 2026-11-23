"""The page's objects are all defined: a call to one that isn't (``Languages.list()`` for ``Language.list()``) throws
when the panel draws, and Quick Settings and Settings stuck at "Loading ..." with nothing said."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "localtc" / "ui" / "static"
# The browser's own (and the libraries the page loads beside app.js).
BUILT_IN = {
    "Array", "Blob", "Date", "Error", "File", "FormData", "Intl", "JSON", "Map", "Math", "Number", "Object", "Promise",
    "Reflect", "RegExp", "Set", "String", "Symbol", "URL", "URLSearchParams", "WeakMap", "WeakSet", "WebSocket",
    "EventSource", "AbortController", "Audio", "Image", "Notification", "ResizeObserver", "IntersectionObserver",
    "MutationObserver", "CustomEvent", "Event", "KeyboardEvent", "TextDecoder", "TextEncoder", "DOMParser", "Uint8Array",
    "Float32Array", "Int16Array", "AudioContext", "MediaRecorder", "L", "Chart",
}
USED = re.compile(r"(?:(?<=\.\.\.)|(?<![\w.$]))([A-Z][A-Za-z0-9_]*)\.[a-zA-Z_$]")  # a name, or one spread (...Name)
DEFINED = re.compile(r"\b(?:const|let|var|class|function)\s+([A-Z][A-Za-z0-9_]*)\b")
REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^") | {""}


def code_of(text: str) -> str:
    """The script with its strings, comments and regular expressions blanked out, keeping what template strings run
    (``${...}``, nested to any depth)."""
    out: list[str] = []
    stack = ["code"]  # "code" or "tpl"; each "code" pushed inside a template counts its own braces
    depth = [0]
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if stack[-1] == "tpl":
            if c == "\\":
                i += 2
            elif c == "`":
                stack.pop()
                i += 1
            elif text.startswith("${", i):
                stack.append("code")
                depth.append(0)
                out.append(" ")
                i += 2
            else:
                i += 1
            continue
        if text.startswith("//", i):
            i = text.find("\n", i) if "\n" in text[i:] else n
        elif text.startswith("/*", i):
            i = text.index("*/", i + 2) + 2
        elif c in "'\"":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            out.append(" '' ")
        elif c == "`":
            stack.append("tpl")
            i += 1
            out.append(" ")
        elif c == "/" and ("".join(out).rstrip()[-1:] in REGEX_AFTER):
            j, in_class = i + 1, False
            while j < n and text[j] != "\n":
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    in_class = True
                elif text[j] == "]":
                    in_class = False
                elif text[j] == "/" and not in_class:
                    break
                j += 1
            i = j + 1
            out.append(" /x/ ")
        elif c == "{":
            depth[-1] += 1
            out.append(c)
            i += 1
        elif c == "}":
            if depth[-1] == 0 and len(stack) > 1:
                stack.pop()
                depth.pop()
                out.append(" ")
            else:
                depth[-1] -= 1
                out.append(c)
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def test_the_scanner_keeps_template_code_and_drops_prose():
    code = code_of("const a = `Talk to ATC. ${x ? `nested ${Foo.bar()} ATC.` : Bar.baz} done.`; // Qux.no\n/['\"]/g;"
                   " [...Spread.list()]")
    assert set(USED.findall(code)) == {"Foo", "Bar", "Spread"}


def test_every_object_the_page_calls_is_defined():
    defined = set(BUILT_IN)
    for p in STATIC.glob("*.js"):
        defined |= set(DEFINED.findall(p.read_text(encoding="utf-8")))
    code = code_of((STATIC / "app.js").read_text(encoding="utf-8"))
    missing = sorted(set(USED.findall(code)) - defined)
    assert not missing, f"app.js calls objects nothing defines: {missing}"
