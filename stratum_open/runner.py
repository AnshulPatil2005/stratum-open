"""One PR, start to finish: read it, check it, review it, sort it, publish.

The Action, the App and the CLI all call `review_pull`; they differ only in
the `Context` they pass (where the token comes from, whether anything is
stored, whether there's a checkout of the default branch to read history
from).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Callable

from stratum_open.diff import FileDiff, parse_diff
from stratum_open.findings import Finding, anchor_to_diff, assign_fingerprints, dedupe, rank, risk_score
from stratum_open.github import publish
from stratum_open.github.api import GitHub, GitHubError
from stratum_open.model import Model
from stratum_open.policy import Policy, load_policy
from stratum_open.review import ReviewResult, _locate, review, shown
from stratum_open.storage import NullStorage, Storage
from stratum_open.track_record import AuthorRecord, author_identity, build_records
from stratum_open.triage import PRFacts, Triage, triage

MAX_BANDIT_FILES = 20
MAX_CONTEXT_FILES = 20


@dataclass
class Context:
    gh: GitHub
    policy: Policy
    model: Model | None
    bot: str  # the login Stratum posts as
    storage: Storage = field(default_factory=NullStorage)
    checkout: str | None = None  # a trusted checkout of the default branch, with history
    history_ref: str = "HEAD"  # the default branch inside `checkout`
    lookup: object | None = None  # registry lookups for the dependency check
    feedback: bool = False  # 👍/👎 counted (the App, which can store them)
    own_checks: tuple[str, ...] = ()  # check runs of Stratum's own job, which aren't CI
    log: Callable[[str], None] = print


@dataclass
class Outcome:
    number: int
    status: str  # published | dry-run | skipped | superseded
    findings: list[Finding] = field(default_factory=list)
    triage: Triage | None = None
    review: ReviewResult | None = None
    summary: str = ""
    note: str = ""


def load_context_policy(gh: GitHub, default_branch: str) -> Policy:
    """Rules come from the default branch only, never from the PR."""
    return load_policy(lambda path: gh.file_text(path, default_branch))


def review_pull(ctx: Context, number: int, *, full: bool = False, post: bool = True) -> Outcome:
    gh, policy = ctx.gh, ctx.policy
    pull = gh.pull(number)
    if pull.get("state") != "open":
        return Outcome(number, "skipped", note="the PR isn't open")
    head = pull["head"]["sha"]
    files = parse_diff(gh.pull_diff(number))
    paths = [f.path for f in files]
    comments = gh.issue_comments(number)
    existing = publish.find_summary(comments, ctx.bot)
    state = publish.State.decode(existing["body"]) if existing else publish.State()
    problems: list[str] = []

    # What the model reads this time: everything, or only what changed since
    # the head it last reviewed.
    changed: set[str] | None = None
    if state.head and not full:
        if state.head == head:
            changed = set()
        else:
            since = gh.compare_files(state.head, head)
            changed = set(since) & set(paths) if since is not None else None
    incremental = changed is not None

    checks = _checks(ctx, files, head)
    notes, hotspots, boundary = _history(ctx, files, pull)
    record = _record(ctx, pull)

    result: ReviewResult | None = None
    if ctx.model is not None and (changed is None or changed):
        result = review(
            title=pull.get("title") or "", description=pull.get("body") or "", files=files, policy=policy,
            model=ctx.model, only_paths=changed, context=notes, hotspots=hotspots,
            log=(lambda prompt, reply: ctx.storage.record_job(gh.repo, number, "prompt", "logged", prompt_log=f"{prompt}\n\n---\n\n{reply}")),
        )
        if not result.ran:
            errors = sorted({c["error"] for c in result.calls if c.get("error")})
            problems.append("The model didn't answer, so this review has only the checks" + (f": {errors[0]}" if errors else "."))
        elif not result.complete:
            problems.append(f"{result.failed_calls} of {len(result.calls)} model calls failed; some files weren't reviewed.")
    elif ctx.model is None:
        problems.append("No model is configured, so this review has only the checks.")

    model_findings = result.findings if result else []
    findings = anchor_to_diff(checks + boundary + model_findings, files)
    assign_fingerprints(findings, files)
    findings = dedupe(findings)

    # A model finding stays until the code it's on changes; the model not
    # repeating it on the next push doesn't make it fixed.
    reviewed_now = set(result.packed.reviewed + result.packed.clipped) if result and result.packed and result.ran else set()
    carried, gone = _carry(state.findings, files, {f.fingerprint for f in findings}, reviewed_now)
    for finding in [f for f in carried if not shown(f, policy)]:  # the policy's bar may have risen since
        carried.remove(finding)
        gone[finding.fingerprint] = f"Hidden: this project shows the model's findings from {policy.review.min_severity} severity up."
    findings = dedupe(findings + carried)

    muted = sorted(ctx.storage.muted_rules(gh.repo)) if ctx.feedback else []
    ignored = set(state.ignored)
    for f in findings:
        if f.fingerprint in ignored:
            gone.setdefault(f.fingerprint, "Ignored on this PR.")
    findings = rank([f for f in findings if f.fingerprint not in ignored and f.rule not in state.ignored_rules and f.rule not in muted])

    facts = PRFacts.from_pull(pull, paths)
    try:
        facts.ci_ok = gh.ci_state(head, ignore={publish.CHECK_NAME, *ctx.own_checks})
        facts.linked_issues = gh.linked_issues(number)
    except GitHubError as exc:
        problems.append(f"Couldn't read CI status or linked issues ({exc.status}).")
    sorted_pr = triage(
        facts, findings, policy,
        matches_description=result.matches_description if result else "", mismatch=result.mismatch if result else "",
        record=record,
    )

    coverage = _coverage(result, incremental, changed, state.head, files)
    summary = publish.Summary(
        findings=findings, inline=set(), policy=policy, model_name=ctx.model.name if ctx.model else None,
        model_summary=(result.summary if result and result.ran else ""),
        matches_description=result.matches_description if result else "", mismatch=result.mismatch if result else "",
        triage=sorted_pr, notes=notes, coverage=coverage, problems=problems, muted=muted,
    )
    new_state = publish.State(
        head=head,
        findings=[publish.state_finding(f, _snippet(f, files)) for f in findings if f.source == "model"],
        ignored=state.ignored, ignored_rules=state.ignored_rules,
        triage={**sorted_pr.as_dict(), "additions": facts.additions, "deletions": facts.deletions},
    )
    if not post:
        return Outcome(number, "dry-run", findings, sorted_pr, result, publish.render_summary(summary, new_state))

    # Don't publish a review of a commit that's no longer the PR's head.
    if gh.pull(number)["head"]["sha"] != head:
        return Outcome(number, "superseded", findings, sorted_pr, result, note="a newer commit arrived")

    threads = gh.review_threads(number)
    current = {f.fingerprint for f in findings}
    for thread in threads:  # checks run on every push, so a check's finding that's gone is fixed
        marker = publish.thread_marker(thread, ctx.bot)
        if marker and not marker[1].startswith("model:") and marker[0] not in current:
            gone.setdefault(marker[0], "Fixed: the check no longer finds this.")
    publish.resolve_threads(gh, number, threads, gone, bot=ctx.bot, log=ctx.log)
    on_lines = {publish.thread_fingerprint(t, ctx.bot) for t in threads if publish.is_open(t, ctx.bot)}
    posted = publish.publish_inline(gh, number, head, findings, threads, bot=ctx.bot, limit=policy.review.max_inline_comments, feedback=ctx.feedback)
    summary.inline = (on_lines | set(posted)) & {f.fingerprint for f in findings}
    body = publish.render_summary(summary, new_state)
    publish.upsert_summary(gh, number, existing, body)
    complete = result.complete if result is not None else True  # no new code to read is a complete review
    try:
        publish.publish_check(gh, head, findings, body.split("<details>")[0], complete=complete)
    except GitHubError as exc:
        ctx.log(f"couldn't publish the check run: {exc}")

    if policy.triage.enabled and not facts.draft:
        current = [label["name"] for label in pull.get("labels", [])]
        publish.apply_label(gh, number, policy, sorted_pr.key, current)
        if policy.triage.close_without_issue and policy.triage.require_linked_issue and not facts.linked_issues and sorted_pr.key != "needs_human":
            gh.comment(number, "Closing this PR: this project asks for every PR to link an issue (`Fixes #123`), and this one doesn't. "
                               "Link one and reopen it, or ask a maintainer. Closed by Stratum under the project's `.stratum/policy.yml`.")
            gh.close(number)

    ctx.storage.record_review(gh.repo, number, head, risk_score(findings), result.summary if result else "", findings)
    return Outcome(number, "published", findings, sorted_pr, result, body)


def _checks(ctx: Context, files: list[FileDiff], head: str) -> list[Finding]:
    """The checks, with the full new text of the files where context matters:
    workflows and manifests always (they're small), Python files for Bandit
    when it's installed."""
    from stratum_open.checks import run_checks
    from stratum_open.checks.bandit import available
    from stratum_open.checks.dependencies import manifest_kind

    def wanted(file: FileDiff) -> bool:
        path = file.path
        workflow = path.startswith(".github/workflows/") or path.rsplit("/", 1)[-1] in ("action.yml", "action.yaml")
        return workflow or manifest_kind(path) is not None

    live = [f for f in files if f.status != "deleted" and not f.binary]
    fetch = [f for f in live if wanted(f)][:MAX_CONTEXT_FILES]
    if available():
        python = sorted((f for f in live if f.path.endswith(".py")), key=lambda f: -f.additions)
        fetch += python[:MAX_BANDIT_FILES]
    contents: dict[str, str] = {}
    for file in fetch:
        try:
            text = ctx.gh.file_text(file.path, head)
        except GitHubError:
            text = None
        if text is not None:
            contents[file.path] = text
    lookup = ctx.lookup if ctx.policy.dependencies.registry_lookup else None
    return run_checks(files, ctx.policy, lookup=lookup, contents=contents or None)


def _history(ctx: Context, files: list[FileDiff], pull: dict) -> tuple[list[str], set[str], list[Finding]]:
    """Notes, hotspot paths and boundary findings from the default branch's
    checkout. Without one (the CLI on a PR URL), boundaries still work from
    the diff alone."""
    from stratum_open.repo.boundaries import boundary_findings

    notes: list[str] = []
    hot: set[str] = set()
    # The default branch knows a renamed file by its old path.
    changed = [f.path for f in files if f.status != "deleted"] + [f.old_path for f in files if f.old_path]
    if ctx.checkout:
        from stratum_open.repo.graph import importers, reach_note
        from stratum_open.repo.hotspots import hotspot_note, hotspot_paths, hotspots
        from stratum_open.repo.ownership import bus_factor_notes, owners

        window = ctx.policy.track_record.window_days
        for name, step in (
            ("imports", lambda: notes.extend(reach_note(importers(ctx.checkout, changed)))),
            ("ownership", lambda: notes.extend(bus_factor_notes(
                owners(ctx.checkout, ctx.history_ref, window_days=window, exclude=ctx.policy.track_record.exclude),
                changed, _identities(ctx, (pull.get("user") or {}).get("login", ""))))),
        ):
            try:
                step()
            except Exception as exc:  # history is extra context; a review never fails on it
                ctx.log(f"{name}: {type(exc).__name__}: {exc}")
        try:
            found = hotspots(ctx.checkout, ctx.history_ref, window_days=window)
            hot = {path for path in hotspot_paths(found) if not ctx.policy.ignored(path)}
            notes.extend(hotspot_note(found, changed))
        except Exception as exc:
            ctx.log(f"hotspots: {type(exc).__name__}: {exc}")
    try:
        boundary = boundary_findings(files, ctx.policy, ctx.checkout)
    except Exception as exc:
        ctx.log(f"boundaries: {type(exc).__name__}: {exc}")
        boundary = []
    return notes, hot, boundary


def _record(ctx: Context, pull: dict) -> AuthorRecord | None:
    """The author's record, only when the policy lets it affect triage."""
    policy = ctx.policy.track_record
    if not (ctx.checkout and policy.enabled and policy.affects_triage):
        return None
    login = (pull.get("user") or {}).get("login", "")
    return record_for(ctx, login)


def _identities(ctx: Context, login: str) -> set[str]:
    """A login and the commit emails GitHub links to it."""
    found = {login} if login else set()
    try:
        found |= ctx.gh.commit_emails(login) if login else set()
    except GitHubError:
        pass
    return found


def record_for(ctx: Context, login: str) -> AuthorRecord | None:
    """A login's record. Commits carry emails, not logins, so the emails
    GitHub links to the login are matched too."""
    policy = ctx.policy.track_record
    if not ctx.checkout or not login or author_identity(login) in {author_identity(e) for e in policy.exclude}:
        return None
    records = build_records(ctx.checkout, ctx.history_ref, window_days=policy.window_days, followup_days=policy.followup_days, exclude=policy.exclude)
    identities = {author_identity(name) for name in _identities(ctx, login)}
    merged = AuthorRecord(author_identity(login))
    for identity in identities:
        if identity in records:
            merged.merged += records[identity].merged
            merged.strikes += records[identity].strikes
    merged.merged.sort(key=lambda pr: pr.merged_at)
    return merged


def _carry(prior: list[dict], files: list[FileDiff], current: set[str], reviewed_now: set[str]) -> tuple[list[Finding], dict[str, str]]:
    """Prior model findings whose code is still there (re-anchored to where it
    is now), and the fingerprints of those whose code changed."""
    by_path = {f.path: f for f in files}
    carried, gone = [], {}
    for data in prior:
        if data.get("source") != "model" or not data.get("fingerprint"):
            continue
        fp = data["fingerprint"]
        if fp in current:
            continue
        file = by_path.get(data.get("path", ""))
        if file is None:
            gone[fp] = "Resolved: this file is no longer changed by the PR."
            continue
        finding = Finding.from_dict(data)
        snippet = data.get("snippet") or ""
        if finding.line_start is not None:
            located = _locate(snippet, file, near=finding.line_start) if snippet else None
            if located is None:
                gone[fp] = "Resolved: the code this was about has changed."
                continue
            finding.line_start, finding.line_end = located
        elif file.path in reviewed_now:
            gone[fp] = "Resolved: the model no longer finds this after the latest changes."
            continue
        carried.append(finding)
    return carried, gone


def _snippet(finding: Finding, files: list[FileDiff]) -> str:
    file = next((f for f in files if f.path == finding.path), None)
    return file.snippet(finding.line_start, finding.line_end) if file and finding.line_start else ""


def _coverage(result: ReviewResult | None, incremental: bool, changed: set[str] | None, previous_head: str, files: list[FileDiff]) -> list[str]:
    lines = []
    if incremental and previous_head:
        if changed:
            lines.append(f"Reviewed only the {_files(len(changed))} changed since {previous_head[:7]}; findings elsewhere were kept.")
        else:
            lines.append(f"No files changed since {previous_head[:7]}; the earlier findings were kept and the checks ran again.")
    if result and result.packed:
        packed = result.packed
        if packed.reviewed or packed.clipped:
            calls = len(packed.chunks)
            lines.append(f"The model read {_files(len(packed.reviewed) + len(packed.clipped))} in {calls} call{'' if calls == 1 else 's'}.")
        if packed.clipped:
            lines.append("Read in part (too big for one call): " + ", ".join(f"`{p}`" for p in packed.clipped[:10]))
        if packed.omitted:
            lines.append(f"Not read by the model ({_files(len(packed.omitted))} past the review's size limit): " + ", ".join(f"`{p}`" for p in packed.omitted[:10]))
    lines.append(f"The checks ran on {'the' if len(files) == 1 else 'all'} {_files(len(files))} changed.")
    return lines


def _files(n: int) -> str:
    return f"{n} file" if n == 1 else f"{n} files"


def checkout_ref(checkout: str, default_branch: str) -> str:
    """The default branch inside a checkout: its remote-tracking ref when
    there is one, so a checked-out PR branch is never read as history."""
    for ref in (f"refs/remotes/origin/{default_branch}", f"refs/heads/{default_branch}"):
        found = subprocess.run(["git", "-C", checkout, "rev-parse", "--verify", "--quiet", ref], capture_output=True, text=True)
        if found.returncode == 0:
            return ref
    return "HEAD"
