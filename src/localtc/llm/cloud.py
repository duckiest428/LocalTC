"""Language models in the cloud: optional, off unless ``[cloud] enabled``. Better answers than a small model on the
sim's PC, at the price of the flight's words leaving it (to the service answering, and only while this is on).

Every service here speaks the OpenAI chat-completions API, so one client serves them all. Each is a ``Provider``: its
address, whether it needs a key, and the models to try there, in order. A *route* is one model at one service; a call
goes down the routes in priority order (Mistral first, for its generous free limits, then the no-key service),
and a route that fails steps aside for a while:

- rate limited or out of free allowance (429, 402): that model, until its ``Retry-After``, else 30 s, doubling each
  time to 10 min (limits are per model; a limit of 0, a model the plan doesn't include, drops it for the session);
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
USER_AGENT = "LocalTC (+https://localtc.tech)"


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


# Priority order: Mistral first (the most generous free limits), then no key, then the other free keys. Model names
# are the services' own, best first; when a flight starts, each keyed service's own model list is read (``discover``)
# and a model it doesn't have is dropped ([cloud] models replaces a service's list).
PROVIDERS: tuple[Provider, ...] = (
    # The free Experiment plan allows ministral at 30-750 requests a minute; mistral-small and -medium show a
    # limit of 0 there (paid plans only), so they come last and drop out on their first refusal.
    Provider("mistral", "Mistral", "https://api.mistral.ai/v1",
             ("ministral-14b-latest", "ministral-8b-latest", "ministral-3b-latest", "mistral-small-latest"),
             key="free", signup="https://console.mistral.ai",
             note="Free Experiment plan: the most generous free limits here (the Ministral models)."),
    Provider("pollinations", "Pollinations", "https://text.pollinations.ai/openai", ("openai-fast",), key="none",
             signup="https://enter.pollinations.ai",
             note="No key, no account. Its anonymous allowance is small and comes and goes: when it's used up, the "
                  "next service answers."),
    Provider("groq", "Groq", "https://api.groq.com/openai/v1",
             ("openai/gpt-oss-120b", "openai/gpt-oss-20b"), key="free",
             signup="https://console.groq.com/keys", note="Free tier, per-model daily limits; answers in well under a second."),
    Provider("aistudio", "Google AI Studio (Gemini)", "https://generativelanguage.googleapis.com/v1beta/openai",
             ("gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3-flash"), key="free",
             signup="https://aistudio.google.com/apikey", note="Free tier with daily limits per model."),
    Provider("cloudflare", "Cloudflare Workers AI", "https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1",
             ("@cf/openai/gpt-oss-120b", "@cf/meta/llama-3.3-70b-instruct-fp8-fast", "@cf/openai/gpt-oss-20b"),
             key="free", signup="https://dash.cloudflare.com/profile/api-tokens",
             note="Free daily allowance. The key is your account ID and an API token with Workers AI access, as "
                  "ACCOUNT_ID:TOKEN."),
    Provider("nvidia", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1",
             ("openai/gpt-oss-20b", "nvidia/nemotron-3.5-lightning-30b-a3b"),
             key="free", signup="https://build.nvidia.com", note="Free developer access."),
    Provider("qwen", "Qwen (Alibaba Model Studio)", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
             ("qwen-flash", "qwen-plus"), key="free", signup="https://modelstudio.console.alibabacloud.com",
             note="Free quota for new accounts. (Qwen Chat's and Qwen Code's own free use isn't open to other apps.)"),
    Provider("siliconflow", "SiliconFlow", "https://api.siliconflow.com/v1", ("Qwen/Qwen3-8B", "deepseek-ai/DeepSeek-V3"),
             key="free", signup="https://cloud.siliconflow.com", note="Some models free; the rest from credits."),
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


def base_url(p: Provider, key: str) -> str:
    """The service's address; Cloudflare's has the account in it (its key is "ACCOUNT_ID:TOKEN")."""
    url = p.base_url.rstrip("/")
    if "{account}" in url:
        url = url.replace("{account}", key.split(":", 1)[0].strip() if ":" in key else "")
    return url


def token(p: Provider, key: str) -> str:
    return key.split(":", 1)[1].strip() if "{account}" in p.base_url and ":" in key else key


Getter = Callable[[str, dict[str, str], float], tuple[int, bytes]]


def _get(url: str, headers: dict[str, str], timeout_s: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b""


def discover(found: list[Route], *, timeout_s: float = 8.0, get: Getter = _get) -> list[Route]:
    """Each keyed service asked which models it has (its /models list), and the routes to models it doesn't have
    dropped: a renamed or retired model then costs nothing in flight. A service that can't say keeps its list."""
    listed: dict[str, set[str] | None] = {}
    for route in found:
        p = route.provider
        if p.id in listed or not route.key or "{account}" in p.base_url:
            continue
        try:
            status, raw = get(base_url(p, route.key) + "/models",
                              {"Authorization": f"Bearer {token(p, route.key)}", "User-Agent": USER_AGENT}, timeout_s)
            ids = {str(m.get("id", "")).removeprefix("models/") for m in json.loads(raw).get("data", [])} if status == 200 else None
        except (OSError, ValueError, AttributeError, urllib.error.URLError):
            ids = None
        listed[p.id] = ids or None
    kept = [r for r in found if listed.get(r.provider.id) is None or r.model in listed[r.provider.id]]
    for r in found:
        if r not in kept:
            log.info("Cloud model %s isn't offered by %s now: skipped", r.model, r.provider.name)
    # A service none of whose models are listed keeps them all: better a refusal in flight than no service.
    for pid in {r.provider.id for r in found} - {r.provider.id for r in kept}:
        kept += [r for r in found if r.provider.id == pid]
    return sorted(kept, key=found.index)


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
            headers["Authorization"] = f"Bearer {token(p, route.key)}"
        url = base_url(p, route.key) + "/chat/completions"
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
            if exc.kind == "limit" and route.provider.key == "none":  # an anonymous allowance is the service's
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
    limits = [v.strip() for n, v in headers.items() if n.lower().startswith("x-ratelimit-limit-req")]
    if status == 429 and limits and all(v == "0" for v in limits):
        return CloudError("model", f"not in your plan (a limit of 0 requests; {message})")
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
