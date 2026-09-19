"""The app: saved settings, flight plans (SimBrief and typed), model profiles, the local server and the
controller that turns bus events into what the page shows."""

import asyncio
import json
import tomllib
import urllib.request
import zipfile
from pathlib import Path

import msgspec
import pytest

from localtc import models
from localtc.config import Config, diff, dump_toml, load_config, merge, save_settings
from localtc.flightplan import FlightPlan, FlightPlanError, apply_plan, manual_plan, parse_altitude, parse_simbrief, simbrief_url
from localtc.sim_api import AtcTransmission, PhaseChanged, ReadbackEvaluated, SessionNote, Transcript
from localtc.ui.controller import AppController, export_report, radio_line
from localtc.ui.server import AppServer, EventStream

ROOT = Path(__file__).parents[1]

# --- settings --------------------------------------------------------------------------------------------------


def test_settings_file_holds_only_changes_and_loads_on_top(tmp_path):
    base = load_config(ROOT / "config" / "localtc.toml", env={}, settings=None)
    cfg = msgspec.convert(msgspec.to_builtins(base), Config)
    cfg.voice.ptt_key = "f13"
    cfg.tts.volume = 0.5
    cfg.ui.dev_mode = True
    path = save_settings(cfg, base=base, path=tmp_path / "settings.toml")
    saved = tomllib.loads(path.read_text())
    assert saved == {"voice": {"ptt_key": "f13"}, "tts": {"volume": 0.5}, "ui": {"dev_mode": True}}
    again = load_config(ROOT / "config" / "localtc.toml", env={}, settings=path)
    assert again.voice.ptt_key == "f13" and again.ui.dev_mode and again.tts.volume == 0.5
    assert again.llm.model == base.llm.model


def test_toml_writer_round_trips():
    data = {"a": 1, "b": "quote \" and \\ slash", "c": [1.5, 2], "d": {"e": True, "f": {"g": "h"}}, "weird key": "x"}
    assert tomllib.loads(dump_toml(data)) == data
    assert diff({"a": {"b": 1, "c": 2}}, {"a": {"b": 1, "c": 3}}) == {"a": {"c": 3}}
    assert merge({"a": {"b": 1, "c": 2}}, {"a": {"c": 3}}) == {"a": {"b": 1, "c": 3}}


# --- flight plans --------------------------------------------------------------------------------------------------

SIMBRIEF = {
    "fetch": {"userid": "123", "status": "Success"},
    "params": {"static_id": {}, "time_generated": "1758300000"},
    "general": {"icao_airline": "ASA", "flight_number": "123", "initial_altitude": "35000", "route": "HAROB6 HAROB  Q7  JINMO"},
    "origin": {"icao_code": "KSEA", "plan_rwy": "16L", "name": "SEATTLE-TACOMA INTL"},
    "destination": {"icao_code": "KLAX", "plan_rwy": "24R", "name": "LOS ANGELES INTL"},
    "alternate": {"icao_code": "KONT"},
    "atc": {"callsign": "ASA123"},
    "aircraft": {"icaocode": "B738", "reg": "N612AS"},
    "navlog": {"fix": [
        {"ident": "HAROB", "type": "wpt", "pos_lat": "47.1", "pos_long": "-122.3", "altitude_feet": "12000",
         "via_airway": "HAROB6", "is_sid_star": "1", "stage": "CLB"},
        {"ident": "JINMO", "type": "wpt", "pos_lat": "40.0", "pos_long": "-120.0", "altitude_feet": "35000",
         "via_airway": "Q7", "is_sid_star": "0", "stage": "CRZ"},
        {"ident": "SADDE", "type": "wpt", "pos_lat": "34.0", "pos_long": "-118.5", "altitude_feet": "9000",
         "via_airway": "SADDE8", "is_sid_star": "1", "stage": "DSC"},
    ]},
}


