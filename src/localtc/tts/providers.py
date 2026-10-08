"""Speech synthesizers behind one interface, and the chain that falls back through them.

A *provider* turns words into mono float audio for a ``Persona`` (persona.py): Piper (on this PC, always there),
Kokoro (on this PC, more natural, slower; kokoro.py) and Azure AI Speech (the cloud, optional; azure.py). Each says
whether it's ready, and fails with a ``TtsError`` of a known kind.

``VoiceChain`` asks them in a fixed order (the selected one, then Kokoro when it's on, then Piper) and the first
good audio is spoken. Every answer is checked (``check_audio``: mono, finite, not silent, not far too long for its
words) before it's used. A provider that fails steps aside for a while, the same way each time:

- timeout, network, service down: 30 s, doubling to 10 min;
- rate limited: its ``Retry-After``, else 60 s;
- out of quota: until the quota resets (the provider says when);
- key refused: until the key is changed;
- bad audio: just that line (three in a row: 60 s);
- still working on a line it ran out of time for: skipped until it's done.

When every provider fails, the chain answers None and ATC carries on as text: a voice failing never stops ATC.
"""

import logging
import statistics
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from localtc.tts.persona import Persona
from localtc.tts.synth import Speech
from localtc.tts.voices import SPEAKERS, delivery_for, speaker_for

log = logging.getLogger(__name__)

KINDS = ("timeout", "quota", "rate_limit", "network", "auth", "invalid_audio", "unavailable", "busy", "error")
REST_S = 30.0
REST_MAX_S = 600.0
RATE_LIMIT_S = 60.0
QUOTA_S = 3600.0
INVALID_RUN = 3
MAX_S_PER_CHAR = 0.2  # audio longer than this per character (and over MIN_LIMIT_S) isn't speech of these words
MIN_LIMIT_S = 5.0


class TtsError(Exception):
    def __init__(self, kind: str, detail: str = "", retry_after: float | None = None) -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind if kind in KINDS else "error"
        self.detail = detail
        self.retry_after = retry_after


class Provider(Protocol):
    id: str  # "piper", "kokoro", "azure"
    name: str
    local: bool  # runs on this PC (nothing leaves it)

    def ready(self) -> str:
        """"" when it can speak; else why not ("no key", "model not downloaded")."""
        ...

    def synthesize(self, text: str, persona: Persona) -> Speech:
        """``text`` (already speakable: tts.aviation) in ``persona``'s voice; raises TtsError."""
        ...


def check_audio(speech: Speech, text: str) -> Speech:
    """``speech`` as mono float32 within -1..1, or TtsError("invalid_audio") when it can't be what was asked for."""
    try:
        audio = np.asarray(speech.audio)
        rate = int(speech.rate)
    except (TypeError, ValueError, AttributeError) as exc:
        raise TtsError("invalid_audio", f"not audio ({exc})") from None
    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0
    if audio.ndim == 2:
        audio = audio.mean(axis=1 if audio.shape[1] <= 2 else 0)
    if audio.ndim != 1 or not np.issubdtype(audio.dtype, np.number):
        raise TtsError("invalid_audio", f"shape {audio.shape}")
    audio = audio.astype(np.float32)
    if not 8000 <= rate <= 96000:
        raise TtsError("invalid_audio", f"sample rate {rate}")
    if len(audio) == 0:
        raise TtsError("invalid_audio", "no audio")
    if not np.all(np.isfinite(audio)):
        raise TtsError("invalid_audio", "not finite")
    peak = float(np.max(np.abs(audio)))
    if peak < 1e-4:
        raise TtsError("invalid_audio", "silent")
    limit = max(MIN_LIMIT_S, MAX_S_PER_CHAR * len(text))
    if len(audio) / rate > limit:
        raise TtsError("invalid_audio", f"{len(audio) / rate:.1f} s for {len(text)} characters")
    if peak > 1.0:
        audio = audio / peak
    return Speech(audio, rate, float(speech.latency_ms), speech.provider, speech.voice)


