"""The language-model interface the ATC core uses, and a backend that replays recorded answers.

The core never talks HTTP: ``localtc.llm.ollama`` implements ``LlmBackend`` for a live
model. Every call is identified by a ``key`` over everything that shapes the answer, so a
replayed session gets the recorded response back without running a model.
"""

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from localtc.sim_api import LlmExchange


@dataclass(frozen=True)
class LlmRequest:
    purpose: str  # "understand" or "phrase"
    system: str
    messages: tuple[tuple[str, str], ...]  # (role, content): few-shot turns, then the real question last
    schema: dict[str, Any]  # JSON schema the answer must follow
    max_tokens: int = 200

    @property
    def prompt(self) -> str:
        """The final user message: the part that changes from call to call."""
        return self.messages[-1][1]

    def key(self, model: str) -> str:
        blob = json.dumps([model, self.purpose, self.system, self.messages, self.schema], sort_keys=True)
        return hashlib.sha1(blob.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class LlmReply:
    text: str | None  # None: no answer (timeout, error, no recording)
    latency_ms: float = 0.0
    error: str = ""  # "timeout", "error: ...", "recorded_miss"


class LlmBackend(Protocol):
    model: str

    def complete(self, request: LlmRequest, *, timeout_s: float) -> LlmReply: ...


@dataclass
class RecordedBackend:
    """Answers from a recording's ``LlmExchange`` events, in the order they were recorded per key."""

    model: str = "recorded"
    responses: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    @classmethod
    def from_events(cls, events: Iterable[Any]) -> "RecordedBackend":
        backend = cls()
        for event in events:
            if isinstance(event, LlmExchange) and event.outcome not in ("timeout", "error", "recorded_miss"):
                backend.model = event.model
                backend.responses[event.key].append(event.response)
        return backend

    def __len__(self) -> int:
        return sum(len(v) for v in self.responses.values())

    def complete(self, request: LlmRequest, *, timeout_s: float) -> LlmReply:
        queue = self.responses.get(request.key(self.model))
        if not queue:
            # The prompt changed since recording (new code, different state): behave like a timeout.
            return LlmReply(None, error="recorded_miss")
        return LlmReply(queue.pop(0))
