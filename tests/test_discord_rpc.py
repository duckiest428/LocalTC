"""The flight on Discord: what the profile shows, and the local connection's frames."""

import io
import json
import struct
import threading

from localtc.discord_rpc import FRAME, HANDSHAKE, Presence, _receive, _send, activity


def test_the_profile_shows_where_and_the_frequency_with_the_button():
    a = activity({"origin": "KLAS", "destination": "KSAN", "phase_label": "Cruise",
                  "tuned": {"station": "Los Angeles Center", "mhz": 125.1}}, 1000)
    assert a["details"] == "KLAS → KSAN · Cruise" and a["state"] == "Los Angeles Center 125.100"
    assert a["buttons"] == [{"label": "Fly on LocalTC", "url": "https://localtc.tech"}]
    assert a["timestamps"] == {"start": 1000}
    assert activity({"origin": "KLAS"}, 1)["state"] == "Not on a frequency"
    assert activity({}, 1) is None


def test_a_frame_is_its_kind_its_length_and_json():
    pipe = io.BytesIO()
    _send(pipe, FRAME, {"a": 1})
    raw = pipe.getvalue()
    assert struct.unpack("<II", raw[:8]) == (FRAME, len(raw) - 8) and json.loads(raw[8:]) == {"a": 1}
    pipe.seek(0)
    assert _receive(pipe) == {"a": 1}


def test_it_shakes_hands_then_sets_the_activity():
    frames, done = [], threading.Event()

    class Pipe:
        def write(self, data):
            frames.append((struct.unpack("<I", data[:4])[0], json.loads(data[8:])))
            if len(frames) == 2:
                done.set()

        def read(self, n):
            return struct.pack("<II", 1, 2) if n == 8 else b"{}"

    Presence("123", open_pipe=Pipe).show({"state": "x"})
    assert done.wait(5)
    assert frames[0] == (HANDSHAKE, {"v": 1, "client_id": "123"})
    assert frames[1][1]["cmd"] == "SET_ACTIVITY" and frames[1][1]["args"]["activity"] == {"state": "x"}


def test_without_an_application_nothing_starts():
    p = Presence("")
    p.show({"state": "x"})
    assert p._thread is None
