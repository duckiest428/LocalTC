"""Talks to a local Ollama server over its HTTP API. Standard library only; nothing leaves the machine.

Answers are forced into the request's JSON schema (Ollama's structured outputs), at temperature 0
with a fixed seed, so the same question gets the same answer.

Where the model runs is Ollama's choice when it loads it: by default as many layers on the graphics card as
fit in the video memory free at that moment, the rest on the CPU. ``cpu_only`` asks for none on the card
(``num_gpu: 0``), leaving it to the sim. A model already loaded stays where it is, whatever a later call
asks, so ``placement()`` says where it is and ``unload()`` lets it load again elsewhere.

A model that isn't loaded costs a cold load on the next call (several seconds on a GPU, tens on a CPU, the
whole prompt read again): a certain timeout. ``keep_warm()`` refreshes it through a long quiet cruise.
"""

import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from localtc.atc_core.llm import LlmReply, LlmRequest

log = logging.getLogger(__name__)
COLD_LOAD_S = 1.0  # a call that spent longer than this loading the model found it unloaded


@dataclass(frozen=True)
class Placement:
    """Where Ollama has the model loaded (/api/ps)."""

    size: int  # bytes in memory: the weights and the context
    size_vram: int  # of which on the graphics card
    context: int = 0

    @property
    def on_gpu(self) -> float:
        return self.size_vram / self.size if self.size else 0.0

    def describe(self) -> str:
        gb = lambda b: f"{b / 1e9:.1f} GB"
        if self.size_vram <= 0:
            return f"{gb(self.size)}, on the CPU"
        if self.on_gpu >= 0.99:
            return f"{gb(self.size)}, all on the graphics card"
        return (f"{gb(self.size)}, split: {gb(self.size_vram)} on the graphics card and the rest on the CPU "
                "(too little free video memory when it loaded: slower than either)")


@dataclass(frozen=True)
class OllamaStatus:
    reachable: bool
    models: tuple[str, ...] = ()
    error: str = ""
    version: str = ""
    sizes: dict = field(default_factory=dict)  # model -> bytes on disk

    def has(self, model: str) -> bool:
        wanted = model if ":" in model else f"{model}:latest"
        return wanted in self.models or model in self.models


