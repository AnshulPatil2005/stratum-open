"""The parts of benchmark/others/pr_agent.py that need neither PR-Agent nor a
model: a case as a repository, PR-Agent's answers as findings, the planted
line in a prompt, the recorder in front of the model server, and the check on
the context window. tests/e2e/test_others_pr_agent.py runs the whole thing
with PR-Agent itself."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "e2e"))

from fake_model import FakeModelServer, Response  # noqa: E402

# Loaded by path: its name is the PR-Agent package's, and it isn't a package of ours.
_spec = importlib.util.spec_from_file_location("others_pr_agent", ROOT / "benchmark" / "others" / "pr_agent.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

BASE = "import os\n\n\ndef allowed(user, admins):\n    return user in admins\n"
HEAD = "import os\n\n\ndef allowed(user, admins):\n    if not user:\n        return False\n    return user in admins\n"
BUG = {"repo": "example/project", "pr": 7, "path": "src/access.py", "line": 5, "find": "    if not user:", "replace": "    if user:"}
PULL = {"title": "Refuse a missing user ", "body": "Fixes #3.\r\n\r\n# Why\nAn empty user was let in."}


def _git(folder: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(folder), "-c", "user.name=test", "-c", "user.email=test@localhost", "-c", "core.autocrlf=false",
                           "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, check=True)
    return done.stdout


@pytest.fixture
def upstream(tmp_path):
    """An upstream project with pull request 7, and that PR's diff as GitHub gives it."""
    repo = tmp_path / "upstream"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "--quiet")
    (repo / "src" / "access.py").write_bytes(BASE.encode())
    (repo / "README.md").write_bytes(b"# Project\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "start")
    (repo / "src" / "access.py").write_bytes(HEAD.encode())
    (repo / "NOTES.md").write_bytes(b"Refuses a missing user.\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "the change")
    _git(repo, "update-ref", "refs/pull/7/head", "HEAD")
    return repo, _git(repo, "diff", "HEAD~1", "HEAD")


def _blob(repo: Path, name: str) -> str:
    return runner._git(repo, "cat-file", "blob", name, text=False).decode()


def test_a_case_becomes_a_repository_with_the_bug_planted(upstream, tmp_path):
    project, diff = upstream
    repo = runner.build_repo(BUG, PULL, diff, tmp_path / "case", BUG["replace"], remote=project.as_uri())

    assert runner._git(repo, "symbolic-ref", "HEAD") == "refs/heads/pr-7"
    assert runner._git(repo, "rev-list", "--count", "base..HEAD") == "1"
    assert _blob(repo, "base:src/access.py") == BASE
    assert _blob(repo, "HEAD:src/access.py") == HEAD.replace(BUG["find"], BUG["replace"])
    assert runner._git(repo, "ls-tree", "-r", "--name-only", "base") == "README.md\nsrc/access.py"  # NOTES.md is the PR's
    assert runner._git(repo, "ls-tree", "-r", "--name-only", "HEAD") == "NOTES.md\nREADME.md\nsrc/access.py"
    # The PR's title and description, as written, are the head commit's message.
    commit = runner._git(repo, "cat-file", "commit", "HEAD", text=False).decode()
    assert commit.split("\n\n", 1)[1] == "Refuse a missing user\n\nFixes #3.\r\n\r\n# Why\nAn empty user was let in."
    # Nothing is checked out, and PR-Agent's provider needs nothing to be.
    assert sorted(path.name for path in repo.iterdir()) == [".git"]
    assert runner.lines_in(repo, "src/access.py") == 7 and runner.lines_in(repo, "missing.py") == 0


def test_the_clean_control_is_the_pr_as_merged_and_needs_no_second_fetch(upstream, tmp_path):
    project, diff = upstream
    runner.build_repo(BUG, PULL, diff, tmp_path / "case", BUG["replace"], remote=project.as_uri())
    repo = runner.build_repo(BUG, PULL, diff, tmp_path / "case", None, remote="nowhere")
    assert _blob(repo, "HEAD:src/access.py") == HEAD and _blob(repo, "base:src/access.py") == BASE


def test_a_case_that_no_longer_matches_fails_loudly(upstream, tmp_path):
    project, diff = upstream
    # The line isn't the one the case names.
    with pytest.raises(SystemExit, match="isn't '    if user is None:'"):
        runner.build_repo({**BUG, "find": "    if user is None:"}, PULL, diff, tmp_path / "a", "x", remote=project.as_uri())
    # The diff doesn't apply to the PR's head.
    with pytest.raises(SystemExit, match="git apply failed"):
        runner.build_repo(BUG, PULL, diff.replace("+    if not user:", "+    if user is None:"), tmp_path / "b", None, remote=project.as_uri())
    # The diff applies, but was made from other files than the ones fetched.
    other = re.sub(r"index [0-9a-f]+\.\.([0-9a-f]+) 100644", r"index 1234567..\1 100644", diff)
    with pytest.raises(SystemExit, match="src/access.py isn't the file the cached diff was made from"):
        runner.build_repo(BUG, PULL, other, tmp_path / "c", None, remote=project.as_uri())


def test_review_issues_become_findings_on_the_lines_pr_agent_gives():
    files = {"src/access.py": None, "README.md": None}
    data = {"review": {"security_concerns": "No", "key_issues_to_review": [
        {"relevant_file": "src/access.py\n", "issue_header": "Possible Bug\n", "issue_content": "Lets an empty\nuser in.\n", "start_line": 5, "end_line": 6},
        {"relevant_file": "`access.py`", "issue_header": "Naming", "issue_content": "Vague.", "start_line": "12", "end_line": 3},
        {"relevant_file": "src/access.py", "issue_header": "Design", "issue_content": "No line given.", "start_line": 0, "end_line": 0},
        {"relevant_file": "docs/guide.md", "issue_header": "Docs", "issue_content": "Not a file of the PR.", "start_line": 2, "end_line": 2},
        {"relevant_file": "", "issue_header": "Nowhere", "issue_content": "No file.", "start_line": 1, "end_line": 1},
        "not an issue",
    ]}}
    found = runner.findings_of("review", data, files, lambda path: 10)
    assert [(f["path"], f["start"], f["end"], f["title"], f["category"], f["severity"]) for f in found] == [
        ("src/access.py", 5, 6, "Possible Bug", "review", ""),
        ("src/access.py", 12, 12, "Naming", "review", ""),  # the path as stratum-open's own rule matches it; an end before the start is dropped
        ("src/access.py", None, None, "Design", "review", ""),
        ("docs/guide.md", 2, 2, "Docs", "review", ""),
    ]
    assert found[0]["detail"] == "Lets an empty user in."
    assert runner.findings_of("review", {"review": {"key_issues_to_review": "none"}}, files, lambda path: 10) == []
    assert runner.findings_of("review", {"review": None}, files, lambda path: 10) == []


def test_improve_suggestions_become_findings_when_pr_agent_would_list_them():
    files = {"src/access.py": None, "README.md": None}
    suggestion = {"relevant_file": "src/access.py\n", "label": "possible issue", "one_sentence_summary": "Reversed check\n",
                  "suggestion_content": "The check is reversed.\n", "existing_code": "x", "improved_code": "y"}
    data = {"code_suggestions": [
        {**suggestion, "score": 9, "relevant_lines_start": 5, "relevant_lines_end": 6},
        {**suggestion, "score": 7, "relevant_lines_start": 9, "relevant_lines_end": 11},  # past the end of a 10-line file
        {**suggestion, "score": 7},  # its second pass failed, so it has no lines
        {**suggestion, "score": 8, "relevant_lines_start": 3, "relevant_lines_end": 2},
        {**suggestion, "relevant_file": "src/other.py", "score": 8, "relevant_lines_start": 1, "relevant_lines_end": 1},  # not in the PR
        {**suggestion, "relevant_file": "README.md", "label": "typo", "score": "3", "relevant_lines_start": 1, "relevant_lines_end": 1},
        {**suggestion, "score": 7, "relevant_lines_start": 2, "relevant_lines_end": 2},
    ]}
    found = runner.findings_of("improve", data, files, lambda path: 10)
    assert [(f["path"], f["start"], f["end"], f["severity"], f["category"], f["title"]) for f in found] == [
        ("src/access.py", 5, 6, "high", "improve: possible issue", "Reversed check"),
        ("README.md", 1, 1, "low", "improve: typo", "Reversed check"),
        ("src/access.py", 2, 2, "medium", "improve: possible issue", "Reversed check"),
    ]
    assert found[0]["detail"] == "The check is reversed. (PR-Agent's score: 9 of 10)"
    assert runner.findings_of("improve", {"code_suggestions": None}, files, lambda path: 10) == []


def test_shown_means_an_added_line_of_the_planted_file():
    numbered = ("The PR code diff:\n======\n\n## File: 'src/access.py'\n\n@@ -4,2 +4,4 @@\n__new hunk__\n4  def allowed(user, admins):\n"
                "5 +    if user:\n6 +        return False\n\n## File: 'tests/test_access.py'\n\n__new hunk__\n9 +    if user:\n======\n")
    assert runner.shown(numbered, "src/access.py", 5, "    if user:")
    assert not runner.shown(numbered, "src/access.py", 9, "    if user:")  # that line is another file's
    assert not runner.shown(numbered, "src/other.py", 5, "    if user:")
    assert not runner.shown(numbered.replace("5 +    if user:", "5      if user:"), "src/access.py", 5, "    if user:")  # there, but not added
    assert not runner.shown("5 +    if user:\n", "src/access.py", 5, "    if user:")  # in no file's part
    # `improve` first shows a diff without line numbers.
    assert runner.shown("\n## File: 'src/access.py'\n@@ -4,2 +4,4 @@\n def allowed(user, admins):\n+    if user:\n", "src/access.py", 5, "    if user:")


def test_the_recorder_passes_calls_through_unchanged_and_keeps_them():
    with FakeModelServer() as fake:
        fake.queue(Response.ok("looks fine", prompt_tokens=4321, output_tokens=12))
        recorder = runner.Recorder(fake.base)
        try:
            body = {"model": "gpt-oss:20b", "prompt": "### System:\nReview this.", "options": {"temperature": 0.2}, "stream": False, "think": "low"}
            answer = requests.post(f"{recorder.url}/api/generate", json=body, timeout=10)
            missing = requests.post(f"{recorder.url}/api/show", json={"name": "gpt-oss:20b"}, timeout=10)
        finally:
            recorder.close()
        assert answer.status_code == 200 and answer.json()["response"] == "looks fine"
        assert missing.status_code == 404
        assert [call["body"] for call in fake.calls] == [body, {"name": "gpt-oss:20b"}]  # what was sent is what arrived
    first, second = recorder.calls
    assert (first["path"], first["ok"], first["error"], first["request"]) == ("/api/generate", True, "", body)
    assert (first["reply"]["prompt_eval_count"], first["reply"]["done_reason"]) == (4321, "stop")
    assert (runner.prompt_of(first), runner.answer_of(first)) == ("### System:\nReview this.", "looks fine")
    assert (second["path"], second["ok"], second["error"]) == ("/api/show", False, "the model server answered 404: not found")


def test_the_recorder_hangs_up_on_the_server_when_the_caller_gives_up():
    with FakeModelServer() as fake:
        fake.queue(Response.hang(seconds=30))
        recorder = runner.Recorder(fake.base)
        try:
            with pytest.raises(requests.Timeout):
                requests.post(f"{recorder.url}/api/generate", json={"model": "m", "prompt": "slow"}, timeout=0.5)
            deadline = time.monotonic() + 10
            while not recorder.calls and time.monotonic() < deadline:
                time.sleep(0.1)
        finally:
            recorder.close()
    [call] = recorder.calls
    assert (call["ok"], call["error"], call["reply"]) == (False, "PR-Agent stopped waiting for the answer", None)
    assert call["seconds"] < 10  # it didn't sit out the server's 30 seconds


def test_a_server_that_is_down_is_a_failed_call():
    with FakeModelServer() as fake:
        gone = fake.base
    recorder = runner.Recorder(gone)
    try:
        answer = requests.post(f"{recorder.url}/api/generate", json={"model": "m", "prompt": "hello"}, timeout=30)
    finally:
        recorder.close()
    assert answer.status_code == 502
    [call] = recorder.calls
    assert not call["ok"] and call["error"].startswith("the model server didn't answer")


def test_pr_agent_gets_its_settings_and_none_of_the_environment(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "a-token")
    monkeypatch.setenv("CONFIG__MODEL", "some-hosted-model")
    monkeypatch.setenv("GITHUB_OUTPUT", "/tmp/output")
    changed = runner.settings("gpt-oss:20b", "http://127.0.0.1:11434", 8192, "low")
    env = runner.environment(changed)
    assert not {"GITHUB_TOKEN", "GITHUB_OUTPUT"} & set(env)
    assert env["CONFIG__MODEL"] == "ollama/gpt-oss:20b" and env["OLLAMA__API_BASE"] == "http://127.0.0.1:11434"
    assert (env["CONFIG__PUBLISH_OUTPUT"], env["CONFIG__CUSTOM_MODEL_MAX_TOKENS"], env["CONFIG__FALLBACK_MODELS"]) == ("false", "8192", "[]")
    assert (env["CONFIG__REASONING_EFFORT"], json.loads(env["CONFIG__ADDITIONAL_REASONING_EFFORT_MODELS"])) == ("low", ["ollama/gpt-oss:20b"])
    # Without a thinking level to match, PR-Agent's own default stands.
    assert not [name for name in runner.settings("qwen2.5-coder:7b", "http://127.0.0.1:11434", 8192) if "REASONING" in name]


class _Ollama:
    """As much of Ollama as the window check talks to: it loads a model on an
    empty prompt and says in /api/ps what it loaded it with."""

    def __init__(self, window: int | None, thinking: bool = True):
        outer = self
        self.loaded: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def _send(self, data: dict) -> None:
                raw = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                models = [{"name": request["model"], "model": request["model"], **({"context_length": window} if window else {})} for request in outer.loaded]
                self._send({"models": models})

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/show":
                    return self._send({"capabilities": ["completion", "tools"] + ["thinking"] * thinking})
                outer.loaded.append(request)
                self._send({"model": request["model"], "response": "", "done": True, "done_reason": "load"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def test_a_run_needs_the_server_to_have_loaded_the_window_asked_for():
    with _Ollama(8192) as ollama:
        assert runner.loaded_window(ollama.url, "gpt-oss:20b", 8192) == 8192
        assert ollama.loaded == [{"model": "gpt-oss:20b", "prompt": "", "stream": False}]  # loaded as PR-Agent's calls will find it: no window named
    with _Ollama(4096) as ollama, pytest.raises(SystemExit, match="a context window of 4096, not 8192: start Ollama with OLLAMA_CONTEXT_LENGTH=8192"):
        runner.loaded_window(ollama.url, "gpt-oss:20b", 8192)
    with _Ollama(None) as ollama, pytest.raises(SystemExit, match="a context window of None, not 8192"):  # a server that doesn't say can't be trusted
        runner.loaded_window(ollama.url, "gpt-oss:20b", 8192)
    with FakeModelServer() as fake, pytest.raises(SystemExit):
        runner.loaded_window(fake.base, "gpt-oss:20b", 8192)


def test_the_thinking_level_is_only_for_a_model_that_thinks():
    with _Ollama(8192) as ollama:
        assert runner.can_think(ollama.url, "gpt-oss:20b") is True
    with _Ollama(8192, thinking=False) as ollama:
        assert runner.can_think(ollama.url, "qwen2.5-coder:7b") is False
    with FakeModelServer() as fake:
        assert runner.can_think(fake.base, "gpt-oss:20b") is None  # a server that doesn't say
