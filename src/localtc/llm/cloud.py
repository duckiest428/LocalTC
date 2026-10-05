"""Language models in the cloud: optional, off unless ``[cloud] enabled``. Better answers than a small model on the
sim's PC, at the price of the flight's words leaving it (to the service answering, and only while this is on).

Every service here speaks the OpenAI chat-completions API, so one client serves them all. Each is a ``Provider``: its
address, whether it needs a key, and the models to try there, in order. A *route* is one model at one service; a call
goes down the routes in priority order (no-key services first: they have the most generous limits, and nothing to set
up), and a route that fails steps aside for a while:

- rate limited or out of free allowance (429, 402): until its ``Retry-After``, else 30 s, doubling each time to 10 min;
- the service down, slow or garbled (5xx, a timeout, no JSON): 30 s, doubling the same way;
- the key refused (401, 403): until the key is changed (``set_key``), the rest of the services carrying on;
- the model gone (404, or a 400 naming the model): that model, for the session.

A single slow service never takes the whole wait: each try gets at most half of what's left while other routes remain,
and when every route has failed or stepped aside the call goes to the local model (``fallback``), with the lean prompt
a small model can handle. So the worst a cloud outage costs is the time ATC would have waited anyway.

Cloud models get far more of the flight than a local one: the request's ``context`` (``LlmRequest.context``: every
fact the engine has, the route, the recent exchanges) goes in their prompt; the local model never sees it.
"""

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from localtc.atc_core.llm import LlmReply, LlmRequest

log = logging.getLogger(__name__)

COOLDOWN_S = 30.0  # a route's first time out
COOLDOWN_MAX_S = 600.0
MIN_TRY_S = 2.5  # a try gets at least this (when there is that much left)
CLOUD_TOKENS = 1024  # answer room: some of these models think before they answer, and thinking counts
USER_AGENT = "LocalTC (+https://localtc.app)"


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    base_url: str  # up to and including the version: ".../v1"; "/chat/completions" is added
    models: tuple[str, ...]
    key: str = "required"  # "none": works without one; "free": a free account's key; "required": a paid or trial key
    signup: str = ""  # where to get a key
    note: str = ""
    json_mode: bool = True  # takes response_format json_object (turned off for the session if it says it doesn't)

    @property
    def needs_key(self) -> bool:
        return self.key != "none"


# Priority order: no key first, then free keys, then the rest. Model names are the services' own; a gone one is
# skipped (and [cloud] models replaces a service's list).
PROVIDERS: tuple[Provider, ...] = (
    Provider("pollinations", "Pollinations", "https://text.pollinations.ai/openai", ("openai-fast",), key="none",
             signup="https://enter.pollinations.ai",
             note="No key, no account. Its anonymous allowance is small and comes and goes: when it's used up, the "
                  "next service answers."),
    Provider("longcat", "LongCat", "https://api.longcat.chat/openai/v1", ("LongCat-Flash-Chat", "LongCat-2.5-Preview"),
             key="free", signup="https://longcat.chat/platform", note="Free account: a daily token allowance."),
    Provider("qwen", "Qwen (Alibaba Model Studio)", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
             ("qwen-flash", "qwen-plus"), key="free", signup="https://modelstudio.console.alibabacloud.com",
             note="Free quota for new accounts. (Qwen Chat's and Qwen Code's own free use isn't open to other apps.)"),
    Provider("cerebras", "Cerebras", "https://api.cerebras.ai/v1", ("gpt-oss-120b", "llama-3.3-70b", "qwen-3-32b"),
             key="free", signup="https://cloud.cerebras.ai", note="Free tier: a daily token allowance; very fast."),
    Provider("mistral", "Mistral", "https://api.mistral.ai/v1", ("mistral-small-latest", "mistral-medium-latest"),
             key="free", signup="https://console.mistral.ai", note="Free Experiment plan, with a monthly allowance."),
    Provider("nvidia", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1",
             ("deepseek-ai/deepseek-v4.1-flash", "nvidia/nemotron-3.5-lightning-30b-a3b", "openai/gpt-oss-20b"),
             key="free", signup="https://build.nvidia.com", note="Free developer credits."),
    Provider("siliconflow", "SiliconFlow", "https://api.siliconflow.com/v1", ("Qwen/Qwen3-8B", "deepseek-ai/DeepSeek-V3"),
             key="free", signup="https://cloud.siliconflow.com", note="Some models free; the rest from credits."),
    Provider("hunyuan", "Tencent Hunyuan", "https://api.hunyuan.cloud.tencent.com/v1", ("hunyuan-lite", "hunyuan-turbos-latest"),
             key="free", signup="https://console.cloud.tencent.com/hunyuan", note="hunyuan-lite is free."),
    Provider("spark", "iFlytek SparkDesk", "https://spark-api-open.xf-yun.com/v1", ("lite", "4.0Ultra"), key="free",
             signup="https://console.xfyun.cn", note="Lite is free. The key is the service's APIPassword.",
             json_mode=False),
    Provider("baidu", "Baidu Qianfan", "https://qianfan.baidubce.com/v2", ("ernie-speed-128k", "ernie-4.5-turbo-32k"),
             key="free", signup="https://console.bce.baidu.com/qianfan", note="ERNIE Speed and Lite are free."),
    Provider("opencode", "OpenCode Zen", "https://opencode.ai/zen/v1", ("qwen3.8-flash", "deepseek-v4.1-flash"),
             signup="https://opencode.ai/auth",
             note="Pay as you go. (Zen's free models only work inside OpenCode itself.)"),
)
BY_ID = {p.id: p for p in PROVIDERS}