@dataclass
class OllamaBackend:
    model: str = "llama3.2:3b"
    base_url: str = "http://127.0.0.1:11434"  # not "localhost": on Windows that tries IPv6 first and costs ~2 s a call
    keep_alive: str = "1h"  # keep the model loaded between transmissions
    num_ctx: int = 4096
    cpu_only: bool = False  # nothing on the graphics card: num_gpu 0 (applies when the model loads)
    cpu_threads: int = 0  # 0: Ollama's choice (every physical core)
    options: dict = field(default_factory=dict)
    last_stats: dict = field(default_factory=dict, repr=False)  # the last call's timings, from Ollama
    loading: bool = field(default=False, repr=False)  # warming up: a load is expected, not news
    _keeper: threading.Event | None = field(default=None, repr=False)

    def load_options(self) -> dict:
        """The options that decide where and how the model loads: the same on every call, or it reloads."""
        opts: dict = {"num_ctx": self.num_ctx}
        if self.cpu_only:
            opts["num_gpu"] = 0
        if self.cpu_threads > 0:
            opts["num_thread"] = self.cpu_threads
        return opts

    def complete(self, request: LlmRequest, *, timeout_s: float) -> LlmReply:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": request.system}]
            + [{"role": role, "content": content} for role, content in request.messages],
            "stream": False,
            "format": request.schema,
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0, "seed": 0, "num_predict": request.max_tokens, **self.load_options(), **self.options},
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
        self._timings(data, request)
        content = (data.get("message") or {}).get("content")
        if not isinstance(content, str):
            return LlmReply(None, (time.monotonic() - started) * 1000, f"error: unexpected response {str(data)[:200]}")
        return LlmReply(content, (time.monotonic() - started) * 1000)

    def _timings(self, data: dict, request: LlmRequest) -> None:
        ms = lambda k: round((data.get(k) or 0) / 1e6)  # Ollama reports nanoseconds
        self.last_stats = {"load_ms": ms("load_duration"), "prompt_tokens": data.get("prompt_eval_count", 0),
                           "prompt_ms": ms("prompt_eval_duration"), "tokens": data.get("eval_count", 0), "gen_ms": ms("eval_duration")}
        st = self.last_stats
        if st["load_ms"] > COLD_LOAD_S * 1000 and not self.loading:
            log.warning("The language model wasn't loaded: this %s call spent %.1f s loading it", request.purpose, st["load_ms"] / 1000)
        log.debug("LLM %s: load %d ms, prompt %d tokens in %d ms, %d tokens in %d ms", request.purpose, st["load_ms"],
                  st["prompt_tokens"], st["prompt_ms"], st["tokens"], st["gen_ms"])

    def placement(self, timeout_s: float = 2.0) -> Placement | None:
        """Where the model is loaded, or None when it isn't (or Ollama can't say)."""
        try:
            running = self._get("/api/ps", timeout_s).get("models", [])
        except (urllib.error.URLError, OSError, ValueError):
            return None
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        for m in running:
            if m.get("name") in (self.model, wanted) or m.get("model") in (self.model, wanted):
                return Placement(int(m.get("size") or 0), int(m.get("size_vram") or 0), int(m.get("context_length") or 0))
        return None

    def unload(self, timeout_s: float = 10.0) -> None:
        """Out of memory now (keep_alive 0): the next call loads it again, with this backend's options."""
        try:
            self._post("/api/generate", {"model": self.model, "keep_alive": 0}, timeout_s)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.info("Couldn't unload %s: %s", self.model, exc)

    def keep_warm(self, keep_alive: str | None = None, timeout_s: float = 300.0) -> None:
        """Load the model if it isn't, and keep it for ``keep_alive`` more (a request with no prompt)."""
        try:
            self._post("/api/generate", {"model": self.model, "keep_alive": keep_alive or self.keep_alive,
                                         "options": self.load_options()}, timeout_s)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.info("Couldn't keep %s loaded: %s", self.model, exc)

    def start_keeping(self, every_s: float = 600.0) -> None:
        """While a flight is on: refresh the model now and then, so a long quiet cruise never unloads it."""
        self.stop_keeping()
        stop = self._keeper = threading.Event()

        def run() -> None:
            while not stop.wait(every_s):
                self.keep_warm()

        threading.Thread(target=run, name="llm-keep-warm", daemon=True).start()

    def stop_keeping(self, keep_alive: str | None = None) -> None:
        """The flight is over: stop refreshing, and leave the model loaded for ``keep_alive`` (the pilot's
        setting; "0" unloads it now)."""
        if self._keeper is not None:
            self._keeper.set()
            self._keeper = None
        if keep_alive is None:
            return
        if keep_alive.strip() in ("0", "0s", "0m"):
            self.unload()
        elif self.placement() is not None:
            self.keep_warm(keep_alive)

    def status(self, timeout_s: float = 2.0) -> OllamaStatus:
        try:
            tags = self._get("/api/tags", timeout_s)
            version = self._get("/api/version", timeout_s).get("version", "")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return OllamaStatus(False, error=str(getattr(exc, "reason", exc)))
        models = tags.get("models", [])
        return OllamaStatus(True, tuple(m.get("name", "") for m in models), version=version,
                            sizes={m.get("name", ""): int(m.get("size") or 0) for m in models})

    def pull(self, *, print_progress: bool = False, on_progress=None) -> bool:
        """Download the model through Ollama (``ollama pull``). Returns True when it's installed.
        ``on_progress(text, fraction or None)`` hears each step."""
        def report(text: str, fraction: float | None = None) -> None:
            if on_progress is not None:
                on_progress(text, fraction)

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
                        report(f"failed: {update['error']}")
                        return False
                    status = update.get("status", "")
                    fraction = None
                    if update.get("total") and update.get("completed") is not None:
                        fraction = update["completed"] / update["total"]
                        status += f" {100 * update['completed'] // update['total']}%"
                    if status != last:
                        print(f"  {status}", flush=True) if print_progress else None
                        report(status, fraction)
                        last = status
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"  failed: {exc}") if print_progress else None
            report(f"failed: {exc}")
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
