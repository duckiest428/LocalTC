"""The cabin crew with a captain who talks another language: the announcements in the airline's own languages first,
then the captain's, then English; everything said to the flight deck in the captain's language."""

import pytest
from test_cabin import facts
from test_every_language import _cabin

from localtc.crew import cabin_incident_words, cabin_words, foreign
from localtc.crew.cabin import CabinCrew, CabinSettings
from localtc.crew.cabin_incidents import ASK, ASK_GROUND, PLAYS, decision_in
from localtc.crew.cabin_lines import FOREIGN

CODES = sorted(foreign.WORDS)


def crew(callsign, language, **settings):
    c = CabinCrew(CabinSettings(crew="claire", **settings), lambda: facts(callsign), seed=3)
    c.talks_in = language
    return c


@pytest.mark.parametrize("callsign, language, order", [
    ("DLH400", "pl", ("de", "pl", "en")),  # Lufthansa: German, then the captain's Polish, then English
    ("DLH400", "de", ("de", "en")),  # (the captain's is the airline's own: once)
    ("DAL12", "de", ("en", "de")),  # Delta: English, then German
    ("AFR22", "es", ("fr", "es", "en")),
    ("ACA1", "fr", ("en", "fr")),
    ("DLH400", "", ("de", "en")),  # no language of the captain's: as the airline has it
])
def test_the_airlines_languages_then_the_captains(callsign, language, order):
    c = crew(callsign, language)
    assert c.languages() == order
    assert tuple(o.language or "en" for o in c.announce("welcome")) == order


def test_without_english_too_the_captains_still_follows_the_airlines():
    assert crew("DLH400", "pl", english_too=False).languages() == ("de", "pl")


def test_an_order_to_the_crew_is_given_once_in_the_first():
    assert [o.language for o in crew("DLH400", "pl").announce("doors_arm_us", first_only=True)] == ["de"]


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("key", ["night", "exit_row", "safety_more", "seatbelt_again_off", "turbulence_service", "aircraft_talk",
                                 "doors_arm_us", "doors_disarm_us", "seatbelt_off_generic"])
def test_every_announcement_is_made_in_every_language(code, key):
    c = crew("DAL12", code)
    said = [o for o in c.announce(key, first_only=False) if o.language == code]
    assert said and "{" not in said[0].text and foreign.speaks(said[0].text, code), said


@pytest.mark.parametrize("code", CODES)
def test_the_new_announcements_are_in_every_language(code):
    assert {"night", "exit_row", "safety_more", "seatbelt_again_off", "turbulence_service", "aircraft_talk"} <= set(FOREIGN[code])


def test_a_number_said_to_the_captain_is_in_digits():
    assert _cabin("de").reply("boarding_left", {"left": "twenty-two"})[0].text == "Noch etwa 22 Minuten Boarding, Captain."


@pytest.mark.parametrize("code", CODES)
def test_every_problem_on_board_is_reported_in_the_captains_language(code):
    for kind in cabin_incident_words.KINDS:
        play = PLAYS[kind]
        for severity, words in play["report"].items():
            for ask in (ASK, ASK_GROUND):
                said = cabin_incident_words.in_language(f"{words.format(row=14)} {ask}", code)
                assert said is not None and ("14" in said or "{row}" not in words) and "{" not in said, (kind, severity)
                assert foreign.speaks(said, code) or code in ("de", "nl", "sv"), said  # ("Captain")
        for part in cabin_incident_words.PARTS:
            assert cabin_incident_words.in_language(play[part], code), (kind, part)
    assert cabin_incident_words.in_language("Captain, that's all sorted: met by paramedics on arrival.", code)
    unanswered = f"{cabin_incident_words.UNANSWERED} {PLAYS['smoke']['continue']}"
    assert cabin_incident_words.in_language(unanswered, code)


def test_a_call_not_all_here_stays_whole_in_english():
    assert cabin_incident_words.in_language("Captain, something nobody wrote.", "de") is None
    assert cabin_incident_words.in_language(PLAYS["smoke"]["continue"], "") is None


def test_the_report_asked_again_says_captain_once():
    words = "Still as I said, Captain: " + PLAYS["medical"]["report"]["minor"].format(row=3)[len("Captain, "):]
    assert cabin_incident_words.in_language(words, "fr").startswith("Comme je vous disais, commandant : un passager au rang 3")


@pytest.mark.parametrize("code, said, decision", [
    ("de", "Weiterfliegen bitte", "continue"), ("de", "Wir weichen aus", "divert"), ("de", "Hilfe anfordern", "assist"), ("de", "Notfall erklären", "escalate"),
    ("fr", "Faites venir les secours", "assist"), ("fr", "On continue", "continue"), ("es", "Desviamos", "divert"), ("it", "Dichiariamo emergenza", "escalate"),
    ("pt", "Continuem", "continue"), ("nl", "We wijken uit", "divert"), ("pl", "Kontynuujcie", "continue"), ("sv", "Kalla på hjälp", "assist"),
    ("ru", "Объявляем бедствие", "escalate"), ("zh", "备降", "divert"),
])
def test_the_captains_decision_in_their_language(code, said, decision):
    assert decision_in(cabin_words.as_english(said, code)) == decision


def test_the_landing_said_in_the_captains_language():
    c = _cabin("ru")
    c._landed_t, c._touchdown_fpm = 1.0, 80.0
    assert foreign.speaks(c._landing_words(), "ru")
    assert c.reply("landing_feedback", {"verdict": c._landing_words()})[0].language == "ru"


@pytest.mark.parametrize("code, sex, said", [("ru", "F", "Как я и говорила"), ("ru", "M", "Как я и говорил"), ("pl", "F", "Tak jak mówiłam")])
def test_the_crew_member_says_it_as_who_they_are(code, sex, said):
    words = "Still as I said, Captain: " + PLAYS["medical"]["report"]["minor"].format(row=3)[len("Captain, "):]
    assert foreign.said_by(cabin_incident_words.in_language(words, code), code, sex).startswith(said)
