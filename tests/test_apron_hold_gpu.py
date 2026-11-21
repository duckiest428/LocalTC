"""An apron controller of its own (Incheon: Delivery, then Apron for the push and start, then Ground for the taxi);
delivery named as each region names it; a hold over a VOR by its name with no flight plan; the sim's placeholder
position never taken for a flight; South Korea's airlines' callsigns; Whisper on the graphics card finding CUDA."""

from helpers.airports import make_airport

from localtc.atc_core.facilities import airport_facilities


INCHEON = make_airport("RKSI", "INCHEON INTL", (37.4602, 126.4407), 23, 150, ("15R", "33L"), {
    "clearance": (121.6, "INCHEON DEL"), "ground": (118.75, "INCHEON GND"), "tower": (118.2, "INCHEON TWR"),
    "departure": (124.3, "INCHEON DEP")})


def incheon(apron_mhz: float = 121.65):
    from msgspec.structs import replace

    from localtc.sim_api import Frequency

    return replace(INCHEON, frequencies=(*INCHEON.frequencies, Frequency(kind="ground", mhz=apron_mhz, name="INCHEON APRON")))


def test_incheon_has_delivery_an_apron_and_ground():
    by = {(f.controller, f.apron): f for f in airport_facilities(incheon(), role="departure")}
    assert by[("clearance", False)].station == "Incheon Delivery"
    assert by[("ground", True)].station == "Incheon Apron" and by[("ground", True)].mhz == 121.65
    assert by[("ground", False)].station == "Incheon Ground" and by[("ground", False)].mhz == 118.75


def test_an_apron_alone_is_the_ground_controller():
    from msgspec.structs import replace

    from localtc.sim_api import Frequency

    only = replace(INCHEON, frequencies=tuple(f for f in INCHEON.frequencies if f.kind != "ground")
                   + (Frequency(kind="ground", mhz=121.65, name="INCHEON APRON"),))
    grounds = [f for f in airport_facilities(only, role="departure") if f.controller == "ground"]
    assert len(grounds) == 1 and not grounds[0].apron and grounds[0].station == "Incheon Apron"


def test_clearance_is_called_clearance_in_the_us_and_canada_and_delivery_elsewhere():
    us = make_airport("KBOS", "GENERAL EDWARD LAWRENCE LOGAN INTL", (42.36, -71.01), 20, 40, ("04R", "22L"),
                      {"clearance": (121.65, "BOSTON CLNC DEL"), "ground": (121.9, "BOSTON GND")})
    assert next(f for f in airport_facilities(us, role="departure") if f.controller == "clearance").station == "Boston Clearance"


def _engine():
    from localtc.atc_core.airport.geometry import AirportGeometry
    from localtc.atc_core.engine import AtcEngine, EngineConfig
    from localtc.sim_api import AirportData

    engine = AtcEngine(EngineConfig(seed=1))
    engine.handle(AirportData(t=0.0, airport=incheon()))
    engine.state.flight.origin = "RKSI"
    engine._rebuild_facilities()
    assert isinstance(engine.geometry("RKSI"), AirportGeometry)
    return engine


def test_delivery_sends_the_flight_to_the_apron_and_the_apron_to_ground_for_the_taxi():
    engine = _engine()
    engine.state.phase = "PARKED"
    assert engine.facility("ground").station == "Incheon Apron"  # (delivery's "contact ... when ready")
    assert engine._ground_stage("request_pushback").station == "Incheon Apron"
    assert engine._ground_stage("ready_to_taxi").station == "Incheon Ground"
    from localtc.atc_core.session import Clearance

    engine.state.clearances["taxi"] = Clearance("taxi", "ground.taxi", "ground", 1.0, readback="correct")
    engine.state.phase = "TAXI_OUT"
    assert engine.facility("ground").station == "Incheon Ground"


def test_ready_to_taxi_on_the_apron_is_handed_to_ground_and_the_push_asked_of_ground_to_the_apron():
    from localtc.atc_core.readback import Interpretation

    engine = _engine()
    engine.state.phase = "PARKED"
    apron, ground = engine._ground_stage("request_pushback"), engine._ground_stage("ready_to_taxi")
    engine._request(Interpretation(kind="request", intent="ready_to_taxi", values={}, text="ready to taxi"), apron, 10.0)
    assert [(s.instruction_id, s.slots["station"]) for s in engine._scheduled][-1] == ("common.contact", "Incheon Ground")
    engine._scheduled.clear()
    engine._request(Interpretation(kind="request", intent="request_pushback", values={}, text="request pushback"), ground, 20.0)
    assert [(s.instruction_id, s.slots["station"]) for s in engine._scheduled][-1] == ("common.contact", "Incheon Apron")


# --- a hold over a VOR by its name ------------------------------------------------------------------------------------


def test_a_navaid_is_found_by_its_name_or_ident_near_the_aircraft():
    from localtc.atc_core import navaids

    pisa = navaids.find("Pisa Vor", 43.70, 10.40)
    assert pisa is not None and pisa.ident == "PIS" and pisa.kind == "VOR" and pisa.said == "Pisa VOR"
    assert navaids.find("PIS", 43.70, 10.40).kind == "VOR"
    assert navaids.find("Pisa NDB", 43.70, 10.40).kind == "NDB"
    assert navaids.find("Pisa VOR", 37.46, 126.44) is None  # (not near Incheon)
    assert navaids.find("Nowhere VOR", 43.70, 10.40) is None