@dataclass
class ProviderState:
    calls: int = 0
    ok: int = 0
    failures: Counter = field(default_factory=Counter)
    last_error: str = ""
    until: float = 0.0  # resting until (the chain's clock)
    strikes: int = 0  # failures in a row
    invalid_run: int = 0
    dead: str = ""  # out until reset (the key refused)
    not_ready: str = ""  # why it can't speak at all
    latency_ms: deque = field(default_factory=lambda: deque(maxlen=50))
    rtf: deque = field(default_factory=lambda: deque(maxlen=50))


class VoiceChain:
    """``providers`` in fallback order (the first is the one chosen). ``timeout_s``: the longest one may take for a
    line before the next is asked."""

    def __init__(self, providers: list[Any], *, timeout_s: float = 8.0, clock: Callable[[], float] = time.monotonic) -> None:
        if not providers:
            raise ValueError("no providers")
        self.providers = list(providers)
        self.timeout_s = timeout_s
        self.clock = clock
        self.states = {p.id: ProviderState() for p in self.providers}
        self._locks = {p.id: threading.Lock() for p in self.providers}
        self._abandoned: set[str] = set()  # providers still on a line they ran out of time for
        self.last_via = ""  # who spoke the last line ("" none: text only)
        self.text_only = 0  # lines nobody could speak

    @property
    def selected(self) -> Any:
        return self.providers[0]

    def speak(self, text: str, persona: Persona) -> Speech | None:
        """The first provider's good audio for ``text``, or None (text only)."""
        for provider in self.providers:
            st = self.states[provider.id]
            if self._resting(provider, st):
                continue
            st.calls += 1
            try:
                speech = check_audio(self._run(provider, text, persona), text)
            except TtsError as exc:
                self._failed(provider, st, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - a synthesizer's own bug is a failure like any other
                self._failed(provider, st, TtsError("error", f"{type(exc).__name__}: {exc}"))
                continue
            st.ok += 1
            st.strikes = st.invalid_run = 0
            st.latency_ms.append(speech.latency_ms)
            seconds = len(speech.audio) / speech.rate
            if seconds > 0:
                st.rtf.append(speech.latency_ms / 1000 / seconds)
            if provider.id != self.last_via:
                if self.last_via or provider is not self.selected:
                    log.info("Voices: %s speaking%s", provider.name,
                             "" if provider is self.selected else f" in place of {self.selected.name}")
                self.last_via = provider.id
            return speech
        if self.last_via:
            log.warning("Voices: no provider could speak; ATC is text only until one can")
        self.last_via = ""
        self.text_only += 1
        return None

    def _resting(self, provider: Any, st: ProviderState) -> bool:
        if st.dead:
            return True
        try:
            st.not_ready = provider.ready() or ""
        except Exception as exc:  # noqa: BLE001
            st.not_ready = f"{type(exc).__name__}: {exc}"
        return bool(st.not_ready) or self.clock() < st.until

    def _run(self, provider: Any, text: str, persona: Persona) -> Speech:
        """``provider.synthesize`` in its own thread, given at most ``timeout_s``. A line it ran out of time for is
        left to finish on its own (its provider skipped until it has: ``busy``)."""
        lock = self._locks[provider.id]
        if provider.id in self._abandoned or not lock.acquire(timeout=self.timeout_s):
            raise TtsError("busy", "still on an earlier line")
        box: dict[str, Any] = {}
        done = threading.Event()

        def work() -> None:
            try:
                box["speech"] = provider.synthesize(text, persona)
            except BaseException as exc:  # noqa: BLE001 - handed to the caller
                box["error"] = exc
            finally:
                self._abandoned.discard(provider.id)
                lock.release()
                done.set()

        threading.Thread(target=work, name=f"localtc-tts-{provider.id}", daemon=True).start()
        if not done.wait(self.timeout_s):
            self._abandoned.add(provider.id)
            if done.is_set():  # finished just now
                self._abandoned.discard(provider.id)
            raise TtsError("timeout", f"no audio in {self.timeout_s:.0f} s")
        if "error" in box:
            raise box["error"]
        return box["speech"]

    def _failed(self, provider: Any, st: ProviderState, exc: TtsError) -> None:
        st.failures[exc.kind] += 1
        st.last_error = str(exc)
        now = self.clock()
        if exc.kind == "auth":
            st.dead = exc.detail or "key refused"
        elif exc.kind == "busy":
            pass
        elif exc.kind == "invalid_audio":
            st.invalid_run += 1
            if st.invalid_run >= INVALID_RUN:
                st.until, st.invalid_run = now + RATE_LIMIT_S, 0
        elif exc.kind == "quota":
            st.until = now + (exc.retry_after or QUOTA_S)
        elif exc.kind == "rate_limit":
            st.until = now + (exc.retry_after or RATE_LIMIT_S)
        else:  # timeout, network, unavailable, error
            st.strikes += 1
            st.until = now + min(REST_S * 2 ** (st.strikes - 1), REST_MAX_S)
        log.info("Voices: %s failed (%s)%s", provider.name, exc,
                 f"; resting {st.until - now:.0f} s" if st.until > now else "")

    def reset(self, provider_id: str) -> None:
        """A provider back in (its key changed): no rest, no strikes."""
        if provider_id in self.states:
            self.states[provider_id] = ProviderState()

    def status(self) -> list[dict]:
        """Each provider: ready or why not, resting, how it has done this flight (latency, real-time factor), and its
        own usage (Azure's characters this month)."""
        now = self.clock()
        out = []
        for i, p in enumerate(self.providers):
            st = self.states[p.id]
            try:
                not_ready = p.ready() or ""
            except Exception as exc:  # noqa: BLE001
                not_ready = str(exc)
            row = {"id": p.id, "name": p.name, "local": bool(p.local), "order": i, "ready": not not_ready and not st.dead,
                   "why": st.dead or not_ready, "resting_s": max(0, round(st.until - now)), "calls": st.calls, "ok": st.ok,
                   "failures": dict(st.failures), "last_error": st.last_error,
                   "latency_ms": round(statistics.median(st.latency_ms)) if st.latency_ms else None,
                   "latency_p95_ms": round(sorted(st.latency_ms)[int(0.95 * (len(st.latency_ms) - 1))]) if st.latency_ms else None,
                   "rtf": round(statistics.median(st.rtf), 3) if st.rtf else None, "speaking": p.id == self.last_via}
            usage = getattr(p, "usage", None)
            if callable(usage):
                row["usage"] = usage()
            out.append(row)
        return out


class PiperVoices:
    """Piper (``synth.PiperSynth``, or anything with ``synthesize(text, speaker)``) as a provider: every station one
    of the multi-speaker voice's speakers, chosen from its name as always (``voices.speaker_for``), the copilot the
    one picked in the settings, and each manner of controller its own pace and expressiveness (voices.DELIVERY)."""

    id = "piper"
    name = "Piper"
    local = True

    def __init__(self, synth: Any, *, speakers: tuple[int, ...] = SPEAKERS) -> None:
        self.synth = synth
        self.speakers = speakers

    def ready(self) -> str:
        return ""

    def speaker(self, persona: Persona) -> int | None:
        count = getattr(self.synth, "speakers", 1)
        if persona.piper_speaker is not None and persona.piper_speaker < count:
            return persona.piper_speaker
        return speaker_for(persona.key, count, self.speakers)

    def synthesize(self, text: str, persona: Persona) -> Speech:
        speaker = self.speaker(persona)
        how = delivery_for(persona.key, persona.manner)
        rate = getattr(self.synth, "rate", None)
        try:
            speech = self.synth.synthesize(text, speaker, rate=rate * how.pace if rate else None,
                                           noise_scale=how.noise_scale, noise_w=how.noise_w)
        except TypeError:  # a synthesizer without the knobs (tests' fakes): its own manner
            speech = self.synth.synthesize(text, speaker)
        voice_file = getattr(self.synth, "voice_file", None)
        voice = f"{voice_file.stem if voice_file else 'piper'}#{speaker}" if speaker is not None else \
            (voice_file.stem if voice_file else "piper")
        return Speech(speech.audio, speech.rate, speech.latency_ms, "piper", voice)
