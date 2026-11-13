"""PR-Agent on the planted-bug benchmark, for a head-to-head with the same model.

    python benchmark/others/pr_agent.py --bugs benchmark/planted/bugs.json \\
        --model gpt-oss:20b@http://localhost:11434 --pr-agent <the Python that has PR-Agent> \\
        --out benchmark/results/<name>/raw/pr-agent-planted [--clean] [--set dev] [--shard 1/8]
    python benchmark/planted.py score benchmark/results/<name>

Each case `planted.py run` reviews with stratum-open is reviewed here with
PR-Agent's `review` and `improve` commands. reviews.csv and findings.csv get
the columns `planted.py run` writes, so `planted.py score` scores both tools
with one rule. README.md in this folder says how PR-Agent is set up, why, and
what the comparison doesn't show.

For every case:
1. The upstream project is fetched at the PR's head and made into a local
   repository: the PR's base on one branch, its head on another, with the
   planted line swapped in unless --clean.
2. PR-Agent's command line reviews that branch with its `local` provider.
   Nothing is published; its findings are read from the JSON it writes.
3. Its calls to the model go through a recorder that passes them on unchanged
   and keeps them. That is how `shown`, the calls and their time are known.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import select
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402
import yaml  # noqa: E402

from bench import CACHE, _fetch, _write  # noqa: E402
from planted import FINDING_COLUMNS, REVIEW_COLUMNS, near, original, plant  # noqa: E402
from stratum_open.diff import parse_diff  # noqa: E402
from stratum_open.review import _match_path  # noqa: E402

VERSION = "0.50.0"  # the PR-Agent release the settings and the output shapes below belong to
WRAPPER = Path(__file__).resolve().parent / "pr_agent_local.py"
REMOTE = "https://github.com/{repo}.git"
TOOLS = ("review", "improve")  # what PR-Agent's GitHub Action runs on a new PR, less `describe`, which reports no defects
TIMEOUT = 1800  # seconds for one model call: twice stratum-open's 900 (PR-Agent's default, 120, is for hosted models)
MODEL_PATHS = ("/api/generate", "/api/chat")
CALL_COLUMNS = ["case", "version", "tool", "seconds", "ok", "prompt_tokens", "output_tokens", "done", "shown", "error"]
REASONING = {"low": "low", "off": "none"}  # stratum-open's review.reasoning, in PR-Agent's words
# All of this process's environment PR-Agent's gets: enough to start Python and git.
KEEP = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LANG")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_ERROR = re.compile(r"\|\s*ERROR\s*\|.*? - (.*)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bugs", required=True)
    parser.add_argument("--model", required=True, help="name@endpoint of the Ollama server, as planted.py takes it")
    parser.add_argument("--out", required=True)
    parser.add_argument("--pr-agent", default=os.environ.get("PR_AGENT_PYTHON"),
                        help="the Python that has PR-Agent installed, in an environment of its own (default: $PR_AGENT_PYTHON)")
    parser.add_argument("--shard", default="1/1", help="k/n: run every n-th case, starting at the k-th")
    parser.add_argument("--context-tokens", type=int, default=8192, help="the window the server must have loaded the model with")
    parser.add_argument("--set", choices=("dev", "test", "all"), default="all")
    parser.add_argument("--clean", action="store_true", help="review the PRs as merged, without the bug: the control")
    parser.add_argument("--policy", help="the stratum-open policy file of the run this is compared with; only its "
                                         "review.reasoning is read, so both tools ask the model to think as much")
    parser.add_argument("--tools", default=",".join(TOOLS), help="PR-Agent commands to run, separated by commas")
    parser.add_argument("--trace", help="a folder to keep every prompt, reply and PR-Agent log in")
    parser.add_argument("--work", default=str(CACHE / "pr-agent"), help="where the cases' repositories are built and kept")
    parser.add_argument("--unverified-window", action="store_true",
                        help="don't ask the server which context window it loaded the model with: for a fake server only")
    args = parser.parse_args()

    if not args.pr_agent:
        raise SystemExit("Name the Python that has PR-Agent installed: --pr-agent, or PR_AGENT_PYTHON.")
    found = versions(args.pr_agent)
    name, _, endpoint = args.model.partition("@")
    server = re.sub(r"/v1/?$", "", (endpoint or "http://localhost:11434").rstrip("/"))
    tools = tuple(tool for tool in args.tools.split(",") if tool)
    if not tools or set(tools) - set(TOOLS):
        raise SystemExit(f"--tools takes {' and '.join(TOOLS)}")
    policy = (yaml.safe_load(Path(args.policy).read_text(encoding="utf-8")) or {}) if args.policy else {}
    wanted = (policy.get("review") or {}).get("reasoning")
    reasoning = REASONING.get(str(wanted), "")
    if reasoning and can_think(server, name) is False:
        reasoning = ""  # LiteLLM would send `think` and Ollama refuse the call; stratum-open drops it for such a model as well
    window = None if args.unverified_window else loaded_window(server, name, args.context_tokens)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    trace = Path(args.trace) if args.trace else None
    if trace:
        trace.mkdir(parents=True, exist_ok=True)
    k, n = (int(part) for part in args.shard.split("/"))
    bugs = [b for b in json.loads(Path(args.bugs).read_text(encoding="utf-8"))
            if args.set in ("all", b.get("set")) and not b.get("invalid")][k - 1::n]
    version = "clean" if args.clean else "planted"
    variant = f"pr-agent-{found['pr-agent']}" + ("" if tools == TOOLS else "-" + "+".join(tools)) + (f"-reasoning-{wanted}" if reasoning else "")

    recorder = Recorder(server)
    changed = settings(name, recorder.url, args.context_tokens, reasoning)
    record = {"versions": found, "model": name, "server": server, "tools": list(tools), "context_tokens": args.context_tokens,
              "window_loaded": window, "settings": {**changed, "OLLAMA__API_BASE": server, "OLLAMA_API_BASE": server}}
    (out / "pr_agent.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    reviews, findings, calls = [], [], []
    for bug in bugs:
        case = f"{bug['repo']}#{bug['pr']}"
        slug = f"{bug['repo'].replace('/', '__')}__{bug['pr']}"
        pull, text = _fetch(bug["repo"], int(bug["pr"]))
        files = parse_diff(text)
        planted = original(bug, files) if args.clean else plant(bug, files)
        repo = build_repo(bug, pull, text, Path(args.work) / slug, None if args.clean else planted.text)
        by_path = {file.path: file for file in files}
        raised, errors, made, failed = [], [], [], 0
        for tool in tools:
            start = len(recorder.calls)
            data, error, log = run_tool(tool, args.pr_agent, repo, environment(changed))
            mine = [call for call in recorder.calls[start:] if call["path"] in MODEL_PATHS]
            for index, call in enumerate(mine):
                call["shown"] = shown(prompt_of(call), bug["path"], bug["line"], planted.text)
                reply = call["reply"] or {}
                calls.append({"case": case, "version": version, "tool": tool, "seconds": round(call["seconds"], 1), "ok": "yes" if call["ok"] else "",
                              "prompt_tokens": reply.get("prompt_eval_count", ""), "output_tokens": reply.get("eval_count", ""),
                              "done": reply.get("done_reason", ""), "shown": "yes" if call["shown"] else "no", "error": call["error"]})
                if trace:
                    (trace / f"{slug}-{version}-{tool}-{index + 1}.txt").write_text(
                        prompt_of(call) + "\n\n===== the model's reply =====\n" + answer_of(call), encoding="utf-8")
            if trace:
                (trace / f"{slug}-{version}-{tool}.log").write_text(log, encoding="utf-8")
            made += mine
            # As planted.py counts them: a call fails when the server fails it, or when its answer can't be used.
            failed += sum(not call["ok"] for call in mine) + bool(error and mine and all(call["ok"] for call in mine))
            if error:
                errors.append(f"{tool}: {error}")
            if data is not None:
                raised += findings_of(tool, data, by_path, lambda path: lines_in(repo, path))
        hits = [f for f in raised if near(SimpleNamespace(path=f["path"], line_start=f["start"], line_end=f["end"]), bug)]
        seconds = sum(call["seconds"] for call in made)
        reviews.append({
            "case": case, "set": bug.get("set", ""), "kind": bug["kind"], "path": bug["path"], "line": bug["line"], "model": name,
            "variant": variant, "version": version, "seconds": round(seconds, 1),
            "shown": "yes" if any(call["shown"] for call in made) else "no", "calls": len(made), "failed_calls": failed,
            "findings": len(raised), "near": len(hits), "error": "; ".join(errors)[:300],
        })
        for f in raised:
            findings.append({
                "case": case, "model": name, "variant": variant, "version": version, "severity": f["severity"], "category": f["category"],
                "path": f["path"], "line": f["start"] or "", "near": "yes" if f in hits else "", "title": f["title"], "detail": f["detail"],
                "verdict": "", "note": "",
            })
        print(f"{case} {name} {variant} {version}: {len(raised)} finding(s), {len(hits)} near the bug, {len(made)} call(s), {seconds:.0f}s"
              + (f", error: {reviews[-1]['error']}" if errors else ""), flush=True)
        _write(out / "reviews.csv", REVIEW_COLUMNS, reviews)
        _write(out / "findings.csv", FINDING_COLUMNS, findings)
        _write(out / "calls.csv", CALL_COLUMNS, calls)
    recorder.close()
    if window is not None:
        loaded_window(server, name, args.context_tokens, after=True)
    return 0


# ── PR-Agent ─────────────────────────────────────────────────────────────


def settings(model: str, server: str, context_tokens: int, reasoning: str = "") -> dict[str, str]:
    """Every PR-Agent setting a run changes from its defaults, as the
    environment variables PR-Agent reads. README.md gives each one's reason."""
    changed = {
        "CONFIG__GIT_PROVIDER": "local-pr",  # its `local` provider, told the PR's title and description (pr_agent_local.py)
        "CONFIG__PUBLISH_OUTPUT": "false",
        "CONFIG__MODEL": f"ollama/{model}",
        "CONFIG__FALLBACK_MODELS": "[]",
        "CONFIG__CUSTOM_MODEL_MAX_TOKENS": str(context_tokens),
        "CONFIG__DUPLICATE_PROMPT_EXAMPLES": "true",
        "OLLAMA__API_BASE": server,
        "OLLAMA_API_BASE": server,  # LiteLLM's own name for it, which its lookups about the model read
        "CONFIG__AI_TIMEOUT": str(TIMEOUT),
        "CONFIG__REPO_CONTEXT_FILES": "[]",
        "PR_REVIEWER__ENABLE_LARGE_PR_CHUNKING": "true",
        "PR_CODE_SUGGESTIONS__PARALLEL_CALLS": "false",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
    }
    if reasoning:
        changed["CONFIG__REASONING_EFFORT"] = reasoning
        # LiteLLM doesn't list Ollama's models as thinking ones, so PR-Agent sends the effort only to a model named here.
        changed["CONFIG__ADDITIONAL_REASONING_EFFORT_MODELS"] = json.dumps([f"ollama/{model}"])
    return changed