def test_a_hold_is_given_over_a_named_vor_with_no_flight_plan():
    """ "Request holding over Pisa VOR" right after departure, no plan filed: "unable" every time."""
    from test_atc_operations import CENTER, engine_at_kbfi

    from localtc.atc_core.facilities import Facility
    from localtc.atc_core.readback import Interpretation
    from localtc.atc_core.readback.expected import EXPECTED
    from localtc.sim_api import OwnshipState

    assert "DEPARTURE" in EXPECTED["request_hold"][1]
    engine = engine_at_kbfi()
    own = OwnshipState(t=500.0, lat=43.60, lon=10.30, alt_msl_ft=3000, alt_indicated_ft=3000, alt_agl_ft=2990, hdg_true=30,
                       hdg_mag=28, gs_kt=110, ias_kt=100, vs_fpm=0, on_ground=False, squawk="1200", com1_mhz=124.28,
                       com2_mhz=0.0, altimeter_inhg=29.92, zulu_s=13 * 3600.0, xpdr_mode="alt")
    engine.state.aircraft = own
    departure = Facility("departure", "Pisa Departure", 124.28, "LIRP")
    engine._hold_request(Interpretation(kind="request", intent="request_hold", values={"fix": "Pisa Vor"},
                                        text="request holding over Pisa VOR"), departure, 500.0, own)
    item = engine._scheduled[-1]
    assert item.instruction_id in ("common.hold", "common.hold_minutes") and item.slots["fix"] == "Pisa VOR"
    assert engine.hold is not None and engine.hold.reason == "requested"
    engine._scheduled.clear()
    engine.hold = None
    engine._hold_request(Interpretation(kind="request", intent="request_hold", values={"fix": "Nowhere"},
                                        text="request holding over nowhere"), CENTER, 600.0, own)
    assert engine._scheduled[-1].instruction_id == "common.say_again"  # (a fix it can't place: never invented)


def test_the_hold_is_read_back_by_the_navaids_name():
    from localtc.atc_core.readback.extract import values_equal

    assert values_equal("fix", "Pisa", "Pisa VOR") and values_equal("fix", "Pisa VOR", "Pisa VOR")
    assert not values_equal("fix", "Elba", "Pisa VOR")


# --- the sim's placeholder position -----------------------------------------------------------------------------------


def test_the_sims_placeholder_position_isnt_a_flight():
    """The sim restarted: 0 N 0 E at 53,820 ft for a few seconds. LocalTC took it for a flight in the cruise ("joined in
    flight, call Accra Oceanic"), kept the level it was at, and after the takeoff from Pisa said "check altitude,
    maintain FL098"."""
    from test_atc_operations import engine_at_kbfi

    from localtc.atc_core.engine import menu_position
    from localtc.sim_api import OwnshipState

    def at(lat, lon, alt, ground):
        return OwnshipState(t=10.0, lat=lat, lon=lon, alt_msl_ft=alt, alt_indicated_ft=alt, alt_agl_ft=alt, hdg_true=0,
                            hdg_mag=0, gs_kt=0, ias_kt=0, vs_fpm=0, on_ground=ground, squawk="1200", com1_mhz=0.0,
                            com2_mhz=0.0, altimeter_inhg=29.92, xpdr_mode="alt")

    assert menu_position(at(0.0, 0.0, 53820, False)) and menu_position(at(0.0, 90.0, 0, True))
    assert not menu_position(at(43.69, 10.40, 6, True))
    engine = engine_at_kbfi()
    assert engine.handle(at(0.0, 0.0, 53820, False)) == []
    assert engine.joined_to is None and engine._joining is None and engine.state.assignments.altitude_ft is None


# --- South Korea's airlines --------------------------------------------------------------------------------------------


def test_south_koreas_airlines_by_their_callsigns():
    from localtc.atc_core.airlines import TELEPHONY

    assert {c: TELEPHONY[c] for c in ("ABL", "ASV", "EOK", "TWB", "PTA", "JJA", "JNA", "ESR")} == {
        "ABL": "Air Busan", "ASV": "Air Seoul", "EOK": "Aerohanguk", "TWB": "Sonorous", "PTA": "Parata Air",
        "JJA": "Jeju Air", "JNA": "Jin Air", "ESR": "Eastar"}
    assert TELEPHONY["KAL"] == "Koreanair" and TELEPHONY["AAR"] == "Asiana"


# --- Whisper on the graphics card ---------------------------------------------------------------------------------------


def test_whisper_on_the_card_adds_the_cuda_folders_however_the_card_was_chosen(monkeypatch, tmp_path):
    """With the graphics card picked in the settings, only the "auto" check added the CUDA libraries' folders: cuBLAS
    wasn't found and Whisper went back to the CPU at every start."""
    import sys
    import types

    from localtc.stt import whisper

    calls = []
    monkeypatch.setattr(whisper, "add_nvidia_dll_dirs", lambda: calls.append(1) or ["x"])

    class Model:
        def __init__(self, path, device, compute_type):
            self.device = device

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    (tmp_path / "faster-whisper-small.en").mkdir()
    (tmp_path / "faster-whisper-small.en" / "model.bin").write_bytes(b"")
    w = whisper.WhisperTranscriber("small.en", device="cuda", compute_type="float16", models_dir=tmp_path, allow_download=False)
    w.load()
    assert calls and w._model.device == "cuda"
