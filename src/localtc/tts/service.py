"""ATC's voice on the bus: ``AtcTransmission`` (and the ATIS, and the copilot) in, radio audio out.

The copilot talks twice over: on the radio (its readbacks, band-limited like your own side of the radio) and on the
intercom (``CrewSpeech``: a dry headset voice, no radio at all), in the same voice either way.

Every station is a person of its own (``persona.Persona``: decided from its name, with its region's English) and
speaks through the radio effect. The words are made speakable first (``aviation.speakable``), the same for every
synthesizer; the synthesizers (Piper, Kokoro, Azure) are tried in order by a ``providers.VoiceChain``, and when none
can speak, the line stays text and ATC carries on. The ATIS repeats while its frequency is tuned, like the real
thing, and stops when the pilot tunes away.
"""

import asyncio
import logging
import zlib
from typing import Any

from localtc.bus import EventBus
from localtc.dsp.clips import CLIPS, clip_id
from localtc.dsp.radio import clean, intercom_effect, radio_effect
from localtc.sim_api import (
    AtcTransmission,
    AtisBroadcast,
    CrewSpeech,
    RadioChatter,
    RadioTuned,
    Transcript,
)
from localtc.tts.aviation import speakable
from localtc.tts.persona import Persona, persona_for
from localtc.tts.player import Clip
from localtc.tts.providers import PiperVoices, VoiceChain
from localtc.tts.voices import PILOT_SPEAKER_SALT, SPEAKERS, shift_key

log = logging.getLogger(__name__)

ATIS_GAP_S = 2.0


def radio_words(text: str) -> str:
    """Numbers left in free text ("altimeter 29.92") read the way ATC says them: digit by digit (aviation.speakable)."""
    return speakable(text)


