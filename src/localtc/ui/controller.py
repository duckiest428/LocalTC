"""The app's side of the page: settings, the flight plan, starting and stopping a flight, and
turning bus events into what the page shows.

Everything here runs on the app's asyncio loop. Slow work (downloads, voice previews, SimBrief)
goes to threads, and model downloads report progress to the page as they go.
"""

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time
import zipfile
from collections import deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import msgspec

from localtc import __version__, models
from localtc.airports import AirportCache, load_airport
from localtc.app import LiveSession, run_session
from localtc.config import (
    Config,
    ConfigError,
    data_dir,
    load_config,
    save_settings,
    settings_path,
)
from localtc.console import PHASES, log_dir
from localtc.flightplan import (
    FlightPlan,
    FlightPlanError,
    apply_plan,
    fetch_simbrief,
    load_plan,
    manual_plan,
    save_plan,
)
from localtc.dsp.clips import CLIPS, clip_id
from localtc.radiolog import radio_line
from localtc.sim_api import (
    AirportData,
    AtcAlert,
    AtcThinking,
    AtcTransmission,
    BusEvent,
    CrewSpeech,
    IntercomHeard,
    OwnshipState,
    RadioChatter,
    Transcript,
    PhaseChanged,
    IntercomPressed,
    IntercomReleased,
    PttPressed,
    PttReleased,
    RadioTuned,
    TrafficControlStatus,
    TrafficSnapshot,
    encode_event,
)
from localtc.ui.server import EventStream, HttpError, Raw, sse
from localtc.ui.companion import CompanionHub, CompanionServer, compact_zones
from localtc.ui.pilot import PilotRoutes
from localtc.ui.updates import Updates

log = logging.getLogger(__name__)

RADIO_HISTORY = 400
OWN_EVERY_S = 0.25
FLIGHT_EVERY_S = 1.0
COMPANION_ZONES_EVERY_S = 30.0  # the phone's ATC zones: again after a handoff or a taxi clearance, else this often
FREQ_LABELS = {"atis": "ATIS", "awos": "AWOS", "asos": "ASOS", "clearance": "CLR", "ground": "GND", "tower": "TWR",
               "departure": "DEP", "approach": "APP", "center": "CTR", "unicom": "UNICOM", "ctaf": "CTAF",
               "multicom": "MULTICOM", "fss": "FSS"}
FREQ_ORDER = list(FREQ_LABELS)
DEPARTING = {"PARKED", "PUSHBACK", "TAXI_OUT", "RUNWAY_HOLD", "TAKEOFF", "DEPARTURE", "CRUISE"}


CLEAR_AFTER_S = 600  # a flight's map, details and radio log stay this long after it ends, then the app is clean
SHIFT_BREAK_S = 5 * 3600  # this long between flights and ATC's on a new shift


def next_shift(last_end: float, shift: int, now: float) -> int:
    """ATC's shift for a flight starting ``now``: the next one after a break of 5 hours or more since the last flight
    ended (each station's controller and voice the same through a flight, others after a long break)."""
    return shift + 1 if last_end and now - last_end >= SHIFT_BREAK_S else shift


