"""Azure AI Speech (Microsoft's neural voices) in the cloud: optional, off unless chosen, and only with your own key.

Through Microsoft's documented text-to-speech REST API (the same service as the Speech SDK, without its 30 MB native
library): SSML in, 24 kHz 16-bit mono PCM out, from the region of your Speech resource. Every regional English Azure
has (US, British, Irish, Australian, Canadian, Indian, ...) is a pool of voices, so a station in Sydney sounds
Australian and one in London British; within a pool stations are cast as with any provider (``persona.Casting``).

What leaves this PC: the words to be spoken (with the station's voice name, pace and pitch, in SSML), to your Azure
Speech resource's region, while this provider is chosen. Microsoft says it doesn't keep the text or the audio of
real-time synthesis. Nothing else of the flight is sent.

The free tier (F0) gives 0.5 million characters a month and 20 requests a minute. Azure counts the text and any
markup inside the voice element (pace and pitch included), so the markup is kept to what changes the voice. LocalTC
counts what it sends (``Usage``, by calendar month, UTC) and stops short of the allowance; Azure has no way to ask
how much of it is left, so the count is LocalTC's own (another program using the same key isn't in it). Lines heard
before (the ATIS each time round, a repeated "say again") come from a cache on this PC and cost nothing.
"""

import datetime as dt
import hashlib
import json
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np

from localtc.tts.persona import Casting, Persona
from localtc.tts.providers import TtsError
from localtc.tts.synth import Speech

log = logging.getLogger(__name__)

ENDPOINT = "https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
VOICES_URL = "https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list"
OUTPUT = "raw-24khz-16bit-mono-pcm"
SAMPLE_RATE = 24000
USER_AGENT = "LocalTC"
FREE_CHARS = 500_000  # a month, F0
FREE_PER_MINUTE = 20  # requests, F0 (not adjustable)
STOP_AT = 0.98  # of the monthly allowance: LocalTC's count is an estimate
SIGNUP = "https://portal.azure.com/#create/Microsoft.CognitiveServicesSpeechServices"
REGION = re.compile(r"^[a-z0-9]{3,40}$")
# Voices that aren't adults, or aren't for a radio.
LEFT_OUT = re.compile(r"Maisie|^AnaNeural|Multilingual|Dragon|HD|Turbo|:")

# The voices to cast from when the region's list can't be read: Azure's GA English neural voices, by locale and sex.
BUILTIN: dict[str, dict[str, tuple[str, ...]]] = {
    "en-US": {"F": ("Ava", "Emma", "Jenny", "Aria", "Sara", "Nancy", "Michelle", "Jane", "Monica", "Amber"),
              "M": ("Andrew", "Brian", "Guy", "Davis", "Jason", "Tony", "Christopher", "Eric", "Roger", "Steffan")},
    "en-GB": {"F": ("Sonia", "Libby", "Abbi", "Bella", "Hollie", "Olivia"),
              "M": ("Ryan", "Thomas", "Alfie", "Elliot", "Ethan", "Noah", "Oliver")},
    "en-AU": {"F": ("Natasha", "Annette", "Carly", "Elsie", "Freya", "Joanne", "Kim", "Tina"),
              "M": ("William", "Darren", "Duncan", "Ken", "Neil", "Tim")},
    "en-CA": {"F": ("Clara",), "M": ("Liam",)},
    "en-IE": {"F": ("Emily",), "M": ("Connor",)},
    "en-IN": {"F": ("Neerja",), "M": ("Prabhat",)},
    "en-NZ": {"F": ("Molly",), "M": ("Mitchell",)},
    "en-ZA": {"F": ("Leah",), "M": ("Luke",)},
    "en-SG": {"F": ("Luna",), "M": ("Wayne",)},
    "en-HK": {"F": ("Yan",), "M": ("Sam",)},
    "en-PH": {"F": ("Rosa",), "M": ("James",)},
}
# A region with only one or two voices of a sex borrows from its nearest: a station of its own, and the rest near it.
NEAREST = {"en-CA": "en-US", "en-PH": "en-US", "en-IE": "en-GB", "en-IN": "en-GB", "en-NZ": "en-AU",
           "en-ZA": "en-GB", "en-SG": "en-GB", "en-HK": "en-GB", "en-AU": "en-GB", "en-GB": "en-GB", "en-US": "en-US"}

