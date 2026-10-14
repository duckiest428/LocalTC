"""The EFB's routes for the app's EFB tab, and its commands from the companion (on the local network, or through the
account): the briefing, the plan and its fuel and weights, the airports' runways, procedures and gates, the charts,
the checklists, the scratchpad, the documents, Airport Ops and the achievements. Works before a flight is running
(the briefing, the plan, the charts, the documents); the rest joins in once one is.

Everything the EFB shows that changes during a flight goes out as one "efb" message to the page and the phone
(``state``), so both stay in step.
"""

import asyncio
import json
import logging
import math
import time
import urllib.parse
from pathlib import Path
from typing import Any

import msgspec

from localtc.efb import perf
from localtc.efb.achievements import Achievements, FlightFacts, facts_from_recording
from localtc.efb.airport_ops import GSX_VARS, AirportOps
from localtc.efb.scratchpad import Scratchpad
from localtc.ui.server import HttpError, Raw

log = logging.getLogger(__name__)

DOC_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8"}
DOC_MAX = 40 * 1024 * 1024
SIMBRIEF_DISPATCH = "https://dispatch.simbrief.com/options/custom"
CHECKLIST_SAYS = {"pause": "hold the checklist", "resume": "resume the checklist", "skip": "skip", "stop": "stop the checklist",
                  "check": "checked"}
EXTRAS_EVERY_S = 900.0
EXTRAS_CHECK_S = 15.0  # how often the flight's airports are looked at: fetched as soon as they're known or change
BRIEFING_NOTAMS = 40  # in the phone's copy of a briefing (the app asks for all of them)
COMMANDS = ("checklist", "scratchpad", "gate", "ops_note", "briefing", "charts", "chart")