def environment(changed: dict[str, str]) -> dict[str, str]:
    """What PR-Agent's process is started with: its settings, and only as
    much of this process's environment as Python and git need. No token
    reaches it, so it can't write anywhere, and nothing a CI job sets changes
    what it does."""
    env = {name: os.environ[name] for name in KEEP if name in os.environ}
    env.update(changed)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "NO_PROXY": "127.0.0.1,localhost", "GIT_TERMINAL_PROMPT": "0"})
    return env


def versions(python: str) -> dict[str, str]:
    code = ("import json, sys; from importlib.metadata import version; "
            "print(json.dumps({'pr-agent': version('pr-agent'), 'litellm': version('litellm'), 'python': sys.version.split()[0]}))")
    try:
        done = subprocess.run([python, "-c", code], capture_output=True, text=True)
    except OSError as exc:
        raise SystemExit(f"Couldn't start {python}: {exc}") from None
    if done.returncode:
        raise SystemExit(f"{python} has no PR-Agent: pip install pr-agent=={VERSION} in an environment of its own.\n{done.stderr.strip()[-300:]}")
    found = json.loads(done.stdout)
    if found["pr-agent"] != VERSION:
        raise SystemExit(f"{python} has PR-Agent {found['pr-agent']}; the settings here and the README are for {VERSION}.")
    return found


