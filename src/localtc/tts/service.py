"""ATC's voice on the bus: ``AtcTransmission`` (and the ATIS, and the copilot) in, radio audio out.

Every station speaks with its own voice (a speaker of a multi-speaker Piper voice, chosen from
its name), through the radio effect. The ATIS repeats while its frequency is tuned, like the
real thing, and stops when the pilot tunes away.
"""

import asyncio
import logging
import re
import zlib
from typing import Any

from localtc.bus import EventBus
from localtc.dsp.radio import clean, radio_effect
from localtc.sim_api import (
    AtcTransmission,
    AtisBroadcast,
    RadioChatter,
    RadioTuned,
    Transcript,
)
from localtc.tts.player import Clip
from localtc.tts.voices import PILOT_SPEAKER_SALT, SPEAKERS, delivery_for, speaker_for

log = logging.getLogger(__name__)

ATIS_GAP_S = 2.0
DIGITS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "niner")
NUMBER = re.compile(r"\d+(?:\.\d+)?")


def radio_words(text: str) -> str:
    """Numbers left in free text ("altimeter 29.92") read the way ATC says them: digit by digit."""
    def spell(match: re.Match) -> str:
        return " ".join("point" if c == "." else DIGITS[int(c)] for c in match.group().replace(",", ""))

    text = re.sub(r"(\d),(\d{3})", r"\1\2", text)
    return NUMBER.sub(spell, text)


class VoiceOut:
    def __init__(
        self,
        bus: EventBus,
        synth: Any,  # PiperSynth, or anything with synthesize(text, speaker) -> Speech and .speakers
        player: Any,  # AudioPlayer, or anything with play(Clip) and cut(kind)
        *,
        effect: bool = True,
        static: float = 0.35,
        atis: bool = True,
        copilot: bool = True,
        speakers: tuple[int, ...] = SPEAKERS,
    ) -> None:
        self.bus, self.synth, self.player = bus, synth, player
        self.effect, self.static, self.atis, self.copilot, self.speakers = effect, static, atis, copilot, speakers
        self._events = bus.subscribe(AtcTransmission, AtisBroadcast, RadioTuned, Transcript, RadioChatter)
        self._lock = asyncio.Lock()  # transmissions are synthesized and queued in the order they were made
        self._atis_task: asyncio.Task | None = None

    async def run(self) -> None:
        try:
            async for ev in self._events:
                if isinstance(ev, AtcTransmission):
                    await self.say(ev.spoken or ev.text, ev.station, "atc", manner=ev.controller or "atc")
                elif isinstance(ev, RadioChatter):  # somebody else on the frequency: the station's voice, or the other crew's
                    if ev.speaker == "atc":
                        await self.say(ev.spoken or ev.text, ev.station, "atc", manner=ev.controller or "atc")
                    else:
                        await self.say(ev.spoken or ev.text, f"chatter {ev.callsign}", "atc", manner="chatter")
                elif isinstance(ev, Transcript) and ev.source == "copilot" and self.copilot and ev.text:
                    await self.say(ev.text, PILOT_SPEAKER_SALT, "pilot")
                elif isinstance(ev, AtisBroadcast) and self.atis:
                    self._stop_atis()
                    self._atis_task = asyncio.create_task(self._loop_atis(ev))
                elif isinstance(ev, RadioTuned) and ev.controller != "atis":
                    self._stop_atis()
        finally:
            self._stop_atis()

    async def say(self, text: str, voice_key: str, kind: str, *, manner: str | None = None) -> Clip | None:
        async with self._lock:
            clip = await asyncio.to_thread(self.render, text, voice_key, kind, manner)
            if clip is not None:
                self.player.play(clip)
            return clip

    def render(self, text: str, voice_key: str, kind: str, manner: str | None = None) -> Clip | None:
        """``manner``: which controller is talking (tower, center, ...), for its pace and cadence (voices.DELIVERY)."""
        words = radio_words(text)
        if not words.strip():
            return None
        speaker = speaker_for(voice_key, self.synth.speakers, self.speakers)
        how = delivery_for(voice_key, manner or kind)
        rate = getattr(self.synth, "rate", None)
        try:
            speech = self.synth.synthesize(words, speaker, rate=rate * how.pace if rate else None,
                                           noise_scale=how.noise_scale, noise_w=how.noise_w)
        except TypeError:  # a synthesizer without the knobs (tests' fakes): its own manner
            speech = self.synth.synthesize(words, speaker)
        seed = zlib.crc32(words.encode())
        if not self.effect:
            audio = clean(speech.audio)
        elif kind == "pilot":  # your own side of the radio: band-limited, no hiss
            audio = radio_effect(speech.audio, speech.rate, static=0.0, seed=seed, squelch=False)
        else:
            audio = radio_effect(speech.audio, speech.rate, static=self.static * (0.6 if kind == "atis" else 1.0), seed=seed)
        log.info("Speaking %s as %s (speaker %s, %.1f s, synthesized in %.0f ms)", kind, voice_key, speaker,
                 speech.seconds, speech.latency_ms)
        return Clip(audio, speech.rate, kind)

    async def _loop_atis(self, ev: AtisBroadcast) -> None:
        clip = await asyncio.to_thread(self.render, ev.spoken, f"{ev.station} ATIS", "atis")
        if clip is None:
            return
        while True:
            playing = Clip(clip.audio, clip.rate, "atis")
            self.player.play(playing)
            await asyncio.to_thread(playing.done.wait)
            await asyncio.sleep(ATIS_GAP_S)

    def _stop_atis(self) -> None:
        if self._atis_task is not None:
            self._atis_task.cancel()
            self._atis_task = None
            self.player.cut("atis")
