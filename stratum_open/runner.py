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
from stratum_open.engine import analyze, effective_policy, snippet_of  # noqa: F401  (effective_policy: re-exported)
from stratum_open.findings import Finding
from stratum_open.github import publish
from stratum_open.github.api import GitHub, GitHubError
from stratum_open.model import Model
from stratum_open.policy import Policy, load_policy, matches_any
from stratum_open.review import ReviewResult
from stratum_open.storage import NullStorage, Storage
from stratum_open.track_record import AuthorRecord, author_identity, build_records, describe_subprocess_error
from stratum_open.triage import PRFacts, Triage, triage
from stratum_open.util import redact

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


def skip_reason(pull: dict, action: str | None, policy: Policy) -> str | None:
    """Why a PR event doesn't start a review, per `review.events` and the
    `review.skip_*` settings; None when it should. `action` is None for a
    review that isn't a PR event (CI finishing), which skips the events check.
    `@stratum review` doesn't ask: a maintainer asking outranks these."""
    rules = policy.review
    if action is not None and action not in rules.events:
        return f"Nothing to do for a {action} event."
    if pull.get("draft") and rules.skip_drafts:
        return "Draft PR: Stratum reviews it once it's ready for review, or on `@stratum review`."
    user = pull.get("user") or {}
    login = user.get("login", "")
    if rules.skip_bots and (user.get("type") == "Bot" or login.endswith("[bot]")):
        return f"A PR by a bot ({login}), and review.skip_bots is on."
    if login.lower() in {name.lower() for name in rules.skip_authors}:
        return f"PRs by {login} aren't reviewed (review.skip_authors)."
    labels = sorted({label.get("name", "") for label in pull.get("labels", [])} & set(rules.skip_labels))
    if labels:
        return f"The PR has the label {labels[0]!r} (review.skip_labels)."
    branch = (pull.get("head") or {}).get("ref", "")
    if branch and matches_any(branch, rules.skip_branches):
        return f"PRs from {branch} aren't reviewed (review.skip_branches)."
    return None


