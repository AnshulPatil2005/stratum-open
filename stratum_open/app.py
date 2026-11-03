"""The GitHub App: one small webhook server the maintainers run, for projects
that would rather install an App than add a workflow to each repo.

It's the only mode that stores anything (findings, 👍/👎 votes, a short job
log; see DATA.md), and the only one that keeps a clone of each repo's default
branch between runs. Run it with `stratum serve`, or any WSGI server pointed
at `stratum_open.app:application`.

Settings, all from the environment:
  STRATUM_APP_ID, STRATUM_APP_PRIVATE_KEY (or STRATUM_APP_PRIVATE_KEY_FILE),
  STRATUM_WEBHOOK_SECRET, STRATUM_DB (default stratum.db), STRATUM_WORKDIR
  (default ./repos), STRATUM_WORKERS (default 2), and the model's
  STRATUM_MODEL_ENDPOINT / STRATUM_MODEL_NAME / STRATUM_MODEL_API_KEY.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import requests

from stratum_open.checks.dependencies import PackageLookup
from stratum_open.github.api import API, GitHub, GitHubError
from stratum_open.github.commands import handle
from stratum_open.github.feedback import collect_votes
from stratum_open.model import model_from_policy
from stratum_open.runner import Context, checkout_ref, load_context_policy, review_pull
from stratum_open.storage import SQLiteStorage, Storage

MAX_BODY = 25 * 1024 * 1024  # GitHub's own webhook payload limit
DELIVERY_TTL = 3600.0  # seconds a delivery ID is remembered
MAX_DELIVERIES = 5000
REVIEW_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}
JOBS_DAYS = 14
PURGE_EVERY = 24 * 3600
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


class AppAuth:
    """App JWTs and short-lived installation tokens."""

    def __init__(self, app_id: str, private_key: str, *, api: str = API, session: requests.Session | None = None):
        self.app_id, self.private_key, self.api = app_id, private_key, api.rstrip("/")
        self.session = session or requests.Session()
        self._tokens: dict[int, tuple[str, float]] = {}
        self._login: str | None = None
        self._lock = threading.Lock()

    def jwt(self) -> str:
        import jwt  # PyJWT, the `app` extra

        now = int(time.time())
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self.app_id}, self.private_key, algorithm="RS256")

    def _app_get(self, path: str) -> Any:
        response = self.session.get(f"{self.api}{path}", headers=self._headers(), timeout=30)
        response.raise_for_status()
        return response.json()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.jwt()}", "Accept": "application/vnd.github+json", "User-Agent": "stratum-open"}

    def token(self, installation_id: int) -> str:
        with self._lock:
            cached = self._tokens.get(installation_id)
            if cached and cached[1] - time.time() > 300:
                return cached[0]
            response = self.session.post(f"{self.api}/app/installations/{installation_id}/access_tokens", headers=self._headers(), timeout=30)
            response.raise_for_status()
            data = response.json()
            expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).timestamp()
            self._tokens[installation_id] = (data["token"], expires)
            return data["token"]

    def bot_login(self) -> str:
        if self._login is None:
            self._login = f"{self._app_get('/app')['slug']}[bot]"
        return self._login

    def installations(self) -> list[dict]:
        return self._app_get("/app/installations?per_page=100")


class Server:
    def __init__(
        self, auth: AppAuth, secret: str, storage: Storage, workdir: str, *,
        env: Mapping[str, str] = os.environ, workers: int = 2, log: Callable[[str], None] = print,
    ):
        self.auth, self.secret, self.storage, self.workdir, self.env, self.log = auth, secret.encode(), storage, workdir, env, log
        self._queue: queue.Queue[tuple] = queue.Queue()
        self._pending: dict[tuple, tuple[str, dict]] = {}
        self._busy: set[tuple] = set()
        self._repo_locks: dict[str, threading.Lock] = {}
        self._deliveries: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()
        os.makedirs(workdir, exist_ok=True)
        for _ in range(workers):
            threading.Thread(target=self._work, daemon=True).start()
        threading.Thread(target=self._purge_loop, daemon=True).start()

    # ── WSGI ─────────────────────────────────────────────────────────────

    def __call__(self, environ: dict, start_response) -> list[bytes]:
        path, method = environ.get("PATH_INFO", ""), environ.get("REQUEST_METHOD", "")
        if path == "/healthz":
            return _respond(start_response, "200 OK", {"ok": True})
        if path != "/webhook" or method != "POST":
            return _respond(start_response, "404 Not Found", {"error": "not found"})
        length = int(environ.get("CONTENT_LENGTH") or 0)
        if length <= 0 or length > MAX_BODY:
            return _respond(start_response, "413 Payload Too Large", {"error": "bad size"})
        body = environ["wsgi.input"].read(length)
        if not self.verify(body, environ.get("HTTP_X_HUB_SIGNATURE_256", "")):
            return _respond(start_response, "401 Unauthorized", {"error": "bad signature"})
        try:
            payload = json.loads(body)
        except ValueError:
            return _respond(start_response, "400 Bad Request", {"error": "not JSON"})
        if self.seen(environ.get("HTTP_X_GITHUB_DELIVERY", "")):
            return _respond(start_response, "202 Accepted", {"queued": False, "duplicate": True})
        accepted = self.dispatch(environ.get("HTTP_X_GITHUB_EVENT", ""), payload)
        return _respond(start_response, "202 Accepted", {"queued": accepted})

    def verify(self, body: bytes, signature: str) -> bool:
        expected = "sha256=" + hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        return bool(self.secret) and hmac.compare_digest(expected, signature)

    def seen(self, delivery: str) -> bool:
        """Whether this delivery came in the last hour. GitHub redelivers a
        webhook it thinks timed out, and each repeat would start another
        review (or answer a command twice). Checked after the signature, so
        a forged ID can't hide a real delivery."""
        if not delivery:
            return False
        now = time.monotonic()
        with self._lock:
            while self._deliveries and now - next(iter(self._deliveries.values())) > DELIVERY_TTL:
                self._deliveries.popitem(last=False)
            if delivery in self._deliveries:
                return True
            self._deliveries[delivery] = now
            if len(self._deliveries) > MAX_DELIVERIES:
                self._deliveries.popitem(last=False)
            return False

    # ── events ───────────────────────────────────────────────────────────

    def dispatch(self, event: str, payload: dict) -> bool:
        installation = (payload.get("installation") or {}).get("id")
        if event == "installation" and payload.get("action") == "deleted":
            for repo in payload.get("repositories") or []:
                self.forget(repo["full_name"])
            return False
        if event == "installation_repositories" and payload.get("action") == "removed":
            for repo in payload.get("repositories_removed") or []:
                self.forget(repo["full_name"])
            return False
        repo = (payload.get("repository") or {}).get("full_name")
        if not installation or not repo:
            return False
        if event == "pull_request" and payload.get("action") in REVIEW_ACTIONS and not payload["pull_request"].get("draft"):
            self.enqueue((repo, payload["pull_request"]["number"], "review"), event, payload)
            return True
        if event in ("issue_comment", "pull_request_review_comment") and payload.get("action") == "created":
            if "@stratum" in (payload.get("comment") or {}).get("body", "").lower():
                number = (payload.get("issue") or payload.get("pull_request") or {}).get("number")
                self.enqueue((repo, number, "comment", payload["comment"]["id"]), event, payload)
                return True
        return False

    def forget(self, repo: str) -> None:
        """Uninstalled: every stored row and the clone go at once."""
        deleted = self.storage.delete_repo(repo)
        shutil.rmtree(self._clone_dir(repo), ignore_errors=True)
        self.log(f"{repo}: uninstalled; deleted {deleted} stored row(s) and its clone")

    def enqueue(self, key: tuple, event: str, payload: dict) -> None:
        """A newer push replaces a review still waiting for the same PR."""
        with self._lock:
            queued = key in self._pending
            self._pending[key] = (event, payload)
            if not queued:
                self._queue.put(key)

    def _work(self) -> None:
        while True:
            key = self._queue.get()
            with self._lock:
                if (key[0], key[1]) in {(k[0], k[1]) for k in self._busy}:
                    self._queue.put(key)  # one job per PR at a time
                    retry = True
                else:
                    retry = False
                    event, payload = self._pending.pop(key)
                    self._busy.add(key)
            if retry:
                time.sleep(1)
                continue
            try:
                self.run(event, payload)
            except Exception as exc:
                self.log(f"{key[0]}#{key[1]}: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}")
                self.storage.record_job(key[0], key[1], event, "error", f"{type(exc).__name__}: {exc}"[:500])
            finally:
                with self._lock:
                    self._busy.discard(key)

    def run(self, event: str, payload: dict) -> None:
        repo = payload["repository"]["full_name"]
        ctx = self.context(payload["installation"]["id"], repo, payload["repository"].get("default_branch") or "main")
        if event == "pull_request":
            number = payload["pull_request"]["number"]
            try:
                collect_votes(ctx.gh, number, ctx.bot, self.storage)
            except GitHubError as exc:
                self.log(f"{repo}#{number}: couldn't read reactions: {exc}")
            outcome = review_pull(ctx, number)
            self.storage.record_job(repo, number, "review", outcome.status, outcome.note)
        else:
            number = (payload.get("issue") or payload.get("pull_request") or {}).get("number")
            done = handle(ctx, event, payload)
            self.storage.record_job(repo, number, "command", done or "none")
        self.storage.purge(findings_days=ctx.policy.data.findings_retention_days, jobs_days=JOBS_DAYS, repo=repo)

    def context(self, installation_id: int, repo: str, default_branch: str) -> Context:
        token = self.auth.token(installation_id)
        gh = GitHub(token, repo, api=self.auth.api)
        policy = load_context_policy(gh, default_branch)
        checkout = self.checkout(repo, token, default_branch, policy.track_record.window_days)
        return Context(
            gh=gh, policy=policy, model=model_from_policy(policy, self.env), bot=self.auth.bot_login(),
            storage=self.storage, checkout=checkout, history_ref=checkout_ref(checkout, default_branch) if checkout else "HEAD",
            lookup=PackageLookup(), feedback=True, log=self.log,
        )

    # ── the clone of each default branch ─────────────────────────────────

    def _clone_dir(self, repo: str) -> str:
        return os.path.join(self.workdir, _SAFE_NAME.sub("_", repo.replace("/", "__")))

    def checkout(self, repo: str, token: str, branch: str, window_days: int) -> str | None:
        """Clone or update the default branch with the window's history. The
        token goes in a header for this one command, never into the clone's
        config or URL."""
        path = self._clone_dir(repo)
        header = "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
        since = (datetime.now(timezone.utc) - timedelta(days=window_days + 1)).strftime("%Y-%m-%d")
        url = f"https://github.com/{repo}.git"
        lock = self._repo_locks.setdefault(repo, threading.Lock())
        with lock:
            try:
                if os.path.isdir(os.path.join(path, ".git")):
                    _git(["-c", f"http.extraHeader={header}", "-C", path, "fetch", "--quiet", f"--shallow-since={since}", "origin", branch])
                    _git(["-C", path, "checkout", "--quiet", "-B", branch, "FETCH_HEAD"])
                    _git(["-C", path, "update-ref", f"refs/remotes/origin/{branch}", "FETCH_HEAD"])
                else:
                    _git(["-c", f"http.extraHeader={header}", "clone", "--quiet", "--single-branch", "--branch", branch, f"--shallow-since={since}", url, path])
                return path
            except subprocess.CalledProcessError as exc:
                self.log(f"{repo}: couldn't update the clone ({(exc.stderr or '').strip()[:200]}); reviewing without history")
                return None

    # ── retention ────────────────────────────────────────────────────────

    def _purge_loop(self) -> None:
        while True:
            time.sleep(60)
            try:
                self.purge_all()
            except Exception as exc:
                self.log(f"purge: {type(exc).__name__}: {exc}")
            time.sleep(PURGE_EVERY)

    def purge_all(self) -> None:
        """Every repo's rows past its own retention, even repos with no PRs
        lately, so the data page's promises hold without any activity."""
        for installation in self.auth.installations():
            token = self.auth.token(installation["id"])
            response = requests.get(f"{self.auth.api}/installation/repositories?per_page=100", timeout=30,
                                    headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
            response.raise_for_status()
            for repo in response.json().get("repositories", []):
                gh = GitHub(token, repo["full_name"], api=self.auth.api)
                policy = load_context_policy(gh, repo.get("default_branch") or "main")
                self.storage.purge(findings_days=policy.data.findings_retention_days, jobs_days=JOBS_DAYS, repo=repo["full_name"])


def _git(args: list[str]) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True, text=True, timeout=600)


