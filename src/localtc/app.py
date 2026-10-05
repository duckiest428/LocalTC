"""Wiring: builds the configured source and connects it to the bus and recorder.

This and ``localtc.sim_bridge`` are the only modules allowed to import the
Windows-only bridge.
"""

import asyncio
import logging
import os
import re
import shutil
import struct
import threading
import time
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

import msgspec

from localtc.airports import AirportCache
from localtc.bus import EventBus, Subscription, pump
from localtc.config import AtcConfig, Config, ConfigError, FlightConfig, LlmConfig, data_dir
from localtc.console import CONSOLE
from localtc.gate_data import GateStore
from localtc.recorder import Recorder
from localtc.recorder.format import AUDIO_DIR
from localtc.replay import ReplaySource
from localtc.sim_api import (
    ATC_EVENT_TYPES,
    Airport,
    AirportData,
    AtcTransmission,
    BusEvent,
    FlightArrived,
    RequestAirportData,
    SessionInfo,
    IntercomHeard,
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
        callsign_check=atc.callsign_check,
        radio_range=atc.radio_range,
        chatter=atc.chatter,
        transition_ft=atc.transition_ft,
        phraseology=atc.phraseology,
        seed=atc.seed,
        thresholds=msgspec.convert(atc.phase, PhaseThresholds),
        unscripted=atc.unscripted,
        notams=atc.notams,
        approach=flight.approach,
        sid=flight.sid or None,
        star=flight.star or None,
        dep_runway=flight.dep_runway or None,
        arr_runway=flight.arr_runway or None,
        enforce_fpln_runways=atc.enforce_fpln_runways,
        traffic_runways=atc.traffic_runways,
        airport_fixes=str(data_dir() / "airport_fixes.toml"),
        route=tuple(RouteFix(ident=f.ident, lat=f.lat, lon=f.lon, alt_ft=f.alt_ft, stage=f.stage, time_s=f.time_s,
                             via=f.via) for f in flight.fixes),
    )


def ollama_backend(llm: LlmConfig):
    from localtc.llm import OllamaBackend

    return OllamaBackend(model=llm.model, base_url=llm.base_url, keep_alive=_in_flight_keep_alive(llm.keep_alive),
                         num_ctx=llm.num_ctx, cpu_only=llm.cpu_only, cpu_threads=llm.cpu_threads)


def _in_flight_keep_alive(setting: str) -> str:
    """During a flight the model stays loaded at least 30 minutes past each call (and is refreshed every 10):
    the pilot's setting is for after it."""
    m = re.fullmatch(r"\s*(-?\d+)\s*([smh]?)\s*", setting or "")
    if m is None:
        return setting
    seconds = int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]
    return setting if seconds < 0 or seconds >= 1800 else "30m"


async def language_model(cfg: Config, source: SimSource):
    """The model backend for this session, or None (grammar only). Never fails the session."""
    llm = cfg.llm
    if not llm.enabled or (llm.mode == "off" and not llm.phrasing):
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
    # Loaded somewhere else than asked (CPU only switched on or off since): out, so it loads again where it should.
    placed = await asyncio.to_thread(backend.placement)
    if placed is not None and placed.gpu == llm.cpu_only and not _WARMING.locked():
        log.info("Language model is loaded %s; reloading it %s", "on the graphics card" if placed.gpu else "on the CPU",
                 "on the CPU only" if llm.cpu_only else "where Ollama puts it")
        await asyncio.to_thread(backend.unload)
    # A daemon thread, not the default executor: exit mustn't wait for a slow first load.
    wait_s = llm.timeout_s * (2.0 if llm.cpu_only else 1.0)  # as build_engine sets it
    threading.Thread(target=warm_up, args=(backend, status), kwargs={"wait_s": wait_s, "patience_s": llm.patience_s,
                                                                  "beyond_facts": llm.beyond_facts},
                     name="llm-warm-up", daemon=True).start()
    backend.start_keeping()
    return backend


_WARMING = threading.Lock()  # held while a warm-up loads the model


