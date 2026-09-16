"""JSON encoding for events: the same bytes go into recordings and over any future IPC."""

import msgspec

from localtc.sim_api.events import BusEvent, Event

_encoder = msgspec.json.Encoder()
_decoder = msgspec.json.Decoder(BusEvent)


def encode_event(event: Event) -> bytes:
    return _encoder.encode(event)


def decode_event(data: bytes | str) -> BusEvent:
    return _decoder.decode(data)
