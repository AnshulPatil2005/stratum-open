"""A fake model server that speaks both Ollama's `/api/chat` and the
OpenAI-compatible `/v1/chat/completions`, scriptable per test.

    with FakeModelServer() as fake:
        fake.queue(Response.ok(json_reply(findings=[...])))
        fake.queue(Response.status(503))
        fake.queue(Response.hang(seconds=60))
        fake.queue(Response.truncated())
        model = Model(fake.url, "fake-7b", timeout=2)

Each request pops the next scripted response; when the queue is empty the
`default` response is served (a valid reply with no findings unless changed).
Every request is recorded in `fake.calls` with its path, parsed body and
time, so tests can assert on what Stratum sent as well as on what it did
with the answer.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def json_reply(findings: list[dict] | None = None, *, summary: str = "Changes the user query.",
               matches_description: str = "yes", mismatch: str = "", **extra: Any) -> str:
    """The JSON object Stratum's prompt asks for, as text."""
    data = {"summary": summary, "matches_description": matches_description, "mismatch": mismatch,
            "findings": findings if findings is not None else [], **extra}
    return json.dumps(data)


def finding(file: str, code: str, *, line: int | None = None, title: str = "Unknown id",
            detail: str = "fetchone returns None for an unknown id and callers index it.",
            severity: str = "high", category: str = "bug", fix: str = "Check for None.", **extra: Any) -> dict:
    data = {"file": file, "code": code, "severity": severity, "category": category, "title": title, "detail": detail, "fix": fix}
    if line is not None:
        data["line"] = line
        data.setdefault("end_line", line)
    data.update(extra)
    return data


@dataclass
class Response:
    kind: str  # ok | status | hang | truncated | raw
    text: str = ""
    status: int = 200
    body: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0
    content_type: str = "application/json"
    prompt_tokens: int = 1200
    output_tokens: int = 80

    @classmethod
    def ok(cls, text: str, **kwargs: Any) -> Response:
        """A normal reply whose assistant message is `text`."""
        return cls("ok", text=text, **kwargs)

    @classmethod
    def status(cls, code: int, body: str | bytes | dict | None = None, headers: dict[str, str] | None = None) -> Response:
        """An HTTP error. `body` defaults to the JSON error Ollama/OpenAI send."""
        if body is None:
            body = {"error": {"message": f"fake model server error {code}", "type": "server_error"}}
        if isinstance(body, dict):
            body = json.dumps(body)
        raw = body.encode("utf-8") if isinstance(body, str) else body
        return cls("status", status=code, body=raw, headers=headers or {})

    @classmethod
    def hang(cls, seconds: float = 60) -> Response:
        """No bytes for `seconds` (the connection stays open), then a normal reply."""
        return cls("hang", seconds=seconds, text=json_reply())

    @classmethod
    def truncated(cls, text: str | None = None) -> Response:
        """Headers promise a full JSON body; the connection closes half way."""
        return cls("truncated", text=text if text is not None else json_reply())

    truncated_stream = truncated

    @classmethod
    def raw(cls, body: str | bytes, status: int = 200, content_type: str = "application/json") -> Response:
        """Exactly these bytes with this status: HTML, an empty body, `null`, a list."""
        raw = body.encode("utf-8") if isinstance(body, str) else body
        return cls("raw", status=status, body=raw, content_type=content_type)

    @classmethod
    def html(cls, status: int = 200) -> Response:
        return cls.raw("<html><body><h1>502 Bad Gateway</h1><p>nginx</p></body></html>", status=status, content_type="text/html")

    @classmethod
    def empty(cls, status: int = 200) -> Response:
        return cls.raw(b"", status=status)


