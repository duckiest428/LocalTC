"""Wiring: builds the configured source and connects it to the bus and recorder.

This and ``localtc.sim_bridge`` are the only modules allowed to import the
Windows-only bridge.
"""

import asyncio
import logging
import shutil
import struct
import threading
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

import msgspec

from localtc.airports import AirportCache
from localtc.bus import EventBus, Subscription, pump
from localtc.config import AtcConfig, Config, ConfigError, FlightConfig, LlmConfig
from localtc.console import CONSOLE
from localtc.recorder import Recorder
from localtc.recorder.format import AUDIO_DIR
from localtc.replay import ReplaySource
from localtc.sim_api import (
    ATC_EVENT_TYPES,
    Airport,
    AirportData,
    AtcTransmission,
    BusEvent,
    RequestAirportData,
    SessionInfo,
    SessionNote,
    SetComFrequency,
    SimSource,
    Transcript,
)

log = logging.getLogger(__name__)


def engine_config(flight: FlightConfig, atc: AtcConfig):
    """Map ``[flight]`` and ``[atc]`` config to the ATC engine's settings."""
    from localtc.atc_core.engine import EngineConfig
    from localtc.atc_core.phase import PhaseThresholds
    from localtc.atc_core.route import RouteFix

    return EngineConfig(
        destination=flight.destination or None,
        cruise_ft=flight.cruise_ft or None,
        callsign=flight.callsign or None,
        rules=flight.rules,
        center_name=atc.center_name,
        center_mhz=atc.center_mhz,
        strict_callsign=atc.strict_callsign,
        transition_ft=atc.transition_ft,
        seed=atc.seed,
        thresholds=msgspec.convert(atc.phase, PhaseThresholds),
        unscripted=atc.unscripted,
        approach=flight.approach,
        sid=flight.sid or None,
        star=flight.star or None,
        route=tuple(RouteFix(ident=f.ident, lat=f.lat, lon=f.lon, alt_ft=f.alt_ft, stage=f.stage, time_s=f.time_s)
                    for f in flight.fixes),
    )


def ollama_backend(llm: LlmConfig):
    from localtc.llm import OllamaBackend

    return OllamaBackend(model=llm.model, base_url=llm.base_url, keep_alive=llm.keep_alive, num_ctx=llm.num_ctx)


async def language_model(cfg: Config, source: SimSource):
    """The model backend for this session, or None (grammar only). Never fails the session."""
    llm = cfg.llm
    if not llm.enabled or (llm.understanding == "off" and not llm.phrasing):
        return None
    if isinstance(source, ReplaySource) and llm.replay != "live":
        if llm.replay == "off":
            return None
        from localtc.atc_core.llm import RecordedBackend

        recorded = RecordedBackend.from_events(source.recording.events())
        if not len(recorded):
            log.info("Recording has no language model answers; replaying with the grammar only (--llm live to ask the model)")
            return None
        log.info("Replaying %d recorded language model answers (%s)", len(recorded), recorded.model)
        return recorded
    backend = ollama_backend(llm)
    status = await asyncio.to_thread(backend.status)
    if not status.reachable:
        log.warning("Ollama isn't running at %s (%s): using the grammar only. Start Ollama, or set [llm] enabled = false.",
                    llm.base_url, status.error)
        return None
    if not status.has(llm.model):
        log.warning("Ollama has no model %r: using the grammar only. Run: ollama pull %s", llm.model, llm.model)
        return None
    # A daemon thread, not the default executor: exit mustn't wait for a slow first load.
    threading.Thread(target=warm_up, args=(backend,), name="llm-warm-up", daemon=True).start()
    return backend


def warm_up(backend, timeout_s: float = 120.0) -> float | None:
    """Load the model and its prompt before the first real call; returns seconds taken."""
    from localtc.atc_core.llm import build_request
    from localtc.atc_core.llm.understand import load_examples
    from localtc.atc_core.readback import InterpretContext

    request = build_request("radio check", None, InterpretContext(phase="PARKED"), load_examples())
    reply = backend.complete(request, timeout_s=timeout_s)
    if reply.text is None:
        log.warning("Language model warm-up failed (%s); the first calls may be slow or use the grammar", reply.error)
        return None
    log.info("Language model %s ready (warm-up %.1f s)", backend.model, reply.latency_ms / 1000, extra=CONSOLE)
    return reply.latency_ms / 1000


