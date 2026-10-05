"""The cloud language models: routes, falling back between services and models, resting after errors, the local
model as the last resort, and the extra context only cloud models get."""

import json

import pytest

from localtc.atc_core.llm import LlmReply, LlmRequest
from localtc.config import CLOUD_ORDER, Config
from localtc.llm import cloud
from localtc.llm.cloud import BY_ID, CloudBackend, extract_json, routes

REQ = LlmRequest("phrase", "You are ATC.", (("user", "say altimeter"),), {"type": "object"}, max_tokens=80,
                 context="- altimeter: 29.92")


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def ok(content: str = '{"reply": "altimeter 29.92"}'):
    return 200, {}, json.dumps({"choices": [{"message": {"content": content}}]}).encode()


class Net:
    """A fake internet: each host answers from its script, and every call is noted."""

    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict, dict, float]] = []

    def __call__(self, url, body, headers, timeout_s):
        self.calls.append((url, body, headers, timeout_s))
        key = next(k for k in self.answers if k.split("#")[0] in url and ("#" not in k or body["model"] == k.split("#")[1]))
        script = self.answers[key]
        answer = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def hosts(self) -> list[str]:
        return [f'{url.split("/")[2]}:{body["model"]}' for url, body, _, _ in self.calls]


class Local:
    model = "llama3.2:3b"
    pace_s = 1.5

    def __init__(self, text='{"reply": "local"}') -> None:
        self.text, self.calls = text, []

    def complete(self, request, *, timeout_s):
        self.calls.append((request, timeout_s))
        return LlmReply(self.text, 10.0) if self.text else LlmReply(None, 10.0, "timeout")


def test_no_key_services_come_first_and_keyless_paid_ones_are_skipped():
    found = routes(CLOUD_ORDER, {"cerebras": "k1"})
    assert [r.provider.id for r in found][0] == "pollinations"
    assert {r.provider.id for r in found} == {"pollinations", "cerebras"}
    assert all(r.key == "k1" for r in found if r.provider.id == "cerebras")


def test_models_setting_replaces_a_services_list():
    found = routes(["cerebras"], {"cerebras": "k"}, {"cerebras": ["my-model"]})
    assert [r.model for r in found] == ["my-model"]


def test_cloud_is_off_by_default():
    assert Config().cloud.enabled is False


def test_first_route_answers_with_the_full_context_and_the_schema():
    net = Net({"pollinations": [ok()]})
    backend = CloudBackend(routes(["pollinations"], {}), transport=net)
    reply = backend.complete(REQ, timeout_s=5)
    assert reply.text == '{"reply": "altimeter 29.92"}'
    system = net.calls[0][1]["messages"][0]["content"]
    assert "- altimeter: 29.92" in system and "JSON schema" in system
    assert net.calls[0][1]["max_tokens"] >= 1024  # room for models that think first
    assert "Authorization" not in net.calls[0][2]  # no key, none sent
    assert backend.last_via == "pollinations/openai-fast"


def test_rate_limit_falls_through_and_the_service_rests():
    clock = Clock()
    net = Net({"pollinations": [(402, {}, b"{}")], "cerebras": [ok()]})
    backend = CloudBackend(routes(["pollinations", "cerebras"], {"cerebras": "k"}), transport=net, clock=clock)
    assert backend.complete(REQ, timeout_s=5).text
    assert backend.complete(REQ, timeout_s=5).text
    hosts = net.hosts()
    assert hosts[0].startswith("text.pollinations.ai") and hosts[1].startswith("api.cerebras.ai")
    assert hosts[2].startswith("api.cerebras.ai")  # pollinations resting: not asked again
    assert net.calls[1][2]["Authorization"] == "Bearer k"
    clock.t += cloud.COOLDOWN_S + 1
    backend.complete(REQ, timeout_s=5)
    assert net.hosts()[3].startswith("text.pollinations.ai")  # rested: first again


def test_rest_doubles_and_honours_retry_after():
    clock = Clock()
    net = Net({"pollinations": [(429, {"Retry-After": "7"}, b"{}")]})
    backend = CloudBackend(routes(["pollinations"], {}), transport=net, clock=clock)
    backend.complete(REQ, timeout_s=5)
    assert backend.routes[0].until == pytest.approx(clock.t + 7)
    net.answers["pollinations"] = [(503, {}, b"{}")]
    clock.t += 8
    backend.complete(REQ, timeout_s=5)
    assert backend.routes[0].until == pytest.approx(clock.t + 2 * cloud.COOLDOWN_S)  # second strike


