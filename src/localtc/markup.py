"""A language model's reply as plain words: the markdown some models put in it ("*shaken, not stirred*") was read out
by the voice as "asterisk", and shown with its stars in the radio log."""

import re

_EMPHASIS = re.compile(r"(\*{1,3}|_{2,3})(?=\S)(.+?)(?<=\S)\1")  # *a*, **a**, ***a***, __a__ (one _ is in words)
_LEFT = re.compile(r"[*`#~]+")


def unmarked(text: str) -> str:
    """``text`` without markdown's emphasis, code ticks and headings; the words inside are kept."""
    if not text or not any(c in text for c in "*_`#~"):
        return text
    text = _EMPHASIS.sub(r"\2", text)
    return " ".join(_LEFT.sub("", text).split())
