"""Live Flights: the public map of pilots who chose to share. The client's heartbeat against a fake server, and the
app's gating: never without the account and the pilot's choice, never a replay, only the listed fields."""

import asyncio

import pytest

from localtc.account import SKY_EVERY_S, SKY_IDLE_S, SKY_KEYS, Account, AccountError, TokenStore
from localtc.config import Config
from localtc.ui.pilot import PilotRoutes


class MemoryStore(TokenStore):
    def __init__(self) -> None:
        super().__init__()
        self._keyring = lambda: None


class SkyServer:
    def __init__(self, mode: str = "off") -> None:
        self.mode = mode
        self.requests: list[tuple[str, str, object]] = []

    def __call__(self, method: str, url: str, body, headers: dict) -> tuple[int, object]:
        path = url.split("https://api.test", 1)[1]
        self.requests.append((method, path, body))
        if path == "/v1/me" and method == "GET":
            return 200, {"email": "p@example.com", "live_share": self.mode, "live_hide_route": False,
                         "live_hide_name": False, "live_delay_min": 0, "live_blocked": False}
        if path == "/v1/me" and method == "PATCH":
            self.mode = body.get("live_share", self.mode)
            return 200, {"display_name": "", "live_share": self.mode, "live_delay_min": body.get("live_delay_min", 0)}
        if path == "/v1/sky/flight" and method == "PUT":
            if self.mode == "off":
                return 403, {"ok": False, "mode": "off", "error": "Sharing is off."}
            return 200, {"ok": True, "mode": self.mode, "flight_id": "0123456789abcdef", "need_fixes": False}
        if path == "/v1/sky/flight" and method == "DELETE":
            if body.get("ended") and self.mode == "flight":
                self.mode = "off"
            return 200, {"ok": True, "mode": self.mode}
        return 404, {"error": "Not found."}

    def beats(self) -> list:
        return [b for m, p, b in self.requests if (m, p) == ("PUT", "/v1/sky/flight")]


FLIGHT = {"callsign": "ACA8272", "type": "A321", "airline": "AC",
          "fixes": [{"ident": "CYVR", "lat": 49.19, "lon": -123.18, "kind": "apt"}, {"ident": "KSEA", "lat": 47.45, "lon": -122.31, "kind": "apt"}], "from": "CYVR", "to": "KSEA", "from_ll": [49.194, -123.184], "to_ll": [47.449, -122.309], "route": "PAE3", "rules": "IFR",
          "phase": "cruise", "lat": 48.1, "lon": -122.9, "alt": 13000, "gs": 310, "hdg": 152, "ground": False,
          "station": "Seattle Center", "mhz": 132.6}


def account(server: SkyServer) -> Account:
    store = MemoryStore()
    store.set("token", "t")
    return Account("https://api.test", store=store, transport=server)


def test_nothing_goes_unless_the_pilot_chose_to_share():
    server = SkyServer("off")
    a = account(server)
    a.sharing_choices()
    assert a.sky(FLIGHT, now=0) is False
    assert server.beats() == []


def test_a_shared_flight_sends_only_the_listed_fields_every_ten_seconds():
    server = SkyServer("always")
    a = account(server)
    a.sharing_choices()
    assert a.sky({**FLIGHT, "email": "x", "voice": "y", "squawk": 1234}, now=0)
    assert set(server.beats()[0]) == set(SKY_KEYS)
    assert not a.sky({**FLIGHT, "lat": 48.2}, now=SKY_EVERY_S - 1)
    assert a.sky({**FLIGHT, "lat": 48.2}, now=SKY_EVERY_S)
    # Parked and not moving: still a heartbeat now and then, so the map keeps it.
    assert not a.sky({**FLIGHT, "lat": 48.2}, now=SKY_EVERY_S * 2)
    assert a.sky({**FLIGHT, "lat": 48.2}, now=SKY_EVERY_S + SKY_IDLE_S)
    assert a.sky_on_map


def test_turned_off_on_the_dashboard_turns_it_off_here():
    server = SkyServer("always")
    a = account(server)
    a.sharing_choices()
    a.sky(FLIGHT, now=0)
    server.mode = "off"
    with pytest.raises(AccountError):
        a.sky({**FLIGHT, "lat": 48.3}, now=SKY_EVERY_S)
    assert not a.sharing_on and not a.sky_on_map
    assert not a.sky(FLIGHT, now=SKY_EVERY_S * 3)


def test_this_flight_only_ends_with_the_flight():
    server = SkyServer("flight")
    a = account(server)
    a.sharing_choices()
    a.sky(FLIGHT, now=0)
    a.sky_stop(ended=True)
    assert ("DELETE", "/v1/sky/flight", {"ended": True}) in server.requests
    assert a.sharing["live_share"] == "off"


def routes(server: SkyServer, source: str = "live") -> PilotRoutes:
    cfg = Config()
    cfg.ui.source = source
    cfg.account.api_url = "https://api.test"  # the fake server, never the real one
    pilot = PilotRoutes(lambda: cfg, lambda kind, data: None, account=account(server))
    pilot.account.sharing_choices()
    return pilot


def test_the_app_never_shares_a_replay():
    server = SkyServer("always")
    asyncio.run(routes(server, "replay").sky(FLIGHT))
    assert server.beats() == []
    asyncio.run(routes(server).sky(FLIGHT))
    assert len(server.beats()) == 1


def test_stop_sharing_takes_the_flight_down_and_turns_it_off():
    server = SkyServer("always")
    pilot = routes(server)
    asyncio.run(pilot.sky(FLIGHT))
    view = asyncio.run(pilot.api_stop_sharing({}))
    assert view["live_share"] == "off" and not view["on_map"]
    assert ("DELETE", "/v1/sky/flight", {"ended": False}) in server.requests


def test_the_route_s_fixes_go_once_not_with_every_heartbeat():
    server = SkyServer("always")
    a = account(server)
    a.sharing_choices()
    a.sky(FLIGHT, now=0)
    a.sky({**FLIGHT, "lat": 48.2}, now=SKY_EVERY_S)
    first, second = server.beats()
    assert first["fixes"] == FLIGHT["fixes"] and "fixes" not in second