class AppController:
    def __init__(self, cfg: Config | None = None, *, config_path: str | None = None,
                 plan_path: Path | None = None, cache: AirportCache | None = None) -> None:
        self.config_path = config_path
        first_run = (path := settings_path()) is not None and not path.exists()
        self.cfg = cfg or load_config(config_path)
        if cfg is None and first_run:
            self.cfg.voice.enabled = True  # the app is for flying with a microphone; the CLI asks with --voice
        self.stream = EventStream(lambda: self.on_connect())  # a page that fell behind starts again from all of it
        self.plan_path = plan_path or data_dir() / "flightplan.json"
        self.plan: FlightPlan | None = load_plan(self.plan_path)
        self.cache = cache or AirportCache()
        self.live: LiveSession | None = None
        self.status, self.status_detail = "idle", ""
        self.radio: deque[dict] = deque(maxlen=RADIO_HISTORY)
        self.own: dict | None = None
        self.traffic: list[dict] = []
        self.airports: dict[str, Any] = {}  # icao -> Airport seen this session
        self.flight: dict = {}
        self.jobs: dict[str, dict] = {}
        self.muted = False
        self.account_prompt = False
        self.traffic_control: dict | None = None  # EXPERIMENTAL traffic control's last status  # show the one-time account suggestion (after the third flight)
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None
        self._ticker: asyncio.Task | None = None
        self._own_sent = 0.0
        self._index: dict[str, dict] | None = None
        self._airport_waiters: dict[str, list[asyncio.Future]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_recording: Path | None = None
        self._clear_task: asyncio.Task | None = None
        self.companion = CompanionHub()
        self.companion.set_route(route_view(self.plan))
        self.window_on_top: Callable[[bool], None] | None = None  # set by the app window (ui/__init__.py)
        self.companion_server: CompanionServer | None = None
        self.pilot = PilotRoutes(lambda: self.cfg, self.publish, hub=self.companion,
                                 on_signed_in=self._companion_on, on_signed_out=self._companion_off)
        self.pilot.on_call = self._phone_say  # a call typed on the phone or the website, through the account
        self._last_atc: AtcTransmission | None = None
        self._zones_who: tuple = ()
        self._zones_at = -COMPANION_ZONES_EVERY_S
        self._zones_task: asyncio.Task | None = None
        self.updates = Updates(lambda: self.cfg.ui.updates, self.publish, lambda: self.live is not None)
        # The transmissions kept to play again ([ui] replay_audio): on to a phone watching through the account.
        CLIPS.enabled = self.cfg.ui.replay_audio
        CLIPS.listeners = [self.companion.add_clip]

    # --- wiring ---------------------------------------------------------------------------------------------

    def routes(self) -> dict:
        get, post = "GET", "POST"
        return {
            (get, "state"): self.api_state,
            (post, "flight/start"): self.api_start,
            (post, "flight/stop"): self.api_stop,
            (post, "flight/plan"): self.api_plan,
            (post, "flight/simbrief"): self.api_simbrief,
            (post, "radio/say"): self.api_say,
            (post, "radio/crew"): self.api_crew,
            (post, "voice/crew_preview"): self.api_crew_preview,
            (post, "support"): self.api_support,
            (post, "radio/ptt"): self.api_ptt,
            (post, "radio/tune"): self.api_tune,
            (post, "radio/copilot"): self.api_copilot,
            (post, "radio/mute"): self.api_mute,
            (post, "account/prompt"): self.api_account_prompt,
            (get, "settings"): self.api_settings,
            (post, "settings"): self.api_save_settings,
            (get, "models"): self.api_models,
            (get, "cloud"): self.api_cloud,
            (post, "cloud/key"): self.api_cloud_key,
            (post, "cloud/test"): self.api_cloud_test,
            (get, "tts"): self.api_tts,
            (post, "tts/key"): self.api_tts_key,
            (post, "tts/test"): self.api_tts_test,
            (post, "models/install"): self.api_install,
            (post, "models/profile"): self.api_profile,
            (get, "devices"): self.api_devices,
            (get, "joystick"): self.api_joystick,
            (post, "joystick/detect"): self.api_joystick_detect,
            (post, "voice/preview"): self.api_preview,
            (get, "airport"): self.api_airport,
            (get, "zones"): self.api_zones,
            (get, "clip"): self.api_clip,
            (get, "airports/search"): self.api_search,
            (post, "dev/note"): self.api_note,
            (get, "dev/sessions"): self.api_sessions,
            (post, "dev/export"): self.api_export,
            (post, "open"): self.api_open,
            (get, "update"): self.api_update,
            (post, "update/check"): self.api_update_check,
            (post, "update/download"): self.api_update_download,
            (post, "update/install"): self.api_update_install,
            **self.pilot.routes(),
        }

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._log_handler = _UiLogHandler(self, loop)
        logging.getLogger().addHandler(self._log_handler)
        loop.create_task(self._companion_on())
        if self.cfg.ui.updates != "off":
            loop.call_later(5.0, lambda: asyncio.ensure_future(self.updates.check()))  # after the window is up

    async def _companion_on(self) -> None:
        """The phone's direct line on this network: only for a signed-in account, with the companion on."""
        c = self.cfg.account
        self.companion.remote_map = c.companion_remote_map
        if not (c.companion and c.companion_lan) or not await asyncio.to_thread(lambda: self.pilot.account.signed_in):
            return
        if self.companion_server is None:
            self.companion_server = CompanionServer(self.companion, zones=self.api_zones, say=self._phone_say)
            try:
                port = await self.companion_server.start(c.companion_port)
                log.info("Companion app: listening on the local network, port %d", port)
            except OSError as exc:
                log.warning("Companion app: can't listen on the local network (%s)", exc)
                self.companion_server = None
                return
        await self.pilot.share_connect(self.companion_server.urls(), self.companion.key)

    async def _companion_off(self) -> None:
        if self.companion_server is not None:
            await self.companion_server.close()
            self.companion_server = None

    def detach(self) -> None:
        if getattr(self, "_log_handler", None) is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None

    def on_connect(self) -> list[bytes]:
        """What a page gets as soon as it opens: the whole current picture."""
        out = [sse("state", self.state()), sse("radio_history", list(self.radio))]
        if self.companion.trail:
            out.append(sse("trail", self.companion.trail))  # the path flown so far: the Live Map's green line
        if self.own:
            out.append(sse("own", self.own))
        if self.traffic:
            out.append(sse("traffic", self.traffic))
        return out

    def publish(self, kind: str, data: Any) -> None:
        self.stream.publish(kind, data)
        if kind == "own":
            self.companion.set_own(data)
            self._companion_zones_soon()
        elif kind == "traffic":
            self.companion.set_traffic(data)
        elif kind == "radio":
            self.companion.add_radio(data)
        elif kind == "flight":
            self.companion.set_status(self.companion_view() if data else {"active": False})
            self.companion.set_airports(self.companion_airports() if data else [])
            if data:
                self._companion_zones_soon()
            else:
                self.companion.set_zones(None)

    def state(self) -> dict:
        return {
            "status": self.status, "detail": self.status_detail,
            "source": self.source_kind, "plan": msgspec.to_builtins(self.plan) if self.plan else None,
            "flight": self.flight, "copilot": self.live.copilot_mode if self.live else self.cfg.copilot.mode,
            "copilot_mode": self.cfg.ui.copilot,
            "muted": self.muted, "dev_mode": self.cfg.ui.dev_mode, "voice": self.cfg.voice.enabled, "theme": self.cfg.ui.theme,
            "ptt": {"mode": self.cfg.voice.ptt, "key": self.cfg.voice.ptt_key, "joystick": self.cfg.voice.ptt_joystick,
                    "intercom": self.cfg.voice.intercom_key if self.cfg.crew.enabled else ""},
            "recording": str(self.live.recording) if self.live and self.live.recording else None,
            "jobs": self.jobs, "map_tiles": self.cfg.ui.map_tiles, "platform": sys.platform,
            "simbrief_user": self.cfg.ui.simbrief_user, "lookup_kinds": list(self.cfg.ui.lookup_kinds),
            "version": __version__, "update": self.updates.view(), "coffee_clicked": self.cfg.ui.coffee_clicked,
            "account_prompt": self.account_prompt, "traffic_control": self.traffic_control,
            "welcome": not self.cfg.ui.welcome_seen,
        }

    async def _count_flight(self) -> None:
        """One more flight flown. After the third, without an account, the app suggests one, once."""
        ui = self.cfg.ui
        ui.flights_done += 1
        if self.source_kind == "live":
            ui.last_flight_end = time.time()
        if ui.flights_done >= ACCOUNT_PROMPT_FLIGHTS and not ui.account_prompted:
            signed_in = await asyncio.to_thread(lambda: self.pilot.account.signed_in)
            self.account_prompt = not signed_in
            ui.account_prompted = True  # asked once, whatever the answer
        try:
            await asyncio.to_thread(save_settings, self.cfg, base=load_config(self.config_path, settings=None))
        except Exception as exc:  # noqa: BLE001 - a count not saved never spoils the end of a flight
            log.info("Couldn't save the flight count: %s", exc)

    async def api_account_prompt(self, args: dict) -> dict:
        """The account suggestion answered (either way): not shown again."""
        self.account_prompt = False
        self._push_state()
        return {}

    def _push_state(self) -> None:
        self.publish("state", self.state())

    def _set_status(self, status: str, detail: str = "") -> None:
        self.status, self.status_detail = status, detail
        self._push_state()
        self.apply_on_top()

    def apply_on_top(self) -> None:
        """The window above the others, as ``[ui] on_top`` asks: always, or while a flight is running."""
        if self.window_on_top is None:
            return
        wanted = self.cfg.ui.on_top == "always" or (self.cfg.ui.on_top == "flying" and self.status in ("starting", "running"))
        try:
            self.window_on_top(wanted)
        except Exception as exc:  # the window is closing, or the platform can't
            log.debug("Couldn't set the window on top: %s", exc)

    def system(self, text: str, level: str = "info") -> None:
        self._radio({"kind": "system", "text": text, "level": level, "t": self.live.now() if self.live else None})

    def _radio(self, line: dict) -> None:
        self.radio.append(line)
        self.publish("radio", line)

    # --- a flight ---------------------------------------------------------------------------------------------

    def flight_config(self) -> Config:
        """The config a flight starts with: the settings, plus the flight plan."""
        cfg = msgspec.convert(msgspec.to_builtins(self.cfg), Config)
        cfg.source.kind = self.source_kind
        if self.plan is not None:
            apply_plan(self.plan, cfg.flight)
        return cfg

    @property
    def source_kind(self) -> str:
        """What the app flies: its own ``[ui] source`` setting, which starts as the live sim. ``[source] kind`` in
        config/localtc.toml is the command line's development default (a recording) and never applies here."""
        return self.cfg.ui.source

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            raise HttpError(409, "a flight is already running")
        if self.source_kind == "live" and next_shift(self.cfg.ui.last_flight_end, self.cfg.atc.shift, time.time()) != self.cfg.atc.shift:
            self.cfg.atc.shift += 1  # back after a break: other controllers on (saved with the flight count)
            log.info("ATC: a new shift (%d) since the last flight", self.cfg.atc.shift)
        if self._clear_task is not None:
            self._clear_task.cancel()
            self._clear_task = None
        cfg = self.flight_config()
        record = cfg.recorder.enabled or cfg.ui.dev_mode
        self._stop = asyncio.Event()
        self.flight, self.own, self.traffic = {}, None, []
        self.radio.clear()
        self.publish("radio_history", [])
        self._set_status("starting", "Loading the models ...")
        if cfg.source.kind == "replay":
            self.system(f"Developer mode: replaying the recording {cfg.replay.path}, not the sim "
                        "(Quick Settings > Sim to change)", "warn")
        else:
            self.system("Connecting to MSFS 2024 ...")
        self._task = asyncio.create_task(self._run(cfg, record))

    async def _run(self, cfg: Config, record: bool) -> None:
        try:
            recording = await run_session(cfg, record=record, on_event=self.on_event, stop=self._stop,
                                          on_ready=self._on_ready)
            self._last_recording = recording or self._last_recording
            self.system(f"Flight ended. Recording: {recording}" if recording else "Flight ended.")
            self.companion.set_status({"active": False})
            asyncio.create_task(self.pilot.live({"active": False}, force=True))
            asyncio.create_task(self.pilot.after_flight())  # the logbook's new line, to the account if signed in
            await self._count_flight()
            self._clear_soon()
            self._set_status("idle")
        except asyncio.CancelledError:
            self._set_status("idle")
        except (ConfigError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            log.warning("Flight stopped: %s", exc)
            self._clear_soon()
            self._set_status("error", str(exc))
        except Exception as exc:
            log.exception("Flight failed")
            self._clear_soon()
            self._set_status("error", f"{type(exc).__name__}: {exc}")
        finally:
            self.live = None
            if self._ticker:
                self._ticker.cancel()

    def _clear_soon(self) -> None:
        """The flight over: its plan is no longer kept for the next time the app opens (it opens clean), and in
        ``CLEAR_AFTER_S`` the map, the flight's details and the radio log are cleared, unless another flight began."""
        if self._clear_task is not None:
            self._clear_task.cancel()
        flown = self.plan
        if flown is not None and self.source_kind == "live":
            try:
                self.plan_path.unlink(missing_ok=True)
            except OSError as exc:
                log.debug("Couldn't remove the flown plan: %s", exc)
        self._clear_task = asyncio.create_task(self._clear_later(flown))

    async def _clear_later(self, flown: FlightPlan | None) -> None:
        await asyncio.sleep(CLEAR_AFTER_S)
        if self.live is None and self.status not in ("starting", "running"):
            self.clear_flight(flown)

    def clear_flight(self, flown: FlightPlan | None = None) -> None:
        """Nothing of the last flight left on screen: the aircraft, the traffic, the path, the radio log and the
        flight's details; its plan too (``flown``), unless a new one was loaded since."""
        self._clear_task = None
        self.flight, self.own, self.traffic = {}, None, []
        self.radio.clear()
        self.companion.trail = []
        if flown is not None and self.plan is flown:
            self.plan = None
            self.companion.set_route(route_view(None))
        self.publish("cleared", {})
        self._push_state()
        log.info("The last flight cleared from the screen")

    def _on_ready(self, live: LiveSession) -> None:
        self.live = live
        self._last_recording = live.recording or self._last_recording
        if self.muted:
            live.mute(True)
        self._set_status("running", f"{live.session.sim_product} {live.session.sim_version}".strip())
        self._ticker = asyncio.create_task(self._tick())

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            self._set_status("stopping")
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=15)
            except TimeoutError:
                self._task.cancel()

    async def shutdown(self) -> None:
        await self.stop()
        await self._companion_off()
        self.detach()

    async def _tick(self) -> None:
        while self.live is not None:
            try:
                self.flight = self.flight_view()
                self.publish("flight", self.flight)
                await self.pilot.live(self.companion_view())
            except Exception:
                log.exception("Flight view failed")
            await asyncio.sleep(FLIGHT_EVERY_S)

    def flight_view(self) -> dict:
        """What the ATC tab shows above the radio log."""
        engine = self.live.engine if self.live else None
        if engine is None:
            return {}
        snap = engine.snapshot()
        departing = snap.phase in DEPARTING or snap.phase is None
        icao = (snap.origin if departing else snap.destination) or snap.origin or snap.destination
        airport = self.airports.get(icao or "") or (engine.geometry(icao).airport if engine.geometry(icao) else None)
        atis = engine.current_atis(icao)
        a = snap.assignments
        pos = snap.position
        dest_geo = engine.geometry(snap.destination)
        ete = None
        if dest_geo is not None and pos and pos.get("gs_kt", 0) > 40:
            from localtc.sim_api.geo import haversine_nm

            nm = haversine_nm(pos["lat"], pos["lon"], dest_geo.airport.lat, dest_geo.airport.lon)
            ete = {"nm": round(nm), "min": round(nm / pos["gs_kt"] * 60)}
        return {
            "callsign": snap.callsign, "aircraft": getattr(engine.state.flight, "aircraft_type", None),
            "origin": snap.origin, "destination": snap.destination, "cruise_ft": snap.cruise_ft,
            "phase": snap.phase, "phase_label": PHASES.get(snap.phase or "", snap.phase or ""),
            "airport": airport_summary(airport, engine) if airport else ({"icao": icao} if icao else None),
            "atis": atis.letter if atis else None,
            "atis_source": engine.atis_source_text(atis, engine.state.aircraft) if atis else None,
            "squawk": a.get("squawk"), "altitude_ft": a.get("altitude_ft"),
            "runway": a.get("departure_runway") if departing else a.get("arrival_runway"),
            "approach": str(a["approach"]) if a.get("approach") else None,
            "tuned": msgspec.to_builtins(snap.tuned) if snap.tuned else None,
            "expected": msgspec.to_builtins(snap.expected_contact) if snap.expected_contact else None,
            "pending": msgspec.to_builtins(snap.pending) if snap.pending else None,
            "ete": ete, "rules": getattr(engine.state.flight, "rules", "IFR"),
            "taxi": f"{taxi['to']} via {' '.join(taxi['taxiways'])}" if (taxi := engine.taxi_path()) else None,
        }

    # --- bus events -> the page --------------------------------------------------------------------------------

    def on_event(self, ev: BusEvent) -> None:  # noqa: C901
        if isinstance(ev, OwnshipState):
            if ev.t - self._own_sent >= OWN_EVERY_S:
                self._own_sent = ev.t
                self.own = own_view(ev)
                self.publish("own", self.own)
            return
        if isinstance(ev, TrafficSnapshot):
            self.traffic = [{"id": t.object_id, "callsign": t.atc_id or f"{t.airline} {t.flight_number}".strip(),
                             "type": _model(t.atc_model), "lat": t.lat, "lon": t.lon, "alt": round(t.alt_ft),
                             "hdg": round(t.hdg_true), "gs": round(t.gs_kt), "ground": t.on_ground} for t in ev.targets]
            self.publish("traffic", self.traffic)
            return
        if isinstance(ev, TrafficControlStatus):  # LocalTC's traffic, for Quick Settings
            self.traffic_control = {"mode": ev.mode, "source": ev.source, "live": ev.live, "parked": ev.parked,
                                    "native": ev.native, "fsltl": ev.fsltl, "note": ev.note, "recent": list(ev.recent)}
            self.publish("traffic_control", self.traffic_control)
            return
        if isinstance(ev, AtcThinking):
            self.publish("thinking", {"station": ev.station, "mhz": ev.frequency_mhz, "busy": ev.busy})
        if self.cfg.ui.dev_mode and not isinstance(ev, AirportData):
            self.publish("dev", {"t": round(ev.t, 2), "json": encode_event(ev).decode(errors="replace")[:4000]})
        line = radio_line(ev)
        if line is not None:
            if CLIPS.enabled and self._has_audio(ev):
                line["audio"] = clip_id(ev)  # its audio, to play again: the app, the phone and the website ask by this
            self._radio(line)
        if isinstance(ev, AtcAlert):
            self.companion.on_event(ev)
        if isinstance(ev, AirportData):
            self.airports[ev.airport.icao] = ev.airport
            if self._index is not None:
                self._index[ev.airport.icao] = _index_entry(ev.airport)
            for future in self._airport_waiters.pop(ev.airport.icao, []):
                if not future.done():
                    future.set_result(ev.airport)
            self.publish("airport", {"icao": ev.airport.icao})
        elif isinstance(ev, PttPressed | PttReleased):
            self.publish("ptt", {"down": isinstance(ev, PttPressed)})
        elif isinstance(ev, IntercomPressed | IntercomReleased):
            self.publish("ptt", {"down": isinstance(ev, IntercomPressed), "intercom": True})
        elif isinstance(ev, PhaseChanged | RadioTuned | AtcTransmission):
            if isinstance(ev, AtcTransmission):
                self._last_atc = ev
                self.companion.on_event(ev)
            if self.live is not None and self.live.engine is not None:
                self.flight = self.flight_view()
                self.publish("flight", self.flight)
                asyncio.create_task(self.pilot.live(self.companion_view(), force=not isinstance(ev, AtcTransmission)))

    def _has_audio(self, ev: BusEvent) -> bool:
        """Whether a radio log line's audio will be kept: what was spoken aloud, or said into the microphone."""
        speaking = self.live is not None and self.live.speaker is not None
        if isinstance(ev, AtcTransmission | RadioChatter | CrewSpeech):
            return speaking
        if isinstance(ev, Transcript):
            return bool(ev.text) and (ev.source == "voice" or (ev.source == "copilot" and speaking and self.cfg.tts.copilot))
        if isinstance(ev, IntercomHeard):
            return bool(ev.text) and ev.source == "voice"
        return False

    async def api_clip(self, args: dict) -> Raw:
        """A kept transmission's audio (8 kHz WAV), by its radio line's ``audio`` id."""
        from localtc.ui.companion import clip_when_ready

        wav = await clip_when_ready(str(args.get("id", "")))
        if wav is None:
            raise HttpError(404, "That transmission isn't kept (Quick Settings → Voice → Play buttons).")
        return Raw("audio/wav", wav)

    def _gate(self) -> str | None:
        engine = self.live.engine if self.live else None
        return engine.state.assignments.gate if engine is not None else None

    def companion_view(self) -> dict:
        """What the companion app shows: the flight's phase, who to talk to, and ATC's last words. No position."""
        f = self.flight or {}
        atc = self._last_atc
        return {
            "active": True, "callsign": f.get("callsign"), "aircraft": f.get("aircraft"),
            "origin": f.get("origin"), "destination": f.get("destination"), "phase": f.get("phase"),
            "phase_label": f.get("phase_label"), "squawk": f.get("squawk"), "altitude_ft": f.get("altitude_ft"),
            "runway": f.get("runway"), "tuned": _station(f.get("tuned")), "next": _station(f.get("expected")),
            "ete": f.get("ete"), "gate": self._gate(), "rules": f.get("rules"),
            "crew": getattr(self.live, "crew", None) is not None,  # the copilot can be talked to (typed to)
            "last_atc": {"station": atc.station, "mhz": atc.frequency_mhz, "text": atc.text} if atc else None,
        }

    def _companion_zones_soon(self) -> None:
        """The Live Map's ATC zones for the phone and the website's Flight Tracker: worked out again after a
        handoff, a taxi clearance or a new runway, and every so often as the flight moves on."""
        f = self.flight
        if not f or self.live is None or not self.cfg.account.companion:
            return
        who = (repr(f.get("tuned")), repr(f.get("expected")), f.get("taxi"), f.get("runway"), self._gate())
        if who == self._zones_who and time.monotonic() - self._zones_at < COMPANION_ZONES_EVERY_S:
            return
        if self._zones_task is not None and not self._zones_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._zones_who, self._zones_at = who, time.monotonic()
        self._zones_task = loop.create_task(self._companion_zones())

    async def _companion_zones(self) -> None:
        try:
            z = await self.api_zones({})
        except Exception:
            log.debug("The companion's ATC zones failed", exc_info=True)
            return
        if self.flight:
            self.companion.set_zones(compact_zones(z))

    def companion_airports(self) -> list[dict]:
        """The flight's airports for the phone's Frequencies and Airports tabs: published data only."""
        engine = self.live.engine if self.live else None
        if engine is None:
            return []
        out = []
        for icao, role in ((engine.state.flight.origin, "departure"), (engine.state.flight.destination, "arrival")):
            geo = engine.geometry(icao) if icao else None
            if geo is None or any(a["icao"] == icao for a in out):
                continue
            detail = airport_detail(geo.airport)
            atis = engine.current_atis(icao)
            out.append({
                "icao": icao, "name": detail["name"], "role": role, "lat": detail["lat"], "lon": detail["lon"],
                "elev_ft": detail["elev_ft"], "atis": atis.letter if atis else None,
                "frequencies": sorted(({k: f[k] for k in ("label", "kind", "mhz", "name")} for f in detail["all_frequencies"]),
                                      key=lambda f: FREQ_ORDER.index(f["kind"]) if f["kind"] in FREQ_ORDER else 99),
                "runways": [{"name": r["name"], "length_ft": r["length_ft"], "heading_mag": r["heading_mag"], "ils": r["ils"]}
                            for r in detail["runways"]],
            })
        return out

    def _phone_say(self, text: str, to: str = "atc") -> None:
        """A call typed on the companion app (on the local network or through the account) or the website's Flight
        Tracker: transmitted on COM1 like one typed here, or ``to`` "crew": said to the copilot on the intercom."""
        try:
            live = self._need_live()
        except HttpError as exc:
            raise RuntimeError(str(exc)) from None
        if to == "crew":
            if getattr(live, "crew", None) is None:
                raise RuntimeError("The copilot isn't on (Quick Settings → Copilot → Intercom)")
            live.say_crew(text)
        else:
            live.say(text, radio=2 if to == "com2" else 1)

    # --- the page's calls ----------------------------------------------------------------------------------------

    async def api_update(self, args: dict) -> dict:
        return self.updates.view()

    async def api_update_check(self, args: dict) -> dict:
        return await self.updates.check(force=True)

    async def api_update_download(self, args: dict) -> dict:
        return await self.updates.download()

    async def api_update_install(self, args: dict) -> dict:
        try:
            return await self.updates.install_now()
        except RuntimeError as exc:
            raise HttpError(409, str(exc)) from None

    async def api_state(self, args: dict) -> dict:
        return self.state()

    async def api_start(self, args: dict) -> dict:
        await self.start()
        return self.state()

    async def api_stop(self, args: dict) -> dict:
        await self.stop()
        return self.state()

    async def api_plan(self, args: dict) -> dict:
        """A typed-in plan (``manual``), or the SimBrief plan the page fetched (``plan``)."""
        try:
            if isinstance(args.get("plan"), dict):
                plan = msgspec.convert(args["plan"], FlightPlan)
            else:
                plan = manual_plan(callsign=args.get("callsign", ""), origin=args.get("origin", ""),
                                   destination=args.get("destination", ""), cruise=args.get("cruise", ""),
                                   alternate=args.get("alternate", ""), route=args.get("route", ""),
                                   aircraft=args.get("aircraft", ""), rules=args.get("rules", "IFR"))
        except (FlightPlanError, msgspec.ValidationError) as exc:
            raise HttpError(400, str(exc)) from None
        self.plan = plan
        self.companion.set_route(route_view(plan))
        await asyncio.to_thread(save_plan, plan, self.plan_path)
        if args.get("start"):
            if self._task is not None and not self._task.done():
                await self.stop()
            await self.start()
        self._push_state()
        return {"plan": msgspec.to_builtins(plan), "summary": plan.summary()}

    async def api_simbrief(self, args: dict) -> dict:
        user = str(args.get("user") or self.cfg.ui.simbrief_user).strip()
        try:
            plan = await asyncio.to_thread(fetch_simbrief, user)
        except FlightPlanError as exc:
            raise HttpError(400, str(exc)) from None
        if user != self.cfg.ui.simbrief_user:
            self.cfg.ui.simbrief_user = user
            await asyncio.to_thread(save_settings, self.cfg, base=load_config(self.config_path, settings=None))
        return {"plan": msgspec.to_builtins(plan), "summary": plan.summary()}

    def _need_live(self) -> LiveSession:
        if self.live is None:
            raise HttpError(409, "start a flight first")
        return self.live

    async def api_say(self, args: dict) -> dict:
        self._need_live().say(str(args.get("text", "")), radio=2 if args.get("radio") in (2, "2") else 1)
        return {}

    async def api_support(self, args: dict) -> dict:
        """Feedback or a support request from Quick Settings, emailed to the maintainer through the server."""
        from localtc.account import AccountError

        message = {k: str(args.get(k, "")).strip() for k in ("kind", "message")}
        message.update(version=__version__, platform=_platform_name(), source="app")
        try:
            answer = await asyncio.to_thread(self.pilot.account.support, message)
        except AccountError as exc:
            raise HttpError(exc.status if 400 <= exc.status < 500 else 502, str(exc)) from None
        return {"message": answer}

    async def api_crew(self, args: dict) -> dict:
        """Typed to the copilot (the intercom), not transmitted."""
        self._need_live().say_crew(str(args.get("text", "")))
        return {}

    async def api_crew_preview(self, args: dict) -> dict:
        """The copilot's voice ([crew] voice_sex and voice_pick, or the ones given), on the intercom."""
        sex = str(args.get("sex") or self.cfg.crew.voice_sex)
        pick = int(args.get("pick", self.cfg.crew.voice_pick))
        seconds = await asyncio.to_thread(_crew_preview, self.cfg, sex, pick)
        return {"seconds": seconds}

    async def api_ptt(self, args: dict) -> dict:
        if not self._need_live().ptt(bool(args.get("down")), 0 if args.get("intercom") else 1):
            raise HttpError(409, "voice input is off (Quick Settings > Push-to-talk)")
        return {}

    async def api_tune(self, args: dict) -> dict:
        mhz = float(args["mhz"])
        if not 118.0 <= mhz <= 137.0:
            raise HttpError(400, f"{mhz} isn't a COM frequency")
        await self._need_live().tune(mhz, int(args.get("radio", 1)))
        return {}

    async def api_copilot(self, args: dict) -> dict:
        """The copilot switch: on works the radio the way Quick Settings says (``ui.copilot``), off gives it back."""
        if "mode" in args:
            if args["mode"] not in ("assist", "full"):
                raise HttpError(400, "mode: assist or full")
            self.cfg.ui.copilot = args["mode"]
            on = self.cfg.copilot.mode != "off"
        else:
            on = bool(args.get("on"))
        mode = self.cfg.ui.copilot if on else "off"
        self.cfg.copilot.mode = mode
        await asyncio.to_thread(save_settings, self.cfg, base=load_config(self.config_path, settings=None))
        if self.live is not None and self.live.copilot_mode != mode:
            self.live.set_copilot(mode)
            self.system("Copilot: " + {"off": "off, you work the radio", "assist": "reads back and changes frequencies",
                                       "full": "works the radio"}[mode])
        self._push_state()
        return self.state()

    async def api_mute(self, args: dict) -> dict:
        self.muted = bool(args.get("muted"))
        if self.live is not None:
            self.live.mute(self.muted)
        self._push_state()
        return {"muted": self.muted}

    async def _traffic_on(self, cfg) -> None:
        """LocalTC's traffic switched on or off in a flight: started now (a live flight), or its aircraft taken out."""
        live = self.live
        if cfg.traffic.enabled and live.traffic is None and live.session.source_kind == "live":
            from localtc.app import start_traffic

            live.traffic = start_traffic(cfg, live.engine, live.source, live.bus)
            self._traffic_task = asyncio.ensure_future(live.traffic.run())
        elif live.traffic is not None:
            await live.traffic.set_on(cfg.traffic.enabled)
        if not cfg.traffic.enabled:
            self.traffic_control = {"mode": "off"}
            self.publish("traffic_control", self.traffic_control)

    async def api_settings(self, args: dict) -> dict:
        return {"settings": msgspec.to_builtins(self.cfg), "path": str(settings_path() or ""),
                "catalog": models.catalog()}

    async def api_save_settings(self, args: dict) -> dict:
        changes = args.get("settings")
        if not isinstance(changes, dict):
            raise HttpError(400, "send {settings: {section: {name: value}}}")
        from localtc.config import merge

        try:
            cfg = msgspec.convert(merge(msgspec.to_builtins(self.cfg), changes), Config)
        except msgspec.ValidationError as exc:
            raise HttpError(400, f"setting not valid: {exc}") from None
        for key in ("ptt_key", "intercom_key"):
            value = getattr(cfg.voice, key)
            if value != getattr(self.cfg.voice, key) and value and sys.platform == "win32":  # key names differ by OS
                try:
                    from localtc.stt.ptt import parse_key

                    parse_key(value)
                except ImportError:
                    pass
                except ValueError as exc:
                    raise HttpError(400, str(exc)) from None
        if cfg.voice.intercom_key and cfg.voice.intercom_key == cfg.voice.ptt_key:
            raise HttpError(400, "The intercom key can't be the push-to-talk key")
        path = await asyncio.to_thread(save_settings, cfg, base=load_config(self.config_path, settings=None))
        live_now = self.live is not None
        interpreter = getattr(self.live.engine, "interpreter", None) if self.live is not None else None
        if cfg.llm.mode != self.cfg.llm.mode and cfg.llm.mode != "off" and getattr(interpreter, "backend", None) is not None:
            interpreter.mode = cfg.llm.mode  # the model's already there: the new mode from the next call
        if (phraser := getattr(self.live.engine, "phraser", None) if self.live is not None else None) is not None:
            phraser.beyond_facts = cfg.llm.beyond_facts  # from the next reply
        if (crew := getattr(self.live, "crew", None) if self.live is not None else None) is not None:
            crew.monitor.verbosity = cfg.crew.verbosity  # what the copilot says by itself, from now
            crew.monitor.hands = cfg.crew.hands == "pm"
            crew.monitor.repeat_atc = cfg.crew.repeat_atc
            if crew.model is not None:  # the copilot's model: its use and its checks from the next question
                crew.model.mode = "off" if cfg.crew.llm == "off" else cfg.crew.mode
                crew.model.beyond_facts = cfg.crew.beyond_facts
        if cfg.traffic.enabled != self.cfg.traffic.enabled and self.live is not None:
            await self._traffic_on(cfg)
        CLIPS.enabled = cfg.ui.replay_audio
        if not CLIPS.enabled:
            CLIPS.clear()
        self.cfg = cfg
        self.companion.remote_map = cfg.account.companion_remote_map
        self.apply_on_top()
        if self.live is not None and self.live.speaker is not None and not self.muted:
            self.live.speaker.player.volume = cfg.tts.volume
        self._push_state()
        return {"saved": str(path), "restart": live_now}

    async def api_cloud(self, args: dict) -> dict:
        """The cloud services: which there are, which have a key (never the key itself), and how each is doing in
        this flight."""
        from localtc.app import cloud_keys
        from localtc.llm.cloud import PROVIDERS

        keys = await asyncio.to_thread(lambda: cloud_keys().all())
        backend = getattr(getattr(self.live.engine, "phraser", None) or getattr(self.live.engine, "interpreter", None),
                          "backend", None) if self.live is not None else None
        live = backend.status() if getattr(backend, "rich", False) else []
        return {"providers": [{"id": p.id, "name": p.name, "key": p.key, "has_key": p.id in keys, "signup": p.signup,
                               "note": p.note, "models": list(self.cfg.cloud.models.get(p.id) or p.models)} for p in PROVIDERS],
                "live": live, "via": getattr(backend, "last_via", "") if live else ""}

    async def api_cloud_key(self, args: dict) -> dict:
        from localtc.app import cloud_keys
        from localtc.llm.cloud import BY_ID

        provider = str(args.get("provider") or "")
        if provider not in BY_ID:
            raise HttpError(400, f"unknown service {provider!r}")
        await asyncio.to_thread(cloud_keys().set, provider, str(args.get("key") or ""))
        return await self.api_cloud({})

    async def api_cloud_test(self, args: dict) -> dict:
        """Each service asked once, now: which answer, how fast, and why not."""
        from localtc.app import cloud_routes
        from localtc.llm.cloud import check, discover

        wanted = args.get("provider")
        found = [r for r in await asyncio.to_thread(cloud_routes, self.cfg) if not wanted or r.provider.id == wanted]
        found = await asyncio.to_thread(discover, found)
        if not wanted:
            found = list({r.provider.id: r for r in reversed(found)}.values())[::-1]  # each service's first model
        results = await asyncio.to_thread(check, found, timeout_s=15.0)  # one service: every model it has
        return {"results": [r.__dict__ for r in results]}

    async def api_tts(self, args: dict) -> dict:
        """The voices: the provider chosen and its fallbacks, whether each can speak and why not, how each has done this
        flight (latency, real-time factor, failures), Azure's characters this month, and whether a key is set (never
        the key itself)."""
        from localtc.app import KEY_ENV, tts_key, tts_region, voice_chain
        from localtc.tts import kokoro

        t = self.cfg.tts
        key = await asyncio.to_thread(tts_key)
        service = getattr(getattr(self.live, "speaker", None), "service", None) if self.live is not None else None
        chain = getattr(service, "voices", None)
        live = chain is not None
        if chain is None:  # no flight: what the next one would have (Piper counted as there)
            chain = await asyncio.to_thread(voice_chain, self.cfg, _PiperStandIn())
        voices_dir = Path(t.voices_dir) if t.voices_dir else None
        from localtc.tts.voices import default_voices_dir

        return {"provider": t.provider, "live": live, "providers": chain.status() if chain else [],
                "text_only": getattr(chain, "text_only", 0), "has_key": bool(key),
                "key_from_env": any(os.environ.get(n) for n in KEY_ENV), "region": tts_region(self.cfg),
                "kokoro": {"package": kokoro.available(), "installed": kokoro.installed(voices_dir or default_voices_dir(),
                                                                                          t.kokoro_model),
                           "model": t.kokoro_model,
                           "size_mb": round((kokoro.FILES[kokoro.MODELS[t.kokoro_model]][0]
                                             + kokoro.FILES[kokoro.VOICES_FILE][0]) / 2**20)},
                "jobs": {k: v for k, v in self.jobs.items() if k == "kokoro"}}

    async def api_tts_key(self, args: dict) -> dict:
        from localtc.app import set_tts_key

        await asyncio.to_thread(set_tts_key, str(args.get("key") or ""))
        service = getattr(getattr(self.live, "speaker", None), "service", None) if self.live is not None else None
        for p in getattr(getattr(service, "voices", None), "providers", ()):
            if p.id == "azure":
                from localtc.app import tts_key

                p.key = await asyncio.to_thread(tts_key)
                service.voices.reset("azure")
        return await self.api_tts({})

    async def api_tts_test(self, args: dict) -> dict:
        """Each provider asked for the same ATC line, now: how long it took and how long the speech is, or why not.
        Azure's test costs about 90 characters."""
        from localtc.app import voice_chain

        return {"results": await asyncio.to_thread(_tts_test, self.cfg, voice_chain, str(args.get("provider") or ""))}

    async def api_models(self, args: dict) -> dict:
        hw = await asyncio.to_thread(models.detect_hardware)
        status = await asyncio.to_thread(models.status, self.cfg)
        ollama = await asyncio.to_thread(_ollama_models, self.cfg)
        return {"hardware": hw.to_dict(), "recommended": models.recommend(hw).id, "catalog": models.catalog(),
                "status": [s.__dict__ for s in status], "ollama": ollama, "jobs": self.jobs,
                "whisper_model": models.whisper_model(self.cfg)}

    async def api_profile(self, args: dict) -> dict:
        try:
            chosen = models.profile(str(args.get("profile")))
        except ValueError as exc:
            raise HttpError(400, str(exc)) from None
        models.apply_profile(self.cfg, chosen)
        await asyncio.to_thread(save_settings, self.cfg, base=load_config(self.config_path, settings=None))
        self._push_state()
        return {"llm": chosen.llm, "whisper": chosen.whisper, "voice": chosen.voice}

    async def api_install(self, args: dict) -> dict:
        kinds = args.get("kinds") or [args.get("kind")]
        for kind in kinds:
            if kind not in ("llm", "whisper", "voice", "kokoro"):
                raise HttpError(400, f"unknown model kind {kind!r}")
            if self.jobs.get(kind, {}).get("state") == "running":
                continue
            self.jobs[kind] = {"state": "running", "message": "Starting ...", "fraction": None}
            asyncio.create_task(self._install(kind))
        self.publish("jobs", self.jobs)
        return {"jobs": self.jobs}

    async def _install(self, kind: str) -> None:
        loop = asyncio.get_running_loop()
        last = [0.0]

        def progress(message: str, fraction: float | None) -> None:
            now = time.monotonic()
            if now - last[0] < 0.3 and fraction not in (None, 1.0):
                return
            last[0] = now
            loop.call_soon_threadsafe(self._job, kind, "running", message, fraction)

        try:
            ok = await asyncio.to_thread(models.install, self.cfg, kind, progress)
            self._job(kind, "done" if ok else "failed", self.jobs[kind]["message"] if not ok else "Ready", 1.0 if ok else None)
        except Exception as exc:
            log.warning("Download of %s failed: %s", kind, exc)
            self._job(kind, "failed", f"{type(exc).__name__}: {exc}", None)

    def _job(self, kind: str, state: str, message: str, fraction: float | None) -> None:
        self.jobs[kind] = {"state": state, "message": message, "fraction": fraction}
        self.publish("jobs", self.jobs)

    async def api_devices(self, args: dict) -> dict:
        return await asyncio.to_thread(_devices)

    async def api_joystick(self, args: dict) -> dict:
        from localtc.stt.joystick import devices

        return {"devices": [{"index": d.index, "name": d.name, "buttons": d.buttons}
                            for d in await asyncio.to_thread(devices)]}

    async def api_joystick_detect(self, args: dict) -> dict:
        """The next joystick button pressed (read as the flight reads it), as MSFS names it; null after 15 s."""
        from localtc.stt.joystick import detect, devices, parse_button

        name = await asyncio.to_thread(detect, 15.0)
        if name is None:
            return {"button": None}
        device, button = parse_button(name)
        found = {d.index: d.name for d in await asyncio.to_thread(devices)}
        return {"button": name, "device": found.get(device, f"controller {device}"), "number": button}

    async def api_preview(self, args: dict) -> dict:
        voice = str(args.get("voice") or self.cfg.tts.voice)
        station = str(args.get("station") or "Seattle Center")
        text = str(args.get("text") or "Skyhawk one seven two lima tango, climb and maintain one zero thousand, "
                                        "contact Seattle Approach one two zero point one.")
        seconds = await asyncio.to_thread(_preview, self.cfg, voice, station, text)
        return {"seconds": seconds}

    async def api_airport(self, args: dict) -> dict:
        icao = str(args.get("icao", "")).strip().upper()
        if not icao:
            raise HttpError(400, "which airport?")
        airport = self.airports.get(icao) or await asyncio.to_thread(self.cache.get, icao)
        if airport is None and self.live is not None and self.live.session.source_kind == "live":
            future = asyncio.get_running_loop().create_future()
            self._airport_waiters.setdefault(icao, []).append(future)
            await self.live.request_airport(icao)
            try:
                airport = await asyncio.wait_for(future, timeout=20)
            except TimeoutError:
                airport = None
        if airport is None:
            hint = "" if self.live else " Start a flight to look it up in the sim."
            raise HttpError(404, f"No data for {icao} yet.{hint}")
        return airport_detail(airport)

    async def api_zones(self, args: dict) -> dict:
        """The Live Map's ATC layer: who works which airspace, and who the flight is talking to."""
        from localtc.ui.zones import zones

        try:
            bounds = tuple(float(args[k]) for k in ("south", "west", "north", "east"))
        except (KeyError, TypeError, ValueError):
            bounds = None
        engine = self.live.engine if self.live else None

        def airport(icao: str):
            if engine is not None and (geo := engine.geometry(icao)) is not None:
                return geo.airport
            return self.airports.get(icao) or self.cache.get(icao)

        known = list((await self._airport_index()).values())
        return await asyncio.to_thread(zones, engine, self.plan, airport, bounds, known)

    async def _airport_index(self) -> dict[str, dict]:
        if self._index is None:
            self._index = await asyncio.to_thread(_build_index, self.cache)
            for airport in self.airports.values():
                self._index[airport.icao] = _index_entry(airport)
        return self._index

    async def api_search(self, args: dict) -> dict:
        query = str(args.get("q", "")).strip().upper()
        await self._airport_index()
        wanted = args.get("kinds")
        if isinstance(wanted, str):
            wanted = [wanted]  # one filter chosen: the query carries it as a single value
        kinds = {k for k in wanted if k in AIRPORT_KINDS} if isinstance(wanted, list) else set(AIRPORT_KINDS)
        kinds = kinds or set(AIRPORT_KINDS)
        pool = [a for a in self._index.values() if a["kind"] in kinds]
        if not query:
            hits = pool[:50]
        else:
            hits = [a for a in pool if a["icao"].startswith(query)]
            hits += [a for a in pool if query in a["name"].upper() and a not in hits]
        counts = {kind: sum(1 for a in self._index.values() if a["kind"] == kind) for kind in AIRPORT_KINDS}
        return {"airports": hits[:50], "known": len(self._index), "matching": len(pool), "counts": counts}

    async def api_note(self, args: dict) -> dict:
        text = str(args.get("text", "")).strip() or "(marked)"
        self._need_live().note(text)  # recorded, and shown when it comes back on the bus
        return {}

    async def api_sessions(self, args: dict) -> dict:
        return {"sessions": await asyncio.to_thread(_recent_sessions, Path(self.cfg.recorder.dir))}

    async def api_export(self, args: dict) -> dict:
        """A zip of a recording, the logs, the settings and the flight plan: everything needed to debug a flight."""
        chosen = args.get("session")
        session_dir = Path(chosen) if chosen else (self.live.recording if self.live and self.live.recording
                                                     else self._last_recording)
        if session_dir is None:
            recent = await asyncio.to_thread(_recent_sessions, Path(self.cfg.recorder.dir))
            session_dir = Path(recent[0]["path"]) if recent else None
        if session_dir is None or not session_dir.is_dir():
            raise HttpError(404, "no recorded flight yet: turn on dev mode and fly one")
        path = await asyncio.to_thread(export_report, session_dir, self.plan_path, flying=self.live is not None)
        _reveal(path)
        return {"path": str(path), "size_mb": round(path.stat().st_size / 2**20, 1)}

    async def api_open(self, args: dict) -> dict:
        """Open a folder the page mentions (logs, recordings) in Explorer/Finder."""
        what = args.get("what")
        if what == "url":
            import webbrowser

            url = str(args.get("url", ""))
            if not url.startswith("https://"):
                raise HttpError(400, "only https links")
            await asyncio.to_thread(webbrowser.open, url)
            return {"url": url}
        target = {"logs": log_dir(), "recordings": Path(self.cfg.recorder.dir).resolve(), "data": data_dir()}.get(what)
        if target is None:
            raise HttpError(400, "logs, recordings or data")
        target.mkdir(parents=True, exist_ok=True)
        _reveal(target)
        return {"path": str(target)}


# --- views ----------------------------------------------------------------------------------------------------


def own_view(ev: OwnshipState) -> dict:
    return {"t": round(ev.t, 1), "lat": ev.lat, "lon": ev.lon, "alt": round(ev.alt_indicated_ft), "agl": round(ev.alt_agl_ft),
            "hdg": round(ev.hdg_true), "hdg_mag": round(ev.hdg_mag), "gs": round(ev.gs_kt), "vs": round(ev.vs_fpm),
            "ground": ev.on_ground, "com1": ev.com1_mhz, "com2": ev.com2_mhz, "squawk": ev.squawk,
            "xpdr": str(ev.xpdr_mode), "tx": ev.com1_tx}


def airport_summary(airport, engine=None) -> dict:
    """The ATC tab's airport box: name and the frequencies, in the order a flight uses them."""
    freqs: dict[str, list[float]] = {}
    for f in airport.frequencies:
        freqs.setdefault(f.kind, [])
        if f.mhz not in freqs[f.kind]:
            freqs[f.kind].append(f.mhz)
    rows = [{"label": FREQ_LABELS.get(kind, kind.upper()), "kind": kind, "mhz": mhzs[0], "others": mhzs[1:]}
            for kind, mhzs in sorted(freqs.items(), key=lambda kv: FREQ_ORDER.index(kv[0]) if kv[0] in FREQ_ORDER else 99)]
    if engine is not None:
        # The departure frequency ATC gives in the clearance, where the sim lists it as approach's (San Francisco's
        # NORCAL 120.35): DEP, as the clearance said, not APP.
        departure = engine.facility("departure")
        if departure is not None and departure.airport == airport.icao and not any(r["kind"] == "departure" for r in rows):
            for row in rows:
                if row["kind"] == "approach" and departure.mhz in [row["mhz"], *row["others"]]:
                    rest = [m for m in [row["mhz"], *row["others"]] if m != departure.mhz]
                    if rest:
                        row["mhz"], row["others"] = rest[0], rest[1:]
                    else:
                        rows.remove(row)
                    break
            at = next((i for i, r in enumerate(rows) if r["kind"] in ("approach", "center")), len(rows))
            rows.insert(at, {"label": "DEP", "kind": "departure", "mhz": departure.mhz, "others": []})
        center = engine.facility("center")
        if center is not None and not any(r["kind"] == "center" for r in rows):
            rows.append({"label": "CTR", "kind": "center", "mhz": center.mhz, "others": []})
    return {"icao": airport.icao, "name": _title(airport.name), "frequencies": rows}


def airport_detail(airport) -> dict:
    from localtc.atc_core.airport import published
    from localtc.sim_api.airport import FEET_PER_METER

    runways = []
    for rw in airport.runways:
        heading = rw.heading_true - airport.magvar
        runways.append({"name": rw.name,
                        "approaches": {end.ident: list(published(airport, end.ident)) for end in (rw.primary, rw.secondary)
                                       if end.ident},
                        "length_ft": round(rw.length_m * FEET_PER_METER),
                        "width_ft": round(rw.width_m * FEET_PER_METER), "heading_mag": round(heading % 360),
                        "ils": [e.ident + (f" ({e.ils_ident})" if e.ils_ident else "") for e in (rw.primary, rw.secondary)
                                if e.ils_ident],
                        "lat": rw.lat, "lon": rw.lon, "heading_true": rw.heading_true, "length_m": rw.length_m})
    return {
        **airport_summary(airport), "lat": airport.lat, "lon": airport.lon, "elev_ft": round(airport.elev_ft),
        "magvar": airport.magvar, "runways": runways, "parking": len(airport.parking),
        "taxiways": sorted({p.name for p in airport.taxi_paths if p.kind == "taxi" and p.name}),
        "all_frequencies": [{"kind": f.kind, "label": FREQ_LABELS.get(f.kind, f.kind.upper()), "mhz": f.mhz,
                             "name": f.name} for f in airport.frequencies],
    }


def _platform_name() -> str:
    import platform

    return f"{platform.system()} {platform.release()}".strip()


def _title(name: str) -> str:
    return name if not name.isupper() else " ".join(w.capitalize() for w in name.split())


def _model(atc_model: str) -> str:
    """ "ATCCOM.AC_MODEL B738.0.text" -> "B738"."""
    import re

    match = re.search(r"AC_MODEL[ _]([A-Z0-9]+)", atc_model or "")
    return match.group(1) if match else ""


# What a cached airport is, for the lookup's filters. The sim's data carries no such label, so it is
# read off the field itself: how long its longest runway is and whether anyone works a frequency there.
AIRPORT_KINDS = ("international", "airport", "heliport")
INTERNATIONAL_RUNWAY_M = 2000.0
ATC_FREQUENCIES = ("tower", "approach", "departure", "center")


def _airport_kind(longest_m: float, has_atc: bool, name: str) -> str:
    if not longest_m:
        return "heliport"  # helipads, hospital pads and the like: somewhere to land, but no runway
    if "INTERNATIONAL" in name.upper() or "INTL" in name.upper():
        return "international"
    return "international" if longest_m >= INTERNATIONAL_RUNWAY_M and has_atc else "airport"


def _entry(icao: str, name: str, lat: float, lon: float, runways, frequencies, elev_ft: float = 0.0) -> dict:
    longest = max((float(r["length_m"] if isinstance(r, dict) else r.length_m) for r in runways), default=0.0)
    kinds = {f["kind"] if isinstance(f, dict) else f.kind for f in frequencies}
    return {"icao": icao, "name": _title(name), "lat": lat, "lon": lon,
            "kind": _airport_kind(longest, bool(kinds & set(ATC_FREQUENCIES)), name),
            "runway_m": round(longest), "elev_ft": round(float(elev_ft or 0)),
            "tower": "tower" in kinds, "approach": bool(kinds & {"approach", "departure"})}


def _index_entry(airport) -> dict:
    return _entry(airport.icao, airport.name, airport.lat, airport.lon, airport.runways, airport.frequencies,
                  getattr(airport, "elev_ft", 0.0))


def _build_index(cache: AirportCache) -> dict[str, dict]:
    import json

    index = {}
    if not cache.directory.is_dir():
        return index
    for path in sorted(cache.directory.glob("*.json")):
        try:  # only the header fields: the files carry whole taxiway graphs
            data = json.loads(path.read_bytes())
            index[data["icao"]] = _entry(data["icao"], data.get("name", ""), data["lat"], data["lon"],
                                         data.get("runways", ()), data.get("frequencies", ()), data.get("elev_ft", 0))
        except (OSError, ValueError, KeyError):
            continue
    return index


def _ollama_models(cfg) -> dict:
    from localtc.app import ollama_backend

    backend = ollama_backend(cfg.llm)
    status = backend.status(timeout_s=1.5)
    where = backend.placement(timeout_s=1.5) if status.reachable else None
    return {"running": status.reachable, "installed": list(status.models), "version": status.version,
            "error": status.error, "loaded": f"Now loaded: {where.describe()}." if where else ""}


def _devices() -> dict:
    try:
        import sounddevice as sd

        from localtc.stt.audio import input_devices
        from localtc.tts.player import output_devices

        default_in, default_out = sd.default.device
        return {"inputs": [{"index": i, "name": n, "default": i == default_in} for i, n, _ in input_devices()],
                "outputs": [{"index": i, "name": n, "default": i == default_out} for i, n, _ in output_devices()]}
    except Exception as exc:
        return {"inputs": [], "outputs": [], "error": str(exc)}


def _preview(cfg: Config, voice: str, station: str, text: str) -> float:
    from localtc.dsp.radio import clean, radio_effect
    from localtc.tts.player import AudioPlayer, Clip
    from localtc.tts.service import radio_words
    from localtc.tts.synth import PiperSynth
    from localtc.tts.voices import download, speaker_for

    voice_file = download(voice, Path(cfg.tts.voices_dir) if cfg.tts.voices_dir else None)
    synth = PiperSynth(voice_file, rate=cfg.tts.rate)
    speech = synth.synthesize(radio_words(text), speaker_for(station, synth.speakers))
    audio = radio_effect(speech.audio, speech.rate, static=cfg.tts.static) if cfg.tts.radio_effect else clean(speech.audio)
    player = AudioPlayer(cfg.tts.output_device or None, volume=cfg.tts.volume)
    player.play(Clip(audio, speech.rate)).done.wait(timeout=30)
    player.close()
    return round(speech.seconds, 1)


class _PiperStandIn:
    """Piper as the status shows it before a flight (the voice isn't loaded until then)."""

    speakers = 904

    def synthesize(self, text, speaker=None):  # never called
        raise RuntimeError("not loaded")


TEST_LINE = "Speedbird one two, runway two seven left, cleared for takeoff, wind two seven zero at eight."


def _tts_test(cfg: Config, voice_chain, wanted: str) -> list[dict]:
    from localtc.tts.persona import persona_for
    from localtc.tts.providers import TtsError, check_audio
    from localtc.tts.synth import PiperSynth
    from localtc.tts.voices import download

    piper = None
    try:
        piper = PiperSynth(download(cfg.tts.voice, Path(cfg.tts.voices_dir) if cfg.tts.voices_dir else None), rate=cfg.tts.rate)
    except Exception:  # noqa: BLE001 - reported as Piper's result
        pass
    chain = voice_chain(cfg, piper)
    out = []
    for p in chain.providers if chain else []:
        if wanted and p.id != wanted:
            continue
        row = {"id": p.id, "name": p.name}
        if why := p.ready():
            out.append(row | {"ok": False, "why": why})
            continue
        try:
            speech = check_audio(p.synthesize(TEST_LINE, persona_for("London Heathrow Tower", "atc", manner="tower", locale="en-GB")), TEST_LINE)
            seconds = len(speech.audio) / speech.rate
            out.append(row | {"ok": True, "latency_ms": round(speech.latency_ms), "seconds": round(seconds, 1),
                              "rtf": round(speech.latency_ms / 1000 / seconds, 3), "voice": speech.voice})
        except TtsError as exc:
            out.append(row | {"ok": False, "why": str(exc)})
        except Exception as exc:  # noqa: BLE001
            out.append(row | {"ok": False, "why": f"{type(exc).__name__}: {exc}"})
    return out


def _crew_preview(cfg: Config, sex: str, pick: int) -> float:
    from localtc.dsp.radio import intercom_effect
    from localtc.tts.player import AudioPlayer, Clip
    from localtc.tts.synth import PiperSynth
    from localtc.tts.voices import crew_speaker, download

    voice_file = download(cfg.tts.voice, Path(cfg.tts.voices_dir) if cfg.tts.voices_dir else None)
    synth = PiperSynth(voice_file, rate=cfg.tts.rate)
    speech = synth.synthesize("Flaps two. Gear down, three green. Landing checklist complete.",
                              crew_speaker(sex, pick, synth.speakers, cfg.tts.voice))
    player = AudioPlayer(cfg.tts.output_device or None, volume=cfg.tts.volume)
    player.play(Clip(intercom_effect(speech.audio, speech.rate), speech.rate, "intercom")).done.wait(timeout=30)
    player.close()
    return round(speech.seconds, 1)


def _recent_sessions(root: Path, limit: int = 15) -> list[dict]:
    if not root.is_dir():
        return []
    sessions = [d for d in root.iterdir() if d.is_dir() and any(d.glob("session.jsonl*"))]
    sessions.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    out = []
    for d in sessions[:limit]:
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        out.append({"name": d.name, "path": str(d.resolve()), "size_mb": round(size / 2**20, 1),
                    "modified": datetime.fromtimestamp(d.stat().st_mtime).isoformat(timespec="minutes")})
    return out


def export_report(session_dir: Path, plan_path: Path | None = None, *, out_dir: Path | None = None,
                  flying: bool = False) -> Path:
    """``LocalTC-report-<session>.zip`` in Downloads (or the data folder): the recording with its audio, the log
    files, the app's settings and the flight plan."""
    out_dir = out_dir or next((d for d in (Path.home() / "Downloads", Path.home() / "Desktop") if d.is_dir()), data_dir())
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    path = out_dir / f"LocalTC-report-{session_dir.name}-{stamp}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(session_dir.rglob("*")):
            if file.is_file():
                zf.write(file, Path("recording") / file.relative_to(session_dir))
        logs = log_dir()
        for file in sorted(logs.glob("localtc.log*")) if logs.is_dir() else []:
            zf.write(file, Path("logs") / file.name)
        for extra in (settings_path(), plan_path, Path("config/localtc.toml")):
            if extra is not None and extra.is_file():
                zf.write(extra, extra.name)
        zf.writestr("README.txt", f"LocalTC flight report\nRecording: {session_dir.name}\nMade: {stamp}\n"
                                  f"Still flying when exported: {'yes' if flying else 'no'}\n"
                                  f"Replay it: localtc replay recording --atc\n")
    return path


def _reveal(path: Path) -> None:
    try:
        if sys.platform == "win32":
            if path.is_file():
                subprocess.Popen(["explorer", "/select,", str(path)])
            else:
                os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])
        elif shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(path.parent if path.is_file() else path)])
    except OSError:
        pass


