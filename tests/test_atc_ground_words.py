"""ATC on the ground and in its words: the gate asked for, the holding point that's the runway's, "hold position" read
back, a speed already below the one assigned, and a plan filed with the flight under way."""

from types import SimpleNamespace

from localtc.atc_core.airport import gates as stands
from localtc.atc_core.airport.geometry import AirportGeometry
from localtc.atc_core.engine import AtcEngine, EngineConfig
from localtc.atc_core.llm.grounding import missing_cue
from localtc.atc_core.readback.normalize import normalize


def test_a_gate_asked_for_before_the_word_gate_is_the_one_asked_for():
    """ "Can we please get Echo 9 as our gate?" was taken for a wrong readback of the taxi to D52."""
    assert stands.requested(normalize("No, can we please get Echo 9 as our gate? Thank you.")) == "E9"
    assert stands.requested(normalize("We'd like to get a taxi to Gate Echo 9.")) == "E9"
    assert stands.requested(normalize("taxi via whiskey golf charlie to the gate")) is None


def test_a_gate_the_scenery_hasnt_gets_the_nearest_of_its_letter(monkeypatch):
    """Las Vegas's scenery has E8 and E10, no E9: E8 or E10, not D52 across the airport."""
    names = ["GATE D 52", "GATE E 8", "GATE E 10", "GATE E 15"]
    fake = [SimpleNamespace(word="gate", label=n.removeprefix("GATE ").replace(" ", ""), index=i) for i, n in enumerate(names)]
    monkeypatch.setattr(stands, "gates", lambda geometry, real=None: fake)
    monkeypatch.setattr(stands, "occupied", lambda gate, geometry, traffic: False)
    assert stands.nearest_named(None, "E9", None, set(), ()).label in ("E8", "E10")
    assert stands.nearest_named(None, "E9", None, {1, 2}, ()).label == "E15"  # the near ones taken
    assert stands.nearest_named(None, "Z9", None, set(), ()) is None


def test_the_holding_point_is_the_runways_not_the_ils_critical_areas():
    """Stopped in the queue at Seattle's ILS line, ground said "contact tower" before the holding point."""
    ils = SimpleNamespace(point=SimpleNamespace(kind="ils_hold_short_no_draw"), xy=(0.0, 0.0))
    line = SimpleNamespace(point=SimpleNamespace(kind="hold_short"), xy=(0.0, 120.0))
    geo = SimpleNamespace(hold_shorts=[ils, line], xy=lambda lat, lon: (0.0, 5.0))
    hold, metres = AirportGeometry.nearest_hold_short(geo, 0.0, 0.0)
    assert hold is line and metres == 115.0
    geo.hold_shorts = [ils]  # only the ILS line drawn: that one, rather than none
    assert AirportGeometry.nearest_hold_short(geo, 0.0, 0.0)[0] is ils


def test_hold_position_read_back_isnt_ready_for_departure():
    tokens = normalize("Hold position, Frontier 4204.")
    assert missing_cue("ready_for_departure", tokens) is not None
    assert missing_cue("ready_for_departure", normalize("Frontier 4204 holding short 16L, ready for departure")) is None


def test_a_plan_filed_with_the_flight_under_way_is_atcs_from_then_on():
    engine = AtcEngine(EngineConfig())
    assert engine.state.flight.destination is None
    engine.file_plan(EngineConfig(destination="klas", cruise_ft=37000, callsign="FFT4204", sid="SUMMA2", star="COKTL4"))
    assert engine.state.flight.destination == "KLAS" and engine.state.flight.cruise_ft == 37000
    assert engine.cfg.star == "COKTL4" and engine.tracker.context_builder.destination == "KLAS"
    assert engine.tracker.detector.cruise_ft == 37000 and str(engine.state.flight.callsign.ident) == "FFT4204"
