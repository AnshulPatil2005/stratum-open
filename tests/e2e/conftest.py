"""Fixtures for the end-to-end suite: a fake model server and a fake GitHub
API per test, a `live` marker that skips (never fails) when no real model
is reachable, and helpers that build a `Context` against the fakes."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import requests

from stratum_open.github.api import GitHub
from stratum_open.model import Model
from stratum_open.policy import parse_policy
from stratum_open.runner import Context

from fake_github import FakeGitHubServer
from fake_model import FakeModelServer

E2E_DIR = Path(__file__).parent
DEFAULT_ENDPOINT = "http://localhost:11434/v1"


def live_endpoint() -> str:
    return os.environ.get("STRATUM_MODEL_ENDPOINT") or DEFAULT_ENDPOINT


def live_model_name() -> str:
    return os.environ.get("STRATUM_MODEL_NAME") or "qwen2.5-coder:7b"


_reachable: bool | None = None


def model_reachable() -> bool:
    """Whether a real model server answers at STRATUM_MODEL_ENDPOINT (or Ollama's default)."""
    global _reachable
    if _reachable is None:
        base = re.sub(r"/v1/?$", "", live_endpoint())
        try:
            _reachable = requests.get(f"{base}/api/version", timeout=2).status_code < 500
        except requests.RequestException:
            try:
                _reachable = requests.get(base, timeout=2).status_code < 500
            except requests.RequestException:
                _reachable = False
    return _reachable


def pytest_collection_modifyitems(config, items):
    for item in items:
        if E2E_DIR in Path(str(item.fspath)).parents or Path(str(item.fspath)).parent == E2E_DIR:
            item.add_marker(pytest.mark.e2e)
        if "live" in item.keywords and not model_reachable():
            item.add_marker(pytest.mark.skip(reason=f"no model server reachable at {live_endpoint()} (set STRATUM_MODEL_ENDPOINT)"))


# ── servers ──────────────────────────────────────────────────────────────


@pytest.fixture
def fake_model():
    with FakeModelServer() as server:
        yield server


@pytest.fixture
def fake_openai_model():
    """A server that only speaks the OpenAI-compatible API (no /api/version)."""
    with FakeModelServer(ollama=False) as server:
        yield server


@pytest.fixture
def fake_github():
    with FakeGitHubServer() as server:
        yield server


# ── clients and contexts ─────────────────────────────────────────────────


@pytest.fixture
def gh(fake_github):
    return GitHub(fake_github.token, fake_github.repo, api=fake_github.url)


@pytest.fixture
def logs():
    """Lines Stratum logged through `Context.log`, for asserting on messages and grepping for secrets."""
    return []


@pytest.fixture
def make_model(fake_model):
    def _make(*, timeout: float = 5, retries: int = 1, name: str = "fake-7b", api_key: str | None = None, context_tokens: int = 16384, endpoint: str | None = None) -> Model:
        return Model(endpoint or fake_model.url, name, api_key=api_key, timeout=timeout, retries=retries, context_tokens=context_tokens)
    return _make


@pytest.fixture
def make_context(fake_github, make_model, logs):
    """A Context against the fakes. `policy_text` is the .stratum/policy.yml text (None = no file)."""
    def _make(*, policy_text: str | None = "triage:\n  enabled: true\n", model: Model | None | str = "default",
              checkout: str | None = None, **kwargs) -> Context:
        if model == "default":
            model = make_model()
        return Context(
            gh=GitHub(fake_github.token, fake_github.repo, api=fake_github.url),
            policy=parse_policy(policy_text), model=model, bot=fake_github.bot, checkout=checkout,
            log=logs.append, **kwargs,
        )
    return _make


# ── shared diffs ─────────────────────────────────────────────────────────

DIFF = """diff --git a/app/users.py b/app/users.py
--- a/app/users.py
+++ b/app/users.py
@@ -1,4 +1,7 @@
 import sqlite3
+import requests

 def get_user(conn, user_id):
-    cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
+    cur = conn.execute(f"SELECT * FROM users WHERE id = {user_id}")
+    return cur.fetchone()
+    print("unreachable")
"""

SQL_LINE = 'cur = conn.execute(f"SELECT * FROM users WHERE id = {user_id}")'


@pytest.fixture
def diff():
    return DIFF


def head_of(number: int) -> str:
    """The head SHA `FakeGitHubServer.add_pull` gives a PR by default."""
    return "a" * 40 if number == 7 else f"{number:040x}"
