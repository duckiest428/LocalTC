"""Voice input on the bus: push-to-talk events in, ``Transcript`` events out.

Whatever the switch (keyboard, joystick via SimConnect, Enter in the terminal), it publishes
``PttPressed``/``PttReleased``. This service records the clip between them, saves it beside the
recording, transcribes it off the event loop, and publishes the transcript. The ATC engine
waits for it before answering.

The intercom key (``radio`` 0) works the same with the same microphone and model, but it's the copilot listening:
``IntercomPressed``/``IntercomReleased`` in, ``IntercomHeard`` out, primed with crew words, never ATC's.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from localtc.bus import EventBus
from localtc.dsp.clips import CLIPS, clip_id
from localtc.stt.audio import SAMPLE_RATE, rms, to_pcm16, trim_silence
from localtc.stt.vocabulary import VocabularyHints, build_prompt, fixup, hotwords
from localtc.sim_api import IntercomHeard, IntercomPressed, IntercomReleased, PttPressed, PttReleased, Transcript

log = logging.getLogger(__name__)

MIN_CLIP_S = 0.3  # shorter: a click on the switch, not speech
SILENCE_RMS = 0.003  # below this the mic heard nothing
NO_SPEECH = 0.8  # Whisper's no-speech probability above which the clip is ignored
# Below this confidence on a short clip, Whisper made it up from noise ("Exhale.", "Be careful.", "There you go." on a
# key pressed for a shortcut, at 0.17-0.34): heard as nothing.
GUESSED_CONFIDENCE, GUESSED_MAX_S = 0.35, 3.0
INTERCOM = 0  # the "radio" of the intercom key: the copilot, not a COM radio
# What Whisper is primed with on the intercom: the words a pilot says to the copilot.
CREW_STYLE = (
    "Gear up. Gear down. Flaps 1. Flaps 2, flaps 3, flaps full, flaps up. Landing lights on. Taxi lights off. "
    "Strobes on. Beacon on. Arm the spoilers. Autopilot on. Autopilot off. Heading mode. Approach mode. "
    "Set heading 270. Altitude 10,000. Flight level 240. Speed 250. Vertical speed minus 1,500. Squawk 4521. "
    "Tune 121.9. Set standby 118.7. Altimeter 29.92. Standard. Parking brake set. Confirm. Negative. Cancel. "
    "How do you hear me? Check. Set. Before start checklist. Your radios. My radios."
)


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
        self._inputs = bus.subscribe(PttPressed, PttReleased, IntercomPressed, IntercomReleased)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._radio = 1
        self._held: int | None = None  # the key being held (a radio, or INTERCOM): the other one waits for it
        self._cancelled = False  # the key was part of a shortcut (Alt+Tab): the clip is dropped

    # Called from the push-to-talk source's thread. ``radio`` INTERCOM: the intercom key.
    def press(self, radio: int = 1) -> None:
        if self._loop is not None:
            event = IntercomPressed(t=self.now()) if radio == INTERCOM else PttPressed(t=self.now(), radio=radio)
            self._loop.call_soon_threadsafe(lambda: self.bus.publish(event))

    def cancel(self, radio: int = 1) -> None:
        """The key came up after being used in a shortcut (another key pressed while it was held): nothing was said."""
        self._cancelled = True
        self.release(radio)

    def release(self, radio: int = 1) -> None:
        if self._loop is not None:
            event = IntercomReleased(t=self.now()) if radio == INTERCOM else PttReleased(t=self.now(), radio=radio)
            self._loop.call_soon_threadsafe(lambda: self.bus.publish(event))

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        async for event in self._inputs:
            if isinstance(event, (PttPressed, IntercomPressed)):
                radio = INTERCOM if isinstance(event, IntercomPressed) else event.radio
                if self._held is not None:  # one microphone: the second key waits for the first to come up
                    continue
                self._held = self._radio = radio
                self.capture.begin()
            elif isinstance(event, (PttReleased, IntercomReleased)):
                radio = INTERCOM if isinstance(event, IntercomReleased) else event.radio
                if self._held != radio:
                    continue
                self._held = None
                if self._cancelled:
                    self._cancelled = False
                    self.capture.end()
                    log.info("Push-to-talk key used in a shortcut: nothing sent")
                    if radio != INTERCOM:  # ATC is waiting for this transmission: it was nothing
                        self.bus.publish(Transcript(t=self.now(), text="", radio=radio, source="cancelled"))
                    continue
                await asyncio.sleep(self.tail_s)  # the last word often ends as the switch is let go
                audio = self.capture.end()
                try:
                    self.bus.publish(await self._transcribe(audio))
                except Exception:
                    log.exception("Speech-to-text failed")
                    self.bus.publish(self._heard("", None, None, 0.0))

    def _heard(self, text: str, confidence: float | None, ref: str | None, stt_ms: float) -> Transcript | IntercomHeard:
        if self._radio == INTERCOM:
            return IntercomHeard(t=self.now(), text=text, confidence=confidence, audio_ref=ref, stt_ms=stt_ms)
        return Transcript(t=self.now(), text=text, radio=self._radio, confidence=confidence, audio_ref=ref, stt_ms=stt_ms,
                          source="voice")

    async def _silent_microphone(self, audio_s: float) -> None:
        """The microphone sent silence. Reopen it: if the system default input was changed (Windows
        Settings > Sound > Input) the new one is used from the next transmission."""
        before = getattr(self.capture, "name", "the microphone")
        log.warning("Push-to-talk for %.1f s but %s heard nothing", audio_s, before)
        if not hasattr(self.capture, "refresh"):
            return
        try:
            after = await asyncio.to_thread(self.capture.refresh)
        except Exception as exc:  # a missing device: keep going, the next press tries again
            log.warning("Could not reopen the microphone: %s", exc)
            return
        if after != before:
            log.warning("Microphone is now %s (the system default input)", after)
        else:
            log.warning("Still using %s. Check that it's the right input in your sound settings, or pick one "
                        "with --mic (see: localtc voice devices)", after)

    async def _transcribe(self, audio) -> Transcript | IntercomHeard:
        audio_s = len(audio) / SAMPLE_RATE
        if audio_s < MIN_CLIP_S:
            log.info("Push-to-talk too short for speech (%.1f s)", audio_s)
            return self._heard("", None, None, 0.0)
        if rms(audio) < SILENCE_RMS:
            await self._silent_microphone(audio_s)
            return self._heard("", None, None, 0.0)
        ref = self.recorder.save_audio(to_pcm16(audio), sample_rate=SAMPLE_RATE) if self.recorder else None
        hints = self.hints() if (self.hints and self.vocabulary) else VocabularyHints()
        if self._radio == INTERCOM:  # the words a pilot says to the copilot, never ATC's names and numbers
            hints = VocabularyHints(style=CREW_STYLE)
        prompt = build_prompt(hints) if self.vocabulary else ""
        speech = trim_silence(audio)
        result = await asyncio.to_thread(self.transcriber.transcribe, speech, prompt=prompt, hotwords=hotwords(hints))
        text = fixup(result.text) if result.no_speech < NO_SPEECH else ""
        if text and result.confidence < GUESSED_CONFIDENCE and audio_s <= GUESSED_MAX_S:
            log.info("Dropped %r: confidence %.2f on %.1f s of audio, made up from noise", text, result.confidence, audio_s)
            text = ""
        log.info("Heard %r in %.1f s of audio (%.1f s with speech, %.0f ms, confidence %.2f)", text, audio_s,
                 result.audio_s, result.latency_ms, result.confidence)
        heard = self._heard(text, result.confidence, ref, result.latency_ms)
        if text and CLIPS.enabled:  # what was said, to play again from the radio log
            await asyncio.to_thread(CLIPS.put, clip_id(heard), speech, SAMPLE_RATE)
        return heard