def review_pull(ctx: Context, number: int, *, full: bool = False, post: bool = True) -> Outcome:
    gh = ctx.gh
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

    notes, hotspots, boundary = _history(ctx, files, pull)
    record = _record(ctx, pull)
    muted = sorted(ctx.storage.muted_rules(gh.repo)) if ctx.feedback else []
    engine = analyze(
        title=pull.get("title") or "", description=pull.get("body") or "", files=files, policy=ctx.policy, model=ctx.model,
        contents=_contents(ctx, files, head), lookup=ctx.lookup, only_paths=changed, previous=state.findings,
        notes=notes, hotspots=hotspots, extra_findings=boundary,
        exclude_fingerprints=set(state.ignored), exclude_rules=set(state.ignored_rules) | set(muted),
        log=(lambda prompt, reply: ctx.storage.record_job(gh.repo, number, "prompt", "logged", prompt_log=f"{prompt}\n\n---\n\n{reply}")),
    )
    policy, findings, result, gone = engine.policy, engine.findings, engine.review, dict(engine.gone)
    # Before the check sweep below, which would call an ignored check "fixed".
    for finding in engine.excluded:
        if finding.fingerprint in state.ignored or finding.rule in state.ignored_rules:
            gone.setdefault(finding.fingerprint, "Ignored on this PR.")
        elif finding.rule in muted:
            gone.setdefault(finding.fingerprint, "Muted in this repository.")
    for call in (result.calls if result else []):
        if call.get("traceback"):  # the model step raised: the summary gets the one-line error, the log the rest
            ctx.log(redact(f"model review failed: {call.get('error', '')}\n{call['traceback']}"))
    if result is not None:
        if not result.ran:
            errors = sorted({c["error"] for c in result.calls if c.get("error")})
            if errors and errors[0].startswith("Stratum hit a bug"):  # ours, not the model's: say so, as the Action does
                problems.append(f"{errors[0].split(':', 1)[0]}, so this review has only the checks.")
            else:
                problems.append("The model didn't answer, so this review has only the checks" + (f": {errors[0]}" if errors else "."))
        else:
            if result.failed_calls:
                problems.append(f"{result.failed_calls} of {len(result.calls)} model calls failed; some files weren't reviewed.")
            if result.salvaged_calls:
                problems.append(f"{result.salvaged_calls} of the model's replies were cut off; anything it found after the cut is missing.")
    elif ctx.model is None:
        problems.append("No model is configured, so this review has only the checks.")

    facts = PRFacts.from_pull(pull, paths)
    try:
        facts.ci_ok = gh.ci_state(head, ignore={policy.review.check_name, *ctx.own_checks})
        facts.linked_issues = gh.linked_issues(number)
    except GitHubError as exc:
        problems.append(f"Couldn't read CI status or linked issues ({exc.status}).")
    sorted_pr = triage(
        facts, findings, policy,
        matches_description=result.matches_description if result else "", mismatch=result.mismatch if result else "",
        record=record,
    )

    coverage = _coverage(result, incremental, changed, state.head, files)
    if ctx.model is not None:
        coverage.append(f"Profile: `{engine.profile}` ({engine.profile_why}).")
    summary = publish.Summary(
        findings=findings, inline=set(), policy=policy, model_name=ctx.model.name if ctx.model else None,
        model_summary=(result.summary if result and result.ran else ""),
        matches_description=result.matches_description if result else "", mismatch=result.mismatch if result else "",
        triage=sorted_pr, notes=notes, coverage=coverage, problems=problems, muted=muted,
    )
    new_state = publish.State(
        head=head,
        findings=[publish.state_finding(f, snippet_of(f, files)) for f in findings if f.source == "model"],
        ignored=state.ignored, ignored_rules=state.ignored_rules,
        triage={**sorted_pr.as_dict(), "additions": facts.additions, "deletions": facts.deletions},
    )
    if not post:
        return Outcome(number, "dry-run", findings, sorted_pr, result, publish.render_summary(summary, new_state))

    # Don't publish a review of a commit that's no longer the PR's head.
    if gh.pull(number)["head"]["sha"] != head:
        return Outcome(number, "superseded", findings, sorted_pr, result, note="a newer commit arrived")

    # Each publishing step is its own attempt: GitHub refusing one (a token
    # that lost a permission, a 5xx) must not stop the others, and the check
    # run is always posted after the comments so it reports whatever got through.
    threads: list[dict] = []
    posted: list[str] = []
    try:
        threads = gh.review_threads(number)
        current = {f.fingerprint for f in findings}
        for thread in threads:  # checks run on every push, so a check's finding that's gone is fixed
            marker = publish.thread_marker(thread, ctx.bot)
            if marker and not marker[1].startswith("model:") and marker[0] not in current:
                gone.setdefault(marker[0], "Fixed: the check no longer finds this.")
        publish.resolve_threads(gh, number, threads, gone, bot=ctx.bot, log=ctx.log)
        posted = publish.publish_inline(gh, number, head, findings, threads, bot=ctx.bot, limit=policy.review.max_inline_comments, feedback=ctx.feedback)
    except GitHubError as exc:
        ctx.log(f"couldn't post inline comments: {exc}")
        problems.append(f"Couldn't post inline comments (GitHub answered {exc.status}), so every finding is listed here instead.")
    on_lines = {publish.thread_fingerprint(t, ctx.bot) for t in threads if publish.is_open(t, ctx.bot)}
    summary.inline = (on_lines | set(posted)) & {f.fingerprint for f in findings}
    body = publish.render_summary(summary, new_state)
    try:
        publish.upsert_summary(gh, number, existing, body)
    except GitHubError as exc:
        ctx.log(f"couldn't post the summary comment: {exc}")
    try:
        publish.publish_check(gh, head, findings, body.split("<details>")[0], conclusion=engine.conclusion, name=policy.review.check_name)
    except GitHubError as exc:
        ctx.log(f"couldn't publish the check run: {exc}")

    if policy.triage.enabled and not facts.draft:
        try:
            labels = [label.get("name", "") for label in pull.get("labels") or [] if isinstance(label, dict)]
            publish.apply_label(gh, number, policy, sorted_pr.key, labels)
            if policy.triage.close_without_issue and policy.triage.require_linked_issue and not facts.linked_issues and sorted_pr.key != "needs_human":
                gh.comment(number, "Closing this PR: this project asks for every PR to link an issue (`Fixes #123`), and this one doesn't. "
                                   "Link one and reopen it, or ask a maintainer. Closed by Stratum under the project's `.stratum/policy.yml`.")
                gh.close(number)
        except GitHubError as exc:
            ctx.log(f"couldn't apply the triage label: {exc}")

    try:
        ctx.storage.record_review(gh.repo, number, head, engine.score, result.summary if result else "", findings)
    except Exception as exc:  # the review is on GitHub already; a broken database loses only the App's copy
        ctx.log(redact(f"couldn't store the review: {type(exc).__name__}: {exc}"))
    return Outcome(number, "published", findings, sorted_pr, result, body)


def _contents(ctx: Context, files: list[FileDiff], head: str) -> dict[str, str] | None:
    """The full new text of the files where the checks need context:
    workflows and manifests always (they're small), Python files for Bandit
    when it's installed."""
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
    return contents or None


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
    if not ctx.checkout or not login or excluded(ctx, login):
        return None
    try:
        records = build_records(ctx.checkout, ctx.history_ref, window_days=policy.window_days, followup_days=policy.followup_days, exclude=policy.exclude)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        # No history (an empty or shallow clone, a missing ref, git not installed, a huge log past its
        # time limit) means no record, never a failed review.
        ctx.log(f"track record: couldn't read the history of {ctx.checkout} at {ctx.history_ref}: {type(exc).__name__} {describe_subprocess_error(exc)}")
        return None
    identities = {author_identity(name) for name in _identities(ctx, login)}
    merged = AuthorRecord(author_identity(login))
    for identity in identities:
        if identity in records:
            merged.merged += records[identity].merged
            merged.strikes += records[identity].strikes
    merged.merged.sort(key=lambda pr: pr.merged_at)
    return merged


def excluded(ctx: Context, login: str) -> bool:
    """Whether the policy keeps this login out of track records."""
    return author_identity(login) in {author_identity(e) for e in ctx.policy.track_record.exclude}


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
            sent = f"{_files(len(packed.reviewed) + len(packed.clipped))} in {calls} call{'' if calls == 1 else 's'}"
            if result.ran:
                lines.append(f"The model read {sent}.")
            else:
                lines.append(f"Stratum sent the model {sent}, but it didn't answer, so they weren't reviewed.")
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