Transport = Callable[[str, str, bytes | None, dict[str, str], float], tuple[int, dict[str, str], bytes]]


def _urllib(method: str, url: str, body: bytes | None, headers: dict[str, str], timeout_s: float):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read() or b""


def month_now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m")


def seconds_to_next_month() -> float:
    now = dt.datetime.now(dt.UTC)
    first = (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=32)).replace(day=1)
    return (first - now).total_seconds()


class Usage:
    """The characters LocalTC sent Azure this calendar month (UTC), kept in a small file (``path``; None: memory)."""

    def __init__(self, path: Path | None, *, limit: int = FREE_CHARS, month: Callable[[], str] = month_now) -> None:
        self.path, self.limit, self._month = path, limit, month
        self._lock = threading.Lock()
        self.data = {"month": month(), "chars": 0, "calls": 0, "cached": 0}
        if path is not None:
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(saved, dict) and saved.get("month") == self.data["month"]:
                    self.data.update({k: int(saved.get(k, 0)) for k in ("chars", "calls", "cached")})
            except (OSError, ValueError):
                pass

    def _roll(self) -> None:
        if self.data["month"] != (m := self._month()):
            self.data = {"month": m, "chars": 0, "calls": 0, "cached": 0}

    @property
    def chars(self) -> int:
        with self._lock:
            self._roll()
            return self.data["chars"]

    def room(self, chars: int, stop_at: float = STOP_AT) -> bool:
        return self.chars + chars <= self.limit * stop_at

    def add(self, chars: int = 0, *, cached: bool = False) -> None:
        with self._lock:
            self._roll()
            if cached:
                self.data["cached"] += 1
            else:
                self.data["chars"] += chars
                self.data["calls"] += 1
            if self.path is not None:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.path.write_text(json.dumps(self.data), encoding="utf-8")
                except OSError as exc:
                    log.info("Azure usage not saved: %s", exc)

    def summary(self) -> dict:
        with self._lock:
            self._roll()
            d = dict(self.data)
        d.update(limit=self.limit, left=max(0, self.limit - d["chars"]),
                 percent=round(100 * d["chars"] / self.limit, 1) if self.limit else 0.0)
        return d