class VoiceOut:
    def __init__(
        self,
        bus: EventBus,
        synth: Any,  # a VoiceChain; or one synthesizer (PiperSynth, or anything with synthesize(text, speaker))
        player: Any,  # AudioPlayer, or anything with play(Clip) and cut(kind)
        *,
        effect: bool = True,
        static: float = 0.35,
        atis: bool = True,
        copilot: bool = True,
        speakers: tuple[int, ...] = SPEAKERS,
        crew_speaker: int | None = None,  # the copilot's Piper voice (``[crew] voice``); None: one picked for "pilot"
        crew_sex: str = "",  # the copilot's sex for the other providers ("F"/"M"; "" from its Piper voice)
        crew_pick: int | None = None,  # and which of their voices of that sex
        shift: int = 0,  # ATC's shift ([atc] shift): each station's voice is the person on shift's
        regional: bool = True,  # each station in its region's English, where a provider has it
    ) -> None:
        self.bus, self.player = bus, player
        self.voices = synth if isinstance(synth, VoiceChain) else VoiceChain([PiperVoices(synth, speakers=speakers)])
        self.effect, self.static, self.atis, self.copilot, self.speakers = effect, static, atis, copilot, speakers
        self.crew_speaker, self.crew_sex, self.crew_pick = crew_speaker, crew_sex, crew_pick
        self.shift, self.regional = shift, regional
        self._events = bus.subscribe(AtcTransmission, AtisBroadcast, RadioTuned, Transcript, RadioChatter, CrewSpeech)
        self._lock = asyncio.Lock()  # transmissions are synthesized and queued in the order they were made
        self._atis_task: asyncio.Task | None = None

    def persona(self, key: str, role: str, *, manner: str = "", locale: str = "") -> Persona:
        if role == "copilot":
            return persona_for(PILOT_SPEAKER_SALT, "copilot", sex=self.crew_sex, pick=self.crew_pick,
                               piper_speaker=self.crew_speaker)
        return persona_for(key, role, manner=manner, locale=locale if self.regional else "")

    async def run(self) -> None:
        try:
            async for ev in self._events:
                try:
                    await self._speak(ev)
                except Exception:  # noqa: BLE001 - a voice failing never stops ATC (nor the next line)
                    log.exception("Voice failed on %s; carrying on", type(ev).__name__)
        finally:
            self._stop_atis()

    async def _speak(self, ev: Any) -> None:
        locale = getattr(ev, "locale", "")
        if isinstance(ev, AtcTransmission):
            await self.say(ev.spoken or ev.text, self.persona(shift_key(ev.station, self.shift), "atc",
                           manner=ev.manner or ev.controller or "atc", locale=locale), "atc", keep=clip_id(ev))
        elif isinstance(ev, RadioChatter):  # somebody else on the frequency: the station's voice, or the other crew's
            if ev.speaker == "atc":
                who = self.persona(shift_key(ev.station, self.shift), "atc", manner=ev.controller or "atc", locale=locale)
            else:
                who = self.persona(f"chatter {ev.callsign}", "chatter", locale=locale)
            await self.say(ev.spoken or ev.text, who, "atc", keep=clip_id(ev))
        elif isinstance(ev, Transcript) and ev.source == "copilot" and self.copilot and ev.text:
            await self.say(ev.text, self.persona("", "copilot"), "pilot", keep=clip_id(ev))
        elif isinstance(ev, CrewSpeech) and (ev.spoken or ev.text):
            await self.say(ev.spoken or ev.text, self.persona("", "copilot"), "intercom", keep=clip_id(ev))
        elif isinstance(ev, AtisBroadcast) and self.atis:
            self._stop_atis()
            self._atis_task = asyncio.create_task(self._loop_atis(ev))
        elif isinstance(ev, RadioTuned) and ev.controller != "atis":
            self._stop_atis()

    async def say(self, text: str, who: Persona | str, kind: str, *, manner: str | None = None, keep: str = "") -> Clip | None:
        """``who``: the person speaking (a voice key: a station). ``keep``: the id to keep the audio under, to be
        played again (``dsp.clips``, when that's on)."""
        async with self._lock:
            clip = await asyncio.to_thread(self.render, text, who, kind, manner)
            if clip is not None:
                self.player.play(clip)
                if keep and CLIPS.enabled:
                    await asyncio.to_thread(CLIPS.put, keep, clip.audio, clip.rate)
            return clip

    def render(self, text: str, who: Persona | str, kind: str, manner: str | None = None) -> Clip | None:
        """The line as heard: speakable words, the person's voice from the first provider that can, then the radio
        (or the intercom). None when there's nothing to say or nobody could say it (text only)."""
        words = speakable(text)
        if not words.strip():
            return None
        if isinstance(who, str):
            role = "copilot" if kind in ("pilot", "intercom") else "atis" if kind == "atis" else "atc"
            who = self.persona(who, role, manner=manner or (kind if role == "atc" else ""))
        speech = self.voices.speak(words, who)
        if speech is None:
            return None
        seed = zlib.crc32(words.encode())
        if kind == "intercom":  # beside you in the cockpit: never the radio
            audio = intercom_effect(speech.audio, speech.rate)
        elif not self.effect:
            audio = clean(speech.audio)
        elif kind == "pilot":  # your own side of the radio: band-limited, no hiss
            audio = radio_effect(speech.audio, speech.rate, static=0.0, seed=seed, squelch=False)
        else:
            audio = radio_effect(speech.audio, speech.rate, static=self.static * (0.6 if kind == "atis" else 1.0), seed=seed)
        log.info("Speaking %s as %s (%s %s, %.1f s, synthesized in %.0f ms)", kind, who.key, speech.provider,
                 speech.voice, len(speech.audio) / speech.rate, speech.latency_ms)
        return Clip(audio, speech.rate, kind)

    async def _loop_atis(self, ev: AtisBroadcast) -> None:
        """The ATIS on a loop, each time round in its next wording (the same information, the words around it
        changing a little, as a recording made by a person). Each wording is synthesized once."""
        texts = ev.variants or (ev.spoken,)
        clips: dict[int, Clip | None] = {}
        turn = 0
        while True:
            index = turn % len(texts)
            if index not in clips:
                who = self.persona(f"{ev.station} ATIS", "atis", locale=ev.locale)
                clips[index] = await asyncio.to_thread(self.render, texts[index], who, "atis")
            clip = clips[index]
            turn += 1
            if clip is None:
                if not any(c is not None for c in clips.values()) and len(clips) == len(texts):
                    return
                continue
            playing = Clip(clip.audio, clip.rate, "atis")
            self.player.play(playing)
            await asyncio.to_thread(playing.done.wait)
            await asyncio.sleep(ATIS_GAP_S)

    def _stop_atis(self) -> None:
        if self._atis_task is not None:
            self._atis_task.cancel()
            self._atis_task = None
            self.player.cut("atis")
