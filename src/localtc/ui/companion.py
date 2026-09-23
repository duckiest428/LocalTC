"""The companion app's feed: what a phone shows of the flight, on the same network or through the account.

``CompanionHub`` keeps the picture a phone needs (the status, the aircraft, the traffic, the route, the
radio log) from what the app already publishes, and hands it out two ways:

- **On the same network**, a small server (``CompanionServer``) on port 47800 streams it directly, as
  Server-Sent Events, to a phone that has the key. The key is made fresh at every start and reaches the
  phone only through the pilot's own account. The server is found by Bonjour (``_localtc._tcp``) or by
  the addresses the account passes on. Nothing goes through the internet.
- **Away from that network**, through the account's relay: only while a phone is actually watching that
  way, and only if the pilot allows it (``[account] companion_remote_map``). The relay keeps it in memory.

The messages are the same either way; ``docs/companion-protocol.md`` describes them.
"""

import asyncio
import contextlib
import hmac
import ipaddress
import json
import logging
import platform
import secrets
import socket
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from localtc import __version__
from localtc.sim_api import AtcAlert, AtcTransmission
from localtc.ui.server import EventStream, _read_request, _write_response, sse

log = logging.getLogger(__name__)

PROTOCOL = 1
SERVICE = "_localtc._tcp.local."
RADIO_BACKLOG = 50
REMOTE_OWN_EVERY_S = 1.0
REMOTE_TRAFFIC_EVERY_S = 5.0
STATUS_KEYS = ("active", "callsign", "aircraft", "origin", "destination", "phase", "phase_label", "squawk",
               "altitude_ft", "runway", "tuned", "next", "ete", "last_atc", "gate", "rules")

# What deserves a banner on the phone. Handoffs and the clearances that change what the pilot does next;
# not every "roger".
CLEARANCE_IDS = {
    "clearance.ifr", "clearance.ifr_at_cruise", "clearance.ifr_sid", "clearance.ifr_sid_at_cruise",
    "ground.taxi_out", "ground.taxi_out_at", "ground.taxi_out_hold_short", "ground.taxi_in", "ground.taxi_to_gate",
    "ground.cross_runway", "ground.pushback", "ground.pushback_straight", "ground.hold_position",
    "tower.takeoff", "tower.luaw", "tower.hold_short_traffic", "tower.land", "tower.go_around",
    "tower.go_around_traffic", "approach.cleared", "approach.missed", "approach.vectors", "approach.descend",
    "center.descend", "center.descend_pd", "center.descend_via", "center.radar_contact", "departure.radar_contact",
    "common.climb", "common.descend", "common.direct",
}
TRAFFIC_IDS = {"common.traffic", "tower.sequence"}
EMERGENCY_IDS = {"common.emergency_copied", "common.emergency_priority", "common.emergency_approach",
                 "common.emergency_land", "common.emergency_intentions"}


def alert_for(ev: object) -> dict | None:
    """The phone's banner for an event, or None."""
    if isinstance(ev, AtcAlert) and ev.kind == "emergency":
        return {"kind": "emergency", "title": "Emergency", "body": ev.detail or "Emergency declared"}
    if not isinstance(ev, AtcTransmission) or not ev.instruction_id:
        return None
    iid = ev.instruction_id
    if iid in EMERGENCY_IDS:
        kind = "emergency"
    elif ".handoff_" in iid or iid in ("common.contact", "tower.exit_contact_ground"):
        kind = "handoff"
    elif iid in TRAFFIC_IDS:
        kind = "traffic"
    elif iid in CLEARANCE_IDS:
        kind = "clearance"
    else:
        return None
    return {"kind": kind, "title": ev.station, "body": ev.text, "mhz": ev.frequency_mhz}


def lan_addresses() -> list[str]:
    """This computer's private IPv4 addresses: where a phone on the same network can reach it."""
    found: list[str] = []
    try:
        import ifaddr

        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                if isinstance(ip.ip, str) and ipaddress.ip_address(ip.ip).is_private and not ip.ip.startswith("127."):
                    found.append(ip.ip)
    except ImportError:
        pass
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))  # no packet is sent; this just picks the outgoing interface
        primary = s.getsockname()[0]
        if ipaddress.ip_address(primary).is_private and not primary.startswith("127."):
            found.insert(0, primary)
    return list(dict.fromkeys(a for a in found if not a.startswith("169.254.")))


