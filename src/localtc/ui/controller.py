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
from localtc.radiolog import radio_line
from localtc.sim_api import (
    AirportData,
    AtcAlert,
    AtcTransmission,
    BusEvent,
    OwnshipState,
    PhaseChanged,
    PttPressed,
    PttReleased,
    RadioTuned,
    TrafficSnapshot,
    encode_event,
)
from localtc.ui.server import EventStream, HttpError, sse
from localtc.ui.companion import CompanionHub, CompanionServer
from localtc.ui.pilot import PilotRoutes
from localtc.ui.updates import Updates

log = logging.getLogger(__name__)

RADIO_HISTORY = 400
OWN_EVERY_S = 0.25
FLIGHT_EVERY_S = 1.0
FREQ_LABELS = {"atis": "ATIS", "awos": "AWOS", "asos": "ASOS", "clearance": "CLR", "ground": "GND", "tower": "TWR",
               "departure": "DEP", "approach": "APP", "center": "CTR", "unicom": "UNICOM", "ctaf": "CTAF",
               "multicom": "MULTICOM", "fss": "FSS"}
FREQ_ORDER = list(FREQ_LABELS)
DEPARTING = {"PARKED", "PUSHBACK", "TAXI_OUT", "RUNWAY_HOLD", "TAKEOFF", "DEPARTURE", "CRUISE"}


