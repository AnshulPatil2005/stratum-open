"""`stratum`: review a PR or local changes, see a track record, check a
policy file, sort the PR queue, manage the App's data, run the App.

The CLI keeps nothing: results go to the terminal or a JSON file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from stratum_open import __version__
from stratum_open.diff import parse_diff
from stratum_open.findings import Finding, anchor_to_diff, assign_fingerprints, dedupe, rank
from stratum_open.model import model_from_policy
from stratum_open.policy import POLICY_PATH, Policy, parse_policy
from stratum_open.track_record import author_identity, build_records

_PR = re.compile(r"(?:https://github\.com/)?([\w.-]+/[\w.-]+)(?:/pull/|#)(\d+)/?$")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stratum", description="PR review and triage on a model you run yourself.")
    parser.add_argument("--version", action="version", version=f"stratum-open {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    review = sub.add_parser("review", help="review a GitHub PR, or your local changes")
    review.add_argument("target", nargs="?", help="a PR: https://github.com/owner/repo/pull/12 or owner/repo#12")
    review.add_argument("--local", metavar="BASE", nargs="?", const="origin/HEAD", help="review this checkout's changes since BASE (default origin/HEAD)")
    review.add_argument("--post", action="store_true", help="publish the review on the PR (default: print it only)")
    review.add_argument("--full", action="store_true", help="review the whole PR, not only what changed since the last review")
    review.add_argument("--checkout", help="a checkout of the PR's default branch, for history-based notes")
    review.add_argument("--no-model", action="store_true", help="run the checks only")
    review.add_argument("--json", metavar="FILE", help="also write the findings to FILE as JSON")

    record = sub.add_parser("record", help="a contributor's track record, from git history")
    record.add_argument("login", help="a GitHub login or a commit email")
    record.add_argument("--repo", default=".", help="the repo's checkout (default: here)")
    record.add_argument("--ref", default="HEAD", help="the default branch (default: HEAD)")

    policy = sub.add_parser("policy", help="check a policy file")
    policy.add_argument("action", choices=["check"])
    policy.add_argument("file", nargs="?", default=POLICY_PATH)

    queue = sub.add_parser("triage", help="the sorted queue of a repo's open PRs")
    queue.add_argument("repo", help="owner/name")
    queue.add_argument("--post", action="store_true", help="update the queue issue on GitHub")

    data = sub.add_parser("data", help="see or delete what the GitHub App stored")
    data.add_argument("action", choices=["delete", "purge", "export"])
    data.add_argument("--db", default=os.environ.get("STRATUM_DB", "stratum.db"))
    data.add_argument("--repo", help="owner/name")
    data.add_argument("--pr", type=int, help="with delete: one PR's findings and job log")
    data.add_argument("--feedback", action="store_true", help="with delete: 👍/👎 votes and muted rules")
    data.add_argument("--all", action="store_true", help="with delete --repo: everything stored for the repo")
    data.add_argument("--days", type=int, default=180, help="with purge: findings older than this go (jobs: 14 days)")

    serve = sub.add_parser("serve", help="run the GitHub App's webhook server")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))

    sub.add_parser("action", help="the GitHub Action's entry point")
    sub.add_parser("needs-model", help="the GitHub Action: print whether this event can call the model")

    args = parser.parse_args(argv)
    return {
        "review": _review, "record": _record, "policy": _policy, "triage": _triage,
        "data": _data, "serve": _serve, "action": _action, "needs-model": _needs_model,
    }[args.command](args)


# ── review ───────────────────────────────────────────────────────────────


def _review(args) -> int:
    if args.local is not None or not args.target:
        return _review_local(args, args.local or "origin/HEAD")
    match = _PR.match(args.target)
    if not match:
        sys.exit(f"Not a PR: {args.target}. Use https://github.com/owner/repo/pull/12 or owner/repo#12.")
    from stratum_open.checks.dependencies import PackageLookup
    from stratum_open.github.api import GitHub
    from stratum_open.runner import Context, checkout_ref, load_context_policy, review_pull

    gh = GitHub(_token(), match.group(1))
    default_branch = gh.repository()["default_branch"]
    policy = load_context_policy(gh, default_branch)
    _policy_notes(policy)
    bot = os.environ.get("STRATUM_BOT_LOGIN") or (gh.get("/user").get("login") if args.post else "")
    ctx = Context(
        gh=gh, policy=policy, model=None if args.no_model else model_from_policy(policy), bot=bot or "-",
        checkout=args.checkout, history_ref=checkout_ref(args.checkout, default_branch) if args.checkout else "HEAD",
        lookup=PackageLookup(), log=lambda line: print(line, file=sys.stderr),
    )
    outcome = review_pull(ctx, int(match.group(2)), full=args.full, post=args.post)
    if outcome.status == "dry-run":
        print(re.sub(r"<!--.*?-->", "", outcome.summary, flags=re.DOTALL).strip())
        print()
        _print_findings(outcome.findings)
    else:
        print(f"PR #{outcome.number}: {outcome.status}" + (f" ({outcome.note})" if outcome.note else ""))
    _write_json(args.json, outcome.findings)
    return 0


def _review_local(args, base: str) -> int:
    """Review `git diff BASE...HEAD` here, with the policy as it is on BASE."""
    from stratum_open.checks import run_checks
    from stratum_open.checks.dependencies import PackageLookup
    from stratum_open.repo.boundaries import boundary_findings
    from stratum_open.review import review

    try:
        text = _git(".", "diff", "--no-color", "--find-renames", f"{base}...HEAD")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"git couldn't compare {base} with HEAD: {(exc.stderr or '').strip() or 'is this a git repo, and does that branch exist?'}")
    files = parse_diff(text)
    if not files:
        print(f"No changes between {base} and HEAD.")
        return 0
    policy = parse_policy(_git_or_none(".", "show", f"{base}:{POLICY_PATH}"))
    _policy_notes(policy)
    findings = run_checks(files, policy, lookup=PackageLookup() if policy.dependencies.registry_lookup else None)
    findings += boundary_findings(files, policy, ".")
    if not args.no_model:
        subject = _git(".", "log", "-1", "--format=%s").strip()
        body = _git(".", "log", "-1", "--format=%b").strip()
        result = review(title=subject, description=body, files=files, policy=policy, model=model_from_policy(policy))
        if not result.ran:
            print("The model didn't answer: " + "; ".join(sorted({c['error'] for c in result.calls if c.get('error')})), file=sys.stderr)
        elif result.summary:
            print(f"What this changes: {result.summary}\n")
        findings += result.findings
    findings = anchor_to_diff(findings, files)
    assign_fingerprints(findings, files)
    findings = rank(dedupe(findings))
    _print_findings(findings)
    _write_json(args.json, findings)
    return 1 if any(f.severity == "critical" for f in findings) else 0


def _print_findings(findings: list[Finding]) -> None:
    if not findings:
        print("No findings.")
        return
    for f in findings:
        where = f"{f.path}:{f.line_start}" if f.line_start else (f.path or "(the PR)")
        print(f"{where}  {f.severity.upper()} {f.category}  {f.title}  [{f.rule}]")
        print(f"    {' '.join(f.detail.split())}")
        if f.suggestion:
            print(f"    Fix: {f.suggestion}")
    print(f"\n{len(findings)} finding(s).")


def _write_json(path: str | None, findings: list[Finding]) -> None:
    if path:
        Path(path).write_text(json.dumps([f.to_dict() for f in findings], indent=2), encoding="utf-8")


# ── the rest ─────────────────────────────────────────────────────────────


def _record(args) -> int:
    records = build_records(args.repo, args.ref)
    record = records.get(author_identity(args.login))
    if record is None:
        print(f"No merged PRs by {args.login} in the last 180 days of {args.ref}. Records match a login only through "
              "GitHub's noreply emails; try the commit email too.")
        return 0
    print(f"{record.author}: {record.summary()}")
    for strike in record.strikes:
        how = "reverted" if strike.kind == "revert" else "fixed by a follow-up"
        print(f"  #{strike.pr.number} {strike.pr.title[:70]}: {how} in {strike.commit[:7]} ({strike.subject[:60]})")
    return 0


def _policy(args) -> int:
    path = Path(args.file)
    policy = parse_policy(path.read_text(encoding="utf-8") if path.exists() else None)
    if not path.exists():
        print(f"No {path}: the defaults apply.")
    for error in policy.errors:
        print(f"error: {error}")
    for warning in policy.warnings:
        print(f"warning: {warning}")
    if not policy.errors:
        print(f"{path} is usable." if path.exists() else "")
    return 1 if policy.errors else 0


def _triage(args) -> int:
    from stratum_open.github.api import GitHub
    from stratum_open.runner import Context, load_context_policy
    from stratum_open.triage_queue import update_queue

    gh = GitHub(_token(), args.repo)
    policy = load_context_policy(gh, gh.repository()["default_branch"])
    bot = os.environ.get("STRATUM_BOT_LOGIN") or "github-actions[bot]"
    print(update_queue(Context(gh=gh, policy=policy, model=None, bot=bot), post=args.post))
    return 0


def _data(args) -> int:
    from stratum_open.storage import SQLiteStorage

    if not Path(args.db).exists():
        sys.exit(f"No database at {args.db}. Only the GitHub App stores data; the Action and the CLI keep nothing.")
    storage = SQLiteStorage(args.db)
    if args.action == "purge":
        print(json.dumps(storage.purge(findings_days=args.days, repo=args.repo)))
    elif args.action == "export":
        if not args.repo:
            sys.exit("export needs --repo owner/name")
        print(json.dumps(storage.export(args.repo), indent=2, default=str))
    elif args.pr is not None:
        if not args.repo:
            sys.exit("delete --pr needs --repo owner/name")
        print(f"Deleted {storage.delete_pr(args.repo, args.pr)} row(s) for {args.repo}#{args.pr}.")
    elif args.feedback:
        print(f"Deleted {storage.delete_feedback(args.repo)} vote and mute row(s).")
    elif args.all and args.repo:
        print(f"Deleted {storage.delete_repo(args.repo)} row(s) for {args.repo}.")
    else:
        sys.exit("Say what to delete: --repo R --pr N, --feedback [--repo R], or --repo R --all.")
    return 0


def _serve(args) -> int:
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIServer, make_server

    from stratum_open.app import from_env

    class Threaded(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    server = make_server(args.host, args.port, from_env(), server_class=Threaded)
    print(f"Stratum App listening on http://{args.host}:{args.port}/webhook")
    server.serve_forever()
    return 0


def _action(args) -> int:
    from stratum_open.action import main as action_main

    return action_main()


def _needs_model(args) -> int:
    from stratum_open.action import needs_model

    print("true" if needs_model() else "false")
    return 0


def _policy_notes(policy: Policy) -> None:
    for error in policy.errors:
        print(f"policy: {error} Using the defaults.", file=sys.stderr)
    for warning in policy.warnings:
        print(f"policy: {warning}", file=sys.stderr)


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        try:
            token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            token = ""
    if not token:
        sys.exit("Set GITHUB_TOKEN (or sign in with the GitHub CLI) to read PRs.")
    return token


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout


def _git_or_none(repo: str, *args: str) -> str | None:
    try:
        return _git(repo, *args)
    except subprocess.CalledProcessError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
