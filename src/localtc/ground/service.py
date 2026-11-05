"""The ground crew on the bus: the pilot's words, the aircraft and ATC's doings in; the crew's lines out, and what they ask
sent to whoever does the work (``api.GroundAdapter``). What's said on the ramp's frequency is theirs. What's said on the
intercom comes through the copilot's service when there's a copilot (crew.service asks them first), else here.
"""

import asyncio
import logging
from typing import Any

import msgspec

from localtc.bus import EventBus
from localtc.ground.api import GroundAdapter
from localtc.ground.crew import Close, Open, Pick, RampCrew, SelectGate, Send
from localtc.ground.model import RampModel
from localtc.sim_api import (
    AircraftVars,
    AtcAlert,
    AtcTransmission,
    GroundServiceState,
    GroundStatus,
    IntercomHeard,
    LlmExchange,
    OwnshipState,
    PttPressed,
    PttReleased,
    RampSpeech,
    SimCommand,
    SetSimVar,
    Transcript,
)
from localtc.sim_api.ramp import on_ramp

log = logging.getLogger(__name__)
TICK_S = 0.5
REPLY_PAUSE_S = 1.0  # an answer comes a beat after the pilot's words
ATC_TALKS_S = 6.0  # ATC just spoke: the crew's own calls wait this long
CALLS_AS = "Ground to cockpit"
MENU_OPEN_LVAR = "L:FSDT_GSX_MENU_OPEN"  # (GSX's documented way, when its API says yes and no menu comes)