PIPER_S_PER_CHAR = 0.063  # Piper at rate 1, measured over ATC phraseology (tools/pick_speakers.py)


def build_engine(cfg: Config, backend=None):  # noqa: C901
    """The ATC engine, with the language model when there is one."""
    from localtc.atc_core.engine import AtcEngine
    from localtc.atc_core.llm import LlmInterpreter, LlmPhraser

    llm = cfg.llm
    interpreter = phraser = None
    if backend is not None and llm.understanding != "off":
        interpreter = LlmInterpreter(backend, mode=llm.understanding, timeout_s=llm.timeout_s,
                                     max_attempts=llm.max_attempts, budget_s=llm.budget_s)
    if backend is not None and llm.phrasing:
        phraser = LlmPhraser(backend, timeout_s=llm.timeout_s, max_attempts=llm.max_attempts, budget_s=llm.budget_s)
    engine = AtcEngine(engine_config(cfg.flight, cfg.atc), interpreter=interpreter, phraser=phraser)
    engine.cfg.await_transcripts = cfg.voice.enabled  # ATC waits for each spoken transmission's transcript
    if cfg.tts.enabled:
        engine.cfg.speech_s_per_char = PIPER_S_PER_CHAR / cfg.tts.rate  # ATC waits for its own words to finish
    return engine


def make_source(cfg: Config) -> SimSource:
    if cfg.source.kind == "live":
        from localtc.sim_bridge.simconnect_source import SimConnectSource

        return SimConnectSource(cfg.live)
    if not cfg.replay.path:
        raise ConfigError("replay.path is required when source.kind = 'replay'")
    return ReplaySource(
        cfg.replay.path,
        speed=cfg.replay.speed,
        start_at=cfg.replay.start_at,
        end_at=cfg.replay.end_at,
        loop=cfg.replay.loop,
        include_radio=cfg.replay.include_radio,
        # A live ATC engine re-creates ATC output; replaying the recorded one would duplicate it.
        exclude_types=(*ATC_EVENT_TYPES, AtcTransmission) if cfg.atc.enabled else (),
    )


