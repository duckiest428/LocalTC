"""The companion app's feed: which ATC calls become banners, the local-network server, and the relay's rules."""

import asyncio
import json
from pathlib import Path

import pytest

from localtc.replay import Recording
from localtc.sim_api import AtcAlert, AtcTransmission
from localtc.ui.companion import CompanionHub, CompanionServer, alert_for

FLIGHT = Path(__file__).parent / "fixtures" / "real_ksan_kphx"


def transmissions() -> list[AtcTransmission]:
    return [e for e in Recording(FLIGHT).events() if isinstance(e, AtcTransmission)]


def test_the_flights_handoffs_and_clearances_become_banners():
    alerts = [a for e in transmissions() if (a := alert_for(e))]
    kinds = {a["kind"] for a in alerts}
    assert {"handoff", "clearance"} <= kinds
    handoffs = [a["body"] for a in alerts if a["kind"] == "handoff"]
    assert any("Los Angeles Center" in b or "Socal Departure" in b for b in handoffs)
    assert any("cleared to land" in a["body"] for a in alerts if a["kind"] == "clearance")


def test_small_talk_is_not_a_banner():
    roger = AtcTransmission(t=1.0, station="Phoenix Tower", frequency_mhz=118.7, text="Frontier 2084, roger.",
                            instruction_id="common.roger")
    assert alert_for(roger) is None


def test_an_emergency_is_a_banner():
    assert alert_for(AtcAlert(t=1.0, kind="emergency", detail="engine failure"))["kind"] == "emergency"


class Relay:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    def __call__(self, kind: str, data: object) -> None:
        self.sent.append((kind, data))


def test_nothing_goes_through_the_server_while_no_phone_watches_that_way():
    hub, relay = CompanionHub(), Relay()
    hub.remote = relay
    hub.set_own({"lat": 33.0, "lon": -117.0})
    hub.set_traffic([{"id": 1}])
    hub.add_radio({"kind": "atc", "text": "Frontier 2084, roger."})
    hub.on_event(AtcAlert(t=1.0, kind="emergency", detail="x"))
    assert relay.sent == []


def test_a_phone_watching_remotely_gets_the_backlog_then_the_stream():
    hub, relay = CompanionHub(), Relay()
    hub.remote = relay
    hub.set_own({"lat": 33.0, "lon": -117.0})
    hub.add_radio({"kind": "atc", "text": "one"})
    hub.add_radio({"kind": "pilot", "text": "two"})
    hub.watching(1)
    assert relay.sent[0] == ("radio", [{"kind": "atc", "text": "one"}, {"kind": "pilot", "text": "two"}])
    assert relay.sent[1][0] == "frame"
    hub.add_radio({"kind": "atc", "text": "three"})
    assert relay.sent[-1] == ("radio", [{"kind": "atc", "text": "three"}])


def test_a_map_opened_mid_flight_gets_the_path_flown_so_far():
    hub, relay = CompanionHub(), Relay()
    hub.remote = relay
    hub.set_status({"active": True})
    for i in range(50):  # 50 positions about 1 km apart, all before anyone watches
        hub.set_own({"lat": 33.0 + i * 0.01, "lon": -117.0})
    hub.set_own({"lat": 33.49001, "lon": -117.0})  # hardly moved: not a new point
    assert len(hub.trail) == 50 and hub.snapshot()["trail"] == hub.trail  # the local stream's hello has it
    hub.watching(1)
    assert ("frame", {"trail": hub.trail}) in relay.sent  # and the relay gets it for the Flight Tracker
    hub.set_status({"active": False})
    hub.set_status({"active": True})
    assert hub.trail == []  # the next flight starts clean


def test_the_route_and_the_atc_zones_go_to_a_phone_and_the_flight_tracker():
    from localtc.ui.companion import compact_zones

    hub, relay = CompanionHub(), Relay()
    hub.remote = relay
    route = {"origin": "KLAX", "destination": "LFPG", "fixes": [{"ident": "DOTSS", "lat": 33.9, "lon": -118.4, "kind": "wpt"}]}
    zones = compact_zones({
        "centers": [{"id": "KZLA", "name": "Los Angeles Center", "label": [34.123456, -117.0],
                     "rings": [[[33.00001, -118.00001], [33.00002, -118.00002], [35.0, -118.0]]]}],
        "terminals": [], "final": None, "taxi": {"points": [[33.9412345, -118.4012345]], "to": "runway 25R", "taxiways": []},
        "airports": [], "classes": []})
    assert zones["centers"][0]["rings"] == [[[33.0, -118.0], [35.0, -118.0]]]  # rounded, the repeat dropped
    assert zones["centers"][0]["label"] == [34.123, -117.0] and zones["taxi"]["points"] == [[33.94123, -118.40123]]
    hub.set_route(route)
    hub.set_zones(zones)
    assert relay.sent == []  # nobody watching through the server yet
    assert hub.snapshot()["route"] == route and hub.snapshot()["zones"] == zones  # the local stream's hello has them
    hub.watching(1)
    assert ("route", route) in relay.sent and ("zones", zones) in relay.sent
    relay.sent.clear()
    hub.set_zones(dict(zones))  # the same again: not resent
    assert relay.sent == []
    hub.set_zones(None)
    assert relay.sent == [("zones", None)]


def test_a_long_flight_keeps_its_whole_shape():
    from localtc.ui.companion import TRAIL_KEEP

    hub = CompanionHub()
    hub.set_status({"active": True})
    for i in range(TRAIL_KEEP * 3):
        hub.set_own({"lat": i * 0.003, "lon": 0.0})
    assert len(hub.trail) <= TRAIL_KEEP and hub.trail[0] == [0.0, 0.0] and hub.trail[-1][0] == round((TRAIL_KEEP * 3 - 1) * 0.003, 4)


