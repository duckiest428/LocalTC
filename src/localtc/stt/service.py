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
import re
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
DEAD_CHECK_S = 0.5  # after reopening it, this long for its first audio
STALLED_S = 1.0  # the microphone sent nothing for this long: it has stopped, reopen it before a press
NO_SPEECH = 0.8  # Whisper's no-speech probability above which the clip is ignored
# Below this confidence on a short clip, Whisper made it up from noise ("Exhale.", "Be careful.", "There you go." on a
# key pressed for a shortcut, at 0.17-0.34): heard as nothing.
GUESSED_CONFIDENCE, GUESSED_MAX_S = 0.35, 3.0
WORDS_PER_S = 6.0  # more words a second than this: made up
INTERCOM = 0  # the "radio" of the intercom key: the copilot, not a COM radio
# What Whisper is primed with on the intercom: the words a pilot says to the copilot.
CREW_STYLE = (
    "Gear up. Gear down. Flaps 1. Flaps 2, flaps 3, flaps full, flaps up. Landing lights on. Taxi lights off. "
    "Strobes on. Beacon on. Arm the spoilers. Autopilot on. Autopilot off. Heading mode. Approach mode. "
    "Set heading 270. Altitude 10,000. Flight level 240. Speed 250. Vertical speed minus 1,500. Squawk 4521. "
    "Tune 121.9. Set standby 118.7. Altimeter 29.92. Standard. Parking brake set. Confirm. Negative. Cancel. "
    "How do you hear me? Check. Set. Before start checklist. Your radios. My radios."
)



# What Whisper says when it hears nothing much: the credits and captions of the videos it learned from. "NASA Jet
# Propulsion Laboratory, California Institute of Technology" came out of 1.1 s of a key pressed by mistake, and
# Ground answered it ("say again") when the pilot hadn't said a word.
CAPTIONS = re.compile(
    r"\b(?:jet propulsion laboratory|california institute of technology|thanks? (?:you )?for watching|"
    r"subscribe to (?:my|the|our) channel|please subscribe|like and subscribe|subtitles? by|captions? by|"
    r"transcribed by|amara\.org|translated by|www\.|copyright|all rights reserved|see you in the next video)", re.I)
QUICK_WORDS_PER_S = 5.0  # this fast from a short, doubtful clip: made up too
DOUBTFUL_CONFIDENCE, SHORT_CLIP_S = 0.5, 2.0


def made_up(text: str, audio_s: float, confidence: float | None = None) -> bool:
    """Words Whisper made up: more than anyone says in that long (a fast call is 4 words a second), a few words
    over and over ("A7, B7, A7, A9, B2, A7, ..." from 0.8 s of noise), a video's credits, or a whole sentence out of a
    second of doubtful sound."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    if len(words) > WORDS_PER_S * audio_s + 6 or CAPTIONS.search(text):
        return True
    if confidence is not None and confidence < DOUBTFUL_CONFIDENCE and audio_s <= SHORT_CLIP_S \
            and len(words) > QUICK_WORDS_PER_S * audio_s:
        return True
    return len(words) >= 16 and len(set(words)) < len(words) / 4


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
        intercom_language: str = "",  # the copilot talked to in this language ("de"); "" English
    ) -> None:
        self.bus, self.now, self.transcriber, self.capture = bus, now, transcriber, capture
        self.recorder, self.hints, self.vocabulary, self.tail_s = recorder, hints, vocabulary, tail_s
        self.intercom_language = intercom_language
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
                if getattr(self.capture, "idle_s", 0.0) > STALLED_S:
                    await self._stalled_microphone()
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

    async def _stalled_microphone(self) -> None:
        """The microphone stopped sending audio without an error: reopened, so this press is heard."""
        log.warning("%s stopped sending audio %.0f s ago; reopening it", getattr(self.capture, "name", "The microphone"),
                    self.capture.idle_s)
        try:
            await asyncio.to_thread(self.capture.refresh)
        except Exception as exc:  # a missing device: keep going, the next press tries again
            log.warning("Could not reopen the microphone: %s", exc)
            return
        asyncio.get_running_loop().create_task(self._still_dead())  # the press goes on meanwhile

    async def _still_dead(self) -> None:
        """Reopened and still nothing: Windows isn't giving the microphone any audio, to any program (a USB
        microphone's stream stuck). "Too short for speech" on every press said nothing of that."""
        await asyncio.sleep(DEAD_CHECK_S)
        if self.capture.idle_s >= DEAD_CHECK_S * 0.8:
            log.warning("%s isn't sending any audio to Windows at all: unplug it and plug it back in (or pick another "
                        "in Quick Settings > Push-to-talk), then press again", getattr(self.capture, "name", "The microphone"))

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
        words = hotwords(hints)
        kwargs = {}
        if self._radio == INTERCOM and self.intercom_language:
            # The copilot talked to in the pilot's language: heard as that language, without the English prompt.
            prompt, words, kwargs = "", "", {"language": self.intercom_language}
        result = await asyncio.to_thread(self.transcriber.transcribe, speech, prompt=prompt, hotwords=words, **kwargs)
        text = fixup(result.text) if result.no_speech < NO_SPEECH else ""
        if text and made_up(text, audio_s) and (prompt or words):
            # Caught in a loop on the airport's names it was given ("United 318, A15, A15, A15, ..." for a 22 s
            # clearance readback, dropped, and ATC asked "how do you read?"): heard again without them.
            log.info("Heard %r, the same few over and over: once more without the airport's words", text[:80])
            again = await asyncio.to_thread(self.transcriber.transcribe, speech, prompt="", hotwords="", **kwargs)
            if again.no_speech < NO_SPEECH and (redone := fixup(again.text)) and not made_up(redone, audio_s):
                result, text = again, redone
        if text and result.confidence < GUESSED_CONFIDENCE and audio_s <= GUESSED_MAX_S:
            log.info("Dropped %r: confidence %.2f on %.1f s of audio, made up from noise", text, result.confidence, audio_s)
            text = ""
        elif text and made_up(text, audio_s, result.confidence):
            log.info("Dropped %r: more words than %.1f s of audio holds, or the same few over and over", text[:80], audio_s)
            text = ""
        log.info("Heard %r in %.1f s of audio (%.1f s with speech, %.0f ms, confidence %.2f)", text, audio_s,
                 result.audio_s, result.latency_ms, result.confidence)
        heard = self._heard(text, result.confidence, ref, result.latency_ms)
        if text and CLIPS.enabled:  # what was said, to play again from the radio log
            await asyncio.to_thread(CLIPS.put, clip_id(heard), speech, SAMPLE_RATE)
        return heard
