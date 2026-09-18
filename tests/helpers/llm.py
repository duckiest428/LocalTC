"""A scripted stand-in for the language model: answers keyed by what the pilot said."""

import json
import re
from typing import Any

from localtc.atc_core.llm import LlmReply, LlmRequest
from localtc.llm.eval import fill_schema

PILOT_RE = re.compile(r'^Pilot(?: asked)?: "(.*)"$', re.MULTILINE)
TIMEOUT = "TIMEOUT"


class ScriptedBackend:
    """``understand``/``phrase`` map the pilot's words to an answer: a dict (filled out to the schema, as a
    constrained model would), a raw string, TIMEOUT, or a list of those for successive attempts.
    Anything unscripted gets an error, which the interpreter treats like a missing model."""

    model = "scripted"

    def __init__(self, understand: dict[str, Any] | None = None, phrase: dict[str, Any] | None = None) -> None:
        self.answers = {"understand": dict(understand or {}), "phrase": dict(phrase or {})}
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest, *, timeout_s: float) -> LlmReply:
        self.requests.append(request)
        match = PILOT_RE.search(request.messages[-1][1]) or PILOT_RE.search(request.prompt)
        # A retry's last message is the correction; the transmission is in the first user turn after the examples.
        if match is None or "can't be used" in request.messages[-1][1]:
            match = next((m for role, text in reversed(request.messages) if role == "user"
                          and (m := PILOT_RE.search(text)) and "can't be used" not in text), None)
        answer = self.answers[request.purpose].get(match.group(1)) if match else None
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if answer is None:
            return LlmReply(None, error="error: unscripted")
        if answer == TIMEOUT:
            return LlmReply(None, latency_ms=timeout_s * 1000, error="timeout")
        if isinstance(answer, dict):
            return LlmReply(json.dumps(fill_schema(answer, request.schema)), latency_ms=5.0)
        return LlmReply(answer, latency_ms=5.0)
