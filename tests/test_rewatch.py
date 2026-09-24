"""Flight replays (replay/rewatch.py): the Denver to Seattle flight, built into something small enough to upload
that still has every call on the radio, and the logbook and app around it (linking a flight to its recording,
the Logbook's endpoints, the upload only when the pilot asks).
"""

import asyncio
import json
import shutil
from pathlib import Path

import msgspec
import pytest
from test_account import FakeServer, MemoryStore

from localtc.account import Account
from localtc.config import Config
from localtc.logbook import FlightRecord, Logbook
from localtc.replay import Recording
from localtc.replay.rewatch import (
    CACHE_FILE,
    at,
    build_replay,
    decode,
    encode,
    replay_for,
)
from localtc.sim_api import AtcTransmission, ReadbackEvaluated, Transcript
from localtc.ui.pilot import PilotRoutes
from localtc.ui.server import HttpError

FLIGHT = Path(__file__).parent / "fixtures" / "real_kden_ksea"


@pytest.fixture(scope="module")
def replay() -> dict:
    return build_replay(Recording(FLIGHT))


# --- the replay ---------------------------------------------------------------------------------------------------

def test_small_enough_to_upload(replay):
    data = encode(replay)
    assert len(data) < 150_000
    assert decode(data) == replay
    assert len(replay["track"]["t"]) < 4000  # thinned from ~10,000 samples


def test_the_track_runs_forward_in_step_and_in_range(replay):
    k = replay["track"]
    assert all(len(col) == len(k["t"]) for col in k.values())
    assert all(b > a for a, b in zip(k["t"], k["t"][1:], strict=False))
    assert k["t"][0] == 0 and k["t"][-1] <= replay["flight"]["duration_s"]
    assert all(0 <= h < 360 for h in k["hdg"]) and set(k["gnd"]) == {0, 1}


def test_takeoff_and_touchdown_are_both_sides_of_the_moment(replay):
    k = replay["track"]
    flips = [i for i in range(1, len(k["t"])) if k["gnd"][i] != k["gnd"][i - 1]]
    assert len(flips) == 2  # off the ground once, back on once
    landing = next(m for m in replay["marks"] if m["kind"] == "landing")
    assert abs(k["t"][flips[1]] - landing["t"]) < 2  # the touchdown point itself, not five seconds on
    assert landing["text"].startswith("Touchdown, -")


def test_every_call_on_the_radio_is_there_in_order(replay):
    events = list(Recording(FLIGHT).events())
    atc = [e.text for e in events if isinstance(e, AtcTransmission)]
    said = [e.text for e in events if isinstance(e, Transcript) and e.text]
    assert [line["text"] for line in replay["radio"] if line["kind"] == "atc"] == atc
    assert [line["text"] for line in replay["radio"] if line["kind"] in ("pilot", "copilot")] == said
    times = [line["t"] for line in replay["radio"]]
    assert times == sorted(times)


def test_readbacks_are_marked_on_the_pilots_line(replay):
    judged = sum(isinstance(e, ReadbackEvaluated) for e in Recording(FLIGHT).events())
    marked = [line for line in replay["radio"] if "ok" in line]
    assert len(marked) >= judged - 2  # one per readback (a readback ATC gave up on has no line of its own)
    clearance = next(line for line in marked if "ZIMMR3" in line["text"])
    assert clearance["ok"] is False and clearance["readback"] == "readback incomplete: squawk"
    assert next(line for line in marked if line["text"].startswith("Squawk 4553"))["ok"] is True
    assert not any(line["kind"] == "readback" for line in replay["radio"])


def test_nothing_private_or_heavy_goes_in(replay):
    assert set(replay) == {"v", "flight", "airports", "route", "track", "radio", "marks"}
    keys = {k for line in replay["radio"] for k in line}
    assert keys <= {"kind", "t", "station", "mhz", "text", "ok", "readback", "unclear"}  # no audio_ref, no model calls
    assert ".wav" not in json.dumps(replay)
    assert {line["kind"] for line in replay["radio"]} <= {"atc", "pilot", "copilot", "atis", "tuned", "alert", "phase"}


def test_the_timeline_has_the_moments_that_matter(replay):
    kinds = [m["kind"] for m in replay["marks"]]
    assert kinds.count("takeoff") == 1 and kinds.count("landing") == 1
    handoffs = [m["text"] for m in replay["marks"] if m["kind"] == "handoff"]
    # As flown on 2026-09-23 (before the fix: Denver Center straight to Seattle Center).
    assert handoffs[:3] == ["Denver Ground to Denver Tower", "Denver Tower to Denver Departure", "Denver Departure to Denver Center"]
    assert "Seattle Center to Seattle Approach" in handoffs
    assert "Runway entered without clearance" in [m["text"] for m in replay["marks"] if m["kind"] == "alert"]