def test_simbrief_plan_is_read():
    plan = parse_simbrief(SIMBRIEF)
    assert (plan.callsign, plan.origin, plan.destination, plan.alternate, plan.cruise_ft) == ("ASA123", "KSEA", "KLAX", "KONT", 35000)
    assert (plan.sid, plan.star, plan.dep_runway, plan.arr_runway, plan.aircraft) == ("HAROB6", "SADDE8", "16L", "24R", "B738")
    assert plan.route == "HAROB6 HAROB Q7 JINMO" and len(plan.fixes) == 3 and plan.fixes[1].lat == 40.0
    assert plan.simbrief_id == "1758300000"  # static_id is empty ({}), the generation time stands in
    assert plan.summary() == "ASA123 KSEA-KLAX FL350"


def test_simbrief_errors_and_urls():
    with pytest.raises(FlightPlanError, match="Unknown UserID"):
        parse_simbrief({"fetch": {"status": "Error: Unknown UserID"}})
    assert simbrief_url("123456").endswith("?userid=123456&json=1")
    assert simbrief_url(" pilot name ").endswith("?username=pilot+name&json=1")
    with pytest.raises(FlightPlanError):
        simbrief_url("")


def test_manual_plan_checks_and_applies():
    plan = manual_plan(callsign="n172-lt", origin="kpae", destination="kbfi", cruise="5,500")
    assert (plan.callsign, plan.origin, plan.destination, plan.cruise_ft) == ("N172LT", "KPAE", "KBFI", 5500)
    assert [parse_altitude(x) for x in ("FL350", "350", "35000", "3500ft", "")] == [35000, 35000, 35000, 3500, 0]
    for bad in ({"destination": ""}, {"destination": "SEATTLE"}, {"destination": "KSEA", "cruise": "high"},
                {"destination": "KSEA", "callsign": "N 1!"}):
        with pytest.raises(FlightPlanError):
            manual_plan(**bad)
    cfg = Config()
    apply_plan(plan, cfg.flight)
    assert (cfg.flight.destination, cfg.flight.cruise_ft, cfg.flight.callsign) == ("KBFI", 5500, "N172LT")


# --- models ------------------------------------------------------------------------------------------------------


def test_profiles_follow_the_hardware():
    assert models.recommend(models.Hardware("win32", 16, 32.0, "RTX 4070", 12.0)).id == "quality"
    assert models.recommend(models.Hardware("win32", 12, 32.0)).id == "balanced"
    assert models.recommend(models.Hardware("win32", 4, 8.0)).id == "light"
    cfg = Config()
    models.apply_profile(cfg, models.profile("light"))
    assert (cfg.llm.model, cfg.voice.model) == ("llama3.2:1b", "tiny.en")
    catalog = models.catalog()
    assert {"llm", "whisper", "voice", "profiles"} <= catalog.keys()
    assert all(p["llm"] in {m["id"] for m in catalog["llm"]} for p in catalog["profiles"])


# --- the page's view of events ----------------------------------------------------------------------------------------


def test_radio_lines():
    atc = radio_line(AtcTransmission(t=5.0, station="Paine Tower", frequency_mhz=120.2, text="N172LT, cleared for takeoff."))
    assert atc == {"kind": "atc", "t": 5.0, "station": "Paine Tower", "mhz": 120.2, "text": "N172LT, cleared for takeoff."}
    assert radio_line(Transcript(t=6.0, text="cleared for takeoff", source="copilot"))["kind"] == "copilot"
    assert radio_line(Transcript(t=6.0, text=""))["kind"] == "system"
    bad = radio_line(ReadbackEvaluated(t=7.0, instruction_id="x", status="incorrect", mismatched={"runway": "34R"}))
    assert bad["ok"] is False and bad["text"] == "readback incorrect: runway 34R"
    assert radio_line(PhaseChanged(t=8.0, previous="PARKED", phase="TAXI_OUT"))["text"] == "Taxiing"
    assert radio_line(SessionNote(t=9.0, text="wrong runway"))["kind"] == "note"


def test_export_report_zips_the_recording(tmp_path):
    session = tmp_path / "20260919-test"
    (session / "audio").mkdir(parents=True)
    (session / "session.jsonl").write_text('{"type":"header"}\n')
    (session / "audio" / "0001.wav").write_bytes(b"RIFF")
    plan = tmp_path / "flightplan.json"
    plan.write_text("{}")
    path = export_report(session, plan, out_dir=tmp_path)
    names = zipfile.ZipFile(path).namelist()
    assert "recording/session.jsonl" in names and "recording/audio/0001.wav" in names
    assert "flightplan.json" in names and "README.txt" in names


