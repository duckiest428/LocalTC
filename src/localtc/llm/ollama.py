"""Talks to a local Ollama server over its HTTP API. Standard library only; nothing leaves the machine.

Answers are forced into the request's JSON schema (Ollama's structured outputs), at temperature 0
with a fixed seed, so the same question gets the same answer.
"""

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from localtc.atc_core.llm import LlmReply, LlmRequest


@dataclass(frozen=True)
class OllamaStatus:
    reachable: bool
    models: tuple[str, ...] = ()
    error: str = ""
    version: str = ""

    def has(self, model: str) -> bool:
        wanted = model if ":" in model else f"{model}:latest"
        return wanted in self.models or model in self.models


@dataclass
class OllamaBackend:
    model: str = "llama3.2:3b"
    base_url: str = "http://127.0.0.1:11434"  # not "localhost": on Windows that tries IPv6 first and costs ~2 s a call
    keep_alive: str = "1h"  # keep the model loaded between transmissions
    num_ctx: int = 4096
    options: dict = field(default_factory=dict)

    def complete(self, request: LlmRequest, *, timeout_s: float) -> LlmReply:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": request.system}]
            + [{"role": role, "content": content} for role, content in request.messages],
            "stream": False,
            "format": request.schema,
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0, "seed": 0, "num_predict": request.max_tokens, "num_ctx": self.num_ctx,
                        **self.options},
        }
        started = time.monotonic()
        try:
            data = self._post("/api/chat", body, timeout_s)
        except TimeoutError:
            return LlmReply(None, (time.monotonic() - started) * 1000, "timeout")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                return LlmReply(None, (time.monotonic() - started) * 1000, "timeout")
            return LlmReply(None, (time.monotonic() - started) * 1000, f"error: {reason}")
        content = (data.get("message") or {}).get("content")
        if not isinstance(content, str):
            return LlmReply(None, (time.monotonic() - started) * 1000, f"error: unexpected response {str(data)[:200]}")
        return LlmReply(content, (time.monotonic() - started) * 1000)

    def status(self, timeout_s: float = 2.0) -> OllamaStatus:
        try:
            tags = self._get("/api/tags", timeout_s)
            version = self._get("/api/version", timeout_s).get("version", "")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return OllamaStatus(False, error=str(getattr(exc, "reason", exc)))
        return OllamaStatus(True, tuple(m.get("name", "") for m in tags.get("models", [])), version=version)

    def pull(self, *, print_progress: bool = False) -> bool:
        """Download the model through Ollama (``ollama pull``). Returns True when it's installed."""
        req = urllib.request.Request(self.base_url.rstrip("/") + "/api/pull",
                                     data=json.dumps({"model": self.model, "stream": True}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        last = ""
        try:
            with urllib.request.urlopen(req, timeout=3600) as resp:
                for line in resp:
                    update = json.loads(line or b"{}")
                    if "error" in update:
                        print(f"  {update['error']}") if print_progress else None
                        return False
                    status = update.get("status", "")
                    if update.get("total") and update.get("completed") is not None:
                        status += f" {100 * update['completed'] // update['total']}%"
                    if print_progress and status != last:
                        print(f"  {status}", flush=True)
                        last = status
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"  failed: {exc}") if print_progress else None
            return False
        return self.status().has(self.model)

    def _post(self, path: str, body: dict, timeout_s: float) -> dict:
        req = urllib.request.Request(self.base_url.rstrip("/") + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read())

    def _get(self, path: str, timeout_s: float) -> dict:
        with urllib.request.urlopen(self.base_url.rstrip("/") + path, timeout=timeout_s) as resp:
            return json.loads(resp.read())