def test_the_remote_map_can_be_turned_off():
    hub, relay = CompanionHub(), Relay()
    hub.remote, hub.remote_map = relay, False
    hub.watching(1)
    hub.set_own({"lat": 33.0, "lon": -117.0})
    assert relay.sent == []


async def fetch(port: int, path: str, key: str | None) -> tuple[int, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    auth = f"Authorization: Bearer {key}\r\n" if key else ""
    writer.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n{auth}\r\n".encode())
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    status = int(head.split(b" ")[1])
    body = await asyncio.wait_for(reader.read(4096), timeout=2)
    writer.close()
    return status, body


@pytest.mark.parametrize("key", [None, "wrong"])
def test_the_local_server_wants_the_key(key):
    async def go() -> int:
        server = CompanionServer(CompanionHub())
        port = await server.start(0, "127.0.0.1", advertise=False)
        try:
            return (await fetch(port, "/companion/v1/stream", key))[0]
        finally:
            await server.close()

    assert asyncio.run(go()) == 401


def test_the_local_stream_starts_with_the_whole_picture():
    async def go() -> dict:
        hub = CompanionHub()
        hub.set_status({"active": True, "callsign": "FFT2084", "phase": "CRUISE", "lat": 1})
        hub.set_own({"lat": 33.0, "lon": -117.0})
        hub.set_route({"origin": "KSAN", "destination": "KPHX", "fixes": []})
        hub.add_radio({"kind": "atc", "text": "Frontier 2084, roger."})
        server = CompanionServer(hub)
        port = await server.start(0, "127.0.0.1", advertise=False)
        try:
            status, body = await fetch(port, "/companion/v1/stream", hub.key)
            assert status == 200
            event, data = body.decode().split("\n")[:2]
            assert event == "event: hello"
            return json.loads(data.removeprefix("data: "))
        finally:
            await server.close()

    hello = asyncio.run(go())
    assert hello["status"] == {"active": True, "callsign": "FFT2084", "phase": "CRUISE"}  # nothing unlisted
    assert hello["own"]["lat"] == 33.0 and hello["route"]["destination"] == "KPHX"
    assert hello["radio"][0]["text"] == "Frontier 2084, roger."


KPHX = {"icao": "KPHX", "name": "Phoenix Sky Harbor", "role": "arrival", "lat": 33.43, "lon": -112.01, "elev_ft": 1135,
        "atis": "D", "frequencies": [{"label": "TWR", "kind": "tower", "mhz": 118.7, "name": "Phoenix Tower"}],
        "runways": [{"name": "08/26", "length_ft": 11489, "heading_mag": 76, "ils": ["26 (IPHX)"]}]}


def test_the_airports_go_out_once_and_again_to_a_new_phone():
    hub, relay = CompanionHub(), Relay()
    hub.remote = relay
    hub.set_airports([KPHX])
    assert relay.sent == []  # nobody watching remotely
    hub.watching(1)
    assert ("airports", [KPHX]) in relay.sent
    relay.sent.clear()
    hub.set_airports([KPHX])  # unchanged: nothing new to send
    assert relay.sent == []
    assert hub.snapshot()["airports"] == [KPHX]


async def post(port: int, path: str, key: str, body: dict) -> tuple[int, dict]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    payload = json.dumps(body).encode()
    writer.write(f"POST {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {key}\r\nContent-Type: application/json\r\n"
                 f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    data = await asyncio.wait_for(reader.read(4096), timeout=2)
    writer.close()
    return int(head.split(b" ")[1]), json.loads(data)


def test_a_call_typed_on_the_phone_is_transmitted():
    said: list[str] = []

    def say(text: str) -> None:
        if text == "boom":
            raise RuntimeError("start a flight first")
        said.append(text)

    async def go():
        hub = CompanionHub()
        server = CompanionServer(hub, say=say)
        port = await server.start(0, "127.0.0.1", advertise=False)
        try:
            ok = await post(port, "/companion/v1/say", hub.key, {"text": "Phoenix Approach, Frontier 2084, with you"})
            empty = await post(port, "/companion/v1/say", hub.key, {"text": "  "})
            idle = await post(port, "/companion/v1/say", hub.key, {"text": "boom"})
            return ok, empty, idle
        finally:
            await server.close()

    ok, empty, idle = asyncio.run(go())
    assert ok == (200, {"ok": True}) and said == ["Phoenix Approach, Frontier 2084, with you"]
    assert empty[0] == 400
    assert idle == (409, {"error": "start a flight first"})


def test_calls_through_the_account_are_transmitted_and_a_failed_one_is_said():
    """A call typed on the phone (away from the PC's Wi-Fi) or the website's Flight Tracker goes out as if typed in
    the app; one that can't (no flight running) comes back in the radio log so whoever typed it knows."""
    from localtc.ui.pilot import PilotRoutes

    class Account:
        def __init__(self) -> None:
            self.calls = ["Socal Approach, ACA795, request vectors"]

        def take_calls(self) -> list[str]:
            calls, self.calls = self.calls, []
            return calls

    hub = CompanionHub()
    pilot = PilotRoutes(lambda: None, lambda kind, data: None, account=Account(), hub=hub)
    said: list[str] = []
    pilot.on_call = said.append
    pilot._deliver_calls()
    assert said == ["Socal Approach, ACA795, request vectors"]

    def nothing_flying(text: str) -> None:
        raise RuntimeError("start a flight first")

    pilot._account.calls = ["radio check"]
    pilot.on_call = nothing_flying
    pilot._deliver_calls()
    assert hub.radio[-1]["kind"] == "alert" and "Not transmitted: start a flight first" in hub.radio[-1]["text"]
