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
    state = {"requests": [], "delay": 0.0, "answer": '{"kind":"question","intent":"","topic":"altimeter"}'}

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
                self._json({"models": [{"name": "llama3.2:3b"}, {"name": "qwen2.5:latest"}]})
            else:
                self._json({"version": "0.9.0"})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((self.path, body))
            time.sleep(state["delay"])
            try:
                self._json({"model": body["model"], "message": {"role": "assistant", "content": state["answer"]}})
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
    reply = OllamaBackend(base_url="http://127.0.0.1:9").complete(request(), timeout_s=1)
    assert reply.text is None and reply.error.startswith("error:")
    assert OllamaBackend(base_url="http://127.0.0.1:9").status().reachable is False


def test_status(server):
    status = OllamaBackend(base_url=server["url"]).status()
    assert status.reachable and status.version == "0.9.0"
    assert status.has("llama3.2:3b") and status.has("qwen2.5") and not status.has("mistral")