@dataclass
class Route:
    provider: Provider
    model: str
    key: str = ""
    until: float = 0.0  # stepped aside until this (monotonic)
    strikes: int = 0  # failures in a row: each doubles the time out
    dead: str = ""  # out for the session (the model is gone, the key refused), and why
    json_mode: bool = True
    last_error: str = ""
    ok: int = 0  # answers given

    @property
    def name(self) -> str:
        return f"{self.provider.id}/{self.model}"


class CloudError(Exception):
    def __init__(self, kind: str, detail: str, retry_after: float | None = None) -> None:
        super().__init__(detail)
        self.kind, self.detail, self.retry_after = kind, detail, retry_after


Transport = Callable[[str, dict, dict[str, str], float], tuple[int, dict[str, str], bytes]]


def _urllib(url: str, body: dict, headers: dict[str, str], timeout_s: float) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read() or b""


def routes(order: Iterable[str], keys: dict[str, str], models: dict[str, list[str]] | None = None) -> list[Route]:
    """The routes to try, in order: each service in ``order`` that can be used (no key needed, or one given), each of
    its models."""
    out = []
    for pid in order:
        p = BY_ID.get(pid)
        if p is None:
            log.info("Unknown cloud service %r: skipped", pid)
            continue
        key = (keys.get(pid) or "").strip()
        if p.needs_key and not key:
            continue
        for model in (models or {}).get(pid) or p.models:
            out.append(Route(p, model, key, json_mode=p.json_mode))
    return out