class GroundService:
    def __init__(self, crew: RampCrew, adapter: GroundAdapter, bus: EventBus, source=None, *, model: RampModel | None = None,
                 clock=None, intercom: bool = False) -> None:
        """``intercom``: the pilot's words on the intercom are heard here (False: the copilot's service hands them over)."""
        self.crew, self.adapter, self.bus, self.source, self.model = crew, adapter, bus, source, model
        self.clock = clock
        kinds: list[Any] = [OwnshipState, AircraftVars, Transcript, PttPressed, PttReleased, AtcTransmission]
        # The aircraft as the copilot reads it (``app`` sets this): an add-on's own brake lever where the sim's variable doesn't follow it.
        self.as_read = lambda own: own
        self._raw_own: OwnshipState | None = None
        if intercom:
            kinds.append(IntercomHeard)
        self._inputs = bus.subscribe(*kinds)
        self._tx_mhz: float | None = None
        self._changed = asyncio.Event()
        self._last_status: tuple | None = None
        self._last_status_t = -1e9
        self._t = 0.0

    def _now(self) -> float:
        return self.clock() if self.clock is not None else self._t

    async def run(self) -> None:
        await self.adapter.start(self._changed.set)
        ticks = asyncio.create_task(self._ticks())
        try:
            async for event in self._inputs:
                try:
                    await self._on(event)
                except Exception:  # noqa: BLE001 - the ground crew failing must never stop the flight
                    log.exception("Ground crew failed on %s", type(event).__name__)
        finally:
            ticks.cancel()
            await self.adapter.stop()

    async def _on(self, event: Any) -> None:
        self._t = max(self._t, getattr(event, "t", self._t))
        if isinstance(event, OwnshipState):
            self._raw_own = event
            self.crew.observe(self.as_read(event))
        elif isinstance(event, AircraftVars):
            self.crew.observe_vars(event.values)
        elif isinstance(event, PttPressed):
            self.crew.keyed = True
            own = self.crew.own
            self._tx_mhz = own.mhz_of(event.radio) if own is not None else None
        elif isinstance(event, PttReleased):
            self.crew.keyed = False
        elif isinstance(event, AtcTransmission):
            self.crew.atc_until = event.t + ATC_TALKS_S
        elif isinstance(event, Transcript):
            await self._on_transcript(event)
        elif isinstance(event, IntercomHeard):
            if self.crew.claims(event.text, event.t, event.to):
                asyncio.create_task(self.answer(event.text, event.t))

    async def _on_transcript(self, event: Transcript) -> None:
        own = self.crew.own
        mhz = self._tx_mhz if self._tx_mhz is not None else (own.mhz_of(event.radio) if own else None)
        self._tx_mhz = None
        if event.source == "copilot" or not event.text.strip() or not on_ramp(mhz, self.crew.ramp_mhz):
            return
        if own is not None and not own.on_ground:
            return  # out of the ramp's reach, like any station too far away
        asyncio.create_task(self.answer(event.text, event.t))

    async def answer(self, text: str, t: float) -> None:
        """The pilot's words: the grammar's reading, or the model's when it has none (or always in the fully LLM mode); the
        answer a beat after, in the crew's own words where there's a model. On its own task: a model never holds up the flight."""
        try:
            outputs = await self._heard(text, t)
            if not any(isinstance(o, RampSpeech) and self.model is not None and self.model.words for o in outputs):
                await asyncio.sleep(REPLY_PAUSE_S)
            else:
                outputs = await self._in_own_words(text, outputs, t)
        except Exception:  # noqa: BLE001
            log.exception("Ground crew failed on what the pilot said")
            return
        await self._out(outputs)

    async def _heard(self, text: str, t: float) -> list[Any]:
        from localtc.ground import grammar

        model = self.model
        crew = self.crew
        asks = grammar.parse(crew.read(text))
        if model is None or not model.reads or (asks and model.mode != "llm"):
            return crew.heard(text, t)
        read, records = await asyncio.to_thread(model.read, text, self._situation(), t)
        for record in records:
            self.bus.publish(record)
        if read.rejected:
            # The model's reading turned away: the grammar's, for that same call, and the pilot's told.
            self.bus.publish(AtcAlert(t=t, kind="llm_rejected", detail=f"ground crew: {read.rejected}"))
            return crew.heard(text, t)
        if not read.asks:
            return crew.heard(text, t)
        out: list[Any] = []
        for ask in _together(read.asks):
            out += crew.do(ask, t)
        return out

    def _situation(self) -> str:
        state = self.crew.state
        from localtc.ground.menu import is_question

        running = [f"{s.id} {s.state}" for s in state.services.values() if s.active or s.state == "completed"]
        menu = f"; a question is up: {state.menu.title}: {', '.join(state.menu.entries)}" if state.menu is not None and is_question(state.menu) else ""
        said = f'; you last said: "{self.crew.last_said}"' if self.crew.last_said else ""
        return f"parked at {state.parking or 'a stand'}; services: {', '.join(running) or 'none running'}{menu}{said}"

    async def _in_own_words(self, text: str, outputs: list[Any], t: float) -> list[Any]:
        said: list[Any] = []
        for output in outputs:
            if isinstance(output, RampSpeech) and self.model is not None and not self._keeps_its_words(output.text):
                words, records = await asyncio.to_thread(self.model.word, text, output.text, t)
                for record in records:
                    self.bus.publish(record)
                    if record.outcome == "rejected":
                        self.bus.publish(AtcAlert(t=t, kind="llm_rejected", detail=f"ground crew: {record.detail}; said in the crew's own script"))
                output = msgspec.structs.replace(output, text=words, spoken="")
            said.append(output)
        return said

    def _keeps_its_words(self, text: str) -> bool:
        """One of the push's standard calls: never reworded. Nor "say again", where there's nothing for a model to word, only an
        answer for it to make up, nor a line with a condition or a fact in it (``crew.AS_WRITTEN``)."""
        from localtc.ground.lines import FIXED, LINES
        from localtc.ground.words import LINES as FOREIGN

        again = {*LINES["say_again"], *(lines["say_again"] for lines in FOREIGN.values())}
        return self.crew.keeps_its_words(text) or text in again or any(text == line or "hand signal" in text for lines in FIXED.values() for line in lines)

    async def from_phone(self, action: str, service: str = "", direction: str = "") -> None:
        """The companion's buttons: what the pilot could have said, and answered the same way."""
        from localtc.ground import grammar as g
        from localtc.ground.api import SERVICES

        if action not in (g.REQUEST, g.CANCEL, g.FINISH, g.OFF, g.PUSH, g.STOP_PUSH):
            raise ValueError(f"Not something the ground crew does: {action}")
        if service and service not in SERVICES:
            raise ValueError(f"Not a service: {service}")
        if direction not in ("", g.NOSE_LEFT, g.NOSE_RIGHT, g.STRAIGHT):
            raise ValueError(f"Not a way to push: {direction}")
        await self._out(self.crew.do(g.Ask(action, (service,) if service else (), direction), self._now()))

    async def _ticks(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._changed.wait(), TICK_S)
                self._changed.clear()
                await self.step(changed=True)
            except TimeoutError:
                await self.step(changed=False)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Ground crew failed on its tick")

    async def step(self, *, changed: bool) -> None:
        """A moment passing, or the services' states having changed."""
        t = self._now()
        advance = getattr(self.adapter, "advance", None)
        if advance is not None:
            advance(t)
        state = self.adapter.state
        outputs = self.crew.update(state, t) if changed or state != self.crew.state else self.crew.tick(t)
        await self._out(outputs)
        self._publish_status(t)

    def _publish_status(self, t: float) -> None:
        state = self.adapter.state
        key = (self.adapter.name, state.connected, state.parking, tuple((s.id, s.state, s.waiting) for s in state.services.values()))
        if key == self._last_status and t - self._last_status_t < 10.0:
            return
        self._last_status, self._last_status_t = key, t
        self.bus.publish(GroundStatus(t=t, provider=self.adapter.name if state.connected else "", connected=state.connected,
                                      parking=state.parking,
                                      services=tuple(GroundServiceState(id=s.id, name=s.name, state=s.state, waiting=s.waiting,
                                                                        status=s.status) for s in state.services.values())))

    async def _out(self, outputs: list[Any]) -> None:
        for output in outputs:
            if isinstance(output, Send):
                result = await self.adapter.trigger(output.service)
                if not result.ok:
                    log.info("Ground crew: %s refused: %s %s", output.service, result.code, result.message)
                    await self._out(self.crew.not_sent(output.service, result.code, self._now()))
            elif isinstance(output, SelectGate):
                result = await self.adapter.select_gate(output.gate)
                log.info("Ground crew told GSX the stand is %s: %s", output.gate, result.code or "ok")
            elif isinstance(output, Pick):
                result = await self.adapter.pick(output.index)
                log.info("Ground crew picked %r: %s", output.label, result.code or "ok")
            elif isinstance(output, Open):
                if output.fallback and self.adapter.name == "gsx" and self.source is not None:
                    await self.source.send(SetSimVar(name=MENU_OPEN_LVAR, value=1.0))
                else:
                    await self.adapter.open_menu()
            elif isinstance(output, Close):
                await self.adapter.close_menu()
            elif isinstance(output, SimCommand):
                if self.source is not None:
                    await self.source.send(output)
            else:
                if isinstance(output, RampSpeech):
                    output = _on_the_air(output)
                    log.info("Ramp (%s): %s", output.channel, output.text)
                self.bus.publish(output)


def _together(asks: Any) -> list[Any]:
    """Several services asked for in one breath, as one request: a model's reading had four, and the crew answered four times."""
    from dataclasses import replace

    from localtc.ground import grammar as g

    merged: list[Any] = []
    for ask in asks:
        if merged and ask.action == g.REQUEST and merged[-1].action == g.REQUEST:
            merged[-1] = replace(merged[-1], services=tuple(dict.fromkeys((*merged[-1].services, *ask.services))))
        else:
            merged.append(ask)
    return merged


def _on_the_air(speech: RampSpeech) -> RampSpeech:
    """Every call opens with who's calling whom, as on a real headset. In English: other languages keep their own wording."""
    if speech.language or speech.text.lower().startswith(CALLS_AS.lower()):
        return speech
    return msgspec.structs.replace(speech, text=f"{CALLS_AS}. {speech.text}", spoken=f"{CALLS_AS}. {speech.spoken}" if speech.spoken else "")


__all__ = ["GroundService", "LlmExchange"]
