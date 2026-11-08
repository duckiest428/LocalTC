"""ATC's voice on the bus: ``AtcTransmission`` (and the ATIS, and the copilot) in, radio audio out.

The copilot talks twice over: on the radio (its readbacks, band-limited like your own side of the radio) and on the
intercom (``CrewSpeech``: a dry headset voice, no radio at all), in the same voice either way.

Every station is a person of its own (``persona.Persona``: decided from its name, with its region's English) and
speaks through the radio effect. The words are made speakable first (``aviation.speakable``), the same for every
synthesizer; the synthesizers (Kokoro when chosen, then Piper) are tried in order by a ``providers.VoiceChain``, and when none
can speak, the line stays text and ATC carries on. The ATIS repeats while its frequency is tuned, like the real
thing, and stops when the pilot tunes away.

The radio is the frequency tuned, the moment it's tuned: a new frequency cuts what's still being said (or waiting to
be) on the old one, and a line from a frequency no longer tuned isn't said at all. Keying the microphone stops what's
being said on the radio (the copilot's intercom carries on).
"""

import asyncio
import logging
import re
import zlib
from typing import Any

import numpy as np

from localtc.bus import EventBus
from localtc.dsp.cabin import boarding_music, chime, in_person_effect, interphone_effect, pa_effect
from localtc.dsp.clips import CLIPS, clip_id
from localtc.dsp.radio import clean, intercom_effect, key_sound, radio_effect
from localtc.sim_api import (
    AtcTransmission,
    AtisBroadcast,
    CabinSound,
    RampSpeech,
    CabinSpeech,
    CrewSpeech,
    PttPressed,
    PttReleased,
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
ATC_ROOM = 0.15  # the background behind a controller: a quiet room
CABIN = (0.6, 1.0)  # ... behind another flight's pilot: a flight deck, from a quiet one to a loud one
SIGNAL = (0.45, 1.0)  # how well another flight comes through: near and strong, or far and faint


def _spread(key: str, span: tuple[float, float], salt: str) -> float:
    """A value in ``span`` decided by the name, the same every flight."""
    low, high = span
    return low + (high - low) * (zlib.crc32(f"{salt}|{key}".encode()) % 1000) / 999


PA_GAP_S = 8.0  # between two of the cabin's announcements
PA_GAP_OTHER_LANGUAGE_S = 1.5  # ... and before the same one in another language
STREAM_CHARS = 90  # an ATC line this long is made and played a sentence at a time


def sentences(text: str) -> list[str]:
    """``text`` cut into its sentences (an announcement is said a sentence at a time)."""
    parts = re.split(r"(?<=[.!?。！？])\s+", text.strip())
    out: list[str] = []
    for part in parts:
        if out and len(part.split()) < 4:  # a short one rides with the one before ("Thank you.")
            out[-1] = f"{out[-1]} {part}"
        elif part:
            out.append(part)
    return out


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
        crew_pace: float = 1.0,  # the copilot's personality: its pace (over 1 quicker)
        crew_expressive: float | None = None,  # ... how lively (0-1; None: the usual)
        crew_locale: str = "",  # ... and its English ("en-GB")
        crew_language: str = "",  # the language it talks with you in, when not English ("de")
        shift: int = 0,  # ATC's shift ([atc] shift): each station's voice is the person on shift's
        regional: bool = True,  # each station in its region's English, where a provider has it
    ) -> None:
        self.bus, self.player = bus, player
        self.voices = synth if isinstance(synth, VoiceChain) else VoiceChain([PiperVoices(synth, speakers=speakers)])
        self.effect, self.static, self.atis, self.copilot, self.speakers = effect, static, atis, copilot, speakers
        self.crew_speaker, self.crew_sex, self.crew_pick = crew_speaker, crew_sex, crew_pick
        self.crew_pace, self.crew_expressive, self.crew_locale = crew_pace, crew_expressive, crew_locale
        self.crew_language = crew_language
        self.shift, self.regional = shift, regional
        self._events = bus.subscribe(AtcTransmission, AtisBroadcast, RadioTuned, Transcript, RadioChatter, CrewSpeech,
                                     CabinSpeech, CabinSound, RampSpeech)
        self._cabin: asyncio.Queue = asyncio.Queue()  # the cabin crew's lines, said in turn beside the radio's
        self._music_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()  # transmissions are synthesized and queued in the order they were made
        self._atis_task: asyncio.Task | None = None
        self._radio = bus.subscribe(RadioTuned, PttPressed, PttReleased)  # at once, not behind the lines being made
        self.sidetone = None  # player.Sidetone: you on the radio while you hold the key (with a microphone)
        self.tuned: str | None = None  # the station tuned now
        self.keyed = False  # the pilot holding the push-to-talk: the other flights aren't heard (nor talked over)
        self._tunes_seen = self._tunes_reached = 0  # changes of frequency: seen at once, and reached in the line order

    def persona(self, key: str, role: str, *, manner: str = "", locale: str = "", language: str = "") -> Persona:
        if role == "copilot":
            return persona_for(PILOT_SPEAKER_SALT, "copilot", sex=self.crew_sex, pick=self.crew_pick,
                               piper_speaker=self.crew_speaker, pace=self.crew_pace, expressive=self.crew_expressive,
                               locale=self.crew_locale if self.regional else "", language=language)
        return persona_for(key, role, manner=manner, locale=locale if self.regional else "")

    async def _watch_radio(self) -> None:
        async for ev in self._radio:
            if isinstance(ev, RadioTuned) and ev.radio == 1:
                self.tuned, self._tunes_seen = ev.station, self._tunes_seen + 1
                if hasattr(self.player, "tuned"):
                    self.player.tuned(ev.station)
            elif isinstance(ev, PttPressed):
                if hasattr(self.player, "keyed"):
                    self.player.keyed()
                self._key(True)
            elif isinstance(ev, PttReleased):
                self._key(False)

    def _key(self, down: bool) -> None:
        """The pilot's push-to-talk: its click (and the receiver's rush when it's let go), and the sidetone."""
        self.keyed = down
        self.player.pilot_talking = down  # the cabin and the crew wait while you're on the radio
        if self.sidetone is not None:
            self.sidetone.key(down)
        if self.effect:
            rate = 22050
            self.player.play(Clip(key_sound(rate, press=down), rate, "effect"))

    def on_air(self, station: str | None) -> bool:
        """A line said by ``station`` is heard: one ATC said after the last change of frequency (ATC knew what was
        tuned), or one from the station tuned now."""
        return station is None or self._tunes_reached >= self._tunes_seen or station == self.tuned

    def off_frequency(self, station: str | None, channel: str) -> bool:
        """Another flight's exchange from a frequency no longer tuned (a chatter goes on after you've left it)."""
        return channel == "chatter" and self.tuned is not None and station != self.tuned

    async def run(self) -> None:
        watcher = asyncio.create_task(self._watch_radio())
        cabin = asyncio.create_task(self._cabin_crew())
        try:
            async for ev in self._events:
                try:
                    await self._speak(ev)
                except Exception:  # noqa: BLE001 - a voice failing never stops ATC (nor the next line)
                    log.exception("Voice failed on %s; carrying on", type(ev).__name__)
        finally:
            watcher.cancel()
            cabin.cancel()
            self._stop_atis()
            self._stop_music()

    async def _speak(self, ev: Any) -> None:
        locale = getattr(ev, "locale", "")
        if isinstance(ev, AtcTransmission):
            await self.say(ev.spoken or ev.text, self.persona(shift_key(ev.station, self.shift), "atc",
                           manner=ev.manner or ev.controller or "atc", locale=locale), "atc", keep=clip_id(ev),
                           station=ev.station)
        elif isinstance(ev, RadioChatter):  # somebody else on the frequency: the station's voice, or the other crew's
            if ev.speaker == "atc":
                who = self.persona(shift_key(ev.station, self.shift), "atc", manner=ev.controller or "atc", locale=locale)
                await self.say(ev.spoken or ev.text, who, "atc", keep=clip_id(ev), station=ev.station, channel="chatter")
            else:  # the other crews: this PC's voices unless asked (each line a cloud voice was credits spent on noise)
                who = self.persona(f"chatter {ev.callsign}", "chatter", locale=locale)
                await self.say(ev.spoken or ev.text, who, "atc", keep=clip_id(ev), local=True,
                               station=ev.station, channel="chatter", strength=_spread(ev.callsign, SIGNAL, "signal"),
                               cabin=_spread(ev.callsign, CABIN, "cabin"))
        elif isinstance(ev, Transcript) and ev.source == "copilot" and self.copilot and ev.text:
            await self.say(ev.text, self.persona("", "copilot"), "pilot", keep=clip_id(ev))
        elif isinstance(ev, CrewSpeech) and (ev.spoken or ev.text):
            # The copilot is one person: talking with you in another language, its English callouts are in that voice too.
            await self.say(ev.spoken or ev.text, self.persona("", "copilot", language=ev.language or self.crew_language),
                           "intercom",
                           keep=clip_id(ev), urgent=ev.kind == "alert")
        elif isinstance(ev, CabinSpeech) and (ev.spoken or ev.text):
            self._cabin.put_nowait(ev)  # (their own queue: the safety demonstration never holds up ATC)
        elif isinstance(ev, RampSpeech) and (ev.spoken or ev.text):
            if ev.channel == "radio":  # on the ramp's frequency: a station like any other, gone when tuned away
                await self.say(ev.spoken or ev.text, self._ramp_voice(ev), "atc", keep=clip_id(ev), station=ev.speaker, local=True)
            else:
                self._cabin.put_nowait(ev)  # the headset, on the interphone
        elif isinstance(ev, CabinSound):
            self._cabin_sound(ev)
        elif isinstance(ev, AtisBroadcast) and self.atis:
            self._stop_atis()
            self._atis_task = asyncio.create_task(self._loop_atis(ev))
        elif isinstance(ev, RadioTuned):
            self._tunes_reached += 1  # the lines from here on were said knowing it
            if ev.controller != "atis":
                self._stop_atis()

    async def say(self, text: str, who: Persona | str, kind: str, *, manner: str | None = None, keep: str = "",
                  local: bool = False, station: str | None = None, channel: str = "", strength: float = 1.0,
                  cabin: float = ATC_ROOM, urgent: bool = False) -> Clip | None:
        """``who``: the person speaking (a voice key: a station). ``keep``: the id to keep the audio under, to be
        played again (``dsp.clips``, when that's on). ``local``: only the voices on this PC. ``station``: who says it
        on the radio (not said once the pilot has tuned away from them). ``channel``: the mixer's ("" by the kind).
        ``strength`` and ``cabin``: how well it comes through and what's behind the speaker (``dsp.radio``)."""
        async with self._lock:
            if not self.on_air(station) or (channel == "chatter" and self.keyed) or self.off_frequency(station, channel):
                return None  # tuned away while it waited its turn, or another flight's line with the pilot on the air
            pieces = sentences(text) if kind == "atc" and len(text) >= STREAM_CHARS else [text]
            if len(pieces) > 1:
                return await self._say_pieces(pieces, who, kind, manner, local, station=station, channel=channel,
                                              strength=strength, cabin=cabin, urgent=urgent, keep=keep)
            clip = await asyncio.to_thread(self.render, text, who, kind, manner, local, strength=strength, cabin=cabin)
            if clip is not None and (not self.on_air(station) or (channel == "chatter" and self.keyed)):
                return None  # ... or while it was being synthesized (Skywest's "ready to taxi" came through as the pilot
                # keyed the microphone: made a second before, it took two to synthesize)
            if clip is not None:
                clip.station, clip.channel, clip.urgent = station, channel, urgent
                self.player.play(clip)
                if keep and CLIPS.enabled:
                    await asyncio.to_thread(CLIPS.put, keep, clip.audio, clip.rate)
            return clip

    async def _say_pieces(self, pieces: list[str], who: Persona | str, kind: str, manner: str | None, local: bool, *,
                          station: str | None, channel: str, strength: float, cabin: float, urgent: bool,
                          keep: str) -> Clip | None:
        """A long transmission a sentence at a time: the first played as soon as it's made, the next made while it
        plays. A 17-second clearance took 10 s to make whole, the pilot waiting 16 s from their call to its first
        word. One transmission still: the squelch opens before the first and closes after the last."""
        played: list[Clip] = []
        for i, piece in enumerate(pieces):
            if played and not self.on_air(station):
                break  # tuned away: the rest isn't heard
            clip = await asyncio.to_thread(self.render, piece, who, kind, manner, local, strength=strength, cabin=cabin,
                                           opens=i == 0, closes=i == len(pieces) - 1)
            if clip is None or (not played and (not self.on_air(station) or (channel == "chatter" and self.keyed))):
                return None
            clip.station, clip.channel, clip.urgent = station, channel, urgent
            self.player.play(clip)
            played.append(clip)
        if not played:
            return None
        whole = Clip(np.concatenate([c.audio for c in played]), played[0].rate, kind)
        whole.station, whole.channel, whole.urgent = station, channel, urgent
        if keep and CLIPS.enabled:
            await asyncio.to_thread(CLIPS.put, keep, whole.audio, whole.rate)
        return whole

    def render(self, text: str, who: Persona | str, kind: str, manner: str | None = None,
               local: bool = False, *, strength: float = 1.0, cabin: float = ATC_ROOM, opens: bool = True,
               closes: bool = True) -> Clip | None:
        """The line as heard: speakable words, the person's voice from the first provider that can, then the radio
        (or the intercom). None when there's nothing to say or nobody could say it (text only)."""
        if isinstance(who, str):
            role = "copilot" if kind in ("pilot", "intercom") else "atis" if kind == "atis" else "atc"
            who = self.persona(who, role, manner=manner or (kind if role == "atc" else ""))
        # (In another language its own voice reads the numbers: "viertausend", never "four zero zero zero".)
        words = text if who.foreign else speakable(text)
        if not words.strip():
            return None
        speech = self.voices.speak(words, who, local=local)
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
            audio = radio_effect(speech.audio, speech.rate, static=self.static * (0.6 if kind == "atis" else 1.0), seed=seed,
                                 strength=strength, cabin=cabin, opens=opens, closes=closes)
        log.info("Speaking %s as %s (%s %s, %.1f s, synthesized in %.0f ms)", kind, who.key, speech.provider,
                 speech.voice, len(speech.audio) / speech.rate, speech.latency_ms)
        return Clip(audio, speech.rate, kind)

    async def _cabin_crew(self) -> None:
        """The cabin crew's lines, one after another: each sentence made in turn with the radio's (a long announcement
        is many sentences; ATC's next line goes between two of them, not after the whole safety demonstration). A
        breath between two announcements (the welcome, the exit rows and the safety demonstration back to back felt
        crammed), a short one before the same said in the airline's other language."""
        last: CabinSpeech | None = None
        while True:
            ev = await self._cabin.get()
            if isinstance(ev, CabinSpeech) and ev.channel == "pa" and last is not None and last.channel == "pa":
                await asyncio.sleep(PA_GAP_OTHER_LANGUAGE_S if ev.language != last.language else PA_GAP_S)
            try:
                await (self._say_ramp(ev) if isinstance(ev, RampSpeech) else self._say_cabin(ev))
            except Exception:  # noqa: BLE001 - a voice failing never stops the next line
                log.exception("Cabin voice failed; carrying on")
            last = ev if isinstance(ev, CabinSpeech) else last

    def _ramp_voice(self, ev: RampSpeech) -> Persona:
        return persona_for(f"ramp {ev.speaker}", "ground_crew", sex=ev.sex, locale=ev.locale if self.regional else "", language=ev.language)

    async def _say_ramp(self, ev: RampSpeech) -> None:
        who = self._ramp_voice(ev)
        kept: list[Any] = []
        rate = 0
        for sentence in sentences(ev.spoken or ev.text):
            async with self._lock:
                speech = await asyncio.to_thread(self.voices.speak, sentence, who, local=True)
            if speech is None:
                break  # nobody could say it: text only
            audio = interphone_effect(speech.audio, speech.rate)
            self.player.play(Clip(audio, speech.rate, "interphone"))
            kept.append(audio)
            rate = speech.rate
        if kept and CLIPS.enabled:  # (to play again from the radio log, the whole call)
            await asyncio.to_thread(CLIPS.put, clip_id(ev), np.concatenate(kept), rate)

    async def _say_cabin(self, ev: CabinSpeech) -> None:
        who = persona_for(f"cabin {ev.speaker}", "cabin", sex=ev.sex, locale=ev.locale if self.regional else "",
                          language=ev.language)
        rate = 22050
        if ev.chime:
            self.player.play(Clip(chime(rate, ev.chime), rate, "chime"))
        kind = {"pa": "pa", "interphone": "interphone"}.get(ev.channel, "deck")
        for sentence in sentences(ev.spoken or ev.text):
            async with self._lock:
                speech = await asyncio.to_thread(self.voices.speak, sentence, who, local=True)
            if speech is None:
                return  # nobody could say it: text only
            effect = {"pa": pa_effect, "interphone": interphone_effect}.get(kind, in_person_effect)
            audio = effect(speech.audio, speech.rate)
            self.player.play(Clip(audio, speech.rate, kind))
            log.info("Cabin %s as %s (%s %s, %.1f s)", kind, who.key, speech.provider, speech.voice,
                     len(speech.audio) / speech.rate)

    def _cabin_sound(self, ev: CabinSound) -> None:
        rate = 22050
        if ev.sound == "chime":
            self.player.play(Clip(chime(rate, ev.detail or "pa"), rate, "chime"))
        elif ev.sound == "music":
            self._stop_music()
            self._music_task = asyncio.create_task(self._loop_music(ev.seed))
        elif ev.sound == "music_stop":
            self._stop_music()

    async def _loop_music(self, seed: int) -> None:
        """The boarding music, round and round until it's stopped."""
        rate = 22050
        music = await asyncio.to_thread(boarding_music, rate, seed=seed)
        while True:
            playing = Clip(music, rate, "ambience")
            self.player.play(playing)
            await asyncio.to_thread(playing.done.wait)

    def _stop_music(self) -> None:
        if self._music_task is not None:
            self._music_task.cancel()
            self._music_task = None
            self.player.cut("ambience")

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
