"""The language model wording ATC replies that have no template: answers to free-form questions
and declining requests the engine doesn't support.

Routine calls never come through here; their phraseology stays exactly as the templates say.
The engine has already decided that the reply is an answer or an "unable". The model only words
it, and the result is checked before it goes on the air:
- no instructions or approvals (no "cleared", "climb", "contact", "squawk", ...);
- every number must come from the facts it was given;
- short, one sentence, callsign added by the template, not the model.
Anything that fails twice falls back to the template "unable".
"""

import json
import re
import time
from collections.abc import Callable
from dataclasses import replace

from localtc.atc_core.llm.backend import LlmBackend, LlmRequest
from localtc.atc_core.phraseology import speech
from localtc.atc_core.values import Phrase
from localtc.sim_api import LlmExchange

SYSTEM = """You word one short reply for an air traffic controller, in standard radio phraseology. \
The controller has already decided what to say; you only put it into words.
- Use only the facts given. Never invent numbers, names or information.
- Never give or approve an instruction: no clearances, altitudes, headings, frequencies to contact, squawk codes \
or taxi routes, and never say "approved". If the pilot asks for something like that, say "unable" and, if it \
fits, "continue as filed".
- If the facts don't answer the question, say "unable, information not available".
- At most 20 words. Do not start with the callsign; it is added for you."""

SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"],
          "additionalProperties": False}

EXAMPLES: tuple[tuple[str, str], ...] = (
    ('Pilot asked: "any weather to report at quebec"\nDecision: answer\nFacts: Quebec wind 240@8; Quebec altimeter 29.92',
     '{"reply":"Quebec wind 240 at 8, altimeter 29.92"}'),
    ('Pilot asked: "request direct Quebec"\nDecision: decline\nFacts: destination Quebec',
     '{"reply":"unable direct at this time, continue as filed"}'),
    ('Pilot asked: "how long until we get there"\nDecision: answer\nFacts: none',
     '{"reply":"unable, information not available"}'),
)

BANNED = {"cleared", "clear", "climb", "descend", "maintain", "turn", "heading", "contact", "squawk", "taxi", "approved",
          "proceed", "vectors", "expect", "line", "takeoff", "monitor", "identify"}
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
MAX_WORDS = 25


class PhraseError(ValueError):
    pass


def user_message(pilot: str, decision: str, facts: dict[str, str]) -> str:
    listed = "; ".join(f"{k} {v}" for k, v in facts.items()) or "none"
    return f'Pilot asked: "{pilot}"\nDecision: {decision}\nFacts: {listed}'


def check_reply(raw: str, facts: dict[str, str], callsigns: tuple[str, ...]) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PhraseError(f"not valid JSON ({exc.msg})") from None
    reply = data.get("reply") if isinstance(data, dict) else None
    if not isinstance(reply, str) or not reply.strip():
        raise PhraseError("reply must be a non-empty string")
    text = " ".join(reply.split()).strip(" .")
    for callsign in callsigns:  # the template adds the callsign; drop it if the model did too
        if callsign and text.lower().startswith(callsign.lower()):
            text = text[len(callsign):].lstrip(" ,")
    words = re.findall(r"[a-z]+", text.lower())
    if not words:
        raise PhraseError("reply has no words")
    if len(text.split()) > MAX_WORDS:
        raise PhraseError(f"reply is longer than {MAX_WORDS} words")
    if banned := sorted(set(words) & BANNED):
        raise PhraseError(f"reply gives an instruction ({', '.join(banned)}); only answer or say unable")
    known = set(NUMBER_RE.findall(" ".join(facts.values())))
    if invented := [n for n in NUMBER_RE.findall(text) if n not in known]:
        raise PhraseError(f"reply has numbers that are not in the facts: {', '.join(invented)}")
    return text


def spoken(text: str) -> str:
    """Numbers read digit by digit, as controllers say them."""
    return NUMBER_RE.sub(lambda m: speech.digits(m.group(0)), text)


class LlmPhraser:
    def __init__(self, backend: LlmBackend, *, timeout_s: float = 2.5, max_attempts: int = 2, budget_s: float = 4.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.backend = backend
        self.timeout_s, self.max_attempts, self.budget_s = timeout_s, max_attempts, budget_s
        self._clock = clock

    def reply(self, *, pilot: str, decision: str, facts: dict[str, str], callsigns: tuple[str, ...], t: float,
              trigger: str = "") -> tuple[Phrase | None, list[LlmExchange]]:
        """``decision`` is "answer" or "decline". Returns the checked wording, or None to use the template."""
        messages: tuple[tuple[str, str], ...] = tuple(
            turn for user, assistant in EXAMPLES for turn in (("user", user), ("assistant", assistant))
        ) + (("user", user_message(pilot, decision, facts)),)
        request = LlmRequest("phrase", SYSTEM, messages, SCHEMA, max_tokens=80)
        exchanges: list[LlmExchange] = []
        deadline = self._clock() + self.budget_s
        for attempt in range(1, self.max_attempts + 1):
            remaining = deadline - self._clock()
            if remaining < 0.2:
                break
            result = self.backend.complete(request, timeout_s=min(self.timeout_s, remaining))

            def record(outcome: str, detail: str = "", _result=result, _request=request, _attempt=attempt) -> None:
                exchanges.append(LlmExchange(
                    t=t, purpose="phrase", model=self.backend.model, key=_request.key(self.backend.model),
                    prompt=_request.prompt, response=_result.text or "", outcome=outcome, detail=detail, trigger=trigger,
                    latency_ms=round(_result.latency_ms, 1), attempt=_attempt,
                ))

            if result.text is None:
                record(result.error.split(":")[0] or "error", result.error)
                break
            try:
                text = check_reply(result.text, facts, callsigns)
            except PhraseError as exc:
                record("invalid", str(exc))
                request = replace(request, messages=(
                    *request.messages, ("assistant", result.text),
                    ("user", f"That reply can't be used: {exc}. Reply with the corrected JSON only."),
                ))
                continue
            record("used")
            return Phrase(text, spoken(text)), exchanges
        return None, exchanges
