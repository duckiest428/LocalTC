"""The radio heard with the English model, the pilot's language with its own, and that language told on the radio."""

import asyncio

import numpy as np

from localtc.bus import EventBus
from localtc.stt.service import INTERCOM, VoiceService


class Ear:
    def __init__(self, name, language=None):
        self.name, self.language, self.calls = name, language, []

    def transcribe(self, audio, *, prompt="", hotwords="", language="en"):
        from localtc.stt.whisper import SttResult

        self.calls.append(language)
        return SttResult(text=f"{self.name} words here", confidence=0.9, no_speech=0.0, audio_s=3.0, latency_ms=1.0)

    def language_of(self, audio):
        return self.language


class Capture:
    def __init__(self):
        self.audio = 0.2 * np.sin(np.arange(48000) / 5.0).astype(np.float32)


def service(language):
    radio, own = Ear("radio"), Ear("own", language)
    s = VoiceService(EventBus(), lambda: 0.0, radio, Capture(), vocabulary=False, intercom_language="es", intercom_transcriber=own)
    return s, radio, own


def hear(s, channel):
    s._radio = channel
    return asyncio.run(s._transcribe(s.capture.audio))


def test_english_on_the_radio_goes_to_the_english_model():
    s, radio, own = service(("en", 0.6))
    hear(s, 1)
    assert radio.calls == ["en"] and own.calls == []


def test_a_strong_accent_isnt_taken_for_the_language():
    s, radio, own = service(("es", 0.6))
    hear(s, 1)
    assert radio.calls == ["en"] and own.calls == []


def test_spanish_on_the_radio_is_heard_as_spanish():
    s, radio, own = service(("es", 0.98))
    hear(s, 1)
    assert radio.calls == [] and own.calls == ["es"]


def test_the_intercom_goes_to_the_languages_model():
    s, radio, own = service(("es", 0.98))
    hear(s, INTERCOM)
    assert radio.calls == [] and own.calls == ["es"]
