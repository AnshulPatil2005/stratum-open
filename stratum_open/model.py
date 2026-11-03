"""The model: any OpenAI-compatible chat server, local by default.

When the endpoint is Ollama, its own `/api/chat` is used instead of the
OpenAI-compatible one, because only the native API takes `num_ctx`. Without
it Ollama runs with a small default window and silently drops the start of a
long prompt, which is where the instructions are.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping

import requests
import yaml

from stratum_open.policy import Policy

RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
_FENCE_ANYWHERE = re.compile(r"```[a-zA-Z]*[ \t]*\n(.*?)\n[ \t]*```", re.DOTALL)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


@dataclass
class Reply:
    text: str
    model: str
    seconds: float
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model, "seconds": round(self.seconds, 1), "ok": self.ok, "error": self.error,
            "prompt_tokens": self.prompt_tokens, "output_tokens": self.output_tokens,
        }


class Model:
    def __init__(
        self,
        endpoint: str,
        name: str,
        *,
        api_key: str | None = None,
        context_tokens: int = 16384,
        timeout: float = 900,
        retries: int = 2,
        session: requests.Session | None = None,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.name = name
        self.api_key = api_key
        self.context_tokens = context_tokens
        self.timeout = timeout
        self.retries = retries
        self.session = session or requests.Session()
        self._ollama: str | None | bool = False  # False = not checked yet

    def complete(self, system: str, user: str, *, max_tokens: int = 2048, json_mode: bool = True) -> Reply:
        started = time.monotonic()
        try:
            base = self._ollama_base()
            if base:
                reply = self._ollama_chat(base, system, user, max_tokens, json_mode)
            else:
                reply = self._openai_chat(system, user, max_tokens, json_mode)
        except requests.RequestException as exc:
            reply = Reply("", self.name, 0, error=f"couldn't reach the model at {self.endpoint}: {type(exc).__name__}")
        except ModelError as exc:
            reply = Reply("", self.name, 0, error=str(exc))
        reply.seconds = time.monotonic() - started
        return reply

    def _ollama_base(self) -> str | None:
        if self._ollama is False:
            base = re.sub(r"/v1$", "", self.endpoint)
            try:
                response = self.session.get(f"{base}/api/version", timeout=3)
                found = response.ok and "version" in response.json()
            except (requests.RequestException, ValueError):
                found = False
            self._ollama = base if found else None
        return self._ollama or None

    def _ollama_chat(self, base: str, system: str, user: str, max_tokens: int, json_mode: bool) -> Reply:
        body: dict[str, Any] = {
            "model": self.name,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"num_ctx": self.context_tokens, "temperature": 0.1, "num_predict": max_tokens},
        }
        if json_mode:
            body["format"] = "json"
        data = self._post(f"{base}/api/chat", body)
        return Reply(
            (data.get("message") or {}).get("content", ""), data.get("model", self.name), 0,
            data.get("prompt_eval_count"), data.get("eval_count"),
        )

    def _openai_chat(self, system: str, user: str, max_tokens: int, json_mode: bool) -> Reply:
        body: dict[str, Any] = {
            "model": self.name,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            data = self._post(f"{self.endpoint}/chat/completions", body)
        except ModelError as exc:
            if not (json_mode and exc.status == 400):
                raise
            del body["response_format"]  # servers that don't support JSON mode
            data = self._post(f"{self.endpoint}/chat/completions", body)
        choices = data.get("choices") or [{}]
        usage = data.get("usage") or {}
        return Reply(
            ((choices[0].get("message") or {}).get("content") or ""), data.get("model", self.name), 0,
            usage.get("prompt_tokens"), usage.get("completion_tokens"),
        )

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for attempt in range(self.retries + 1):
            response = self.session.post(url, json=body, headers=headers, timeout=self.timeout)
            if response.status_code in RETRY_STATUSES and attempt < self.retries:
                time.sleep(min(30.0, float(response.headers.get("Retry-After") or 2 * 3**attempt)))
                continue
            if not response.ok:
                raise ModelError(f"the model server answered {response.status_code}: {response.text[:200]}", response.status_code)
            try:
                return response.json()
            except ValueError:
                raise ModelError("the model server sent something that isn't JSON", response.status_code) from None
        raise ModelError("the model server kept failing", 0)  # pragma: no cover


class ModelError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def model_from_policy(policy: Policy, env: Mapping[str, str] = os.environ) -> Model:
    """The policy names the model; the environment can point at a different
    server (the Action's own Ollama, or a GPU box) and carries the API key,
    which never goes in the policy file."""
    endpoint = env.get("STRATUM_MODEL_ENDPOINT") or policy.review.endpoint
    name = env.get("STRATUM_MODEL_NAME") or policy.model_name
    api_key = env.get("STRATUM_MODEL_API_KEY") or None
    return Model(endpoint, name, api_key=api_key, context_tokens=policy.review.context_tokens)


def repair_text(text: str) -> str:
    """Undo double-encoded UTF-8 ("lowâ€‘severity" for "low‑severity"),
    which models' replies pick up in transit, and drop U+FFFD, which marks
    bytes lost for good. ftfy only touches text that shows the signature."""
    if not text:
        return text
    try:
        import ftfy

        text = ftfy.fix_text(text)
    except ImportError:  # pragma: no cover  (a dependency; kept optional for embedders)
        pass
    return text.replace("�", "")


def parse_json(text: str) -> Any | None:
    """The first JSON object in a model's reply. Small models wrap it in
    fences (sometimes with prose around them), add a sentence after it, leave
    a trailing comma, or write near-JSON (single quotes, unquoted keys) that
    YAML still reads."""
    text = repair_text(text).strip()
    blocks = [m.group(1) for m in _FENCE_ANYWHERE.finditer(text) if "{" in m.group(1)] + [text]
    decoder = json.JSONDecoder()
    for block in blocks:
        start = block.find("{")
        if start < 0:
            continue
        body = block[start:]
        for candidate in (body, _TRAILING_COMMA.sub(r"\1", body)):
            try:
                value, _ = decoder.raw_decode(candidate)
                return value
            except ValueError:
                continue
        end = body.rfind("}")
        if end > 0:
            try:
                value = yaml.safe_load(body[: end + 1])
            except yaml.YAMLError:
                value = None
            if isinstance(value, dict):
                return value
    return None


def salvage(text: str) -> dict[str, Any] | None:
    """What can still be used from a reply that isn't one valid object,
    usually because the model ran out of tokens mid-answer: the short text
    fields, and every complete finding before the break. None if there's
    nothing."""
    text = repair_text(text or "")
    out: dict[str, Any] = {}
    for key in ("summary", "matches_description", "mismatch"):
        match = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if match:
            try:
                out[key] = json.loads(f'"{match.group(1)}"')
            except ValueError:
                out[key] = match.group(1)
    findings: list[dict[str, Any]] = []
    start = re.search(r'"findings"\s*:\s*\[', text)
    if start:
        decoder, index = json.JSONDecoder(), start.end()
        while True:
            while index < len(text) and text[index] in " \t\r\n,":
                index += 1
            if index >= len(text) or text[index] != "{":
                break
            try:
                item, index = decoder.raw_decode(text, index)
            except ValueError:
                break
            if isinstance(item, dict):
                findings.append(item)
    if not out and not findings:
        return None
    out["findings"] = findings
    return out
