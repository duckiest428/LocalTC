"""FAA speech formatting and template library validation/rendering."""

import pytest

from localtc.atc_core.phraseology import TemplateError, TemplateLibrary
from localtc.atc_core.phraseology import speech
from localtc.atc_core.values import Approach, Callsign, Wind


@pytest.mark.parametrize(("value", "spoken"), [
    ("34L", "three four left"), ("04", "four"), ("16R", "one six right"), ("09C", "niner center"), ("36", "three six"),
])
def test_runway(value, spoken):
    assert speech.runway(value) == spoken


@pytest.mark.parametrize(("value", "spoken"), [
    (121.8, "one two one point eight"), (124.675, "one two four point six seven five"), (120.0, "one two zero point zero"),
    (118.305, "one one eight point three zero five"),
])
def test_frequency(value, spoken):
    assert speech.frequency(value) == spoken


@pytest.mark.parametrize(("value", "spoken", "display"), [
    (5000, "five thousand", "5,000"), (3500, "three thousand five hundred", "3,500"),
    (12000, "one two thousand", "12,000"), (10500, "one zero thousand five hundred", "10,500"),
    (800, "eight hundred", "800"), (18000, "flight level one eight zero", "FL180"), (35000, "flight level three five zero", "FL350"),
])
def test_altitude(value, spoken, display):
    assert speech.altitude(value) == spoken
    assert speech.altitude_display(value) == display


@pytest.mark.parametrize(("callsign", "spoken", "display"), [
    (Callsign("N172LT"), "november one seven two lima tango", "N172LT"),
    (Callsign("N172LT", type_name="Skyhawk", abbreviated=True), "Skyhawk two lima tango", "Skyhawk 2LT"),
    (Callsign("N172LT", abbreviated=True), "november two lima tango", "2LT"),
    (Callsign("ASA123", telephony="Alaska", flight_number="123"), "Alaska one twenty-three", "Alaska 123"),
    (Callsign("DAL1234", telephony="Delta", flight_number="1234"), "Delta twelve thirty-four", "Delta 1234"),
    (Callsign("UAL100", telephony="United", flight_number="100"), "United one hundred", "United 100"),
    (Callsign("SWA5", telephony="Southwest", flight_number="5"), "Southwest five", "Southwest 5"),
])
def test_callsign(callsign, spoken, display):
    assert speech.callsign(callsign) == spoken
    assert speech.callsign_display(callsign) == display


def test_misc_formats():
    assert speech.squawk("4521") == "four five two one"
    assert speech.heading(5) == "zero zero five" and speech.heading(360) == "three six zero"
    assert speech.altimeter(29.92) == "two niner niner two"
    assert speech.taxi_route(("C", "A", "A1")) == "charlie, alpha, alpha one"
    assert speech.wind(Wind(150, 8)) == "one five zero at eight" and speech.wind(Wind(150, 2)) == "calm"
    assert speech.approach(Approach("ILS", "14R")) == "I L S runway one four right"
    assert speech.airport_name("SNOHOMISH CO (PAINE FLD)", "KPAE") == "Paine Field"
    assert speech.airport_name("BOEING FLD/KING CO INTL", "KBFI") == "Boeing Field"
    assert speech.station_name("SEATTLE APPROACH") == "Seattle Approach"


def test_builtin_templates_load_and_render_both_forms():
    library = TemplateLibrary.load()
    rendered = library.render("tower.takeoff", {"callsign": Callsign("N172LT"), "runway": "34L"})
    assert rendered.text == "N172LT, runway 34L, fly runway heading, cleared for takeoff."
    assert rendered.spoken == "november one seven two lima tango, runway three four left, fly runway heading, cleared for takeoff."
    assert rendered.expected == {"runway": "34L", "cleared_for_takeoff": True}
    assert (rendered.required, rendered.controller) == (("runway", "cleared_for_takeoff"), "tower")


def test_render_errors():
    library = TemplateLibrary.load()
    with pytest.raises(TemplateError, match="missing slot"):
        library.render("tower.takeoff", {"callsign": Callsign("N172LT")})
    with pytest.raises(TemplateError, match="expects"):
        library.render("tower.takeoff", {"callsign": Callsign("N172LT"), "runway": 34})
    with pytest.raises(TemplateError, match="no template"):
        library.get("tower.nope")


def test_validation_catches_bad_templates(tmp_path):
    (tmp_path / "bad.toml").write_text(
        '[[template]]\nid = "x.bad"\ncontroller = "tower"\ntext = ["{callsign}, {nonsense}"]\n'
        'readback = { required = ["telepathy"] }\n'
    )
    with pytest.raises(TemplateError) as err:
        TemplateLibrary.load(tmp_path)
    message = str(err.value)
    assert "unknown slot {nonsense}" in message
    assert "no readback extractor for 'telepathy'" in message
    assert "no pilot_readback example" in message


def test_extra_template_dirs_override(tmp_path):
    (tmp_path / "custom.toml").write_text(
        '[[template]]\nid = "common.roger"\ncontroller = "any"\ntext = ["{callsign}, copy that."]\n'
    )
    library = TemplateLibrary.load(tmp_path)
    assert library.render("common.roger", {"callsign": Callsign("N1")}).text == "N1, copy that."