def test_the_flight_is_named_and_placed(replay):
    f = replay["flight"]
    assert (f["callsign"], f["origin"], f["destination"]) == ("DAL2543", "KDEN", "KSEA")
    assert f["started_at"] == "2026-09-23T22:18:21Z"  # a minute before the first call, not when the sim opened
    assert set(replay["airports"]) == {"KDEN", "KSEA"}
    assert replay["route"][0]["ident"] == "MUGBE" and len(replay["route"]) > 10


def test_where_the_aircraft_was_at_a_moment(replay):
    k = replay["track"]
    i = at(k, 5000.0)
    assert k["t"][i] <= 5000.0 < k["t"][i + 1]
    assert k["alt"][i] > 35000  # cruising


def test_built_once_then_kept(tmp_path):
    rec = shutil.copytree(FLIGHT, tmp_path / "20260923-181635_live")
    first = replay_for(rec)
    assert (rec / CACHE_FILE).read_bytes() == first
    (rec / CACHE_FILE).write_bytes(encode({"v": 1, "cached": True}))
    assert decode(replay_for(rec)) == {"v": 1, "cached": True}  # from the cache, not rebuilt


# --- the logbook and the app ------------------------------------------------------------------------------------

def flight(**extra) -> FlightRecord:
    return FlightRecord(id="kden-ksea", started_at="2026-09-23T22:18:00Z", ended_at="2026-09-24T01:16:00Z",
                        callsign="DAL2543", origin="KDEN", destination="KSEA", air_min=149.4, landed=True, **extra)


def test_an_older_line_finds_its_recording(tmp_path):
    recordings = tmp_path / "recordings"
    shutil.copytree(FLIGHT, recordings / "20260923-181635_live")
    shutil.copytree(FLIGHT, recordings / "20260923-181635_replay")  # a replay of it: not the flight itself
    book = Logbook(tmp_path / "logbook.db")
    book.add(flight())
    book.add(FlightRecord(id="other", started_at="2026-09-20T10:00:00Z", ended_at="2026-09-20T11:00:00Z"))
    assert book.link_recordings(recordings) == 1
    assert book.get("kden-ksea").recording == str((recordings / "20260923-181635_live").resolve())
    assert book.get("other").recording in ("", None)


@pytest.fixture
def app(tmp_path):
    rec = shutil.copytree(FLIGHT, tmp_path / "recordings" / "20260923-181635_live")
    book = Logbook(tmp_path / "logbook.db")
    book.add(flight(recording=str(rec)))
    server = FakeServer()
    cfg = Config()
    cfg = msgspec.structs.replace(cfg, account=msgspec.structs.replace(cfg.account, api_url="https://api.test"),
                                  recorder=msgspec.structs.replace(cfg.recorder, dir=str(tmp_path / "recordings")))
    account = Account("https://api.test", store=MemoryStore(), transport=server, logbook=book)
    routes = PilotRoutes(lambda: cfg, lambda *_: None, logbook=book, account=account)
    return routes, server, book, account


def test_the_logbook_says_what_can_be_replayed_not_where_it_is(app):
    routes, *_ = app
    row = asyncio.run(routes.api_logbook({}))["flights"][0]
    assert row["has_recording"] is True and "recording" not in row
    played = asyncio.run(routes.api_replay({"id": "kden-ksea"}))
    assert played["flight"]["id"] == "kden-ksea" and played["radio"]
    with pytest.raises(HttpError):
        asyncio.run(routes.api_replay({"id": "nope"}))


def test_upload_syncs_the_line_first_and_needs_the_account(app):
    routes, server, _, account = app
    with pytest.raises(HttpError):
        asyncio.run(routes.api_replay_upload({"id": "kden-ksea"}))  # not signed in
    account.start("pilot@example.com")
    account.finish("pilot@example.com", "123456", "PC")
    row = asyncio.run(routes.api_replay_upload({"id": "kden-ksea"}))["flights"][0]
    assert "kden-ksea" in server.flights and decode(server.replays["kden-ksea"])["flight"]["callsign"] == "DAL2543"
    assert row["replay_uploaded_at"]
    row = asyncio.run(routes.api_replay_remove({"id": "kden-ksea"}))["flights"][0]
    assert server.replays == {} and row["replay_uploaded_at"] is None


@pytest.mark.parametrize("setting", [False, True])
def test_after_a_flight_the_replay_goes_up_only_with_the_setting(app, setting):
    routes, server, _, account = app
    account.start("pilot@example.com")
    account.finish("pilot@example.com", "123456", "PC")
    cfg = routes.cfg()
    cfg = msgspec.structs.replace(cfg, account=msgspec.structs.replace(cfg.account, upload_replays=setting))
    routes.cfg = lambda: cfg
    asyncio.run(routes.after_flight())
    assert "kden-ksea" in server.flights  # the line syncs either way
    assert ("kden-ksea" in server.replays) is setting
