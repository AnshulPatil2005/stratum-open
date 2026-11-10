import json as jsonlib

import pytest

from stratum_open.engine import effective_policy
from stratum_open.model import Model
from stratum_open.policy import parse_policy


class Response:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.ok = data, status, status < 400
        self.text = jsonlib.dumps(data)
        self.content = self.text.encode()

    def json(self):
        return self._data


class Recorder:
    """Records each posted body; refuses with 400 a body that holds any of `refuse`."""

    def __init__(self, reply, refuse=()):
        self.reply, self.refuse, self.bodies = reply, set(refuse), []

    def get(self, url, **kwargs):
        if url.startswith("http://localhost:11434") and url.endswith("/api/version"):
            return Response({"version": "0.40.13"})
        return Response({}, 404)

    def post(self, url, json=None, **kwargs):
        self.bodies.append(dict(json))
        if self.refuse & set(json):
            return Response({"error": "unknown field"}, 400)
        return Response(self.reply)


OLLAMA_REPLY = {"model": "gpt-oss:20b", "message": {"role": "assistant", "content": '{"findings": []}'}}
OPENAI_REPLY = {"model": "m", "choices": [{"message": {"content": '{"findings": []}'}}]}


@pytest.mark.parametrize("reasoning, think", [("default", None), ("low", "low"), ("off", False)])
def test_ollama_gets_the_think_level(reasoning, think):
    session = Recorder(OLLAMA_REPLY)
    model = Model("http://localhost:11434/v1", "gpt-oss:20b", session=session, reasoning=reasoning)
    assert model.complete("s", "u").ok
    assert session.bodies[-1].get("think", None) == think


def test_an_ollama_that_refuses_think_is_asked_again_without_it():
    session = Recorder(OLLAMA_REPLY, refuse={"think"})
    reply = Model("http://localhost:11434/v1", "qwen2.5-coder:7b", session=session, reasoning="off").complete("s", "u")
    assert reply.ok and reply.text == '{"findings": []}'
    assert ["think" in body for body in session.bodies] == [True, False]


def test_an_openai_server_gets_reasoning_effort_and_drops_what_it_refuses():
    session = Recorder(OPENAI_REPLY)
    Model("https://api.example.com/v1", "m", session=session, reasoning="low").complete("s", "u")
    assert session.bodies[-1]["reasoning_effort"] == "low"
    # A server refusing both optional fields gets one retry per field, reasoning_effort first.
    session = Recorder(OPENAI_REPLY, refuse={"reasoning_effort", "response_format"})
    reply = Model("https://api.example.com/v1", "m", session=session, reasoning="low").complete("s", "u")
    assert reply.ok
    assert [sorted(k for k in body if k in ("reasoning_effort", "response_format")) for body in session.bodies] == [
        ["reasoning_effort", "response_format"], ["response_format"], []]


def test_the_policy_sets_it_on_the_model():
    model = Model("http://localhost:11434/v1", "gpt-oss:20b", session=Recorder(OLLAMA_REPLY))
    effective_policy(parse_policy("review:\n  profile: small\n  reasoning: low\n"), model)
    assert model.reasoning == "low"
    assert parse_policy("review:\n  reasoning: lots\n").review.reasoning == "default"


def test_a_model_built_from_the_policy_has_its_reasoning_level():
    # Commands like `/stratum explain` use model_from_policy() without effective_policy().
    from stratum_open.model import model_from_policy
    model = model_from_policy(parse_policy("review:\n  reasoning: off\n"), env={})
    assert model.reasoning == "off"
