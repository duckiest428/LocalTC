"""ATC in the pilot's own language: every template worded in each of LocalTC's languages, and every readback in it
checked the same way as one in English (the pilot's words read through the language's lexicon)."""

import random
from pathlib import Path

import pytest
from test_readback import READBACK_TEMPLATES, random_slots

from localtc.atc_core.phraseology import TemplateLibrary, tongues
from localtc.atc_core.readback import GrammarInterpreter, InterpretContext, PendingReadback
from localtc.atc_core.readback.normalize import normalize, render
from localtc.atc_core.readback.translate import to_english
from localtc.atc_core.region import Region, speaking

LANG_DIR = Path(__file__).parents[1] / "src" / "localtc" / "atc_core" / "phraseology" / "templates" / "lang"
LANGUAGES = sorted(p.stem for p in LANG_DIR.glob("*.toml"))
LIBRARIES = {code: TemplateLibrary.load(style="icao", language=code) for code in LANGUAGES}
ENGLISH = TemplateLibrary.load(style="icao")


def test_every_language_has_its_templates():
    assert set(LANGUAGES) == set(tongues.CODES)


@pytest.mark.parametrize("code", LANGUAGES)
def test_every_template_and_fragment_is_worded_in_the_language(code):
    assert sorted(LIBRARIES[code].untranslated) == []


def _readback_cases():
    return [(code, iid) for code in LANGUAGES for iid in READBACK_TEMPLATES]


@pytest.mark.parametrize(("code", "instruction"), _readback_cases())
def test_the_ideal_readback_in_the_language_is_correct(code, instruction):
    """Said ("montons niveau un deux zéro") and as speech-to-text writes it ("montons niveau 120"), in a region
    with flight levels from 6,000 ft and one from 18,000."""
    library = LIBRARIES[code]
    rng = random.Random(f"{code}{instruction}")
    for region in (Region("icao", 6000), Region("faa", 18000)):
        with speaking(region):
            for _ in range(12):
                slots = random_slots(rng)
                rendered = ENGLISH.render(instruction, slots)
                pending = PendingReadback(instruction, rendered.controller, rendered.expected, rendered.required,
                                          rendered.optional)
                for said in (library.pilot_readback(instruction, slots), library.pilot_readback_written(instruction, slots)):
                    english = to_english(said, code)
                    result = GrammarInterpreter().interpret(english, pending, InterpretContext(callsign=slots["callsign"]))
                    assert (result.kind, result.status) == ("readback", "correct"), (said, english, render(normalize(english)), result)


@pytest.mark.parametrize(("code", "instruction"), _readback_cases())
def test_what_atc_says_in_the_language_carries_every_value(code, instruction):
    """The controller's words in the language, read back through the lexicon, hold what must be read back: what
    the language model's wording is checked against."""
    library = LIBRARIES[code]
    rng = random.Random(f"{code}{instruction}atc")
    with speaking(Region("icao", 6000)):
        for _ in range(6):
            slots = random_slots(rng)
            rendered = library.render(instruction, slots)
            english = to_english(rendered.text, code)
            pending = PendingReadback(instruction, rendered.controller, rendered.expected, rendered.required, rendered.optional)
            result = GrammarInterpreter().interpret(english, pending, InterpretContext(callsign=slots["callsign"]))
            assert not result.missing and not result.mismatched, (rendered.text, english, result)