class FakeModelServer:
    def __init__(self, *, ollama: bool = True, host: str = "127.0.0.1"):
        self.ollama = ollama  # answer /api/version, so Stratum takes the Ollama path
        self.default = Response.ok(json_reply())
        self._queue: list[Response] = []
        self._lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []
        self.stopping = threading.Event()
        self._server = ThreadingHTTPServer((host, 0), _handler(self))
        self._server.daemon_threads = True
        self._server.block_on_close = False  # a keep-alive connection from requests.Session must not block stop()
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> FakeModelServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self.stopping.set()
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> FakeModelServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def url(self) -> str:
        """The endpoint as a policy would name it: Ollama's OpenAI-compatible one."""
        return f"{self.base}/v1"

    # ── scripting ────────────────────────────────────────────────────────

    def queue(self, *responses: Response) -> FakeModelServer:
        with self._lock:
            self._queue.extend(responses)
        return self

    def reset(self) -> None:
        with self._lock:
            self._queue.clear()
            self.calls.clear()

    def _next(self) -> Response:
        with self._lock:
            return self._queue.pop(0) if self._queue else self.default

    @property
    def chat_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["path"] in ("/api/chat", "/v1/chat/completions")]

    def prompts(self) -> list[tuple[str, str]]:
        """(system, user) of every chat call, in order."""
        out = []
        for call in self.chat_calls:
            messages = (call.get("body") or {}).get("messages") or []
            system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
            user = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
            out.append((system, user))
        return out


def _handler(fake: FakeModelServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "fake-model/1"

        def log_message(self, format: str, *args: Any) -> None:  # quiet
            pass

        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            fake.calls.append({"method": "GET", "path": path, "body": None, "time": time.monotonic()})
            if path == "/api/version" and fake.ollama:
                return self._json(200, {"version": "0.9.9-fake"})
            if path == "/api/tags" and fake.ollama:
                return self._json(200, {"models": [{"name": "fake-7b", "model": "fake-7b"}]})
            if path in ("/", "/v1/models"):
                return self._json(200, {"object": "list", "data": [{"id": "fake-7b", "object": "model"}]})
            return self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            path = self.path.split("?")[0]
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else None
            except ValueError:
                body = {"_unparsed": raw[:200].decode("utf-8", "replace")}
            fake.calls.append({"method": "POST", "path": path, "body": body, "time": time.monotonic(),
                               "headers": {k: v for k, v in self.headers.items()}})
            if path not in ("/api/chat", "/v1/chat/completions"):
                return self._json(404, {"error": "not found"})
            response = fake._next()
            if response.kind == "hang":
                fake.stopping.wait(response.seconds)
                if fake.stopping.is_set():
                    return
            if response.kind in ("ok", "hang"):
                return self._json(200, self._shape(path, body, response))
            if response.kind == "status":
                return self._bytes(response.status, response.body or b"", "application/json", response.headers)
            if response.kind == "raw":
                return self._bytes(response.status, response.body or b"", response.content_type, response.headers)
            if response.kind == "truncated":
                full = json.dumps(self._shape(path, body, response)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(full)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(full[: max(1, len(full) // 2)])
                self.wfile.flush()
                self.close_connection = True
                return
            raise AssertionError(f"unknown response kind {response.kind}")

        def _shape(self, path: str, body: dict | None, response: Response) -> dict[str, Any]:
            model = (body or {}).get("model") or "fake-7b"
            if path == "/api/chat":
                return {
                    "model": model, "created_at": "2026-01-01T00:00:00Z", "done": True, "done_reason": "stop",
                    "message": {"role": "assistant", "content": response.text},
                    "prompt_eval_count": response.prompt_tokens, "eval_count": response.output_tokens,
                    "total_duration": 1_000_000_000,
                }
            return {
                "id": "chatcmpl-fake", "object": "chat.completion", "created": 1_700_000_000, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": response.text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": response.prompt_tokens, "completion_tokens": response.output_tokens,
                          "total_tokens": response.prompt_tokens + response.output_tokens},
            }

        def _json(self, status: int, data: Any) -> None:
            self._bytes(status, json.dumps(data).encode("utf-8"), "application/json")

        def _bytes(self, status: int, data: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if data:
                self.wfile.write(data)

    return Handler
