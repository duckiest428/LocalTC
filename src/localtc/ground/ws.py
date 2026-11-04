"""A small WebSocket client on asyncio's streams (RFC 6455), for GSX's server on this PC: text frames, nothing more, and no
new package for the installer to carry."""

import asyncio
import base64
import hashlib
import os
import struct

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_TEXT, OP_CLOSE, OP_PING, OP_PONG = 1, 8, 9, 10


class WebSocketClosed(Exception):
    pass


class WebSocket:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader, self.writer = reader, writer

    @classmethod
    async def connect(cls, host: str, port: int, timeout_s: float = 5.0) -> "WebSocket":
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port, limit=2 ** 20), timeout_s)
        key = base64.b64encode(os.urandom(16))
        writer.write(b"GET / HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (host.encode(), port, key))
        await writer.drain()
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout_s)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError) as exc:
            writer.close()
            raise WebSocketClosed("no answer to the handshake") from exc
        lines = head.decode("latin-1").split("\r\n")
        accept = base64.b64encode(hashlib.sha1(key + GUID).digest()).decode()
        headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
        if " 101 " not in lines[0] + " " or headers.get("sec-websocket-accept") != accept:
            writer.close()
            raise WebSocketClosed(f"refused: {lines[0]}")
        return cls(reader, writer)

    async def _send(self, op: int, data: bytes) -> None:
        n, mask = len(data), os.urandom(4)
        if n < 126:
            head = bytes((0x80 | op, 0x80 | n))
        elif n < 65536:
            head = bytes((0x80 | op, 0x80 | 126)) + struct.pack(">H", n)
        else:
            head = bytes((0x80 | op, 0x80 | 127)) + struct.pack(">Q", n)
        self.writer.write(head + mask + bytes(c ^ mask[i & 3] for i, c in enumerate(data)))
        await self.writer.drain()

    async def send(self, text: str) -> None:
        await self._send(OP_TEXT, text.encode("utf-8"))

    async def _exactly(self, n: int) -> bytes:
        try:
            return await self.reader.readexactly(n)
        except (asyncio.IncompleteReadError, ConnectionError) as exc:
            raise WebSocketClosed("the connection went") from exc

    async def receive(self) -> str:
        """The next whole text message (pings answered on the way, frames put back together)."""
        parts: list[bytes] = []
        while True:
            first, second = await self._exactly(2)
            final, op, n = first & 0x80, first & 0x0F, second & 0x7F
            if n == 126:
                n = struct.unpack(">H", await self._exactly(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", await self._exactly(8))[0]
            mask = await self._exactly(4) if second & 0x80 else b""
            data = await self._exactly(n)
            if mask:
                data = bytes(c ^ mask[i & 3] for i, c in enumerate(data))
            if op == OP_PING:
                await self._send(OP_PONG, data)
            elif op == OP_CLOSE:
                raise WebSocketClosed("closed by the server")
            elif op != OP_PONG:
                parts.append(data)
                if final:
                    return b"".join(parts).decode("utf-8", "replace")

    async def close(self) -> None:
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass
