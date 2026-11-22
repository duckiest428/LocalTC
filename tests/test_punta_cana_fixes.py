"""Punta Cana to San Juan: the radio log's frequencies as ATC took them, a line-up that sent an arrival around, the
traffic moving before its clearances, the ground left behind, a gate's letter, and the words of the models."""

import json

from test_atc_operations import engine_at_kbfi, on_final, released

from localtc.atc_core.readback.written import with_frequency
from localtc.markup import unmarked
from localtc.sim_api import TrafficTarget


def test_the_log_shows_the_frequency_atc_took():
    assert with_frequency("Tower on 118, Dec. 7, 7, 7, 5. Frontier 38.", 118.775) == "Tower on 118.775. Frontier 38."
    assert with_frequency("Departure on normal normal 9 decimal 75.", 119.75) == "Departure on 119.75."
    assert with_frequency("Climb flight level 060. Departure is on 9 decimal 75. Squawk 4101. Frontier 38.", 119.75) \
        == "Climb flight level 060. Departure is on 119.75. Squawk 4101. Frontier 38."
    assert with_frequency("It's on one center on 1, 2, 1340, 75. Frontier 38.", 134.075) == "It's on one center on 134.075. Frontier 38."
    assert with_frequency("Tower on 13205, Frontier 38.", 132.05) == "Tower on 132.05, Frontier 38."
    assert with_frequency("Ground on 121 decimal seven, Frontier 38.", 121.7) == "Ground on 121.7, Frontier 38."
    # Nothing like it: left as it was (and the squawk is never the frequency)
    assert with_frequency("San Juan Approach on 109.4, Frontier 38.", 119.4) == "San Juan Approach on 109.4, Frontier 38."
    assert with_frequency("Squawk 4101, Frontier 38.", 119.75) == "Squawk 4101, Frontier 38."


def test_an_empty_press_isnt_the_airports_taxiways():
    from localtc.stt.vocabulary import VocabularyHints, echoes_prompt

    hints = VocabularyHints(callsign="FFT38", taxiways=("A", "B", "D", "E", "F"), runways=("08", "26", "09", "27"))
    assert echoes_prompt("A, B, D, E, F.", hints)  # ground said "say again" to nothing said
    assert not echoes_prompt("Via A, D, E, Frontier 38.", hints)
    assert not echoes_prompt("Line up and wait.", hints) and not echoes_prompt("Roger.", hints)


def test_a_runway_written_r9_ends_the_taxi_route():
    from localtc.atc_core.readback.extract import taxi_routes
    from localtc.atc_core.readback.normalize import normalize

    said = normalize("Holding point, runway 8, via A D E Ultra R9, we have information Echo, Frontier 38.")
    assert taxi_routes(said, ("A", "D", "E")) == [("A", "D", "E")]


def test_the_models_stars_are_never_read_out():
    assert unmarked("the coffee, which I promise will be *shaken, not stirred*.") == "the coffee, which I promise will be shaken, not stirred."
    assert unmarked("**Welcome** aboard") == "Welcome aboard"
    assert unmarked("Gate B_12 and the jet_bridge") == "Gate B_12 and the jet_bridge"


def test_a_stands_letter_is_the_sims_name_list():
    from localtc.sim_bridge.facilities import _parking_suffix

    assert _parking_suffix(13) == "B"  # Punta Cana's gate 20B, once "20M"
    assert _parking_suffix(12) == "A" and _parking_suffix(37) == "Z" and _parking_suffix(ord("C")) == "C"
    assert _parking_suffix(0) == "" and _parking_suffix(5) == ""


def test_the_dominican_republic_flies_altitudes_below_18000():
    from localtc.atc_core.region import region_for

    assert region_for("MDPC").transition_ft == 18000 and region_for("MDPC").icao


def test_no_line_up_behind_a_heavy_with_an_arrival_due_first():
    """Lined up behind a departing 787, two minutes of wake to wait, and the arrival on a seven mile final went around."""
    engine = engine_at_kbfi()
    engine.state.flight.aircraft_type = "A321"
    geo = engine.geometry("KBFI")
    end = geo.end("14R")
    lat, lon = geo.frame.to_latlon(*end.threshold)

    def rolling(model: str) -> TrafficTarget:
        return TrafficTarget(object_id=9, atc_id="ACA1411", atc_model=model, lat=lat, lon=lon, alt_ft=20.0,
                             hdg_true=end.heading_true, gs_kt=60.0, on_ground=True)

    arrival = on_final(engine, "14R", 7.0)
    assert released(engine, (rolling("B788"), arrival)) == "tower.hold_short_traffic"
    engine2 = engine_at_kbfi()
    engine2.state.flight.aircraft_type = "A321"
    assert released(engine2, (rolling("A320"), on_final(engine2, "14R", 7.0))) == "tower.luaw"  # no wake: time enough


def test_an_optional_part_the_pilot_never_said_isnt_wrong():
    """ "Climb flight level 060" filed by the model under the cruise, and ATC: "negative, expect flight level 150"."""
    from localtc.atc_core.llm.understand import Answer, LlmInterpreter
    from localtc.atc_core.readback.interpreter import Interpretation
    from localtc.atc_core.readback.normalize import normalize
    from localtc.atc_core.readback.interpreter import PendingReadback

    pending = PendingReadback(instruction_id="clearance.ifr_sid", controller="clearance", issued_t=0.0, required=("altitude", "squawk"),
                              optional=("cruise", "frequency"),
                              expected={"altitude": 6000, "squawk": "4101", "cruise": 15000, "frequency": 119.75})
    text = "Cleared via MELLA2W, climb 6,000, departure 119.75, squawk 4101, Frontier 38."
    answer = Answer(kind="readback", values={"altitude": 6000, "squawk": "4101", "cruise": 6000})
    grammar = Interpretation(kind="readback", callsign_heard=True)
    got = LlmInterpreter._readback(normalize(text), answer, pending, grammar, text)
    assert got is not None and got.status == "correct" and "cruise" not in got.mismatched


def test_the_ramps_question_stays_a_question():
    from localtc.ground.model import RampModel

    class Backend:
        model = "test"

        def __init__(self, reply: str) -> None:
            self.reply = reply

        def complete(self, request, timeout_s):
            from types import SimpleNamespace

            return SimpleNamespace(text=json.dumps({"reply": self.reply}), error="", latency_ms=1.0)

    draft = "We've still got the GPU on. Do you want to push regardless?"
    said, _ = RampModel(Backend("Hold off on pushback, we've still got the GPU hooked up."), "llm").word("Request pushback.", draft, 0.0)
    assert said == draft
    said, _ = RampModel(Backend("GPU's still on, push anyway?"), "llm").word("Request pushback.", draft, 0.0)
    assert said == "GPU's still on, push anyway?"
