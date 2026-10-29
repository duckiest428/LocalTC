"""LocalTC in the pilot's language (ui/static/i18n.js): every word of the window has its translation in every
language LocalTC offers, each keeps the values of its English ("Step {0} of {1}"), and the list of words is the one
the app's sources have today (``python tools/i18n.py extract`` after changing the window's words, then translate
what ``missing`` lists)."""

import importlib.util
import json
from pathlib import Path

import pytest

from localtc.languages import LANGUAGES

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("i18n_tool", ROOT / "tools" / "i18n.py")
i18n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(i18n)

ENGLISH = json.loads((i18n.OUT / "strings.json").read_text(encoding="utf-8"))
CODES = [code for code in LANGUAGES if code != "en"]


def test_the_words_listed_are_the_windows_words_today():
    assert ENGLISH == i18n.strings(), "run python tools/i18n.py extract, then translate what's missing"


@pytest.mark.parametrize("code", CODES)
def test_every_word_is_translated_and_keeps_its_values(code):
    have = json.loads((i18n.OUT / f"{code}.json").read_text(encoding="utf-8"))
    missing = [s for s in ENGLISH if s not in have]
    assert not missing, f"{code}: {len(missing)} untranslated, the first: {missing[:3]}"
    assert not [s for s in have if s not in ENGLISH], f"{code}: translations of words the window no longer has"
    wrong = [s for s, t in have.items() if not i18n.placeholders(t) <= i18n.placeholders(s)]
    assert not wrong, f"{code}: values the English doesn't have: {wrong[:3]}"
    assert all(t.strip() for t in have.values())


def test_every_language_offered_has_its_file():
    assert sorted(p.stem for p in i18n.OUT.glob("*.json") if p.stem != "strings") == sorted(CODES)
