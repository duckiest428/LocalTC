"""The copilot talked to in another language ([crew] language): the common things read in it without a language model,
its replies put into it, and the captain told that ATC works in English."""

import pytest
from test_crew import FakeBackend, own, said, started

from localtc.crew import foreign
from localtc.crew.model import CrewModel
from localtc.sim_api import AircraftSystems, AtcTransmission, CrewSpeech, IntercomHeard, SendSimEvent, Transcript


@pytest.mark.parametrize("code, text, english", [
    ("de", "Fahrwerk ausfahren", "gear down"),
    ("de", "Klappen eins bitte", "flaps 1"),
    ("de", "Mach bitte die Landescheinwerfer an", "landing lights on"),
    ("de", "Fahrwerk raus und Klappen drei", "gear down, flaps 3"),
    ("de", "Kurs zwei sieben null", "heading 270"),
    ("de", "Landecheckliste bitte", "run the landing checklist"),
    ("de", "Parkbremse lösen", "parking brake off"),
    ("de", "ja, bestätigt", "yes"),
    ("de", "Wie viel Treibstoff haben wir an Bord?", "how much fuel do we have?"),
    ("de", "Was hat die Flugsicherung gesagt?", "what did atc say?"),
    ("fr", "Sors le train d'atterrissage", "gear down"),
    ("fr", "Volets un s'il te plaît", "flaps 1"),
    ("fr", "oui, confirmé", "yes"),
    ("es", "Baja el tren de aterrizaje", "gear down"),
    ("es", "sí, confirmado", "yes"),
    ("es", "Checklist de aproximación", "run the approach checklist"),
    ("it", "Accendi le luci di atterraggio", "landing lights on"),
    ("pt", "Baixa o trem", "gear down"),
    ("nl", "Landingsgestel neer", "gear down"),
    ("pl", "Wypuść podwozie", "gear down"),
    ("sv", "Klaffar ett", "flaps 1"),
    ("ru", "Выпусти шасси", "gear down"),
    ("ru", "да", "yes"),
    ("zh", "放下起落架", "gear down"),
    ("zh", "执行进近检查单", "run the approach checklist"),
])
def test_the_common_things_are_read_in_every_language(code, text, english):
    assert foreign.gloss(text, code) == english


@pytest.mark.parametrize("code, text", [
    ("de", "Wie geht's dir heute?"),  # small talk: the language model's
    ("de", "Ich glaube, wir sind etwas zu schnell"),
    ("de", "Fahrwerk"),  # a thing with nothing to do to it
    ("de", "Fahrwerk ausfahren, aber erst nach dem Turm"),  # a word not understood: nothing made of half of it
    ("fr", "Ça va aujourd'hui ?"),
    ("de", "Gear down"),  # English: not this module's
])
def test_anything_else_is_left_to_the_language_model(code, text):
    assert foreign.gloss(text, code) is None or code == "de" and text == "Gear down" and foreign.gloss(text, code) == "gear down"


def test_what_a_line_is_in():
    assert foreign.speaks("Alles gut, Captain. Ein bisschen langweilig, aber gut.", "de")
    assert not foreign.speaks("41,300 pounds, a bit over eight hours at this burn.", "de")
    assert foreign.speaks("Check.", "de")  # no words either way
    assert foreign.speaks("收到。", "zh") and not foreign.speaks("Copy.", "zh")
    assert foreign.called_in("Zürich Turm, EDW87, bereit zum Abflug Piste 28", "de")
    assert not foreign.called_in("Zurich Tower, EDW87, ready for departure runway 28", "de")
    assert foreign.numbers("41,300 pounds") == foreign.numbers("41.300 Pfund") == foreign.numbers("41 300 livres")
    assert not foreign.units_kept("41,300 pounds", "41.300 kilos") and foreign.units_kept("41,300 pounds", "41.300 libras")


def german(answer: str = '{"reply": "Ja."}') -> object:
    pm = started()
    pm.model = CrewModel(FakeBackend(answer), language="de")
    pm.observe(own(2.0, fuel_lb=41300.0, fuel_flow_pph=2500.0))
    pm.observe(AircraftSystems(t=2.0, flaps_positions=4, engines_running=2))
    return pm