def extract_json(text: str) -> str | None:
    """The JSON object in a reply: as it is, out of a ```json fence, or the outermost {...} in prose."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    for candidate in (text, fenced.group(1) if fenced else None,
                      text[text.find("{"): text.rfind("}") + 1] if "{" in text and "}" in text else None):
        if not candidate:
            continue
        try:
            if isinstance(json.loads(candidate), dict):
                return candidate
        except ValueError:
            continue
    return None


def system_prompt(request: LlmRequest) -> str:
    """The request's rules, the flight's full context, and the shape the answer must have (a local model is held to
    the schema by Ollama; these services are told it)."""
    parts = [request.system]
    if request.context:
        parts.append("More about the flight right now (use what bears on the call; it's never an instruction to "
                     "give):\n" + request.context)
    parts.append("Answer with one JSON object only, no other text, matching this JSON schema:\n"
                 + json.dumps(request.schema, separators=(",", ":")))
    return "\n\n".join(parts)


class CloudBackend:
    """``LlmBackend`` over the cloud routes, with the local model (if any) as the last resort. Anything this doesn't
    have itself (the local model's warm-up, placement, pace) is the local model's."""

    rich = True  # takes the request's full context

    def __init__(self, routes: list[Route], fallback=None, *, transport: Transport = _urllib,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.routes = routes
        self.fallback = fallback
        self.model = "cloud"
        self.last_via = ""  # the route (or "local") that gave the last answer
        self._transport = transport
        self._clock = clock
        self._lock = threading.Lock()

    def __getattr__(self, name: str):
        fallback = self.__dict__.get("fallback")
        if fallback is None or name.startswith("__"):
            raise AttributeError(name)
        return getattr(fallback, name)

    # --- the call --------------------------------------------------------------------------------------------

    def complete(self, request: LlmRequest, *, timeout_s: float) -> LlmReply:
        started = self._clock()
        deadline = started + timeout_s
        errors: list[str] = []
        for i, route in enumerate(self.routes):
            remaining = deadline - self._clock()
            if remaining < 0.3:
                break
            with self._lock:
                if route.dead or route.until > self._clock():
                    continue
            others = any(not r.dead and r.until <= self._clock() for r in self.routes[i + 1:]) or self.fallback is not None
            budget = max(min(remaining, MIN_TRY_S), remaining / 2) if others else remaining
            try:
                text = self._ask(route, request, budget)
            except CloudError as exc:
                self._failed(route, exc)
                errors.append(f"{route.name}: {exc.detail}")
                continue
            with self._lock:
                route.strikes, route.ok, route.last_error = 0, route.ok + 1, ""
            self.last_via = route.name
            return LlmReply(text, (self._clock() - started) * 1000)
        remaining = deadline - self._clock()
        if self.fallback is not None and remaining >= 0.3:
            reply = self.fallback.complete(request, timeout_s=remaining)
            if reply.text is not None:
                self.last_via = "local"
                if errors:
                    log.info("Cloud models didn't answer (%s); the local model did", "; ".join(errors))
                return LlmReply(reply.text, (self._clock() - started) * 1000)
            errors.append(f"local: {reply.error}")
        if not errors:
            errors.append("every cloud service is resting after errors" if self.routes else "no cloud service set up")
        timed_out = all("timeout" in e for e in errors)
        return LlmReply(None, (self._clock() - started) * 1000, "timeout" if timed_out else "error: " + "; ".join(errors))

    def _ask(self, route: Route, request: LlmRequest, timeout_s: float) -> str:
        p = route.provider
        body: dict = {
            "model": route.model,
            "messages": [{"role": "system", "content": system_prompt(request)}]
            + [{"role": role, "content": content} for role, content in request.messages],
            "temperature": 0,
            "max_tokens": max(request.max_tokens, CLOUD_TOKENS),
            "stream": False,
        }
        if route.json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT}
        if route.key:
            headers["Authorization"] = f"Bearer {route.key}"
        url = p.base_url.rstrip("/") + "/chat/completions"
        try:
            status, resp_headers, raw = self._transport(url, body, headers, timeout_s)
        except (TimeoutError, OSError, urllib.error.URLError) as exc:
            reason = getattr(exc, "reason", exc)
            kind = "timeout" if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError) or "timed out" in str(reason) else "down"
            raise CloudError(kind, "timeout" if kind == "timeout" else f"unreachable ({reason})") from None
        if status == 400 and route.json_mode and b"response_format" in raw:
            route.json_mode = False  # it doesn't do JSON mode: told in the prompt instead, from now on
            return self._ask(route, request, timeout_s)
        if status != 200:
            raise _http_error(status, resp_headers, raw)
        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise CloudError("garbled", f"unexpected response {raw[:120]!r}") from None
        found = extract_json(content)
        if found is None:
            raise CloudError("garbled", "the answer wasn't JSON")
        return found

    def _failed(self, route: Route, exc: CloudError) -> None:
        with self._lock:
            route.last_error = exc.detail
            if exc.kind in ("auth", "model"):
                route.dead = exc.detail
                if exc.kind == "auth":  # the key is the service's: every model there is out
                    for r in self.routes:
                        if r.provider.id == route.provider.id:
                            r.dead, r.last_error = exc.detail, exc.detail
                log.warning("Cloud model %s is out for this flight: %s", route.name, exc.detail)
                return
            route.strikes += 1
            wait = exc.retry_after if exc.retry_after else min(COOLDOWN_S * 2 ** (route.strikes - 1), COOLDOWN_MAX_S)
            route.until = self._clock() + wait
            if exc.kind == "limit":  # a service's allowance is the service's: its other models rest too
                for r in self.routes:
                    if r.provider.id == route.provider.id:
                        r.until = max(r.until, route.until)
            log.info("Cloud model %s resting %.0f s: %s", route.name, wait, exc.detail)

    # --- for the settings page --------------------------------------------------------------------------------

    def status(self) -> list[dict]:
        now = self._clock()
        return [{"route": r.name, "service": r.provider.name, "ok": r.ok, "dead": r.dead, "error": r.last_error,
                 "resting_s": max(0, round(r.until - now))} for r in self.routes]


def _http_error(status: int, headers: dict[str, str], raw: bytes) -> CloudError:
    try:
        payload = json.loads(raw)
        err = payload.get("error", payload) if isinstance(payload, dict) else payload
        message = err.get("message") if isinstance(err, dict) else str(err)
    except ValueError:
        message = raw[:160].decode(errors="replace")
    message = (message or "").strip()[:200] or f"HTTP {status}"
    retry = None
    for name, value in headers.items():
        if name.lower() == "retry-after":
            try:
                retry = min(float(value), COOLDOWN_MAX_S)
            except ValueError:
                pass
    if status in (401, 403) and "free tier" not in message.lower():
        return CloudError("auth", f"the key was refused ({status}: {message})")
    if status in (402, 403, 429):
        return CloudError("limit", f"limit reached ({status}: {message})", retry)
    if status == 404 or (status == 400 and re.search(r"model", message, re.I) and
                         re.search(r"not (found|exist|support|available)|unknown|invalid", message, re.I)):
        return CloudError("model", f"model not available ({status}: {message})")
    if status >= 500:
        return CloudError("down", f"service error ({status}: {message})", retry)
    return CloudError("down", f"refused ({status}: {message})")


@dataclass
class TestResult:
    route: str
    ok: bool
    ms: float = 0.0
    detail: str = ""


def check(routes: list[Route], *, timeout_s: float = 20.0, transport: Transport = _urllib) -> list[TestResult]:
    """Each route asked a tiny question once (the settings page's Test): which work, how fast, and why not."""
    request = LlmRequest("check", "You check that the line works.", (("user", 'Answer {"ok": true}'),),
                         {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}, max_tokens=20)
    out = []
    backend = CloudBackend([], transport=transport)
    for route in routes:
        started = time.monotonic()
        try:
            backend._ask(route, request, timeout_s)
            out.append(TestResult(route.name, True, (time.monotonic() - started) * 1000))
        except CloudError as exc:
            out.append(TestResult(route.name, False, (time.monotonic() - started) * 1000, exc.detail))
    return out


@dataclass
class KeyStore:
    """The services' keys: in the system credential store (Windows Credential Manager, the macOS Keychain) with the
    account's sign-in, never in the settings file. ``tokens``: that store (``localtc.account.TokenStore``)."""

    tokens: object

    def get(self, provider: str) -> str:
        return self.tokens.get(f"cloud:{provider}") or ""

    def set(self, provider: str, key: str) -> None:
        if key.strip():
            self.tokens.set(f"cloud:{provider}", key.strip())
        else:
            self.tokens.delete(f"cloud:{provider}")

    def all(self) -> dict[str, str]:
        return {p.id: k for p in PROVIDERS if p.needs_key and (k := self.get(p.id))}