def _respond(start_response, status: str, body: dict) -> list[bytes]:
    data = json.dumps(body).encode()
    start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(data)))])
    return [data]


def from_env(env: Mapping[str, str] = os.environ) -> Server:
    missing = [k for k in ("STRATUM_APP_ID", "STRATUM_WEBHOOK_SECRET") if not env.get(k)]
    key = env.get("STRATUM_APP_PRIVATE_KEY")
    if not key and env.get("STRATUM_APP_PRIVATE_KEY_FILE"):
        with open(env["STRATUM_APP_PRIVATE_KEY_FILE"], encoding="utf-8") as handle_file:
            key = handle_file.read()
    if not key:
        missing.append("STRATUM_APP_PRIVATE_KEY")
    if missing:
        raise SystemExit("Missing settings: " + ", ".join(missing) + ". See the README's GitHub App section.")
    return Server(
        AppAuth(env["STRATUM_APP_ID"], key, api=env.get("STRATUM_GITHUB_API", API)),
        env["STRATUM_WEBHOOK_SECRET"],
        SQLiteStorage(env.get("STRATUM_DB", "stratum.db")),
        env.get("STRATUM_WORKDIR", "repos"),
        env=env,
        workers=int(env.get("STRATUM_WORKERS", "2")),
    )


def application(environ: dict, start_response):  # for gunicorn and friends
    global _server
    if _server is None:
        _server = from_env()
    return _server(environ, start_response)


_server: Server | None = None
