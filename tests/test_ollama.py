"""The Ollama client against a fake server on localhost: request shape, answers, timeouts, errors."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from localtc.atc_core.llm import build_request
from localtc.atc_core.llm.understand import load_examples
from localtc.atc_core.readback import InterpretContext
from localtc.llm import OllamaBackend


@pytest.fixture
def server():
    state = {"requests": [], "delay": 0.0, "answer": '{"kind":"question","intent":"","topic":"altimeter"}', "ps": [],
             "load_ns": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/api/tags":
                self._json({"models": [{"name": "llama3.2:3b", "size": 2_000_000_000}, {"name": "qwen2.5:latest"}]})
            elif self.path == "/api/ps":
                self._json({"models": state["ps"]})
            else:
                self._json({"version": "0.9.0"})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((self.path, body))
            time.sleep(state["delay"])
            try:
                self._json({"model": body["model"], "message": {"role": "assistant", "content": state["answer"]},
                            "load_duration": state["load_ns"], "prompt_eval_count": 2100, "prompt_eval_duration": 90_000_000,
                            "eval_count": 37, "eval_duration": 900_000_000})
            except OSError:
                pass  # the client gave up

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield state
    httpd.shutdown()


def request():
    return build_request("what's the altimeter", None, InterpretContext(phase="CRUISE", station="Montreal Center"),
                         load_examples())


def test_chat_request_and_answer(server):
    backend = OllamaBackend(base_url=server["url"])
    reply = backend.complete(request(), timeout_s=5)
    assert reply.text == server["answer"] and reply.error == "" and reply.latency_ms > 0
    path, body = server["requests"][0]
    assert path == "/api/chat" and body["model"] == "llama3.2:3b" and body["stream"] is False
    assert body["format"] == request().schema  # the answer is forced into the form
    assert body["options"]["temperature"] == 0 and body["options"]["seed"] == 0
    roles = [m["role"] for m in body["messages"]]
    assert roles[0] == "system" and roles[-1] == "user" and "assistant" in roles  # few-shot turns in between
    assert body["messages"][-1]["content"] == request().prompt
    assert body["keep_alive"] == "1h"


def test_timeout(server):
    server["delay"] = 1.0
    reply = OllamaBackend(base_url=server["url"]).complete(request(), timeout_s=0.2)
    assert (reply.text, reply.error) == (None, "timeout")


def test_not_running():
    # Nothing listens on port 9. Refusal is instant on macOS/Linux; Windows can take ~2 s and time out instead.
    reply = OllamaBackend(base_url="http://127.0.0.1:9").complete(request(), timeout_s=5)
    assert reply.text is None and (reply.error == "timeout" or reply.error.startswith("error:"))
    assert OllamaBackend(base_url="http://127.0.0.1:9").status(timeout_s=5).reachable is False


def test_status(server):
    status = OllamaBackend(base_url=server["url"]).status()
    assert status.reachable and status.version == "0.9.0"
    assert status.has("llama3.2:3b") and status.has("qwen2.5") and not status.has("mistral")


def test_cpu_only_loads_nothing_on_the_graphics_card(server):
    OllamaBackend(base_url=server["url"], cpu_only=True, cpu_threads=6).complete(request(), timeout_s=5)
    OllamaBackend(base_url=server["url"]).complete(request(), timeout_s=5)
    (_, cpu), (_, auto) = server["requests"]
    assert cpu["options"]["num_gpu"] == 0 and cpu["options"]["num_thread"] == 6
    assert "num_gpu" not in auto["options"] and "num_thread" not in auto["options"]  # Ollama's own choice


def test_a_call_that_found_the_model_unloaded_says_so(server, caplog):
    backend = OllamaBackend(base_url=server["url"])
    backend.complete(request(), timeout_s=5)
    assert backend.last_stats == {"load_ms": 0, "prompt_tokens": 2100, "prompt_ms": 90, "tokens": 37, "gen_ms": 900}
    assert "wasn't loaded" not in caplog.text
    server["load_ns"] = 7_200_000_000
    backend.complete(request(), timeout_s=5)
    assert "wasn't loaded" in caplog.text and "7.2 s" in caplog.text


def test_where_the_model_is_loaded(server):
    backend = OllamaBackend(base_url=server["url"])
    assert backend.placement() is None
    server["ps"] = [{"name": "llama3.2:3b", "size": 2_550_000_000, "size_vram": 2_550_000_000, "context_length": 4096}]
    assert "all on the graphics card" in backend.placement().describe()
    server["ps"][0]["size_vram"] = 0
    assert backend.placement().describe().endswith("on the CPU")
    server["ps"][0]["size_vram"] = 1_000_000_000
    assert "split" in backend.placement().describe()


def test_kept_warm_through_the_flight_then_left_as_the_pilot_says(server):
    backend = OllamaBackend(base_url=server["url"], keep_alive="30m", cpu_only=True)
    backend.start_keeping(every_s=0.05)
    time.sleep(0.2)
    backend.stop_keeping("0")
    pings = [b for p, b in server["requests"] if p == "/api/generate" and "prompt" not in b and b["keep_alive"] == "30m"]
    assert pings and pings[0]["options"]["num_gpu"] == 0  # loads it where it belongs, if it had gone
    assert server["requests"][-1] == ("/api/generate", {"model": "llama3.2:3b", "keep_alive": 0})  # "0": unloaded now
    count = len(server["requests"])
    time.sleep(0.15)
    assert len(server["requests"]) == count  # stopped


@pytest.mark.parametrize(("setting", "in_flight"), [("0", "30m"), ("5m", "30m"), ("30m", "30m"), ("1h", "1h"), ("24h", "24h"), ("-1", "-1")])
def test_in_a_flight_the_model_stays_loaded_whatever_the_setting(setting, in_flight):
    from localtc.app import _in_flight_keep_alive

    assert _in_flight_keep_alive(setting) == in_flight