def warm_up(backend, status=None, timeout_s: float = 180.0, *, wait_s: float | None = None,
            patience_s: float = 15.0, beyond_facts: bool = False) -> float | None:
    """Load the model, and read its prompts (understanding, phrasing, rewording), before the first real call:
    Ollama keeps what it has read, so a call then only reads its own last lines. Then one ordinary call, to see
    how long one takes on this PC now (the sim running): ATC's wait follows it (``llm.backend.waits``), and the
    pilot hears when it's slower than ``wait_s``. Returns seconds taken."""
    from localtc.atc_core.llm import build_request
    from localtc.atc_core.llm.backend import PACE_FACTOR
    from localtc.atc_core.llm.phrase import phrase_request, reword_request
    from localtc.atc_core.llm.understand import load_examples
    from localtc.atc_core.readback import InterpretContext

    if not _WARMING.acquire(blocking=False):
        # A session started again (reconnecting to the sim) while the last one's warm-up is still loading the
        # model: one load at a time. Two at once each took twice as long (82 s and 42 s on one PC).
        log.info("Language model warm-up already under way")
        return None
    started = time.monotonic()
    if hasattr(backend, "loading"):
        backend.loading = True
    try:
        # Phrasing first, understanding last: where Ollama keeps one prompt read, it's the one the pilot's first
        # call needs.
        for request in (phrase_request("radio check", "answer", {"callsign": "November 1 2 3"}, beyond_facts=beyond_facts),
                        reword_request("radio check", "read you five"),
                        build_request("radio check", None, InterpretContext(phase="PARKED"), load_examples())):
            reply = backend.complete(request, timeout_s=timeout_s)
            if reply.text is None:
                log.warning("Language model warm-up failed (%s); the first calls may be slow or use the grammar", reply.error)
                return None
        backend.complete(build_request("request higher", None, InterpretContext(phase="CRUISE"), load_examples()),
                         timeout_s=timeout_s)  # read already: what an ordinary call costs
    finally:
        if hasattr(backend, "loading"):
            backend.loading = False
        _WARMING.release()
    seconds = time.monotonic() - started
    where = backend.placement() if hasattr(backend, "placement") else None
    pace = getattr(backend, "pace_s", None)
    log.info("Language model %s ready (warm-up %.1f s%s)%s", backend.model, seconds,
             f"; a call takes about {pace:.1f} s" if pace else "", f": {where.describe()}" if where else "", extra=CONSOLE)
    if where is not None and where.gpu and where.on_gpu < 0.99:
        # Split between the card and the CPU: slower than either, and the part on the card takes video memory the
        # sim needs (with MSFS there's often little left).
        log.warning("The language model only partly fit on the graphics card (%s). It's quicker, and leaves the "
                    "card to the sim, on the CPU alone: Quick Settings → ATC → \"Run the language model on the CPU only\".", where.describe())
    if pace and wait_s and min(PACE_FACTOR * pace, patience_s) > wait_s:  # (it only ever waits longer, never shorter)
        log.warning("The language model takes about %.1f s a call on this PC right now, too close to ATC's wait of "
                    "%.0f s: ATC waits up to %.0f s for it instead. For quicker answers, fewer calls to it: Quick Settings → "
                    "ATC → Script or model → Semi or Fully scripted.",
                    pace, wait_s, min(PACE_FACTOR * pace, patience_s))
    if where is not None and status is not None:
        on_disk = status.sizes.get(backend.model) or status.sizes.get(f"{backend.model}:latest") or 0
        # LocalTC keeps three prompts read (understanding, phrasing, rewording): three contexts. More is memory for
        # nothing.
        if on_disk and where.size > 3.4 * on_disk:
            log.warning("Ollama holds %.1f GB for this %.1f GB model (context %d): room for more than the three contexts "
                        "LocalTC uses. Setting OLLAMA_NUM_PARALLEL=3 for Ollama frees the rest.",
                        where.size / 1e9, on_disk / 1e9, where.context)
    return seconds


PIPER_S_PER_CHAR = 0.063  # Piper at rate 1, measured over ATC phraseology (tools/pick_speakers.py)


