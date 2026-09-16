"""On-disk recording format.

A recording is a directory::

    20260916-114500_live/
      session.jsonl        # or session.jsonl.gz
      audio/0001.wav       # PTT captures, referenced by events' audio_ref

``session.jsonl`` line 1 is a ``RecordingHeader``; every later line is one
bus event (see ``localtc.sim_api.events``).
"""

from typing import Any

import msgspec

from localtc.sim_api import SessionInfo

SCHEMA_VERSION = 1
SESSION_FILE = "session.jsonl"
AUDIO_DIR = "audio"


class RecordingHeader(msgspec.Struct, frozen=True, kw_only=True, tag_field="type", tag="header"):
    schema: int
    localtc_version: str
    created: str  # ISO 8601
    session: SessionInfo
    config: dict[str, Any] = {}


_encoder = msgspec.json.Encoder()
header_decoder = msgspec.json.Decoder(RecordingHeader)


def encode_line(obj: msgspec.Struct) -> bytes:
    return _encoder.encode(obj) + b"\n"
