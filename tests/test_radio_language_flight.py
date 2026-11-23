"""A flight in French: the pilot calls in it, ATC answers in it, the readbacks in it are checked, and English again
the moment the pilot calls in English."""

import msgspec
from test_engine import parked_engine, transmissions

from localtc.sim_api import Transcript


def say(engine, own, t, text, language="fr"):
    outputs = engine.handle(Transcript(t=t, text=text, source="voice", language=language))
    for tick in range(1, 6):
        outputs += engine.handle(msgspec.structs.replace(own, t=t + tick))
    return outputs


def test_a_clearance_asked_for_in_french_is_given_and_read_back_in_french():
    engine, own = parked_engine(destination="KBFI", cruise_ft=12000, radio_language="fr")
    (clearance,) = transmissions(say(engine, own, 5, "Paine Prévol, Cessna 172LT, demandons la clairance IFR pour Boeing Field"))
    assert clearance.language == "fr" and "autorisé à destination de Boeing Field" in clearance.text, clearance.text
    assert "transpondeur" in clearance.text and "montez et maintenez" in clearance.text
    squawk = engine.state.pending.expected["squawk"]
    altitude = engine.state.pending.expected["altitude"]
    readback = (f"Autorisés à destination de Boeing Field selon le plan de vol, montons et maintenons {altitude} pieds, "
                f"transpondeur {squawk}, Cessna 2LT")
    replies = transmissions(say(engine, own, 30, readback))
    assert engine.state.pending is None, (readback, replies)
    assert replies and replies[0].language == "fr" and "collationnement correct" in replies[0].text, [r.text for r in replies]


def test_called_in_english_again_atc_is_english_again():
    engine, own = parked_engine(destination="KBFI", cruise_ft=12000, radio_language="fr")
    (french,) = transmissions(say(engine, own, 5, "Paine Prévol, Cessna 172LT, demandons la clairance IFR pour Boeing Field"))
    assert french.language == "fr"
    (english,) = transmissions(say(engine, own, 30, "Say again, Cessna 2LT", language="en"))
    assert english.language == "" and "cleared to Boeing Field" in english.text, english.text


def test_english_only_keeps_the_radio_english():
    engine, own = parked_engine(destination="KBFI", cruise_ft=12000)
    (clearance,) = transmissions(say(engine, own, 5, "Paine Clearance, Cessna 172LT, IFR to Boeing Field", language="en"))
    assert clearance.language == "" and "cleared to Boeing Field" in clearance.text


def test_the_engines_own_words_are_in_the_language_too():
    from localtc.atc_core.phraseology.tongues.messages import reworded

    assert reworded("number 2, delay 10 minutes due to arrival traffic", "fr") == \
        "numéro 2, délai 10 minutes en raison du trafic à l'arrivée"
    assert reworded("2 o'clock, 5 miles, opposite direction, FL120, Boeing 737", "de") == \
        "2 Uhr, 5 Meilen, Gegenrichtung, FL120, Boeing 737"
    assert reworded("traffic on 3 mile final", "zh") == "五边3海里有飞机"


def test_a_clearance_in_french_has_its_runway_in_french():
    engine, own = parked_engine(destination="KBFI", cruise_ft=12000, radio_language="fr")
    (clearance,) = transmissions(say(engine, own, 5, "Paine Prévol, Cessna 172LT, demandons la clairance IFR pour Boeing Field"))
    assert "expect runway" not in clearance.text and "radar vectors" not in clearance.text, clearance.text