class AudioCache:
    """Speech heard before, on disk (16-bit PCM), so a repeated line isn't sent again: ``max_mb`` at most, the
    oldest dropped first."""

    def __init__(self, folder: Path, max_mb: float = 50.0) -> None:
        self.folder, self.max_bytes = folder, int(max_mb * 2**20)

    @staticmethod
    def key(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]

    def get(self, key: str) -> np.ndarray | None:
        path = self.folder / f"{key}.pcm"
        try:
            data = path.read_bytes()
            path.touch()
        except OSError:
            return None
        return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0 if data else None

    def put(self, key: str, pcm: bytes) -> None:
        if self.max_bytes <= 0:
            return
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / f"{key}.pcm").write_bytes(pcm)
            files = sorted(self.folder.glob("*.pcm"), key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
            while files and total > self.max_bytes:
                oldest = files.pop(0)
                total -= oldest.stat().st_size
                oldest.unlink(missing_ok=True)
        except OSError as exc:
            log.info("Azure audio not cached: %s", exc)


def ssml(text: str, voice: str, locale: str, *, rate: float = 1.0, pitch: int = 0, style: str = "") -> tuple[str, int]:
    """The SSML for one line, and its billable characters (all of it inside ``<voice>``: Azure doesn't bill the
    ``<speak>`` and ``<voice>`` elements). The pace and pitch only when they change something."""
    body = escape(text)
    attrs = []
    percent = round((rate - 1.0) * 100)
    if percent:
        attrs.append(f'rate="{percent:+d}%"')
    if pitch:
        attrs.append(f'pitch="{pitch:+d}%"')
    if attrs:
        body = f"<prosody {' '.join(attrs)}>{body}</prosody>"
    if style:
        body = f'<mstts:express-as style="{style}" styledegree="0.6">{body}</mstts:express-as>'
    doc = (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xmlns:mstts="https://www.w3.org/2001/mstts" '
           f'xml:lang="{locale}"><voice name="{voice}">{body}</voice></speak>')
    return doc, len(body)


class AzureVoices:
    id = "azure"
    name = "Azure AI Speech"
    local = False

    def __init__(self, key: str, region: str, *, usage: Usage | None = None, cache: AudioCache | None = None,
                 rate: float = 1.0, styles: bool = False, per_minute: int = FREE_PER_MINUTE, timeout_s: float = 6.0,
                 transport: Transport = _urllib, clock: Callable[[], float] = time.monotonic) -> None:
        self.key, self.region = key.strip(), region.strip().lower()
        self.counter = usage or Usage(None)
        self.cache = cache
        self.rate, self.styles, self.per_minute, self.timeout_s = rate, styles, per_minute, timeout_s
        self.transport, self.clock = transport, clock
        self.casting = Casting()
        self._sent: deque[float] = deque()
        self._lock = threading.Lock()
        self._voices: dict[str, dict[str, tuple[str, ...]]] | None = None
        self._styles: dict[str, frozenset[str]] = {}

    def ready(self) -> str:
        if not self.key:
            return "no Azure Speech key (Settings > Voices, or the AZURE_SPEECH_KEY environment variable)"
        if not REGION.match(self.region):
            return "no Azure region (the region of your Speech resource, as eastus or westeurope)"
        if not self.counter.room(1):
            return f"this month's free characters are used ({self.counter.chars:,} of {self.counter.limit:,})"
        return ""

    # --- voices ------------------------------------------------------------------------------------------------

    def voices(self) -> dict[str, dict[str, tuple[str, ...]]]:
        """The region's English voices by locale and sex (read once a session; Azure's list, else the built-in)."""
        if self._voices is not None:
            return self._voices
        found: dict[str, dict[str, list[str]]] = {}
        try:
            status, _, body = self.transport("GET", VOICES_URL.format(region=self.region), None, self._headers(), self.timeout_s)
            if status == 200:
                for v in json.loads(body):
                    name, locale = str(v.get("ShortName", "")), str(v.get("Locale", ""))
                    if not locale.startswith("en-") or v.get("VoiceType") != "Neural" or v.get("Status") not in (None, "GA"):
                        continue
                    if LEFT_OUT.search(name.split("-", 2)[-1]):
                        continue
                    sex = "F" if v.get("Gender") == "Female" else "M"
                    found.setdefault(locale, {}).setdefault(sex, []).append(name)
                    self._styles[name] = frozenset(v.get("StyleList") or ())
        except (OSError, ValueError, TypeError) as exc:
            log.info("Azure's voice list unavailable (%s): the built-in list", exc)
        if not found:
            found = {loc: {sex: [f"{loc}-{n}Neural" for n in names] for sex, names in by.items()} for loc, by in BUILTIN.items()}
        self._voices = {loc: {sex: tuple(names) for sex, names in by.items()} for loc, by in found.items()}
        return self._voices

    def pool_for(self, persona: Persona) -> tuple[str, list[str]]:
        """The locale and voices a persona is cast from: its region's English and sex; a region with few voices
        first, then its nearest's; no region in particular: American."""
        voices = self.voices()
        locale = next((loc for loc in voices if loc.lower() == persona.locale.lower()), "en-US")
        sex = persona.sex if persona.sex in ("F", "M") else "M"
        pool = list(voices.get(locale, {}).get(sex, ()))
        near = NEAREST.get(locale, "en-US")
        if len(pool) < 4 and near != locale:
            pool += [v for v in voices.get(near, {}).get(sex, ()) if v not in pool]
        if not pool:
            pool = list(voices.get("en-US", {}).get(sex, ())) or [f"en-US-{BUILTIN['en-US'][sex][0]}Neural"]
        return locale, pool

    def voice_for(self, persona: Persona) -> tuple[str, str]:
        locale, pool = self.pool_for(persona)
        return locale, self.casting.cast(persona, pool)

    # --- speaking ----------------------------------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Ocp-Apim-Subscription-Key": self.key, "User-Agent": USER_AGENT}

    def _take_slot(self) -> None:
        """The free tier's 20 requests a minute: refused here, before Azure refuses it."""
        with self._lock:
            now = self.clock()
            while self._sent and now - self._sent[0] >= 60.0:
                self._sent.popleft()
            if self.per_minute and len(self._sent) >= self.per_minute:
                raise TtsError("rate_limit", f"{self.per_minute} requests a minute", retry_after=60.0 - (now - self._sent[0]))
            self._sent.append(now)

    def synthesize(self, text: str, persona: Persona) -> Speech:
        if why := self.ready():
            raise TtsError("quota" if "used" in why else "unavailable", why,
                           retry_after=seconds_to_next_month() if "used" in why else None)
        locale, voice = self.voice_for(persona)
        style = persona.style if self.styles and persona.style in self._styles.get(voice, frozenset()) else ""
        doc, billable = ssml(text, voice, locale, rate=self.rate * persona.pace, pitch=persona.pitch, style=style)
        started = time.monotonic()
        key = AudioCache.key(self.region, voice, doc)
        if self.cache is not None and (cached := self.cache.get(key)) is not None and len(cached):
            self.counter.add(cached=True)
            return Speech(cached, SAMPLE_RATE, (time.monotonic() - started) * 1000, "azure", f"{voice} (cached)")
        if not self.counter.room(billable):
            raise TtsError("quota", f"this month's free characters are used ({self.counter.chars:,})",
                           retry_after=seconds_to_next_month())
        self._take_slot()
        headers = self._headers() | {"Content-Type": "application/ssml+xml", "X-Microsoft-OutputFormat": OUTPUT}
        try:
            status, got, body = self.transport("POST", ENDPOINT.format(region=self.region), doc.encode("utf-8"), headers,
                                               self.timeout_s)
        except (TimeoutError, socket.timeout) as exc:
            raise TtsError("timeout", str(exc) or "no answer") from None
        except (urllib.error.URLError, OSError) as exc:
            raise TtsError("network", str(getattr(exc, "reason", exc))) from None
        if status != 200:
            self._refused(status, got, body)
        self.counter.add(billable)
        if len(body) < 2 or len(body) % 2:
            raise TtsError("invalid_audio", f"{len(body)} bytes")
        if self.cache is not None:
            self.cache.put(key, body)
        audio = np.frombuffer(body, dtype="<i2").astype(np.float32) / 32768.0
        return Speech(audio, SAMPLE_RATE, (time.monotonic() - started) * 1000, "azure", voice)

    def _refused(self, status: int, headers: dict[str, str], body: bytes) -> None:
        detail = body[:200].decode("utf-8", "replace").strip() or f"HTTP {status}"
        retry = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
        try:
            retry_s = float(retry) if retry else None
        except ValueError:
            retry_s = None
        if status == 429:
            raise TtsError("rate_limit", detail, retry_after=retry_s)
        if status == 403 and "quota" in detail.lower():
            raise TtsError("quota", detail, retry_after=seconds_to_next_month())
        if status in (401, 403):
            raise TtsError("auth", "the key was refused (or isn't for this region)")
        if status >= 500:
            raise TtsError("unavailable", detail)
        raise TtsError("error", f"HTTP {status}: {detail}")

    def usage(self) -> dict:
        """This month's characters (LocalTC's count), the allowance and the request limit, for the status."""
        return self.counter.summary() | {"per_minute": self.per_minute, "region": self.region}