class CompanionHub:
    def __init__(self) -> None:
        self.key = secrets.token_urlsafe(32)
        self.stream = EventStream()  # phones on the local network
        self.status: dict = {"active": False}
        self.own: dict | None = None
        self.traffic: list[dict] = []
        self.route: dict | None = None
        self.radio: deque[dict] = deque(maxlen=RADIO_BACKLOG)
        self.remote: Callable[[str, Any], None] | None = None  # to the relay; set while signed in
        self.remote_map = True
        self.remote_watchers = 0
        self._sent_own = 0.0
        self._sent_traffic = 0.0
        self._backlog_sent = False

    # --- what the app publishes ----------------------------------------------------------------------------

    def set_status(self, status: dict) -> None:
        self.status = {k: status.get(k) for k in STATUS_KEYS if k in status}
        self._local("status", self.status)

    def set_own(self, own: dict) -> None:
        self.own = own
        self._local("own", own)
        if self._remote_ok() and time.monotonic() - self._sent_own >= REMOTE_OWN_EVERY_S:
            self._sent_own = time.monotonic()
            self._send("frame", {"own": own})

    def set_traffic(self, traffic: list[dict]) -> None:
        self.traffic = traffic
        self._local("traffic", traffic)
        if self._remote_ok() and time.monotonic() - self._sent_traffic >= REMOTE_TRAFFIC_EVERY_S:
            self._sent_traffic = time.monotonic()
            self._send("frame", {"traffic": traffic})

    def set_route(self, route: dict | None) -> None:
        self.route = route
        self._local("route", route)

    def add_radio(self, line: dict) -> None:
        self.radio.append(line)
        self._local("radio", line)
        if self._remote_ok():
            self._send("radio", [line])

    def on_event(self, ev: object) -> None:
        alert = alert_for(ev)
        if alert is not None:
            self._local("alert", alert)
            if self.remote_watchers > 0:
                self._send("alert", alert)

    def watching(self, watchers: int) -> None:
        """How many phones follow through the relay, from its latest answer. A new one gets the radio log."""
        was = self.remote_watchers
        self.remote_watchers = watchers
        if watchers == 0:
            self._backlog_sent = False
        elif self._remote_ok() and (was == 0 or not self._backlog_sent):
            self._backlog_sent = True
            self._send("radio", list(self.radio))
            if self.own is not None:
                self._send("frame", {"own": self.own, "traffic": self.traffic})

    def snapshot(self) -> dict:
        return {"protocol": PROTOCOL, "version": __version__, "status": self.status, "own": self.own,
                "traffic": self.traffic, "route": self.route, "radio": list(self.radio)}

    def _local(self, kind: str, data: Any) -> None:
        self.stream.publish(kind, data)

    def _remote_ok(self) -> bool:
        return self.remote is not None and self.remote_map and self.remote_watchers > 0

    def _send(self, kind: str, data: Any) -> None:
        if self.remote is not None:
            self.remote(kind, data)


class CompanionServer:
    """The phone's direct line on the local network: an SSE stream and the ATC zones, behind the key."""

    def __init__(self, hub: CompanionHub, zones: Callable[[dict], Any] | None = None) -> None:
        self.hub = hub
        self.zones = zones
        self._server: asyncio.base_events.Server | None = None
        self._zeroconf: Any = None
        self._clients: set[asyncio.Task] = set()  # open connections, closed with the server
        self.port = 0

    async def start(self, port: int = 47800, host: str = "0.0.0.0", *, advertise: bool = True) -> int:
        try:
            self._server = await asyncio.start_server(self._handle, host, port)
        except OSError:  # taken: any free port (Bonjour and the account still tell the phone where)
            self._server = await asyncio.start_server(self._handle, host, 0)
        self.port = self._server.sockets[0].getsockname()[1]
        if advertise:
            await self._advertise()
        return self.port

    def urls(self) -> list[str]:
        return [f"http://{ip}:{self.port}" for ip in lan_addresses()]

    async def close(self) -> None:
        if self._zeroconf is not None:
            with contextlib.suppress(Exception):
                await self._zeroconf.async_close()
            self._zeroconf = None
        if self._server is not None:
            self._server.close()
            for task in list(self._clients):  # a phone's open stream would otherwise hold the close up
                task.cancel()
            await self._server.wait_closed()
            self._server = None

    async def _advertise(self) -> None:
        try:
            from zeroconf import Error as ZeroconfError
            from zeroconf import ServiceInfo
            from zeroconf.asyncio import AsyncZeroconf
        except ImportError:
            log.info("zeroconf isn't installed: phones find LocalTC through the account only")
            return
        addresses = [socket.inet_aton(ip) for ip in lan_addresses()]
        if not addresses:
            return
        name = f"LocalTC on {platform.node() or 'this PC'}".replace(".", "-")[:60]
        info = ServiceInfo(SERVICE, f"{name}.{SERVICE}", addresses=addresses, port=self.port,
                           properties={"protocol": str(PROTOCOL), "version": __version__})
        try:
            self._zeroconf = AsyncZeroconf()
            await self._zeroconf.async_register_service(info)
        except (OSError, ZeroconfError) as exc:  # no multicast here: the account's addresses still work
            log.info("Bonjour advertising failed: %s", exc)
            self._zeroconf = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._clients.add(task)
        try:
            request = await _read_request(reader)
            if request is None:
                return
            method, target, headers, _ = request
            path = target.partition("?")[0]
            given = headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(given.encode(), self.hub.key.encode()):
                _write_response(writer, 401, "application/json", b'{"error":"sign in to LocalTC on the phone"}', keep_alive=False)
                await writer.drain()
                return
            if method == "GET" and path == "/companion/v1/stream":
                await self._stream(writer)
            elif method == "GET" and path == "/companion/v1/hello":
                body = json.dumps({"protocol": PROTOCOL, "version": __version__, "name": platform.node()}).encode()
                _write_response(writer, 200, "application/json", body, keep_alive=False)
            elif method == "GET" and path == "/companion/v1/zones" and self.zones is not None:
                body = json.dumps(await self.zones({}), default=str).encode()
                _write_response(writer, 200, "application/json", body, keep_alive=False)
            else:
                _write_response(writer, 404, "application/json", b'{"error":"not found"}', keep_alive=False)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        except Exception:
            log.exception("Companion request failed")
        finally:
            writer.close()
            self._clients.discard(task)

    async def _stream(self, writer: asyncio.StreamWriter) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
                     b"Connection: keep-alive\r\n\r\n")
        queue = self.hub.stream.open()
        try:
            writer.write(sse("hello", self.hub.snapshot()))
            await writer.drain()
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    message = b": keep-alive\n\n"
                writer.write(message)
                await writer.drain()
        finally:
            self.hub.stream.close(queue)