class AppController:
    def __init__(self, cfg: Config | None = None, *, config_path: str | None = None,
                 plan_path: Path | None = None, cache: AirportCache | None = None) -> None:
        self.config_path = config_path
        first_run = (path := settings_path()) is not None and not path.exists()
        self.cfg = cfg or load_config(config_path)
        if cfg is None and first_run:
            self.cfg.voice.enabled = True  # the app is for flying with a microphone; the CLI asks with --voice
        self.stream = EventStream()
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
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None
        self._ticker: asyncio.Task | None = None
        self._own_sent = 0.0
        self._index: dict[str, dict] | None = None
        self._airport_waiters: dict[str, list[asyncio.Future]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_recording: Path | None = None
        self.companion = CompanionHub()
        self.companion.set_route(route_view(self.plan))
        self.companion_server: CompanionServer | None = None
        self.pilot = PilotRoutes(lambda: self.cfg, self.publish, hub=self.companion,
                                 on_signed_in=self._companion_on, on_signed_out=self._companion_off)
        self._last_atc: AtcTransmission | None = None
        self.updates = Updates(lambda: self.cfg.ui.updates, self.publish, lambda: self.live is not None)

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
            (post, "support"): self.api_support,
            (post, "radio/ptt"): self.api_ptt,
            (post, "radio/tune"): self.api_tune,
            (post, "radio/copilot"): self.api_copilot,
            (post, "radio/mute"): self.api_mute,
            (get, "settings"): self.api_settings,
            (post, "settings"): self.api_save_settings,
            (get, "models"): self.api_models,
            (post, "models/install"): self.api_install,
            (post, "models/profile"): self.api_profile,
            (get, "devices"): self.api_devices,
            (post, "voice/preview"): self.api_preview,
            (get, "airport"): self.api_airport,
            (get, "zones"): self.api_zones,
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
        if self.own:
            out.append(sse("own", self.own))
        if self.traffic:
            out.append(sse("traffic", self.traffic))
        return out

    def publish(self, kind: str, data: Any) -> None:
        self.stream.publish(kind, data)
        if kind == "own":
            self.companion.set_own(data)
        elif kind == "traffic":
            self.companion.set_traffic(data)
        elif kind == "radio":
            self.companion.add_radio(data)
        elif kind == "flight":
            self.companion.set_status(self.companion_view() if data else {"active": False})
            self.companion.set_airports(self.companion_airports() if data else [])

    def state(self) -> dict:
        return {
            "status": self.status, "detail": self.status_detail,
            "source": self.source_kind, "plan": msgspec.to_builtins(self.plan) if self.plan else None,
            "flight": self.flight, "copilot": self.live.copilot_mode if self.live else self.cfg.copilot.mode,
            "copilot_mode": self.cfg.ui.copilot,
            "muted": self.muted, "dev_mode": self.cfg.ui.dev_mode, "voice": self.cfg.voice.enabled,
            "ptt": {"mode": self.cfg.voice.ptt, "key": self.cfg.voice.ptt_key, "joystick": self.cfg.voice.ptt_joystick},
            "recording": str(self.live.recording) if self.live and self.live.recording else None,
            "jobs": self.jobs, "map_tiles": self.cfg.ui.map_tiles, "platform": sys.platform,
            "simbrief_user": self.cfg.ui.simbrief_user, "lookup_kinds": list(self.cfg.ui.lookup_kinds),
            "version": __version__, "update": self.updates.view(), "coffee_clicked": self.cfg.ui.coffee_clicked,
        }

    def _push_state(self) -> None:
        self.publish("state", self.state())

    def _set_status(self, status: str, detail: str = "") -> None:
        self.status, self.status_detail = status, detail
        self._push_state()

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
            self._set_status("idle")
        except asyncio.CancelledError:
            self._set_status("idle")
        except (ConfigError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            log.warning("Flight stopped: %s", exc)
            self._set_status("error", str(exc))
        except Exception as exc:
            log.exception("Flight failed")
            self._set_status("error", f"{type(exc).__name__}: {exc}")
        finally:
            self.live = None
            if self._ticker:
                self._ticker.cancel()

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
            "squawk": a.get("squawk"), "altitude_ft": a.get("altitude_ft"),
            "runway": a.get("departure_runway") if departing else a.get("arrival_runway"),
            "approach": str(a["approach"]) if a.get("approach") else None,
            "tuned": msgspec.to_builtins(snap.tuned) if snap.tuned else None,
            "expected": msgspec.to_builtins(snap.expected_contact) if snap.expected_contact else None,
            "pending": msgspec.to_builtins(snap.pending) if snap.pending else None,
            "ete": ete, "rules": getattr(engine.state.flight, "rules", "IFR"),
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
        if self.cfg.ui.dev_mode and not isinstance(ev, AirportData):
            self.publish("dev", {"t": round(ev.t, 2), "json": encode_event(ev).decode(errors="replace")[:4000]})
        line = radio_line(ev)
        if line is not None:
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
        elif isinstance(ev, PhaseChanged | RadioTuned | AtcTransmission):
            if isinstance(ev, AtcTransmission):
                self._last_atc = ev
                self.companion.on_event(ev)
            if self.live is not None and self.live.engine is not None:
                self.flight = self.flight_view()
                self.publish("flight", self.flight)
                asyncio.create_task(self.pilot.live(self.companion_view(), force=not isinstance(ev, AtcTransmission)))

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
            "last_atc": {"station": atc.station, "mhz": atc.frequency_mhz, "text": atc.text} if atc else None,
        }

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

    def _phone_say(self, text: str) -> None:
        """A call typed on the companion app: transmitted on COM1 like one typed here."""
        try:
            self._need_live().say(text)
        except HttpError as exc:
            raise RuntimeError(str(exc)) from None

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
        self._need_live().say(str(args.get("text", "")))
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

    async def api_ptt(self, args: dict) -> dict:
        if not self._need_live().ptt(bool(args.get("down"))):
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
        if cfg.voice.ptt_key != self.cfg.voice.ptt_key and sys.platform == "win32":  # key names differ by OS
            try:
                from localtc.stt.ptt import parse_key

                parse_key(cfg.voice.ptt_key)
            except ImportError:
                pass
            except ValueError as exc:
                raise HttpError(400, str(exc)) from None
        path = await asyncio.to_thread(save_settings, cfg, base=load_config(self.config_path, settings=None))
        live_now = self.live is not None
        self.cfg = cfg
        self.companion.remote_map = cfg.account.companion_remote_map
        if self.live is not None and self.live.speaker is not None and not self.muted:
            self.live.speaker.player.volume = cfg.tts.volume
        self._push_state()
        return {"saved": str(path), "restart": live_now}

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
            if kind not in ("llm", "whisper", "voice"):
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

    status = ollama_backend(cfg.llm).status(timeout_s=1.5)
    return {"running": status.reachable, "installed": list(status.models), "version": status.version,
            "error": status.error}


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
            "fixes": [{"ident": f.ident, "lat": f.lat, "lon": f.lon} for f in plan.fixes]}
