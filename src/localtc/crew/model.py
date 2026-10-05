"""The language model on the intercom: what the grammar can't read.

``[crew] llm``:
- ``off``: the grammar and the data answers only; anything else gets "say again?".
- ``questions``: the model answers what the pilot asks or says, from the copilot's facts and nothing else.
- ``full``: also commands said in other words ("could you drop the gear for me"). The copilot never acts on the
  model's reading straight away: it says what it understood and waits for "confirm".

Every reply is checked: a number in it must be in the facts or the pilot's words, and a command's value must have
been said. What fails the check isn't said; the pilot hears "say again?".
"""

import json
import logging
import re
from dataclasses import dataclass

from localtc.atc_core.llm.backend import LlmRequest, waits
from localtc.crew.answers import numbers_in
from localtc.crew.commands import Command
from localtc.sim_api import LlmExchange

log = logging.getLogger(__name__)

ACTIONS = ("gear", "flaps", "light", "spoilers", "autopilot", "ap_mode", "autothrottle", "heading", "altitude",
           "speed", "vs", "squawk", "com_active", "com_standby", "com_swap", "altimeter", "parking_brake")
SYSTEM = """You are the first officer (pilot monitoring) in an airliner cockpit, talking with the captain on the \
intercom. Answer the captain in one or two short spoken sentences, the way a calm, professional first officer \
would. Use ONLY the facts given; if the answer isn't in them, say you don't have it. Never invent numbers.

If the captain asks you to set, move or switch something in the cockpit, answer with kind "command" and the action:
gear (value up|down), flaps (value: the setting, e.g. 1, 2, full, up), light (target landing|taxi|strobe|beacon|nav|logo, \
value on|off), spoilers (value arm|disarm|extend|retract), autopilot (value on|off), ap_mode (target \
heading|nav|approach|altitude|vs|flc, value on), autothrottle (value on|off), heading (value degrees), altitude \
(value feet), speed (value knots), vs (value feet per minute, negative down), squawk (value 4 digits), com_active or \
com_standby (value MHz), com_swap, altimeter (value inches like 29.92, or target hpa and value like 1013), \
parking_brake (value on|off). Otherwise kind "reply". Your words go in "reply" either way."""
SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["reply", "command"]},
        "action": {"type": "string", "enum": ["", *ACTIONS]},
        "value": {"type": "string"},
        "target": {"type": "string"},
        "reply": {"type": "string"},
    },
    "required": ["kind", "reply"],
}
EXAMPLES = (
    ('Facts:\nfuel: 41,300 pounds\nendurance: 6 hours 10 minutes\n\nCaptain: "are we good on gas?"',
     '{"kind": "reply", "action": "", "value": "", "target": "", "reply": "Yes, 41,300 pounds on board, a bit over six hours at this burn."}'),
    ('Facts:\naltitude: 4,980 feet\ngear: up\n\nCaptain: "drop the gear for me would you"',
     '{"kind": "command", "action": "gear", "value": "down", "target": "", "reply": "Gear down."}'),
    ('Facts:\naltitude: 35,000 feet\n\nCaptain: "how\'s the weather looking at the destination"',
     '{"kind": "reply", "action": "", "value": "", "target": "", "reply": "I don\'t have their weather yet, no ATIS until we\'re closer."}'),
    ('Facts:\nheading: 240\n\nCaptain: "bring the bug round to three one zero"',
     '{"kind": "command", "action": "heading", "value": "310", "target": "", "reply": "Heading 310."}'),
)
WORD_VALUES = {"up", "down", "on", "off", "arm", "disarm", "extend", "retract", "full"}


@dataclass(frozen=True)
class Reading:
    reply: str
    command: Command | None = None


class CrewModel:
    def __init__(self, backend, *, mode: str = "full", timeout_s: float = 8.0, patience_s: float = 20.0) -> None:
        self.backend, self.mode, self.timeout_s, self.patience_s = backend, mode, timeout_s, patience_s

    def ask(self, t: float, text: str, facts: dict[str, str],
            more: dict[str, str] | None = None) -> tuple[Reading | None, list[LlmExchange]]:
        """The model's answer to ``text`` (checked), and the exchange to record; None when it has nothing usable.
        ``more``: the rest of the flight, for a model that can take it (a cloud one); a reply may use it too."""
        if self.mode == "off" or self.backend is None:
            return None, []
        prompt = "Facts:\n" + "\n".join(f"{k}: {v}" for k, v in facts.items()) + f'\n\nCaptain: "{text}"'
        messages = tuple(m for q, a in EXAMPLES for m in (("user", q), ("assistant", a))) + (("user", prompt),)
        request = LlmRequest("crew", SYSTEM, messages, SCHEMA, max_tokens=120,
                             context="\n".join(f"- {k}: {v}" for k, v in (more or {}).items()))
        facts = {**(more or {}), **facts}  # what the reply is checked against
        timeout_s, _ = waits(self.backend, self.timeout_s, self.timeout_s, self.patience_s)
        result = self.backend.complete(request, timeout_s=timeout_s)
        model = getattr(self.backend, "model", "")
        if result.text is None:
            return None, [LlmExchange(t=t, purpose="crew", model=model, key=request.key(model), prompt=prompt,
                                      response="", outcome="timeout" if result.error == "timeout" else "error",
                                      detail=result.error, latency_ms=result.latency_ms)]
        reading, why = self._check(result.text, text, facts)
        exchange = LlmExchange(t=t, purpose="crew", model=model, key=request.key(model), prompt=prompt,
                               response=result.text, outcome="used" if reading is not None else "rejected", detail=why,
                               latency_ms=result.latency_ms)
        return reading, [exchange]

    def _check(self, raw: str, said: str, facts: dict[str, str]) -> tuple[Reading | None, str]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None, "not JSON"
        reply = str(data.get("reply", "")).strip()
        if data.get("kind") == "command" and self.mode == "full":
            action, value, target = (str(data.get(k, "")).strip().lower() for k in ("action", "value", "target"))
            if action not in ACTIONS:
                return None, f"no such action {action!r}"
            heard = said.lower()
            if value and value not in WORD_VALUES and not (set(re.findall(r"\d+", value)) <= numbers_in(said) | _spoken_digits(heard)):
                return None, f"the captain didn't say {value}"
            return Reading(reply or f"{action} {value}".strip(), Command(action, value, target)), ""
        if not reply:
            return None, "no reply"
        known = numbers_in(said) | {n for v in facts.values() for n in numbers_in(v)}
        made_up = {n for n in numbers_in(reply) if n not in known}
        if made_up:
            return None, f"reply has {', '.join(sorted(made_up))}, which isn't in the facts"
        return Reading(reply), ""


def _spoken_digits(text: str) -> set[str]:
    """The numbers in words as the grammar reads them ("three one zero" -> "310")."""
    from localtc.atc_core.readback.normalize import normalize

    return {t.text.split(".")[0] for t in normalize(text) if t.kind == "number"}
