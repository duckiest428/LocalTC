"""Voice input on the bus: push-to-talk events in, ``Transcript`` events out.

Whatever the switch (keyboard, joystick via SimConnect, Enter in the terminal), it publishes
``PttPressed``/``PttReleased``. This service records the clip between them, saves it beside the
recording, transcribes it off the event loop, and publishes the transcript. The ATC engine
waits for it before answering.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from localtc.bus import EventBus
from localtc.stt.audio import SAMPLE_RATE, rms, to_pcm16
from localtc.stt.vocabulary import VocabularyHints, build_prompt, fixup, hotwords
from localtc.sim_api import PttPressed, PttReleased, Transcript

log = logging.getLogger(__name__)

MIN_CLIP_S = 0.3  # shorter: a click on the switch, not speech
SILENCE_RMS = 0.003  # below this the mic heard nothing
NO_SPEECH = 0.8  # Whisper's no-speech probability above which the clip is ignored


class VoiceService:
    def __init__(
        self,
        bus: EventBus,
        now: Callable[[], float],
        transcriber: Any,  # WhisperTranscriber, or anything with transcribe(audio, prompt=, hotwords=)
        capture: Any,  # AudioCapture, or anything with begin()/end()
        *,
        recorder: Any = None,  # Recorder: clips are saved beside the recording
        hints: Callable[[], VocabularyHints] | None = None,
        vocabulary: bool = True,
        tail_s: float = 0.25,
    ) -> None:
        self.bus, self.now, self.transcriber, self.capture = bus, now, transcriber, capture
        self.recorder, self.hints, self.vocabulary, self.tail_s = recorder, hints, vocabulary, tail_s
        self._inputs = bus.subscribe(PttPressed, PttReleased)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._radio = 1

    # Called from the push-to-talk source's thread.
    def press(self, radio: int = 1) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(lambda: self.bus.publish(PttPressed(t=self.now(), radio=radio)))

    def release(self, radio: int = 1) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(lambda: self.bus.publish(PttReleased(t=self.now(), radio=radio)))

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        async for event in self._inputs:
            if isinstance(event, PttPressed):
                self._radio = event.radio
                self.capture.begin()
            elif isinstance(event, PttReleased):
                await asyncio.sleep(self.tail_s)  # the last word often ends as the switch is let go
                audio = self.capture.end()
                try:
                    self.bus.publish(await self._transcribe(audio))
                except Exception:
                    log.exception("Speech-to-text failed")
                    self.bus.publish(Transcript(t=self.now(), text="", radio=self._radio, source="voice"))

    async def _transcribe(self, audio) -> Transcript:
        audio_s = len(audio) / SAMPLE_RATE
        if audio_s < MIN_CLIP_S or rms(audio) < SILENCE_RMS:
            log.info("Push-to-talk with no speech (%.1f s)", audio_s)
            return Transcript(t=self.now(), text="", radio=self._radio, source="voice")
        ref = self.recorder.save_audio(to_pcm16(audio), sample_rate=SAMPLE_RATE) if self.recorder else None
        hints = self.hints() if (self.hints and self.vocabulary) else VocabularyHints()
        prompt = build_prompt(hints) if self.vocabulary else ""
        result = await asyncio.to_thread(self.transcriber.transcribe, audio, prompt=prompt, hotwords=hotwords(hints))
        text = fixup(result.text) if result.no_speech < NO_SPEECH else ""
        log.info("Heard %r in %.1f s of audio (%.0f ms, confidence %.2f)", text, result.audio_s, result.latency_ms,
                 result.confidence)
        return Transcript(t=self.now(), text=text, radio=self._radio, confidence=result.confidence, audio_ref=ref,
                          stt_ms=result.latency_ms, source="voice")