def run_tool(tool: str, python: str, repo: Path, env: dict[str, str]) -> tuple[dict | None, str, str]:
    """One PR-Agent command on the repository's checked-out branch, against
    `base`: its JSON result (None if it gave none), why not, and its log."""
    result = repo / f"{tool}.json"
    result.unlink(missing_ok=True)
    done = subprocess.run([python, str(WRAPPER), "--pr_url", "base", "--json-output", str(result), tool],
                          cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    log = _ANSI.sub("", done.stdout + done.stderr)
    try:
        data = json.loads(result.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        return data, "", log
    # Why not: the last error PR-Agent logged, or the end of what it printed if it died before logging one.
    said = _ERROR.findall(log) or [line for line in log.split("\n") if line.strip()][-1:]
    return None, " ".join(said[-1].split()) if said else f"no result (exit {done.returncode})", log


def findings_of(tool: str, data: dict, by_path: dict, lines_in: Callable[[str], int]) -> list[dict]:
    """What PR-Agent's JSON reports, one entry per issue that names a file:
    the key issues of `review`, and the suggestions of `improve` that its
    comment would list (it drops one whose file isn't in the PR or whose
    lines aren't in the file). Lines are PR-Agent's own; an issue without a
    usable one keeps its file and can't be near the bug."""
    out = []
    if tool == "review":
        review = data.get("review")
        issues = review.get("key_issues_to_review") if isinstance(review, dict) else None
        for issue in issues if isinstance(issues, list) else []:
            if not isinstance(issue, dict) or not _text(issue.get("relevant_file")):
                continue
            start, end = _line(issue.get("start_line")), _line(issue.get("end_line"))
            start = start if start and start > 0 else None
            out.append({"path": _match_path(_text(issue["relevant_file"]), by_path) or _text(issue["relevant_file"]),
                        "start": start, "end": (end if end and end >= start else start) if start else None,
                        "title": _text(issue.get("issue_header")), "detail": _text(issue.get("issue_content")),
                        "severity": "", "category": "review"})
        return out
    suggestions = data.get("code_suggestions")
    for suggestion in suggestions if isinstance(suggestions, list) else []:
        if not isinstance(suggestion, dict):
            continue
        path = _match_path(_text(suggestion.get("relevant_file")), by_path)
        start, end = _line(suggestion.get("relevant_lines_start")), _line(suggestion.get("relevant_lines_end"))
        if path is None or start is None or end is None or start < 1 or end < start or end > lines_in(path):
            continue
        score = _line(suggestion.get("score"))
        out.append({"path": path, "start": start, "end": end, "title": _text(suggestion.get("one_sentence_summary")),
                    "detail": _text(suggestion.get("suggestion_content")) + (f" (PR-Agent's score: {score} of 10)" if score is not None else ""),
                    # Its own impact label: high from 9, medium from 7 (new_score_mechanism_th_high and _medium).
                    "severity": "" if score is None else "high" if score >= 9 else "medium" if score >= 7 else "low",
                    "category": f"improve: {_text(suggestion.get('label'))}"})
    return out


def _text(value) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _line(value) -> int | None:
    """A line number or a score as PR-Agent reads one: a whole number, or text that is one."""
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def shown(prompt: str, path: str, number: int, text: str) -> bool:
    """Whether the planted line is in a prompt as an added line of its file:
    numbered, as PR-Agent shows a diff to `review` (`641 +code`), or bare, as
    it first shows it to `improve` (`+code`)."""
    wanted = {f"{number} +{text}".rstrip(), f"+{text}".rstrip()}
    return any(
        section.startswith(f"'{path}'") and any(line.rstrip() in wanted for line in section.split("\n"))
        for section in prompt.split("\n## File: ")[1:]
    )


def prompt_of(call: dict) -> str:
    request = call["request"] if isinstance(call["request"], dict) else {}
    if isinstance(request.get("prompt"), str):
        return request["prompt"]
    return "\n\n".join(str(m.get("content") or "") for m in request.get("messages") or [] if isinstance(m, dict))


def answer_of(call: dict) -> str:
    """The model's answer to a call, after its thinking where the server sent that too."""
    reply = call["reply"] if isinstance(call["reply"], dict) else {}
    message = reply.get("message") if isinstance(reply.get("message"), dict) else {}
    thinking = reply.get("thinking") or message.get("thinking")
    answer = str(reply.get("response") or message.get("content") or call["error"] or "")
    return f"(thinking) {thinking}\n(answer) {answer}" if thinking else answer


# ── the case as a repository ─────────────────────────────────────────────


def build_repo(bug: dict, pull: dict, diff: str, folder: Path, line: str | None, remote: str = REMOTE) -> Path:
    """The upstream project as a repository PR-Agent's `local` provider
    reviews: branch `base` is the PR's base, and the checked-out branch
    `pr-<number>` one commit on it, the PR's head, with `line` in place of
    the line the bug is planted on (None leaves the PR as merged). The
    commit's message is the PR's title and description.

    Only the PR's head is fetched. The base is that tree with the PR's diff
    taken back out: the diff stratum-open reviews, so both tools see one
    change. Nothing is checked out, because the provider reads commits and
    never the working tree, and a bare repository is always clean to it."""
    number = int(bug["pr"])
    if not (folder / ".git").exists():
        folder.mkdir(parents=True, exist_ok=True)
        _git(folder, "init", "--quiet", "--bare", ".git")
    if _git(folder, "rev-parse", "--quiet", "--verify", "refs/upstream/head", check=False) is None:
        _git(folder, "fetch", "--quiet", "--depth", "1", remote.format(repo=bug["repo"]), f"refs/pull/{number}/head")
        _git(folder, "update-ref", "refs/upstream/head", "FETCH_HEAD")

    _git(folder, "read-tree", "refs/upstream/head")
    _git(folder, "apply", "--cached", "-R", "--whitespace=nowarn", stdin=diff.encode("utf-8"))
    _same_blobs(folder, diff, f"{bug['repo']}#{number}")
    base = _git(folder, "commit-tree", _git(folder, "write-tree"), "-m", "The pull request's base")

    _git(folder, "read-tree", "refs/upstream/head")
    if line is not None:
        mode, blob, _ = _git(folder, "ls-files", "--stage", "--", bug["path"]).split(None, 2)
        lines = _git(folder, "cat-file", "blob", blob, text=False).decode("utf-8").split("\n")
        if len(lines) < bug["line"] or lines[bug["line"] - 1] != bug["find"]:
            raise SystemExit(f"{bug['repo']}#{number} {bug['path']}:{bug['line']} isn't {bug['find']!r} at the PR's head")
        lines[bug["line"] - 1] = line
        blob = _git(folder, "hash-object", "-w", "--no-filters", "--stdin", stdin="\n".join(lines).encode("utf-8"))
        _git(folder, "update-index", "--cacheinfo", f"{mode},{blob},{bug['path']}")
    message = f"{(pull.get('title') or '').strip()}\n\n{pull.get('body') or ''}"
    head = _git(folder, "commit-tree", _git(folder, "write-tree"), "-p", base, "-F", "-", stdin=message.encode("utf-8"))

    _git(folder, "update-ref", "refs/heads/base", base)
    _git(folder, "update-ref", f"refs/heads/pr-{number}", head)
    _git(folder, "symbolic-ref", "HEAD", f"refs/heads/pr-{number}")
    return folder


def _same_blobs(folder: Path, diff: str, case: str) -> None:
    """Fail unless the base just rebuilt in the index and the fetched head
    hold the very files the diff was made from: its `index old..new` lines
    name them by hash. A PR whose head moved since it was cached fails here."""
    old = new = None
    header = False  # between a file's `diff --git` line and its first hunk, where no line is code
    for raw in diff.split("\n"):
        ids = re.match(r"index ([0-9a-f]+)\.\.([0-9a-f]+)", raw)
        if raw.startswith("diff --git "):
            old, new, header = None, None, True
        elif raw.startswith("@@"):
            header = False
        elif header and ids:
            old, new = ids.groups()
        elif header and raw.startswith(("--- a/", "+++ b/")) and old is not None:
            path = raw[6:].rstrip("\t")
            if raw.startswith("--- "):
                have = (_git(folder, "ls-files", "--stage", "--", path).split() + ["", ""])[1]
            else:
                have = _git(folder, "rev-parse", "--quiet", "--verify", f"refs/upstream/head:{path}", check=False) or ""
            want = old if raw.startswith("--- ") else new
            if not have.startswith(want):
                raise SystemExit(f"{case}: {path} isn't the file the cached diff was made from ({have[:12] or 'missing'}, not {want})")


def lines_in(repo: Path, path: str) -> int:
    """The length of a file at the PR's head, counted as PR-Agent counts it."""
    text = _git(repo, "cat-file", "blob", f"HEAD:{path}", text=False, check=False)
    return 0 if text is None else text.count(b"\n") + (not text.endswith(b"\n"))


def _git(folder: Path, *args: str, stdin: bytes | None = None, text: bool = True, check: bool = True):
    """git's output, stripped; None for a failure that `check=False` allows."""
    # A fixed author and date, so a case's commits are the same on every machine.
    who = {f"GIT_{role}_{field}": value for role in ("AUTHOR", "COMMITTER")
           for field, value in (("NAME", "planted-bug benchmark"), ("EMAIL", "benchmark@localhost"), ("DATE", "2000-01-01T00:00:00Z"))}
    done = subprocess.run(["git", "-C", str(folder), *args], input=stdin, capture_output=True, env={**os.environ, **who, "GIT_TERMINAL_PROMPT": "0"})
    if done.returncode:
        if not check:
            return None
        raise SystemExit(f"git {args[0]} failed in {folder}: {done.stderr.decode('utf-8', 'replace').strip()[:400]}")
    return done.stdout.decode("utf-8").strip() if text else done.stdout


# ── the model server ─────────────────────────────────────────────────────


def loaded_window(server: str, model: str, wanted: int, after: bool = False) -> int:
    """The context window Ollama has loaded the model with, which must be the
    one asked for. PR-Agent's requests don't set a window (`num_ctx`), so the
    server's own default decides, and Ollama's is smaller than 8,192 unless
    it was started with OLLAMA_CONTEXT_LENGTH. An empty prompt loads the
    model; /api/ps then says what it was loaded with."""
    try:
        requests.post(f"{server}/api/generate", json={"model": model, "prompt": "", "stream": False}, timeout=900).raise_for_status()
        running = requests.get(f"{server}/api/ps", timeout=30).json().get("models")
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise SystemExit(f"Couldn't load {model} on {server} and read its context window: {exc}") from None
    names = (model, model if ":" in model else f"{model}:latest")
    window = next((m.get("context_length") for m in running or [] if isinstance(m, dict) and (m.get("name") in names or m.get("model") in names)), None)
    if window != wanted:
        raise SystemExit(f"{server} has {model} loaded with a context window of {window}, not {wanted}: "
                         f"start Ollama with OLLAMA_CONTEXT_LENGTH={wanted}, as PR-Agent's documentation says to."
                         + (" It changed during the run, so these results don't count." if after else ""))
    return window


def can_think(server: str, model: str) -> bool | None:
    """Whether Ollama lists the model as a thinking one; None if it doesn't say."""
    try:
        abilities = requests.post(f"{server}/api/show", json={"model": model}, timeout=30).json().get("capabilities")
    except (requests.RequestException, ValueError, AttributeError):
        return None
    return "thinking" in abilities if isinstance(abilities, list) else None


class Recorder:
    """A pass-through in front of the model server that keeps every exchange.
    PR-Agent's requests reach the server exactly as it sent them; this only
    reads them, to know what the model was shown, how often and for how
    long."""

    def __init__(self, upstream: str):
        self.upstream = urlsplit(upstream)
        self.calls: list[dict] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), type("Handler", (_PassThrough,), {"recorder": self}))
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class _PassThrough(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    recorder: Recorder

    def log_message(self, format: str, *args) -> None:
        pass

    def do_GET(self) -> None:
        self._forward()

    def do_POST(self) -> None:
        self._forward()

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        upstream = self.recorder.upstream
        connection = http.client.HTTPConnection(upstream.hostname, upstream.port or 80)
        got: dict = {}

        def ask() -> None:
            try:
                connection.request(self.command, self.path, body=body or None,
                                   headers={k: v for k, v in self.headers.items() if k.lower() in ("content-type", "accept", "authorization")})
                response = connection.getresponse()
                got.update(status=response.status, type=response.getheader("Content-Type") or "application/json", body=response.read())
            except (OSError, http.client.HTTPException) as exc:
                got.setdefault("error", f"the model server didn't answer: {type(exc).__name__}")

        started = time.monotonic()
        asking = threading.Thread(target=ask, daemon=True)
        asking.start()
        while asking.is_alive():
            asking.join(0.5)
            if asking.is_alive() and self._hung_up():
                # Hang up on the server too, so it stops generating for nobody, as it would if PR-Agent had called it directly.
                got["error"] = "PR-Agent stopped waiting for the answer"
                try:
                    connection.sock.shutdown(socket.SHUT_RDWR)
                except (OSError, AttributeError):
                    pass
                break
        asking.join(5)
        connection.close()
        failed = "error" in got
        if not failed:
            try:
                self.send_response(got["status"])
                self.send_header("Content-Type", got["type"])
                self.send_header("Content-Length", str(len(got["body"])))
                self.end_headers()
                self.wfile.write(got["body"])
            except OSError:
                pass
        else:
            self.close_connection = True
            if got["error"].startswith("the model server"):
                self.send_error(502, got["error"])
        reply = None if failed else _json(got["body"])
        said = reply.get("error") if isinstance(reply, dict) and isinstance(reply.get("error"), str) else ""
        refused = "" if failed or got["status"] == 200 else f"the model server answered {got['status']}" + (f": {' '.join(said.split())[:160]}" if said else "")
        self.recorder.calls.append({"path": self.path.split("?")[0], "request": _json(body), "reply": reply, "seconds": time.monotonic() - started,
                                    "error": got.get("error") or refused, "ok": not failed and got["status"] == 200})

    def _hung_up(self) -> bool:
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            return bool(readable) and self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True


def _json(raw: bytes):
    try:
        return json.loads(raw)
    except ValueError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
