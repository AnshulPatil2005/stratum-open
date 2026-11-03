"""The assertions every end-to-end test ends with.

    assert_contract(fake_github, number=7, head=HEAD, logs=logs, stderr=stderr)

1. No unhandled traceback reached the user (stderr, logs).
2. A check run exists for the head and is completed (never `in_progress`).
3. Nothing was posted outside the diff.
4. No secret appears in logs or in anything posted.
5. (Separately, `assert_idempotent`) a second run posts no duplicates.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from stratum_open.diff import parse_diff

from fake_github import FakeGitHubServer

TRACEBACK = "Traceback (most recent call last)"
SECRET_MARKERS = ("ghp_", "sk-", "-----BEGIN", "Bearer ")


def assert_no_traceback(*texts: str) -> None:
    for text in texts:
        assert TRACEBACK not in (text or ""), f"an unhandled traceback reached the user:\n{text[-2500:]}"


def assert_check_completed(fake: FakeGitHubServer, head: str, *, expect: bool = True) -> list[dict]:
    """Every check run Stratum created for `head` is `completed` with a conclusion.
    With `expect`, at least one exists."""
    runs = fake.check_runs_for(head)
    if expect:
        assert runs, f"no check run was posted for {head[:7]}"
    for run in runs:
        assert run.get("status") == "completed", f"check run left {run.get('status')!r}"
        assert run.get("conclusion") in ("success", "neutral", "failure"), run.get("conclusion")
        assert run.get("name") == "Stratum"
    return runs


def assert_inline_within_diff(fake: FakeGitHubServer, number: int, diff: str | None = None) -> None:
    files = {f.path: f for f in parse_diff(diff if diff is not None else (fake.diffs.get(number) or ""))}
    for comment in fake.inline_comments(number):
        file = files.get(comment["path"])
        assert file is not None, f"a comment was posted on {comment['path']}, which the PR doesn't change"
        line, start = comment.get("line"), comment.get("start_line") or comment.get("line")
        assert line is not None and file.can_comment(start, line), f"comment on {comment['path']}:{start}-{line} is outside the diff"


def assert_no_secrets(fake: FakeGitHubServer, *texts: str, secrets: Iterable[str] = ()) -> None:
    haystack = "\n".join([fake.posted_text(), *[t or "" for t in texts]])
    for secret in [fake.token, *secrets]:
        assert secret and secret not in haystack, f"a secret ({secret[:6]}…) leaked into logs or posted text"


def assert_contract(fake: FakeGitHubServer, *, number: int, head: str, logs: Iterable[str] = (), stderr: str = "",
                    stdout: str = "", secrets: Iterable[str] = (), check_run: bool = True) -> None:
    joined_logs = "\n".join(logs)
    assert_no_traceback(stderr, joined_logs)
    assert_check_completed(fake, head, expect=check_run)
    assert_inline_within_diff(fake, number)
    assert_no_secrets(fake, joined_logs, stderr, stdout, secrets=secrets)


def assert_idempotent(fake: FakeGitHubServer, number: int, run) -> None:
    """Running the same PR again posts no second summary and no second copy of an inline comment."""
    before_summaries = len(fake.summary_comments(number))
    before_inline = [(c["path"], c.get("line"), c["body"].split("\n", 1)[0]) for c in fake.inline_comments(number)]
    run()
    assert len(fake.summary_comments(number)) == before_summaries == 1, "a re-run posted another summary comment"
    after_inline = [(c["path"], c.get("line"), c["body"].split("\n", 1)[0]) for c in fake.inline_comments(number)]
    assert sorted(after_inline) == sorted(before_inline), "a re-run duplicated inline comments"


# ── running the CLI / Action as a process ────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_stratum(args: list[str], *, env: dict[str, str], timeout: float = 120, dev: bool = True, cwd: str | None = None) -> subprocess.CompletedProcess:
    """`python -m stratum_open.cli ARGS` with a clean environment plus `env`.
    `dev` turns on -X dev and warnings-as-errors (the requests/urllib3 version
    warning on the developer's machine is not Stratum's and is filtered)."""
    base = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "PATHEXT", "COMSPEC", "LOCALAPPDATA", "APPDATA")}
    base.update({"PYTHONPATH": str(REPO_ROOT), "PYTHONIOENCODING": os.environ.get("STRATUM_E2E_IOENCODING", "cp1252"), "NO_COLOR": "1"})
    base.update(env)
    flags = ["-X", "dev", "-W", "error", "-W", "ignore::Warning:requests", "-W", "ignore::DeprecationWarning", "-W", "ignore::ResourceWarning"] if dev else []
    return subprocess.run([sys.executable, *flags, "-m", "stratum_open.cli", *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=base, timeout=timeout, cwd=cwd or str(REPO_ROOT))