def build_engine(cfg: Config, backend=None):  # noqa: C901
    """The ATC engine, with the language model when there is one."""
    from localtc.atc_core.engine import AtcEngine
    from localtc.atc_core.llm import LlmInterpreter, LlmPhraser

    llm = cfg.llm
    interpreter = phraser = None
    slower = 2.0 if llm.cpu_only and getattr(backend, "cpu_only", False) else 1.0  # a CPU answers in about twice the time
    timeout_s = llm.timeout_s * slower
    budget_s = max(llm.budget_s, llm.timeout_s) * slower  # (a budget shorter than one call would cut every call short)
    if backend is not None and llm.mode != "off":
        interpreter = LlmInterpreter(backend, mode=llm.mode, timeout_s=timeout_s,
                                     max_attempts=llm.max_attempts, budget_s=budget_s, patience_s=llm.patience_s)
    if backend is not None and llm.phrasing:
        phraser = LlmPhraser(backend, timeout_s=timeout_s, max_attempts=llm.max_attempts, budget_s=budget_s,
                             beyond_facts=llm.beyond_facts,
                             patience_s=llm.patience_s)
    engine = AtcEngine(engine_config(cfg.flight, cfg.atc), interpreter=interpreter, phraser=phraser)
    engine.cfg.await_transcripts = cfg.voice.enabled  # ATC waits for each spoken transmission's transcript
    engine.cfg.gate_radius_m = cfg.session.gate_radius_m
    # The airports' real gate names, from OpenStreetMap (cached; fetched in the background). LOCALTC_REAL_GATES=""
    # turns it off whatever the settings say (the tests: no network, the same gates on every machine).
    if cfg.atc.real_gates and os.environ.get("LOCALTC_REAL_GATES", "1"):
        engine.gate_source = GateStore(data_dir() / "gates").get
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
    if cfg.voice.enabled and cfg.crew.enabled and cfg.voice.intercom_joystick and not cfg.live.intercom_input:
        cfg.live.intercom_input = cfg.voice.intercom_joystick
    source = make_source(cfg)
    session = await source.start()
    log.info("Source ready: %s %s %s", session.source_kind, session.sim_product, session.sim_version)

    bus = EventBus()
    recorder: Recorder | None = None
    consumers: list[asyncio.Task] = []
    pump_task: asyncio.Task | None = None
    backend = None
    typed_task: asyncio.Task | None = None
    voice = speaker = None
    engine = atc_service = None
    pm = None
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

            backend = await language_model(cfg, source)
            engine = build_engine(cfg, backend)
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
            if cfg.crew.enabled:  # the copilot on the intercom: hears the pilot, works the aircraft
                from localtc.crew.pm import PilotMonitoring
                from localtc.crew.profiles import load_all
                from localtc.crew.service import CrewService

                from localtc.crew.model import CrewModel

                crew_model = (CrewModel(backend, mode=cfg.crew.llm, timeout_s=cfg.llm.timeout_s * (2.0 if cfg.llm.cpu_only else 1.0),
                                        patience_s=cfg.llm.patience_s)
                              if backend is not None and cfg.crew.llm != "off" else None)
                service = atc_service
                pm = PilotMonitoring(engine, profiles=load_all(data_dir() / "profiles"), model=crew_model,
                                     verbosity=cfg.crew.verbosity, hands=cfg.crew.hands, perf=cfg.flight.perf,
                                     plan_source=cfg.flight.plan_source,
                                     radio_mode=lambda: service.copilot.mode if service.copilot is not None else "off")
                crew = CrewService(pm, bus, source)
                consumers.append(asyncio.create_task(crew.run()))
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

        arrived: asyncio.Event | None = None
        if cfg.atc.enabled and cfg.session.auto_stop_at_gate and (session.source_kind == "live" or cfg.session.auto_stop_in_replay):
            arrived = asyncio.Event()
            consumers.append(asyncio.create_task(_stop_at_gate(bus.subscribe(FlightArrived), arrived, cfg.session.auto_stop_delay_s)))
        pump_task = asyncio.create_task(pump(source, bus))
        if on_ready is not None:
            on_ready(LiveSession(cfg=cfg, bus=bus, source=source, session=session, engine=engine, atc=atc_service,
                                 voice=voice, speaker=speaker, recording=recorder.session_dir if recorder else None,
                                 crew=pm))
        waits = [asyncio.create_task(e.wait()) for e in (stop, arrived) if e is not None]
        await asyncio.wait({pump_task, *waits}, return_when=asyncio.FIRST_COMPLETED)
        for task in waits:
            task.cancel()
    finally:
        if backend is not None and hasattr(backend, "stop_keeping"):
            # The flight's over: the model stays loaded as long as the pilot's setting says, not ours.
            threading.Thread(target=backend.stop_keeping, args=(cfg.llm.keep_alive,), name="llm-release", daemon=True).start()
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
            _save_flight(flight_log, engine, recorder.session_dir if recorder else None)
    return recorder.session_dir if recorder else None