class EfbRoutes:
    def __init__(self, controller) -> None:
        import os

        from localtc.config import data_dir

        self.c = controller
        # ``LOCALTC_EFB_DIR``: where the notes, achievements and documents are kept ("": nowhere, the tests).
        where = os.environ.get("LOCALTC_EFB_DIR")
        folder = None if where == "" else Path(where) if where else data_dir() / "efb"
        self.docs_dir = folder / "documents" if folder is not None else None
        self.scratchpad = Scratchpad(folder / "scratchpad.json" if folder is not None else None)
        self.achievements = Achievements(folder / "achievements.json" if folder is not None else None)
        self.ops: AirportOps | None = None
        self.facts: FlightFacts | None = None
        self.checklist: dict | None = None  # the copilot's checklist as it stands (``ChecklistState``)
        self.atc_ops: dict | None = None  # ATC's conditions, hold, routing (``AtcOperations``)
        self.hazards: list[dict] = []  # the SIGMETs, TFRs and areas ATC routes around (``OpsSnapshot`` "hazards")
        self.new_achievements: list[dict] = []
        self._chartfox = None
        self._push_at = 0.0
        self.extras: dict = {"briefings": {}, "charts": {}}  # the flight's airports' briefings and chart lists
        self._extras_task: asyncio.Task | None = None
        self.scratchpad.listeners.append(lambda notes: self.push())
        self.achievements.listeners.append(lambda a: self.push())

    # --- wiring -----------------------------------------------------------------------------------------------

    def routes(self) -> dict:
        get, post = "GET", "POST"
        return {
            (get, "efb"): self.api_state,
            (get, "efb/briefing"): self.api_briefing,
            (get, "efb/airport"): self.api_airport,
            (get, "efb/perf"): self.api_perf,
            (get, "efb/alternates"): self.api_alternates,
            (get, "efb/simbrief_link"): self.api_simbrief_link,
            (get, "ops/around"): self.api_around,
            (get, "efb/checklists"): self.api_checklists,
            (post, "efb/checklist"): self.api_checklist,
            (get, "efb/scratchpad"): self.api_scratchpad,
            (post, "efb/scratchpad"): self.api_scratchpad_change,
            (get, "efb/gates"): self.api_gates,
            (post, "efb/gate"): self.api_gate,
            (post, "efb/ops_note"): self.api_ops_note,
            (get, "efb/charts"): self.api_charts,
            (get, "efb/chart"): self.api_chart,
            (post, "efb/chartfox/connect"): self.api_chartfox_connect,
            (post, "efb/chartfox/collect"): self.api_chartfox_collect,
            (post, "efb/chartfox/disconnect"): self.api_chartfox_disconnect,
            (get, "efb/docs"): self.api_docs,
            (get, "efb/doc"): self.api_doc,
            (post, "efb/docs/open"): self.api_docs_open,
            (get, "efb/achievements"): self.api_achievements,
            (get, "data/keys"): self.api_keys,
            (post, "data/keys"): self.api_keys_set,
        }

    @property
    def live(self):
        return self.c.live

    @property
    def engine(self):
        return self.c.live.engine if self.c.live is not None else None

    def flight_started(self, live) -> None:
        engine = live.engine
        self.ops = AirportOps(engine)
        self.ops.listeners.append(lambda s: self.push())
        plan = self.c.plan
        self.facts = FlightFacts(flight=str(getattr(live, "recording", "") or time.time()),
                                 callsign=getattr(plan, "callsign", "") or "", aircraft=getattr(plan, "aircraft", "") or "",
                                 origin=getattr(plan, "origin", "") or "", destination=getattr(plan, "destination", "") or "")
        self.checklist = self.atc_ops = None
        self.extras = {"briefings": {}, "charts": {}}
        if self._extras_task is not None:
            self._extras_task.cancel()
        self._extras_task = asyncio.ensure_future(self._extras_loop())
        if live.crew is None and live.source is not None:
            # No copilot to watch GSX's L:vars along with its own: Airport Ops asks for them itself.
            from localtc.sim_api import WatchVars

            asyncio.ensure_future(live.source.send(WatchVars(names=tuple(GSX_VARS.values()))))
        self.push(force=True)

    def flight_ended(self, record=None) -> None:
        facts = self.facts
        if facts is not None:
            if record is not None:
                facts.landing_vs_fpm = getattr(record, "landing_vs_fpm", None)
                facts.flight = getattr(record, "id", facts.flight)
            if self.ops is not None:
                svc = self.ops.s.services
                facts.services_done = all(svc.get(k) is not None and svc[k].state == "done" and svc[k].source == "GSX"
                                          for k in ("fuel", "catering", "boarding"))
            self.new_achievements = self.achievements.award(facts)
        try:
            self.new_achievements += self.achievements.milestones(self.c.pilot.logbook.flights(limit=5000))
        except Exception:  # noqa: BLE001 - the logbook not there yet: the milestones wait
            log.debug("Milestones not worked out", exc_info=True)
        self.facts = None
        if self._extras_task is not None:
            self._extras_task.cancel()
            self._extras_task = None
        self.push(force=True)

    def observe(self, ev) -> None:
        """Every bus event: the flight's facts, Airport Ops, the checklist and ATC's operations."""
        from localtc.sim_api import AtcOperations, ChecklistState, OpsSnapshot

        if self.facts is not None:
            self.facts.observe(ev)
        if self.ops is not None:
            self.ops.observe(ev)
        if isinstance(ev, ChecklistState):
            try:
                self.checklist = {"id": ev.checklist, "name": ev.name, "status": ev.status, "index": ev.index,
                                  "source": ev.source, **(json.loads(ev.data) if ev.data else {})}
            except ValueError:
                self.checklist = {"id": ev.checklist, "name": ev.name, "status": ev.status}
            self.push()
        elif isinstance(ev, OpsSnapshot) and ev.kind == "hazards":
            try:
                rows = json.loads(ev.data or "[]")
            except ValueError:
                return
            # For the phone's map: each outline thinned to at most 60 points.
            self.hazards = [{**{k: h.get(k) for k in ("id", "kind", "name", "hazard", "floor_ft", "ceiling_ft")},
                             "polygon": h.get("polygon", [])[:: max(1, len(h.get("polygon", [])) // 60)]}
                            for h in rows[:40] if isinstance(h, dict)]
            self.push()
        elif isinstance(ev, AtcOperations):
            try:
                self.atc_ops = json.loads(ev.data or "{}")
            except ValueError:
                return
            self.push()

    def state(self) -> dict:
        """What the EFB shows that changes with the flight (for the page and the phone)."""
        return {
            "checklist": self.checklist, "ops": self.ops.to_json() if self.ops is not None else None,
            "atc": self.atc_ops, "scratchpad": self.scratchpad.for_flight(self._flight_key())[-60:],
            "achievements": {"earned": self.achievements.to_json()["earned"], "total": len(self.achievements.to_json()["achievements"]),
                             "new": self.new_achievements[-5:]},
            "chartfox": {"connected": self.chartfox.connected, "credit": self._credit()},
            "briefings": self.extras.get("briefings", {}), "charts": self.extras.get("charts", {}),
            "book": self._book_summary(), "hazards": self.hazards,
        }

    def _book_summary(self) -> dict | None:
        """The aircraft's checklists (names only), for the phone's Run buttons."""
        crew = getattr(self.live, "crew", None) if self.live is not None else None
        book = getattr(crew, "_book", None) if crew is not None else None
        if book is None:
            return None
        return {"name": book.name, "source": book.source,
                "checklists": [{"id": c.id, "name": c.name, "phase": c.phase} for c in book.checklists]}

    async def _extras_loop(self) -> None:
        """The flight's airports' briefings and chart lists, kept fresh (every ``EXTRAS_EVERY_S``), so the phone has
        them through the account too, where it can't ask."""
        fetched: list[str] = []
        fetched_at = -math.inf
        while True:
            try:
                plan = self.c.plan
                engine = self.engine
                # The flight starts before the sim (or the plan) has said where from and to: no airports yet then.
                # Looked at again every few seconds, and fetched the moment they're known or change.
                airports = [a for a in dict.fromkeys(
                    [getattr(plan, "origin", ""), getattr(plan, "destination", ""), getattr(plan, "alternate", "")]
                    + ([engine.state.flight.origin, engine.state.flight.destination] if engine is not None else []))
                    if a][:3]
                if airports and (airports != fetched or time.monotonic() - fetched_at >= EXTRAS_EVERY_S):
                    for icao in airports:
                        try:
                            self.extras["briefings"][icao] = trim_briefing(await self.api_briefing({"icao": icao}))
                        except Exception:  # noqa: BLE001 - one airport's briefing failing leaves the others
                            log.info("EFB briefing for %s failed", icao, exc_info=True)
                        if self.chartfox.connected:
                            try:
                                charts = await self.api_charts({"icao": icao})
                                self.extras["charts"][icao] = {k: [{f: c.get(f) for f in ("id", "name", "code", "type", "runways",
                                                                                              "has_georef")} for c in v]
                                                               for k, v in charts.get("groups", {}).items()}
                            except Exception:  # noqa: BLE001
                                log.info("EFB charts for %s failed", icao, exc_info=True)
                    fetched, fetched_at = airports, time.monotonic()
                    self.push(force=True)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.info("EFB extras failed", exc_info=True)
            await asyncio.sleep(EXTRAS_CHECK_S)

    def push(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._push_at < 0.5:
            return
        self._push_at = now
        # A copy, not the live dicts: the briefings and charts (and Ops' de-icing) are filled in place, so the
        # companion's "changed?" check compared the state with itself and the phone never got them.
        data = json.loads(json.dumps(self.state(), default=str))
        try:
            self.c.publish("efb", data)
            self.c.companion.set_efb(data)
        except Exception:  # noqa: BLE001 - the page or the phone not there: nothing lost
            log.debug("EFB push failed", exc_info=True)

    def _flight_key(self) -> str:
        p = self.c.plan
        return f"{getattr(p, 'origin', '')}-{getattr(p, 'destination', '')}" if p else ""

    def _credit(self) -> dict:
        from localtc.ops.chartfox import CREDIT, DISCLAIMER

        return {**CREDIT, "disclaimer": DISCLAIMER}

    @property
    def chartfox(self):
        if self._chartfox is None:
            from localtc.app import ops_data
            from localtc.ops.chartfox import ChartFox

            data = ops_data(self.c.cfg)
            self._chartfox = ChartFox(data.keys, data.cache, account_url=self.c.cfg.account.api_url)
        return self._chartfox

    # --- the page's calls ---------------------------------------------------------------------------------------

    async def api_state(self, args: dict) -> dict:
        return self.state()

    async def api_briefing(self, args: dict) -> dict:
        """An airport's briefing: its METAR, TAF and NOTAMs (and what ATC works to of them), the ATIS ATC gives and
        where it's from, each with its age."""
        from localtc.app import ops_data

        icao = _icao(args)
        engine = self.engine
        metar = ""
        if engine is not None and (report := engine._reports.get(icao)) is not None:
            metar = getattr(report, "raw", "") or ""
        brief = await asyncio.to_thread(ops_data(self.c.cfg).briefing, icao, metar)
        if engine is not None and (info := engine.current_atis(icao)) is not None:
            brief["atis"] = {"letter": info.letter, "runway": info.runway, "approach": info.approach,
                             "source": engine.atis_source_text(info, engine.state.aircraft),
                             "notices": [n.text(info.icao_style) for n in (info.operations.notices if info.operations else ())]}
        return brief

    async def api_airport(self, args: dict) -> dict:
        """Runways (lengths, ILS, approaches), frequencies, the plan's procedures and the gates."""
        detail = await self.c.api_airport(args)
        icao = detail.get("icao") or _icao(args)
        plan = self.c.plan
        procedures = {}
        if plan is not None and icao == plan.origin and plan.sid:
            procedures["sid"] = plan.sid
        if plan is not None and icao == plan.destination and plan.star:
            procedures["star"] = plan.star
        detail["procedures"] = procedures
        engine = self.engine
        if engine is not None:
            detail["gates"] = engine.gate_map(icao)
        return detail

    async def api_perf(self, args: dict) -> dict:
        """Fuel and weights: SimBrief's when the plan has them, else an estimate for the type over the route."""
        plan = self.c.plan
        code = str(args.get("type") or (plan.aircraft if plan else "") or "").upper()
        kind = perf.aircraft(code)
        if kind is None:
            return {"type": code, "known": False, "why": f"No performance data for {code or 'this aircraft'}."}
        points = [(f.lat, f.lon) for f in plan.fixes] if plan is not None and plan.fixes else []
        distance = float(args.get("distance_nm") or 0) or perf.route_nm(points)
        if not distance and plan is not None:
            ends = [await self._latlon(plan.origin), await self._latlon(plan.destination)]
            distance = perf.route_nm([e for e in ends if e is not None])
        fuel = perf.from_simbrief(plan.perf) if plan is not None and not args.get("estimate") else None
        alt_nm = None
        if plan is not None and plan.alternate:
            a, b = await self._latlon(plan.destination), await self._latlon(plan.alternate)
            alt_nm = perf._nm(a[0], a[1], b[0], b[1]) if a and b else None
        from localtc.atc_core.airport.aircraft_size import wake_category

        if fuel is None:
            fuel = perf.fuel_plan(kind, distance or 0.0, alternate_nm=alt_nm, extra_kg=float(args.get("extra_kg") or 0),
                                  wake=wake_category(code) or "M")
        pax = int(args.get("pax") if args.get("pax") not in (None, "") else round(kind.seats * 0.85))
        cargo = float(args.get("cargo_kg") or 0)
        weights = perf.weights(kind, pax=pax, cargo_kg=cargo, block_kg=fuel.block_kg, trip_kg=fuel.trip_kg,
                               taxi_kg=fuel.taxi_kg)
        return {"type": code, "name": kind.name, "known": True, "distance_nm": round(distance), "fuel": fuel.to_json(),
                "weights": weights, "note": "SimBrief's plan" if fuel.source == "SimBrief" else
                "An estimate from typical figures for the type: for simulation, not real flight planning."}

    async def _latlon(self, icao: str) -> tuple[float, float] | None:
        if not icao:
            return None
        entry = (await self.c._airport_index()).get(icao.upper())
        return (entry["lat"], entry["lon"]) if entry else None

    async def api_alternates(self, args: dict) -> dict:
        icao = _icao(args)
        here = await self._latlon(icao)
        if here is None:
            raise HttpError(404, f"{icao} isn't known yet.")
        plan = self.c.plan
        need_ft = 6000
        if plan is not None and (kind := perf.aircraft(plan.aircraft)) is not None:
            need_ft = 7000 if kind.mtow_kg > 150000 else 5000 if kind.mtow_kg < 30000 else 6000
            need_ft = 2500 if kind.piston else need_ft
        index = [{**a, "longest_ft": round(a.get("runway_m", 0) * 3.28084)} for a in (await self.c._airport_index()).values()
                 if a.get("kind") != "heliport" and "seaplane" not in str(a.get("name", "")).lower()]  # no water runways
        return {"icao": icao, "min_runway_ft": need_ft, "alternates": perf.alternates(here, index, min_runway_ft=need_ft, exclude=icao)}

    async def api_simbrief_link(self, args: dict) -> dict:
        """SimBrief's planner opened with this plan filled in (its public dispatch link), to make the OFP there."""
        plan = self.c.plan
        if plan is None:
            raise HttpError(409, "No plan to send")
        query = {k: v for k, v in {"orig": plan.origin, "dest": plan.destination, "altn": plan.alternate,
                                   "type": plan.aircraft, "route": plan.route, "callsign": plan.callsign,
                                   "fl": plan.cruise_ft or ""}.items() if v}
        return {"url": f"{SIMBRIEF_DISPATCH}?{urllib.parse.urlencode(query)}"}

    async def api_around(self, args: dict) -> dict:
        from localtc.app import ops_data

        try:
            lat, lon = float(args["lat"]), float(args["lon"])
        except (KeyError, TypeError, ValueError):
            raise HttpError(400, "lat and lon") from None
        cfg = self.c.cfg.ops
        got = await asyncio.to_thread(ops_data(self.c.cfg).around, lat, lon)
        if not cfg.weather:
            got.update(sigmets=[], radar=None)
        if not cfg.restrictions:
            got.update(areas=[])
        return got

    # --- checklists -----------------------------------------------------------------------------------------

    async def api_checklists(self, args: dict) -> dict:
        crew = getattr(self.live, "crew", None)
        book = getattr(crew, "_book", None) if crew is not None else None
        return {"book": book.to_json() if book is not None else None, "current": self.checklist,
                "mode": getattr(crew.monitor, "checklist_mode", "") if crew is not None else "",
                "copilot": crew is not None,
                "why": "" if crew is not None else "The copilot isn't on (Quick Settings → Copilot)"}

    async def api_checklist(self, args: dict) -> dict:
        """Run a checklist, or pause, resume, skip or stop the one being read; "check" answers the item asked. Said
        to the copilot on the intercom as the pilot would."""
        crew = getattr(self.live, "crew", None)
        if self.live is None or crew is None:
            raise HttpError(409, "The copilot isn't on (Quick Settings → Copilot)")
        action = str(args.get("action", "run"))
        if action == "run":
            book = getattr(crew, "_book", None)
            cl = book.get(str(args.get("id", ""))) if book is not None else None
            if cl is None:
                raise HttpError(404, "No such checklist for this aircraft")
            self.live.say_crew(f"{cl.name} checklist")
        elif action in CHECKLIST_SAYS:
            self.live.say_crew(CHECKLIST_SAYS[action] if action != "check" else str(args.get("answer") or "checked"))
        else:
            raise HttpError(400, "run, pause, resume, skip, stop or check")
        return {"ok": True}

    # --- scratchpad -----------------------------------------------------------------------------------------

    async def api_scratchpad(self, args: dict) -> dict:
        notes = self.scratchpad.for_flight(None if args.get("all") else self._flight_key())
        issued = self._issued()
        for n in notes:
            n["checked"] = self.scratchpad.checked(n, issued)
        return {"notes": notes}

    def _issued(self) -> dict[str, str]:
        engine = self.engine
        if engine is None:
            return {}
        a = engine.state.assignments
        out = {"squawk": a.squawk, "altitude": str(a.altitude_ft) if a.altitude_ft else None,
               "frequency": f"{a.departure_mhz:.3f}" if a.departure_mhz else None,
               "runway": a.departure_runway or a.arrival_runway, "atis": a.atis}
        return {k: v for k, v in out.items() if v}

    async def api_scratchpad_change(self, args: dict, source: str = "app") -> dict:
        op = str(args.get("op", "add"))
        try:
            if op == "add":
                phase = self.engine.state.phase if self.engine is not None else ""
                note = self.scratchpad.add(str(args.get("text", "")), flight=self._flight_key(), phase=phase or "",
                                           source=source, kind=str(args.get("kind", "")), pinned=bool(args.get("pinned")))
                return {"note": note}
            if op == "edit":
                return {"note": self.scratchpad.edit(str(args.get("id")), **{k: args[k] for k in ("text", "pinned", "kind")
                                                                            if k in args})}
            if op == "delete":
                self.scratchpad.delete(str(args.get("id")))
                return {"ok": True}
        except KeyError:
            raise HttpError(404, "No such note") from None
        except ValueError as exc:
            raise HttpError(400, str(exc)) from None
        raise HttpError(400, "add, edit or delete")

    # --- gates and Airport Ops ----------------------------------------------------------------------------------

    async def api_gates(self, args: dict) -> dict:
        engine = self.engine
        icao = str(args.get("icao") or (engine.state.flight.destination if engine is not None else "") or "").upper()
        if engine is None:
            raise HttpError(409, "Start a flight to see the gates")
        got = engine.gate_map(icao)
        if got is None:
            raise HttpError(404, f"{icao}'s stands aren't known yet")
        got["requests"] = self.ops.s.requests[-10:] if self.ops is not None else []
        return got

    async def api_gate(self, args: dict) -> dict:
        if self.ops is None:
            raise HttpError(409, "Start a flight first")
        return self.ops.request_gate(str(args.get("gate", "")).strip())

    async def api_ops_note(self, args: dict) -> dict:
        if self.ops is None:
            raise HttpError(409, "Start a flight first")
        try:
            return self.ops.note(str(args.get("text", "")), str(args.get("kind", "maintenance")))
        except ValueError as exc:
            raise HttpError(400, str(exc)) from None

    # --- charts -----------------------------------------------------------------------------------------------

    async def api_charts(self, args: dict) -> dict:
        from localtc.ops.chartfox import ChartFoxError, for_phase

        icao = _icao(args)
        if not self.chartfox.connected:
            return {"icao": icao, "connected": False, "credit": self._credit()}
        try:
            groups, got = await asyncio.to_thread(self.chartfox.airport_charts, icao)
        except ChartFoxError as exc:
            raise HttpError(502, str(exc)) from None
        engine = self.engine
        phase = engine.state.phase if engine is not None else None
        runway = None
        if engine is not None:
            a = engine.state.assignments
            runway = a.arrival_runway if icao == engine.state.flight.destination else a.departure_runway
        return {"icao": icao, "connected": True, "groups": groups or {}, "credit": self._credit(),
                "first": [c["id"] for c in for_phase(groups or {}, phase, runway)][:3],
                "got": got.to_json() if got is not None else None}

    async def api_chart(self, args: dict) -> dict:
        from localtc.ops.chartfox import ChartFoxError, position_on

        try:
            chart = await asyncio.to_thread(self.chartfox.chart, str(args.get("id", "")))
        except ChartFoxError as exc:
            raise HttpError(502, str(exc)) from None
        if chart is None:
            raise HttpError(404, "No such chart")
        own = self.engine.state.aircraft if self.engine is not None else None
        height = float(args.get("height") or 0)
        if own is not None and chart.get("georef") and height:
            chart["own"] = position_on(chart["georef"], own.lat, own.lon, height)
        chart["credit"] = self._credit()
        return chart

    async def api_chartfox_connect(self, args: dict) -> dict:
        """The page opens this in the browser; then it asks ``collect`` until the pilot has signed in."""
        return {"url": self.chartfox.sign_in_url()}

    async def api_chartfox_collect(self, args: dict) -> dict:
        from localtc.ops.chartfox import ChartFoxError

        try:
            done = await asyncio.to_thread(self.chartfox.collect)
        except ChartFoxError as exc:
            raise HttpError(502, str(exc)) from None
        if done:
            self.push(force=True)
        return {"connected": done}

    async def api_chartfox_disconnect(self, args: dict) -> dict:
        self.chartfox.disconnect()
        self.push(force=True)
        return {"connected": False}

    # --- documents --------------------------------------------------------------------------------------------

    async def api_docs(self, args: dict) -> dict:
        """The flight documents: whatever's in the EFB's documents folder (PDFs, pictures, text)."""
        if self.docs_dir is None:
            return {"folder": "", "docs": []}
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        files = sorted((p for p in self.docs_dir.iterdir() if p.is_file() and p.suffix.lower() in DOC_TYPES),
                       key=lambda p: p.name.lower())
        return {"folder": str(self.docs_dir), "docs": [{"name": p.name, "size": p.stat().st_size,
                                                         "kind": p.suffix.lower().lstrip(".")} for p in files]}

    async def api_doc(self, args: dict) -> Raw:
        name = Path(str(args.get("name", ""))).name  # a name in the folder, never a path out of it
        if self.docs_dir is None:
            raise HttpError(404, "No such document")
        path = self.docs_dir / name
        if not name or not path.is_file() or path.suffix.lower() not in DOC_TYPES:
            raise HttpError(404, "No such document")
        if path.stat().st_size > DOC_MAX:
            raise HttpError(413, "Too big to show here")
        return Raw(DOC_TYPES[path.suffix.lower()], await asyncio.to_thread(path.read_bytes))

    async def api_docs_open(self, args: dict) -> dict:
        from localtc.ui.controller import _reveal

        if self.docs_dir is None:
            raise HttpError(409, "No documents folder")
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        _reveal(self.docs_dir)
        return {"ok": True}

    # --- achievements --------------------------------------------------------------------------------------------

    async def api_achievements(self, args: dict) -> dict:
        if args.get("recording"):
            facts = await asyncio.to_thread(facts_from_recording, Path(str(args["recording"])))
            from localtc.efb.achievements import met_by

            return {"recording": str(args["recording"]), "met": met_by(facts), "unsafe": facts.unsafe}
        try:
            self.achievements.milestones(await asyncio.to_thread(self.c.pilot.logbook.flights, 5000))
        except Exception:  # noqa: BLE001
            log.debug("Milestones not worked out", exc_info=True)
        return self.achievements.to_json()

    # --- the data services' keys (EFB > Data) ----------------------------------------------------------

    async def api_keys(self, args: dict) -> dict:
        from localtc.app import ops_data

        keys = ops_data(self.c.cfg).keys
        return {"services": keys.status(), "chartfox": self.chartfox.connected}

    async def api_keys_set(self, args: dict) -> dict:
        from localtc.app import ops_data
        from localtc.ops.keys import NAMES

        keys = ops_data(self.c.cfg).keys
        for name, value in (args.get("keys") or {}).items():
            if name in NAMES and not name.startswith("chartfox"):
                await asyncio.to_thread(keys.set, name, str(value or ""))
        return {"services": keys.status()}

    # --- the phone's commands (companion.py: on the local network; pilot.py: through the account) ---------------

    async def command(self, cmd: dict, source: str = "phone") -> dict:
        action = str(cmd.get("action", ""))
        if action == "checklist":  # {"action": "checklist", "do": "run" | "pause" | ..., "id": ...}
            return await self.api_checklist({**cmd, "action": str(cmd.get("do") or "run")})
        if action == "scratchpad":
            return await self.api_scratchpad_change(cmd, source=source)
        if action == "gate":
            return await self.api_gate(cmd)
        if action == "ops_note":
            return await self.api_ops_note(cmd)
        if action == "briefing":
            return await self.api_briefing(cmd)
        if action == "charts":
            return await self.api_charts(cmd)
        if action == "chart":
            return await self.api_chart(cmd)
        if action == "gates":
            return await self.api_gates(cmd)
        if action == "perf":
            return await self.api_perf(cmd)
        if action == "checklists":
            return await self.api_checklists(cmd)
        if action == "achievements":
            return await self.api_achievements({})
        if action == "briefing_all":  # every airport of the flight at once (the phone's Briefing on the PC's Wi-Fi)
            return {"briefings": self.extras.get("briefings", {})}
        raise HttpError(400, f"unknown EFB command {action!r}")


def trim_briefing(b: dict) -> dict:
    """A briefing small enough to go to the phone with the rest of the EFB."""
    out = dict(b)
    notams = dict(out.get("notams") or {})
    notams["items"] = [{k: n.get(k) for k in ("id", "text", "works_to", "in_force", "start", "end")} | {"text": str(n.get("text", ""))[:300]}
                       for n in (notams.get("items") or [])[:BRIEFING_NOTAMS]]
    out["notams"] = notams
    return out


def _icao(args: dict) -> str:
    icao = str(args.get("icao", "")).strip().upper()
    if not (3 <= len(icao) <= 4 and icao.isalnum()):
        raise HttpError(400, "which airport?")
    return icao


def to_builtins(obj: Any) -> Any:
    return msgspec.to_builtins(obj)