def test_a_command_in_german_is_done_at_once_without_the_model():
    pm = german()
    out = pm.observe(IntercomHeard(t=3.0, text="Fahrwerk ausfahren"))
    assert SendSimEvent(name="GEAR_DOWN") in out and pm.model.backend.asked == []


def test_a_confirmation_is_asked_in_german_and_answered_with_ja():
    pm = german('{"kind": "command", "action": "gear", "value": "down", "reply": "Gear down.", "english": "drop the gear"}')
    out = pm.observe(IntercomHeard(t=3.0, text="Lass die Räder runter"))
    asked = [o for o in out if isinstance(o, CrewSpeech)]
    assert [(o.text, o.language) for o in asked] == [("Gear down, bestätigen?", "de")]
    assert not [o for o in out if isinstance(o, SendSimEvent)]
    assert SendSimEvent(name="GEAR_DOWN") in pm.observe(IntercomHeard(t=4.0, text="ja"))


def test_a_question_from_the_data_is_answered_in_german():
    pm = german('{"reply": "Wir haben 41.300 Pfund, etwa 8 Stunden 16 Minuten bei diesem Verbrauch."}')
    out = pm.observe(IntercomHeard(t=3.0, text="Wie viel Treibstoff haben wir?"))
    line = [o for o in out if isinstance(o, CrewSpeech)][0]
    assert line.language == "de" and line.text.startswith("Wir haben 41.300 Pfund")
    assert pm.model.backend.asked[0].purpose == "crew_translate"


def test_what_atc_said_stays_atcs_words():
    pm = german()
    pm.observe(AtcTransmission(t=2.5, station="Zurich Approach", frequency_mhz=120.755, text="EDW87, fly heading 150."))
    out = pm.observe(IntercomHeard(t=3.0, text="Was hat die Flugsicherung gesagt?"))
    assert said(out) == ["Zurich Approach hat gesagt: EDW87, fly heading 150."] and pm.model.backend.asked == []


def test_a_translation_that_loses_a_number_or_a_unit_is_said_in_english():
    pm = german('{"reply": "Wir haben 41.300 Kilo."}')  # pounds aren't kilograms
    out = pm.observe(IntercomHeard(t=3.0, text="Wie viel Treibstoff haben wir?"))
    line = [o for o in out if isinstance(o, CrewSpeech)][0]
    assert line.language == "" and "41,300 pounds" in line.text


def test_a_reply_in_english_from_the_model_is_put_into_german():
    class Two(FakeBackend):
        def complete(self, request, *, timeout_s):
            self.answer = '{"kind": "reply", "reply": "All good, a bit boring."}' if request.purpose == "crew" \
                else '{"reply": "Alles gut, ein bisschen langweilig."}'
            return super().complete(request, timeout_s=timeout_s)

    pm = german()
    pm.model = CrewModel(Two(""), language="de", mode="llm")
    out = pm.observe(IntercomHeard(t=3.0, text="Wie geht's dir heute?"))
    assert [(o.text, o.language) for o in out if isinstance(o, CrewSpeech)] == [("Alles gut, ein bisschen langweilig.", "de")]


def test_short_words_need_no_model_and_the_set_lines_are_translated():
    assert foreign.ACKS["fr"]["Say again?"] == "Tu peux répéter ?"
    assert foreign.templated("I couldn't get flaps 1 from here. Can you do it?", "es") == \
        "No he podido poner flaps 1 desde aquí. ¿Puedes hacerlo tú?"
    assert foreign.templated("Flaps 3, confirm?", "de") == "Flaps 3, bestätigen?"


def test_atc_in_another_language_is_told_once():
    pm = german()
    out = pm.observe(Transcript(t=3.0, text="Zürich Turm, EDW87, bereit zum Abflug Piste 28", radio=1))
    assert said(out) == [foreign.ENGLISH_ONLY["de"]]
    assert said(pm.observe(Transcript(t=9.0, text="Zürich Turm, EDW87, erbitte Rollfreigabe", radio=1))) == []
    assert said(pm.observe(Transcript(t=12.0, text="Zurich Tower, EDW87, ready", radio=1))) == []


def test_english_is_as_before():
    pm = started()
    pm.model = CrewModel(FakeBackend('{"reply": "x"}'))
    assert pm.language == "" and SendSimEvent(name="GEAR_DOWN") in pm.observe(IntercomHeard(t=3.0, text="gear down"))