async def _stop_at_gate(events: Subscription, arrived: asyncio.Event, delay_s: float) -> None:
    """Sets ``arrived`` (the session then ends) once ATC sees the aircraft parked at a gate at the destination, after
    ``delay_s`` for ATC's last words. Once: the session is over after it."""
    async for event in events:
        if isinstance(event, FlightArrived):
            log.info("Arrived: %s at %s. The flight stops in %.0f s (Quick Settings → ATC → Stop the flight at the gate).",
                     event.gate, event.airport, delay_s, extra=CONSOLE)
            await asyncio.sleep(delay_s)
            arrived.set()
            return


def _save_flight(flight_log, engine: object | None, recording: Path | None = None) -> None:
    """The flight's line in the logbook, if it went anywhere. A failure here must not lose the session."""
    from localtc.logbook import Logbook

    try:
        record = flight_log.finish(engine)
        if record is not None:
            record.recording = str(recording.resolve()) if recording is not None else ""
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
    crew: object | None = None  # crew.pm.PilotMonitoring

    def now(self) -> float:
        return self.source.clock.now()

    def say(self, text: str) -> None:
        """A typed pilot transmission on COM1."""
        if text.strip():
            self.bus.publish(Transcript(t=self.now(), text=text.strip(), source="typed"))

    def say_crew(self, text: str) -> None:
        """Typed to the copilot, as if said on the intercom."""
        if text.strip():
            self.bus.publish(IntercomHeard(t=self.now(), text=text.strip(), source="typed"))

    def note(self, text: str) -> None:
        self.bus.publish(SessionNote(t=self.now(), text=text))

    def ptt(self, down: bool, radio: int = 1) -> bool:
        """Push-to-talk from the app's button (``radio`` 0: the intercom button). False without voice input."""
        if self.voice is None:
            return False
        (self.voice.service.press if down else self.voice.service.release)(radio)
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
    from localtc.tts.voices import crew_speaker

    copilot_voice = crew_speaker(cfg.crew.voice_sex, cfg.crew.voice_pick, synth.speakers, t.voice)
    service = VoiceOut(bus, synth, player, effect=t.radio_effect, static=t.static, atis=t.atis, copilot=t.copilot,
                       crew_speaker=copilot_voice)
    return VoiceOutput(service, player)


@dataclass
class VoiceInput:
    service: object
    capture: object
    ptt: object | None = None
    intercom: object | None = None  # the intercom key's listener

    def close(self) -> None:
        for listener in (self.ptt, self.intercom):
            if listener is not None:
                listener.stop()
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

        ptt = KeyboardPtt(v.ptt_key, service.press, service.release, on_cancel=service.cancel)
        ptt.start()
    elif v.ptt == "joystick":
        log.info("Push-to-talk: %s (through the sim)", cfg.live.ptt_input or v.ptt_joystick, extra=CONSOLE)
    intercom = None
    if cfg.crew.enabled and v.intercom_key and v.ptt != "enter" and (v.ptt != "keyboard" or v.intercom_key != v.ptt_key):
        from localtc.stt.ptt import KeyboardPtt
        from localtc.stt.service import INTERCOM

        try:
            intercom = KeyboardPtt(v.intercom_key, lambda: service.press(INTERCOM), lambda: service.release(INTERCOM),
                                   what="Intercom (the copilot)", on_cancel=lambda: service.cancel(INTERCOM))
            intercom.start()
        except ValueError as exc:
            log.warning("No intercom key: %s", exc)
    return VoiceInput(service, capture, ptt, intercom)


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