def test_refused_key_takes_out_every_model_of_that_service():
    net = Net({"cerebras": [(401, {}, b'{"error": {"message": "bad key"}}')], "mistral": [ok()]})
    backend = CloudBackend(routes(["cerebras", "mistral"], {"cerebras": "x", "mistral": "y"}), transport=net)
    assert backend.complete(REQ, timeout_s=5).text
    assert all(r.dead for r in backend.routes if r.provider.id == "cerebras")
    backend.complete(REQ, timeout_s=5)
    assert sum(1 for h in net.hosts() if "cerebras" in h) == 1


def test_a_missing_model_is_skipped_but_the_service_stays():
    first, second = BY_ID["cerebras"].models[:2]
    net = Net({f"cerebras#{first}": [(404, {}, b'{"message": "model not found"}')], f"cerebras#{second}": [ok()]})
    backend = CloudBackend(routes(["cerebras"], {"cerebras": "k"}), transport=net)
    assert backend.complete(REQ, timeout_s=5).text
    assert net.hosts() == [f"api.cerebras.ai:{first}", f"api.cerebras.ai:{second}"]
    assert backend.routes[0].dead and not backend.routes[1].dead


def test_prose_or_fenced_json_is_taken_and_non_json_moves_on():
    assert extract_json('Sure! ```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('<think>hmm {x}</think>{"a": 2}') == '{"a": 2}'
    assert extract_json("no json here") is None
    net = Net({"pollinations": [ok("I can't help with that")], "mistral": [ok('{"reply": "ok"}')]})
    backend = CloudBackend(routes(["pollinations", "mistral"], {"mistral": "k"}), transport=net)
    assert backend.complete(REQ, timeout_s=5).text == '{"reply": "ok"}'


def test_a_service_without_json_mode_is_asked_again_without_it():
    net = Net({"mistral": [(400, {}, b'{"message": "response_format is not supported"}'), ok()]})
    backend = CloudBackend(routes(["mistral"], {"mistral": "k"}), transport=net)
    assert backend.complete(REQ, timeout_s=5).text
    assert "response_format" in net.calls[0][1] and "response_format" not in net.calls[1][1]
    assert backend.routes[0].json_mode is False


def test_one_slow_service_never_takes_the_whole_wait():
    net = Net({"pollinations": [TimeoutError()], "mistral": [ok()]})
    backend = CloudBackend(routes(["pollinations", "mistral"], {"mistral": "k"}), transport=net)
    assert backend.complete(REQ, timeout_s=6).text
    assert net.calls[0][3] == pytest.approx(3.0)  # half, leaving the rest for the next


def test_everything_down_falls_back_to_the_local_model_with_the_lean_prompt():
    local = Local()
    net = Net({"pollinations": [OSError("no route")]})
    backend = CloudBackend(routes(["pollinations"], {}), fallback=local, transport=net)
    reply = backend.complete(REQ, timeout_s=5)
    assert reply.text == '{"reply": "local"}' and backend.last_via == "local"
    assert local.calls[0][0] is REQ  # the local backend reads only what it can (it ignores ``context``)
    # Resting: straight to the local model, nothing sent to the cloud.
    backend.complete(REQ, timeout_s=5)
    assert len(net.calls) == 1 and len(local.calls) == 2


def test_everything_failing_is_an_error_with_reasons():
    net = Net({"pollinations": [(500, {}, b"{}")]})
    reply = CloudBackend(routes(["pollinations"], {}), transport=net).complete(REQ, timeout_s=5)
    assert reply.text is None and "pollinations/openai-fast" in reply.error
    timed_out = CloudBackend(routes(["pollinations"], {}), transport=Net({"pollinations": [TimeoutError()]}))
    assert timed_out.complete(REQ, timeout_s=5).error == "timeout"


def test_local_models_tools_are_reached_through_the_cloud_backend():
    backend = CloudBackend([], fallback=Local())
    assert backend.pace_s == 1.5
    assert not hasattr(CloudBackend([]), "placement")


def test_context_changes_the_key_only_when_there_is_some():
    lean = LlmRequest("phrase", "s", (("user", "u"),), {})
    assert lean.key("m") == LlmRequest("phrase", "s", (("user", "u"),), {}, context="").key("m")
    assert lean.key("m") != LlmRequest("phrase", "s", (("user", "u"),), {}, context="x").key("m")


def test_keys_live_in_the_credential_store():
    class Tokens(dict):
        def set(self, k, v):
            self[k] = v

        def delete(self, k):
            self.pop(k, None)

    store = cloud.KeyStore(Tokens())
    store.set("cerebras", " abc ")
    assert store.all() == {"cerebras": "abc"}
    store.set("cerebras", "")
    assert store.all() == {}


def test_check_reports_each_route():
    net = Net({"pollinations": [ok('{"ok": true}')], "mistral": [(401, {}, b"{}")]})
    results = cloud.check(routes(["pollinations", "mistral"], {"mistral": "k"}), transport=net)
    assert [(r.route.split("/")[0], r.ok) for r in results][:2] == [("pollinations", True), ("mistral", False)]