async def run_session(
    cfg: Config,
    *,
    record: bool | None = None,
    on_event: Callable[[BusEvent], None] | None = None,
    stop: asyncio.Event | None = None,
    typed_input: bool = False,
    on_ready: Callable[["LiveSession"], None] | None = None,
) -> Path | None:
    """Run the source until it ends, ``stop`` is set, or the task is cancelled (Ctrl-C).

    Returns the recording directory if recording was enabled. With ``typed_input``,
    lines typed on stdin become pilot transmissions (``Transcript`` events). ``on_ready`` gets
    the running session's controls (the app uses them) once everything has started.
    """
    record = cfg.recorder.enabled if record is None else record
    if cfg.voice.enabled and cfg.voice.ptt == "joystick" and not cfg.live.ptt_input:
        cfg.live.ptt_input = cfg.voice.ptt_joystick  # the bridge binds it before connecting
    source = make_source(cfg)
    session = await source.start()
    log.info("Source ready: %s %s %s", session.source_kind, session.sim_product, session.sim_version)

    bus = EventBus()
    recorder: Recorder | None = None
    consumers: list[asyncio.Task] = []
    pump_task: asyncio.Task | None = None
    typed_task: asyncio.Task | None = None
    voice = speaker = None
    engine = atc_service = None
    flight_log = None
    try:
        if record:
            recorder = Recorder.create(
                cfg.recorder.dir,
                session,
                config=msgspec.to_builtins(cfg),
                flush_interval=cfg.recorder.flush_interval_s,
                compress=cfg.recorder.compress,
            )
            await recorder.start()
            if isinstance(source, ReplaySource):
                _copy_audio(source, recorder)
            consumers.append(asyncio.create_task(recorder.consume(bus.subscribe())))
        if on_event is not None:
            consumers.append(asyncio.create_task(_dispatch(bus.subscribe(), on_event)))
        if session.source_kind == "live":
            consumers.append(asyncio.create_task(_cache_airports(bus.subscribe(AirportData), AirportCache())))
        flight_log = None
        if session.source_kind == "live" and cfg.logbook.enabled:
            from localtc.logbook import FlightLog

            flight_log = FlightLog()
            consumers.append(asyncio.create_task(_dispatch(bus.subscribe(), flight_log.feed)))
        if cfg.atc.enabled:
            from localtc.airports import load_airport_dir
            from localtc.atc_core.service import AtcService

            engine = build_engine(cfg, await language_model(cfg, source))
            for directory in cfg.atc.airport_dirs:
                for airport in load_airport_dir(directory):
                    engine.handle(AirportData(t=0.0, airport=airport))
            copilot = None
            if cfg.copilot.mode != "off":
                from localtc.copilot import Copilot

                copilot = Copilot(engine, mode=cfg.copilot.mode, delay_s=(cfg.copilot.delay_min_s, cfg.copilot.delay_max_s))
                log.info("Copilot: %s", "reads back and changes frequencies" if cfg.copilot.mode == "assist"
                         else "works the radio for the whole flight", extra=CONSOLE)
            atc_service = AtcService(engine, bus, source, AirportCache(), copilot=copilot)
            consumers.append(asyncio.create_task(atc_service.run()))
        speaker = await start_tts(cfg, bus) if cfg.tts.enabled and cfg.atc.enabled else None
        if speaker is not None:
            consumers.append(asyncio.create_task(speaker.service.run()))
        voice = None
        if cfg.voice.enabled:
            voice = await start_voice(cfg, bus, source, recorder, engine if cfg.atc.enabled else None)
            consumers.append(asyncio.create_task(voice.service.run()))
        enter_ptt = voice.service if voice is not None and cfg.voice.ptt == "enter" else None
        typed_task = (asyncio.create_task(_typed_transmissions(bus, source, ptt=enter_ptt))
                      if (typed_input or enter_ptt) else None)

        pump_task = asyncio.create_task(pump(source, bus))
        if on_ready is not None:
            on_ready(LiveSession(cfg=cfg, bus=bus, source=source, session=session, engine=engine, atc=atc_service,
                                 voice=voice, speaker=speaker, recording=recorder.session_dir if recorder else None))
        if stop is None:
            await pump_task
        else:
            stop_task = asyncio.create_task(stop.wait())
            await asyncio.wait({pump_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            stop_task.cancel()
    finally:
        await source.stop()
        if pump_task is not None and not pump_task.done():
            with suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(pump_task, timeout=2.0)
        if typed_task is not None:
            typed_task.cancel()  # its thread may sit in readline(); don't wait for another Enter
        if voice is not None:
            voice.close()
        if speaker is not None:
            speaker.close()
        bus.close()
        await asyncio.gather(*consumers, return_exceptions=True)
        if recorder is not None:
            await recorder.close()
            log.info("Recording saved to %s", recorder.session_dir)
        if flight_log is not None:
            _save_flight(flight_log, engine)
    return recorder.session_dir if recorder else None


def _save_flight(flight_log, engine: object | None) -> None:
    """The flight's line in the logbook, if it went anywhere. A failure here must not lose the session."""
    from localtc.logbook import Logbook

    try:
        record = flight_log.finish(engine)
        if record is not None:
            Logbook().add(record)
            log.info("Logbook: %s %s -> %s", record.callsign, record.origin or "?", record.destination or "?")
    except Exception:
        log.exception("Couldn't write the logbook")


@dataclass
class LiveSession:
    """A running session's controls, for the app: everything it can do while a flight is on."""

    cfg: Config
    bus: EventBus
    source: SimSource
    session: SessionInfo
    engine: object | None = None  # AtcEngine
    atc: object | None = None  # AtcService
    voice: "VoiceInput | None" = None
    speaker: "VoiceOutput | None" = None
    recording: Path | None = None

    def now(self) -> float:
        return self.source.clock.now()

    def say(self, text: str) -> None:
        """A typed pilot transmission on COM1."""
        if text.strip():
            self.bus.publish(Transcript(t=self.now(), text=text.strip(), source="typed"))

    def note(self, text: str) -> None:
        self.bus.publish(SessionNote(t=self.now(), text=text))

    def ptt(self, down: bool) -> bool:
        """Push-to-talk from the app's button. False without voice input."""
        if self.voice is None:
            return False
        (self.voice.service.press if down else self.voice.service.release)()
        return True

    async def tune(self, mhz: float, radio: int = 1) -> None:
        from localtc.atc_core.facilities import channel_khz

        await self.source.send(SetComFrequency(hz=channel_khz(mhz) * 1000, radio=radio))

    async def request_airport(self, icao: str) -> None:
        await self.source.send(RequestAirportData(icao=icao.upper()))

    def set_copilot(self, mode: str) -> bool:
        """"off", "assist" or "full", mid-flight. The copilot picks up from where the flight is."""
        if self.atc is None or self.engine is None:
            return False
        if mode == "off":
            self.atc.copilot = None
            return True
        from localtc.copilot import Copilot

        current = self.atc.copilot or getattr(self, "_copilot", None)
        if current is None:
            c = self.cfg.copilot
            current = Copilot(self.engine, mode=mode, delay_s=(c.delay_min_s, c.delay_max_s))
        current.mode = mode
        self._copilot = current
        self.atc.copilot = current
        return True

    @property
    def copilot_mode(self) -> str:
        copilot = getattr(self.atc, "copilot", None)
        return copilot.mode if copilot is not None else "off"

    def mute(self, muted: bool) -> None:
        """ATC's voice off (text only) or back on."""
        if self.speaker is not None:
            self.speaker.player.volume = 0.0 if muted else self.cfg.tts.volume


async def _typed_transmissions(bus: EventBus, source: SimSource, stdin=None, *, ptt=None) -> None:
    """Each line typed on stdin is a pilot transmission on COM1. With ``ptt`` (voice with ptt = "enter"), an
    empty line starts and ends a spoken transmission instead."""
    import sys

    stdin = stdin or sys.stdin
    if ptt is not None:
        print("\n>>> Press Enter to start talking and Enter again to stop (or type a transmission).\n"
              ">>> Tune COM1 first: its line shows which ATC facility answers there.\n", flush=True)
    else:
        print(
            "\n>>> Type a pilot transmission and press Enter (sent on COM1). Tune COM1 first:\n"
            ">>> its line shows which ATC facility answers on that frequency.\n",
            flush=True,
        )
    talking = False
    while True:
        line = await asyncio.to_thread(stdin.readline)
        if not line:
            return
        if text := line.strip():
            bus.publish(Transcript(t=source.clock.now(), text=text, source="typed"))
        elif ptt is not None:
            talking = not talking
            (ptt.press if talking else ptt.release)()
            print(">>> talking... (Enter to stop)" if talking else ">>> sent", flush=True)


@dataclass
class VoiceOutput:
    service: object
    player: object

    def close(self) -> None:
        self.player.close()


async def start_tts(cfg: Config, bus: EventBus) -> VoiceOutput | None:
    """Piper, the radio effect and the speakers. Never fails the session: without them ATC is text only."""
    t = cfg.tts
    try:
        from localtc.tts.player import AudioPlayer
        from localtc.tts.service import VoiceOut
        from localtc.tts.synth import PiperSynth
        from localtc.tts.voices import download, installed
    except ImportError as exc:
        log.warning("ATC voice unavailable (%s): text only. Reinstall with the installer, or pip install piper-tts", exc)
        return None
    voices_dir = Path(t.voices_dir) if t.voices_dir else None
    try:
        if not installed(t.voice, voices_dir):
            log.info("Downloading ATC voice %s (about 80 MB, once) ...", t.voice, extra=CONSOLE)
        voice_file = await asyncio.to_thread(download, t.voice, voices_dir)
        synth = await asyncio.to_thread(PiperSynth, voice_file, rate=t.rate)
        player = AudioPlayer(t.output_device or None, volume=t.volume)
    except Exception as exc:  # no network for the first download, a bad device name, a broken voice file
        log.warning("ATC voice unavailable (%s): text only", exc)
        return None
    log.info("ATC voice: %s through %s", t.voice, player.name, extra=CONSOLE)
    service = VoiceOut(bus, synth, player, effect=t.radio_effect, static=t.static, atis=t.atis, copilot=t.copilot)
    return VoiceOutput(service, player)


@dataclass
class VoiceInput:
    service: object
    capture: object
    ptt: object | None = None

    def close(self) -> None:
        if self.ptt is not None:
            self.ptt.stop()
        self.capture.close()


async def start_voice(cfg: Config, bus: EventBus, source: SimSource, recorder, engine) -> VoiceInput:
    """Microphone, Whisper and the push-to-talk switch for a session."""
    from localtc.stt.audio import AudioCapture
    from localtc.stt.service import VoiceService
    from localtc.stt.whisper import WhisperTranscriber

    v = cfg.voice
    transcriber = WhisperTranscriber(v.model, device=v.device, compute_type=v.compute_type, models_dir=v.models_dir or None,
                                     beam_size=v.beam_size)
    log.info("Loading %s ...", transcriber.description, extra=CONSOLE)
    seconds = await asyncio.to_thread(transcriber.warm_up)
    log.info("%s ready (%.1f s)", transcriber.description, seconds, extra=CONSOLE)
    capture = AudioCapture(v.input_device or None, pre_roll_s=v.pre_roll_ms / 1000)
    capture.start()
    log.info("Microphone: %s%s", capture.name, "" if v.input_device else " (the system default input)", extra=CONSOLE)
    hints = None
    if engine is not None:
        from localtc.voice import flight_hints

        hints = lambda: flight_hints(engine)  # noqa: E731
    service = VoiceService(bus, source.clock.now, transcriber, capture, recorder=recorder, hints=hints,
                           vocabulary=v.vocabulary, tail_s=v.tail_ms / 1000)
    ptt = None
    if v.ptt == "keyboard":
        from localtc.stt.ptt import KeyboardPtt

        ptt = KeyboardPtt(v.ptt_key, service.press, service.release)
        ptt.start()
    elif v.ptt == "joystick":
        log.info("Push-to-talk: %s (through the sim)", cfg.live.ptt_input or v.ptt_joystick, extra=CONSOLE)
    return VoiceInput(service, capture, ptt)


async def _cache_airports(sub: Subscription, cache: AirportCache) -> None:
    async for event in sub:
        try:
            path = await asyncio.to_thread(cache.put, event.airport)
            log.info("Cached %s airport data at %s", event.airport.icao, path)
        except OSError as exc:
            log.warning("Could not cache %s: %s", event.airport.icao, exc)


@dataclass
class FacilityDebugReport:
    session: SessionInfo
    airport: Airport | None = None
    # (message id, Type field, bytes after a 40-byte header) -> count
    messages: Counter = field(default_factory=Counter)
    raw_path: Path | None = None


async def debug_airport(
    cfg: Config, icao: str | None, *, raw_path: Path | None = None, timeout: float = 60.0, dll_factory=None
) -> FacilityDebugReport:
    """Fetch one airport from the live sim (or the nearest one if ``icao`` is None) with raw diagnostics."""
    from localtc.sim_bridge.simconnect_source import SimConnectSource

    raw_file = open(raw_path, "wb") if raw_path else None
    counts: Counter = Counter()

    def tap(buf: bytes) -> None:
        size, _, msg_id = struct.unpack_from("<III", buf)
        facility_data = msg_id in (28, 29, 30, 31) and size > 16
        type_field = struct.unpack_from("<I", buf, 24)[0] if facility_data and len(buf) >= 28 else None
        counts[(msg_id, type_field, size - 40 if facility_data else size)] += 1
        if raw_file:
            raw_file.write(struct.pack("<I", len(buf)) + buf)

    cfg.live.nearest_airport_interval_s = 5.0 if icao is None else 0.0
    source = SimConnectSource(cfg.live, raw_tap=tap, dll_factory=dll_factory)
    try:
        session = await source.start()
        report = FacilityDebugReport(session=session, messages=counts, raw_path=raw_path)
        if icao:
            await source.send(RequestAirportData(icao=icao))

        async def wait() -> None:
            async for event in source.events():
                if isinstance(event, AirportData) and (icao is None or event.airport.icao.upper() == icao.upper()):
                    report.airport = event.airport
                    return

        with suppress(TimeoutError):
            await asyncio.wait_for(wait(), timeout)
        return report
    finally:
        await source.stop()
        if raw_file:
            raw_file.close()


async def _dispatch(sub: Subscription, callback: Callable[[BusEvent], None]) -> None:
    async for event in sub:
        callback(event)


def _copy_audio(source: ReplaySource, recorder: Recorder) -> None:
    """Re-recorded replays keep their audio_refs, so bring the referenced WAVs along."""
    audio = source.recording.root / AUDIO_DIR
    if audio.is_dir():
        shutil.copytree(audio, recorder.session_dir / AUDIO_DIR, dirs_exist_ok=True)