# --- the server and a whole flight through the controller --------------------------------------------------------------


def _get(url: str) -> tuple[int, dict | str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            body = r.read().decode()
            return r.status, json.loads(body) if r.headers.get_content_type() == "application/json" else body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        return exc.code, json.loads(body) if exc.headers.get_content_type() == "application/json" else body


def _post(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_the_app_flies_a_replay_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALTC_SETTINGS", str(tmp_path / "settings.toml"))
    cfg = load_config(ROOT / "config" / "localtc.toml", env={}, settings=None)
    cfg.source.kind, cfg.replay.path, cfg.replay.speed = "replay", str(ROOT / "tests/fixtures/ifr_kpae_kbfi"), 0.0
    cfg.replay.end_at = 60.0
    cfg.ui.source = "replay"  # the app's own setting; config/localtc.toml's [source] never applies to it
    cfg.voice.enabled = cfg.tts.enabled = cfg.recorder.enabled = cfg.llm.enabled = False
    controller = AppController(cfg, plan_path=tmp_path / "flightplan.json", cache=None)
    lines: list[dict] = []

    async def main() -> None:
        controller.attach(asyncio.get_running_loop())
        server = AppServer(controller.routes(), controller.stream, controller.on_connect)
        port = await server.start(0)
        base = f"http://127.0.0.1:{port}"
        queue = controller.stream.open()
        call = lambda *a: asyncio.to_thread(*a)  # noqa: E731
        status, page = await call(_get, base + "/")
        assert status == 200 and "<title>LocalTC</title>" in page
        assert (await call(_get, base + "/static/../../app.py"))[0] == 404
        status, body = await call(_post, base + "/api/flight/plan", {"destination": "KBFI", "cruise": "7000",
                                                                      "callsign": "N172LT", "start": True})
        assert status == 200 and body["plan"]["cruise_ft"] == 7000
        for _ in range(100):
            if controller.status in ("running", "idle", "error") and controller._task.done():
                break
            await asyncio.sleep(0.05)
        await controller._task
        while not queue.empty():
            message = queue.get_nowait().decode()
            if message.startswith("event: radio\n"):
                lines.append(json.loads(message.split("data: ", 1)[1]))
        status, state = await call(_get, base + "/api/state")
        assert state["status"] == "idle" and state["plan"]["destination"] == "KBFI"
        assert (await call(_post, base + "/api/radio/say", {"text": "hello"}))[0] == 409  # no flight running
        status, found = await call(_get, base + "/api/airports/search?q=KP")
        assert status == 200
        await server.close()
        controller.detach()

    asyncio.run(main())
    assert any(line["kind"] == "tuned" and "Paine" in line["text"] for line in lines)
    assert (tmp_path / "flightplan.json").is_file()


def test_event_stream_drops_nothing_for_a_reader_and_forgets_closed_pages():
    stream = EventStream()
    queue = stream.open()
    stream.publish("radio", {"a": 1})
    assert queue.get_nowait() == b'event: radio\ndata: {"a":1}\n\n'
    stream.close(queue)
    stream.publish("radio", {"a": 2})
    assert stream.listeners == 0


def test_flight_plan_round_trips_through_json(tmp_path):
    from localtc.flightplan import load_plan, save_plan

    plan = parse_simbrief(SIMBRIEF)
    save_plan(plan, tmp_path / "p.json")
    assert load_plan(tmp_path / "p.json") == plan
    assert load_plan(tmp_path / "missing.json") is None
    assert isinstance(msgspec.convert(msgspec.to_builtins(plan), FlightPlan), FlightPlan)


def test_the_app_flies_the_sim_whatever_the_config_file_says(tmp_path):
    cfg = Config()
    cfg.source.kind = "replay"  # config/localtc.toml's development default, and developer mode on: still live
    cfg.ui.dev_mode = True
    controller = AppController(cfg, plan_path=tmp_path / "plan.json")
    assert controller.flight_config().source.kind == "live" and controller.state()["source"] == "live"
    cfg.ui.source = "replay"  # only the app's own setting switches it
    assert controller.flight_config().source.kind == "replay"
