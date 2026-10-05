"""Transmissions kept to play again (dsp.clips): small WAVs under the radio line's id, off unless asked for."""

import io
import wave

import numpy as np

from localtc.dsp.clips import ClipStore, clip_id, to_wav
from localtc.sim_api import AtcTransmission, Transcript


def test_a_clip_is_a_small_telephone_quality_wav():
    wav = to_wav(np.sin(np.linspace(0, 400, 22050)).astype(np.float32) * 0.2, 22050)
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 1, 8000)
        assert abs(w.getnframes() - 8000) <= 1
        frames = np.frombuffer(w.readframes(w.getnframes()), dtype=np.uint8)
    assert frames.max() > 230  # brought up to near full scale


def test_the_id_is_the_lines_and_differs_by_event():
    atc = AtcTransmission(t=12.3, station="Seattle Tower", frequency_mhz=119.9, text="Alaska 123, cleared to land.")
    pilot = Transcript(t=12.3, text="Alaska 123, cleared to land.")
    assert clip_id(atc) == clip_id(AtcTransmission(t=12.3, station="x", frequency_mhz=1.0, text="Alaska 123, cleared to land."))
    assert clip_id(atc) != clip_id(pilot) and len(clip_id(atc)) == 16


def test_kept_only_when_on_and_only_the_last_few():
    store = ClipStore(keep=3)
    heard: list[str] = []
    store.listeners.append(lambda key, wav: heard.append(key))
    assert store.put("a", np.zeros(800), 8000) is None  # off
    store.enabled = True
    for key in "abcd":
        store.put(key, np.zeros(800, dtype=np.float32), 8000)
    assert store.get("a") is None and store.get("d") is not None and heard == list("abcd")
