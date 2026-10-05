"""A small HTTP server for the app, on 127.0.0.1 only. Standard library, asyncio.

- ``GET /`` and ``/static/...``: the page (``ui/static``).
- ``GET /api/<name>`` and ``POST /api/<name>`` (JSON body): the controller's handlers.
- ``GET /api/events``: a Server-Sent Events stream of everything the page shows live.

Nothing listens on the network: the page and the server are the same program, like a desktop app.
"""

import asyncio
import json
import logging
import mimetypes
import urllib.parse
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, NamedTuple

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
MAX_BODY = 2_000_000  # a shared card's picture, as a data URL, is the biggest thing a page sends
# Types that mustn't come from the machine's own table: Windows registries map .js to text/plain now and then,
# and a browser won't run a module ("import('./cardmodel.js')") served as that.
TYPES = {".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".woff2": "font/woff2", ".svg": "image/svg+xml"}
Handler = Callable[[dict[str, Any]], Awaitable[Any]]


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Raw(NamedTuple):
    """A call's answer that isn't JSON: its type and its bytes."""

    content_type: str
    body: bytes


BACKLOG = 2000  # messages a page that stopped reading may fall behind before it's caught up from scratch


class EventStream:
    """Fans events out to every open page.

    A page that stops reading (a window the sim covers, which WebView2 freezes; a phone app in the background) falls
    behind. Past BACKLOG messages its queue is emptied and refilled with ``catch_up()``: the whole current picture,
    the path flown included. Dropping the newest messages instead lost hours of the flight (the map drew one straight
    line across them) and the radio lines in between."""

    def __init__(self, catch_up: Callable[[], list[bytes]] | None = None) -> None:
        self._queues: set[asyncio.Queue] = set()
        self.catch_up = catch_up

    def publish(self, kind: str, data: Any) -> None:
        message = sse(kind, data)
        for queue in list(self._queues):
            if queue.qsize() >= BACKLOG:
                while not queue.empty():
                    queue.get_nowait()
                for caught_up in (self.catch_up() if self.catch_up else []):
                    queue.put_nowait(caught_up)
            queue.put_nowait(message)

    def open(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._queues.add(queue)
        return queue

    def close(self, queue: asyncio.Queue) -> None:
        self._queues.discard(queue)

    @property
    def listeners(self) -> int:
        return len(self._queues)


class AppServer:
    def __init__(self, routes: dict[tuple[str, str], Handler], stream: EventStream,
                 on_connect: Callable[[], list[bytes]] | None = None, static_dir: Path = STATIC) -> None:
        self.routes = routes
        self.stream = stream
        self.on_connect = on_connect  # what a newly opened page needs first (the current state)
        self.static_dir = static_dir.resolve()
        self._server: asyncio.base_events.Server | None = None
        self.port = 0

    async def start(self, port: int = 0) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:  # keep-alive: a page makes many small requests
                request = await _read_request(reader)
                if request is None:
                    return
                method, target, headers, body = request
                path, _, query = target.partition("?")
                if path == "/api/events":
                    await self._events(writer)
                    return
                status, content_type, payload = await self._respond(method, path, query, body)
                _write_response(writer, status, content_type, payload, keep_alive=headers.get("connection") != "close")
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        except Exception:
            log.exception("App server request failed")
        finally:
            writer.close()

    async def _respond(self, method: str, path: str, query: str, body: bytes) -> tuple[int, str, bytes]:
        if path.startswith("/api/"):
            handler = self.routes.get((method, path.removeprefix("/api/")))
            if handler is None:
                return 404, "application/json", b'{"error":"no such call"}'
            try:
                # A name given once is its value; given more than once it is the list of them, so a
                # query can carry a set ("?kinds=international&kinds=heliport").
                args = {k: (v[-1] if len(v) == 1 else v) for k, v in urllib.parse.parse_qs(query).items()}
                if body:
                    parsed = json.loads(body)
                    if not isinstance(parsed, dict):
                        raise HttpError(400, "send a JSON object")
                    args.update(parsed)
                result = await handler(args)
                if isinstance(result, Raw):  # not JSON: a transmission's audio
                    return 200, result.content_type, result.body
                return 200, "application/json", json.dumps(result, default=str).encode()
            except HttpError as exc:
                return exc.status, "application/json", json.dumps({"error": str(exc)}).encode()
            except ValueError as exc:  # bad input the handler rejected
                return 400, "application/json", json.dumps({"error": str(exc)}).encode()
            except Exception as exc:
                log.exception("App call %s %s failed", method, path)
                return 500, "application/json", json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode()
        if method != "GET":
            return 405, "text/plain", b"method not allowed"
        return self._static(path)

    def _static(self, path: str) -> tuple[int, str, bytes]:
        relative = "index.html" if path in ("/", "") else urllib.parse.unquote(path.lstrip("/")).removeprefix("static/")
        file = (self.static_dir / relative).resolve()
        if not file.is_relative_to(self.static_dir) or not file.is_file():
            return 404, "text/plain", b"not found"
        content_type = TYPES.get(file.suffix) or mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        return 200, content_type, file.read_bytes()

    async def _events(self, writer: asyncio.StreamWriter) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
                     b"Connection: keep-alive\r\n\r\n")
        queue = self.stream.open()
        try:
            for message in (self.on_connect() if self.on_connect else []):
                writer.write(message)
            await writer.drain()
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    message = b": keep-alive\n\n"
                writer.write(message)
                await writer.drain()
        finally:
            self.stream.close(queue)


def sse(kind: str, data: Any) -> bytes:
    return f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'), default=str)}\n\n".encode()


async def _read_request(reader: asyncio.StreamReader):
    line = await reader.readline()
    if not line:
        return None
    try:
        method, target, _ = line.decode("latin-1").split(" ", 2)
    except ValueError:
        return None
    headers: dict[str, str] = {}
    while (header := await reader.readline()) not in (b"\r\n", b"\n", b""):
        name, _, value = header.decode("latin-1").partition(":")
        headers[name.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0") or 0)
    if length > MAX_BODY:
        return None
    body = await reader.readexactly(length) if length else b""
    return method.upper(), target, headers, body


_REASONS = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed", 409: "Conflict",
            500: "Internal Server Error"}


def _write_response(writer: asyncio.StreamWriter, status: int, content_type: str, body: bytes, *,
                    keep_alive: bool) -> None:
    head = (f"HTTP/1.1 {status} {_REASONS.get(status, 'OK')}\r\nContent-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
            f"Connection: {'keep-alive' if keep_alive else 'close'}\r\n\r\n")
    writer.write(head.encode() + body)