# Warnings that are diagnostics, not news for the pilot: the sim refusing a request, an airport's data slow to come.
ACCOUNT_PROMPT_FLIGHTS = 3  # flights before the app suggests an account (once)
QUIET_WARNINGS = ("SimConnect exception", "Timed out waiting for", "No airport data returned", "Skipping unparseable",
                  "Couldn't ask for arrival", "Arrival procedures not available")


class _UiLogHandler(logging.Handler):
    """Lines meant for the pilot (``extra=CONSOLE``) and warnings go to the radio log as system lines."""

    def __init__(self, controller: AppController, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(logging.INFO)
        self.controller, self.loop = controller, loop

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.WARNING and not getattr(record, "console", False):
            return
        if record.name.startswith("localtc.ui"):
            return
        try:
            text = record.getMessage()
        except Exception:
            return
        if text.startswith("Copilot: ") and record.levelno >= logging.WARNING:
            return  # also published as an alert, which the radio log shows
        if text.startswith(QUIET_WARNINGS):
            return  # the sim's own hiccups: in the log file for a bug report, never in the radio log
        level = "warn" if record.levelno >= logging.WARNING else "info"
        try:
            self.loop.call_soon_threadsafe(self.controller.system, text, level)
        except RuntimeError:
            pass  # the loop has closed


__all__ = ["AppController", "airport_detail", "airport_summary", "export_report", "load_airport", "radio_line"]


def _station(facility: dict | None) -> dict | None:
    return {"station": facility.get("station"), "mhz": facility.get("mhz")} if facility else None


def route_view(plan: FlightPlan | None) -> dict | None:
    """The planned route for the companion's map."""
    if plan is None:
        return None
    return {"origin": plan.origin, "destination": plan.destination,
            "fixes": [{"ident": f.ident, "lat": f.lat, "lon": f.lon, "kind": f.kind} for f in plan.fixes]}
