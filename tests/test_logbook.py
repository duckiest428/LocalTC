"""The logbook: a flight summed up from its events, kept in SQLite, and totalled."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_phase import own

from localtc.app import engine_config
from localtc.atc_core.engine import AtcEngine
from localtc.config import load_config, with_recorded
from localtc.logbook import FlightLog, Logbook, totals
from localtc.replay import Recording
from localtc.sim_api import SIM_EVENT_TYPES

FLIGHT = Path(__file__).parent / "fixtures" / "real_ksan_kphx"


@pytest.fixture(scope="module")
def ksan_kphx():
    """The San Diego to Phoenix flight into a FlightLog, as it was on the bus: everything recorded (the ATC
    and readback events of the live flight too). The engine runs alongside for the gates and runways."""
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    engine = AtcEngine(engine_config(cfg.flight, cfg.atc))
    flight_log = FlightLog(started=datetime(2026, 9, 20, 21, 26, tzinfo=UTC))
    for event in Recording(FLIGHT).events():
        flight_log.feed(event)
        if isinstance(event, SIM_EVENT_TYPES):
            engine.handle(event)
    return flight_log.finish(engine)


def test_the_flight_is_summed_up(ksan_kphx):
    r = ksan_kphx
    assert (r.origin, r.destination, r.callsign) == ("KSAN", "KPHX", "FFT2084")
    assert r.landed and r.arrival_runway == "26"
    assert 50 < r.air_min < 70  # about an hour in the air
    assert r.block_min > r.air_min
    assert 250 < r.distance_nm < 400  # 263 nm direct; the route is longer
    assert r.max_alt_ft > 30000
    assert -1000 < r.landing_vs_fpm < 0
    assert r.readbacks > 10 and 0 < r.readbacks_correct <= r.readbacks
    assert r.departure_gate  # it pushed back from a gate at San Diego


def test_nothing_is_logged_for_a_session_that_never_moved():
    flight_log = FlightLog()
    for t in range(10):
        flight_log.feed(own(float(t)))
    assert flight_log.finish() is None


def test_the_logbook_keeps_totals_and_sync_state(tmp_path, ksan_kphx):
    book = Logbook(tmp_path / "logbook.db")
    book.add(ksan_kphx)
    assert [f.id for f in book.flights()] == [ksan_kphx.id]
    assert book.get(ksan_kphx.id) == ksan_kphx
    t = book.totals()
    assert t["flights"] == 1 and t["airports"] == ["KPHX", "KSAN"] and t["landings"] == 1
    assert [f.id for f in book.unsynced()] == [ksan_kphx.id]
    book.mark_synced([ksan_kphx.id], "2026-09-22T00:00:00Z")
    assert book.unsynced() == []
    book.forget_sync()
    assert len(book.unsynced()) == 1
    assert book.delete(ksan_kphx.id) and book.flights() == []


def test_totals_of_nothing():
    assert totals([])["flights"] == 0 and totals([])["readback_accuracy"] is None
