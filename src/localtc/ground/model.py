"""The ground crew with a cloud language model: what the pilot asks read by the model when the grammar can't, and the crew's
answers in their own words. Only ever a cloud model (without one: the grammar and the script, and that's all). It works with
a closed list of things it may ask for, and whatever it reads goes through the same checks as the pilot's plain words: it
can't call a service that doesn't exist or get a push past the brake or ATC. The headset's words on the push are never its.
"""

import json
from dataclasses import dataclass
from typing import Any

from localtc.ground import grammar as g
from localtc.ground.api import SERVICES
from localtc.atc_core.llm.backend import LlmRequest
from localtc.sim_api import LlmExchange

MODES = ("scripted", "semi", "mostly_llm", "llm")
ACTIONS = (g.BRAKES_SET, g.WHERE, g.REQUEST, g.CANCEL, g.FINISH, g.HURRY, g.STATUS, g.PUSH, g.STOP_PUSH, g.OFF, g.GATE, g.YES, g.NO, g.OPTION, g.FULL)
DIRECTIONS = (g.NOSE_LEFT, g.NOSE_RIGHT, g.STRAIGHT)
SCHEMA = {"type": "object", "properties": {
    "asks": {"type": "array", "items": {"type": "object", "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "services": {"type": "array", "items": {"type": "string", "enum": list(SERVICES)}},
        "direction": {"type": "string", "enum": ["", *DIRECTIONS]},
        "gate": {"type": "string"},
        "option": {"type": "integer"}}, "required": ["action"]}},
    "understood": {"type": "boolean"}}, "required": ["asks", "understood"]}
READ_SYSTEM = (
    "You read what an airline pilot says to the ramp agent into structured requests. Services: "
    "GPU (ground power), OperateJetways, OperateStairs, Deboarding, Cleaning, Catering, Refueling, Water, Lavatory, DeIce, "
    "Boarding, Departure (the push). Actions: request, cancel, finish (complete it now), hurry, status, push (with direction "
    "nose_left, nose_right or straight; tail left means nose_right, tail right means nose_left), stop_push, off (take it "
    "away, the way to ask for a jetway or stairs to be disconnected), gate (a stand asked for), where (which stand they're on), brakes_set (they say the "
    "parking brake is set, or just \"it's set\" after you asked for it), yes, no, option (a numbered choice), full (the whole turnaround). Only what was really "
    "asked for: something they tell you isn't a request, and a service named in the situation isn't being asked about. "
    'Not something for the ramp, or unclear: {"asks": [], "understood": false}. Reply as JSON: '
    '{"asks": [{"action": "request", "services": ["Catering"]}], "understood": true}')
WORD_SYSTEM = (
    "You are the ramp agent on the radio with the flight crew. Put what you're about to say in your own words: one short "
    "spoken sentence or two, a working person on a busy ramp, plain and friendly. Keep what it says: the same services, the "
    "same numbers and names. Add nothing: no gate, clearance or time that isn't in it, no promise, and never ask them to let you know "
    "when they're ready. Don't say who's calling whom. "
    'Reply as JSON: {"reply": "..."}')


@dataclass
class ModelRead:
    asks: tuple[g.Ask, ...]
    rejected: str = ""  # why the model's reading was turned away ("": it wasn't)


def checked(raw: str) -> ModelRead:
    """The model's reply, kept only if it's all within the list."""
    try:
        data = json.loads(raw)
        items = data["asks"]
        if not isinstance(items, list):
            raise TypeError
    except (ValueError, KeyError, TypeError) as exc:
        return ModelRead((), f"not the JSON asked for ({exc})")
    asks: list[g.Ask] = []
    for item in items:
        if not isinstance(item, dict) or item.get("action") not in ACTIONS:
            return ModelRead((), f"an action that isn't on the list: {item}")
        services = item.get("services") or []
        direction = item.get("direction") or ""
        if not isinstance(services, list) or any(s not in SERVICES for s in services):
            return ModelRead((), f"a service that isn't on the list: {services}")
        if direction not in ("", *DIRECTIONS):
            return ModelRead((), f"a direction that isn't on the list: {direction}")
        asks.append(g.Ask(item["action"], tuple(services), direction, str(item.get("gate") or "")[:8], int(item.get("option") or 0)))
    return ModelRead(tuple(asks))


class RampModel:
    def __init__(self, backend: Any, mode: str = "mostly_llm", wait_s: float = 15.0, language: str = "") -> None:
        """``language``: its name ("German"), when the pilot talks in one: what the crew say stays in it."""
        self.backend, self.mode, self.wait_s, self.language = backend, mode, wait_s, language

    @property
    def reads(self) -> bool:
        return self.backend is not None and self.mode != "scripted"

    @property
    def words(self) -> bool:
        return self.backend is not None and self.mode in ("mostly_llm", "llm")

    def _ask(self, purpose: str, system: str, prompt: str, schema: dict, t: float, max_tokens: int) -> tuple[str, LlmExchange]:
        request = LlmRequest("crew", system, (("user", prompt),), schema, max_tokens=max_tokens)
        result = self.backend.complete(request, timeout_s=self.wait_s)
        model = getattr(self.backend, "model", "")
        return result.text or "", LlmExchange(t=t, purpose=purpose, model=model, key=request.key(model), prompt=prompt,
                                              response=result.text or "", outcome="used" if result.text else "error",
                                              detail=result.error or "", latency_ms=result.latency_ms)

    def read(self, heard: str, situation: str, t: float) -> tuple[ModelRead, list[LlmExchange]]:
        """What the pilot asked for, as the model reads it."""
        text, record = self._ask("ramp_read", READ_SYSTEM, f"Situation: {situation}\nPilot: \"{heard}\"", SCHEMA, t, 200)
        if not text:
            return ModelRead((), record.detail or "no answer"), [record]
        read = checked(text)
        if read.rejected:
            record = LlmExchange(t=t, purpose=record.purpose, model=record.model, key=record.key, prompt=record.prompt,
                                 response=record.response, outcome="rejected", detail=read.rejected, latency_ms=record.latency_ms)
        return read, [record]

    def word(self, heard: str, draft: str, t: float) -> tuple[str, list[LlmExchange]]:
        """The crew's line in their own words; the script's when the model can't, which is the same answer."""
        system = WORD_SYSTEM + (f" Say it in {self.language}, as what you were going to say is." if self.language else "")
        text, record = self._ask("ramp_word", system, f"Pilot said: \"{heard}\"\nYou were going to say: \"{draft}\"",
                                 {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]}, t, 120)
        try:
            reply = str(json.loads(text).get("reply", "")).strip() if text else ""
        except (ValueError, AttributeError):
            reply = ""
        return reply or draft, [record]
