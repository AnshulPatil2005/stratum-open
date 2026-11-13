"""One PR, start to finish: read it, check it, review it, sort it, publish.

The Action, the App and the CLI all call `review_pull`; they differ only in
the `Context` they pass (where the token comes from, whether anything is
stored, whether there's a checkout of the default branch to read history
from).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable

from stratum_open.diff import FileDiff, parse_diff, sendable
from stratum_open.engine import analyze, default_conclusion, effective_policy, snippet_of  # noqa: F401  (effective_policy: re-exported)
from stratum_open.findings import Finding, placeable, risk_score
from stratum_open.github import publish
from stratum_open.github.api import GitHub, GitHubError
from stratum_open.model import Model
from stratum_open.policy import Policy, load_policy, matches_any
from stratum_open.review import ReviewResult
from stratum_open.review_large import LargeReview
from stratum_open.storage import NullStorage, Storage
from stratum_open.track_record import AuthorRecord, author_identity, build_records, describe_subprocess_error
from stratum_open.triage import PRFacts, Triage, triage
from stratum_open.util import count, redact

MAX_BANDIT_FILES = 20
MAX_CONTEXT_FILES = 20
MAX_MOVED_FILES = 20  # files moved into a check's scope, which the checks read in full
MAX_MOVED_BYTES = 1_000_000  # the contents API's own limit for a file's text
MAX_REVIEW_TEXT_FILES = 20  # files whose text is fetched for review.context_lines
# The JSON size of the unread paths kept in the summary's state (about 60 paths), so they don't crowd the
# findings out of the comment; past it, the next run reads everything since the old head.
MAX_UNREAD_BYTES = 4000


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


def review_pull(ctx: Context, number: int, *, full: bool = False, post: bool = True, retry: bool = True) -> Outcome:
    """`retry=False` makes no model call at all (a redraw, like an ignore):
    what the model hasn't read is left for the next review."""
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
    # the head it last reviewed, plus what it couldn't read there. With no
    # model or `retry=False` it reads nothing, and what changed since that
    # head is what's left unread, so that's worked out on a full review too.
    reads = ctx.model is not None and retry
    since: set[str] | None = None  # the files changed since `state.head`; None when that isn't known
    if state.head and (not full or not reads):
        if state.head == head:
            since = set()
        else:
            compared = gh.compare_files(state.head, head)
            since = set(compared) & set(paths) if compared is not None else None
    changed = None if full else since
    # `unread` records paths, not passes: a file read again goes through every
    # pass (per-file, lenses, chunked, verify, synthesis), even if one failed.
    retried: set[str] = set()
    if changed is not None and reads:
        retried = (set(state.unread) & set(paths)) - changed
        changed = changed | retried
    incremental = changed is not None

    notes, hotspots, boundary, file_notes = _history(ctx, files, pull)
    record = _record(ctx, pull)
    muted = sorted(ctx.storage.muted_rules(gh.repo)) if ctx.feedback else []
    # Whether the profile shows code around each hunk, so the files' text is worth fetching.
    around = ctx.model is not None and effective_policy(ctx.policy, ctx.model)[0].review.context_lines > 0
    contents = _contents(ctx, files, head, around=around)
    engine = analyze(
        title=pull.get("title") or "", description=pull.get("body") or "", files=files, policy=ctx.policy, model=ctx.model,
        contents=contents, lookup=ctx.lookup, only_paths=changed if retry else set(), first=retried,
        previous=state.findings,
        notes=notes, hotspots=hotspots, file_notes=file_notes, extra_findings=boundary,
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
        if result.calls and not result.ran:  # no calls: nothing it was given could be sent
            problems.extend(model_problems(sorted({c["error"] for c in result.calls if c.get("error")})))
        else:
            if result.failed_calls:
                problems.append(f"{result.failed_calls} of {len(result.calls)} model calls failed; some files weren't reviewed.")
            if result.salvaged_calls:
                problems.append(f"{result.salvaged_calls} of the model's replies were cut off; anything it found after the cut is missing.")
    elif ctx.model is None:
        problems.append("No model is configured, so this review has only the checks.")
    large = engine.large
    if large is not None:
        file_calls = [c for c in large.calls if c.get("pass") != "synthesis"]
        if large.failed_calls:
            # A file the chunked review read in full isn't unread; one it didn't read is read again next time.
            covered = not (large.unread & engine.unread)
            problems.append(f"{large.failed_calls} of {len(file_calls)} per-file model calls failed; "
                            + ("the chunked review still covered those files." if covered
                               else "the next review reads again the ones the chunked review didn't read in full either."))
        if large.salvaged_calls:
            problems.append(f"{large.salvaged_calls} of the per-file replies were cut off; anything found after the cut is missing.")

    # What the model read this time, and what's left for the next review: the
    # files whose call failed or was cut off, and those it was to read again
    # but didn't. A file to read again that the size limit left out is
    # dropped, as on a first read (the files read again are packed first, so
    # only their own size does that). When the model read nothing, what
    # changed joins what was unread. Files that are never sent (binary,
    # deleted, ignored) are never unread.
    packed = result.packed if result else None
    read = set(packed.reviewed + packed.clipped) if packed else set()
    read = (read | set(large.reviewed if large else [])) - engine.unread
    if not reads:
        left = set(state.unread) | (since if since is not None else set(paths))
    else:
        left = engine.unread | (set(state.unread) - read - set(packed.omitted if packed else []))
    by_path = {f.path: f for f in files}
    unread = sorted(p for p in left if p in by_path and sendable(by_path[p], policy.ignored))
    waiting = len(set(unread) - engine.unread) if ctx.model is not None else 0
    if waiting:
        problems.append(f"The model hasn't read {_files(waiting)} yet; the next review reads {'it' if waiting == 1 else 'them'}.")
    # A review that left files unread isn't complete, whichever run failed to read them.
    complete = engine.complete and not waiting

    facts = PRFacts.from_pull(pull, paths)
    try:
        facts.ci_ok = gh.ci_state(head, ignore={policy.review.check_name, *ctx.own_checks})
        facts.linked_issues = gh.linked_issues(number)
    except GitHubError as exc:
        problems.append(f"Couldn't read CI status or linked issues ({exc.status}).")

    # Each `@stratum` command runs on its own, so an `ignore` can land while
    # this review was reading the PR: read the summary again before writing
    # it, keep what was ignored meanwhile, and leave those findings out (the
    # score and the check's conclusion then follow the findings that are left).
    score, conclusion = engine.score, engine.conclusion
    if complete != engine.complete:
        conclusion = default_conclusion(findings, complete, policy, score)
    if post:
        latest = publish.find_summary(gh.issue_comments(number), ctx.bot)
        if latest is not None:
            fresh = publish.State.decode(latest["body"])
            added = [fp for fp in fresh.ignored if fp not in state.ignored]
            added_rules = [rule for rule in fresh.ignored_rules if rule not in state.ignored_rules]
            state.ignored, state.ignored_rules = state.ignored + added, state.ignored_rules + added_rules
            for finding in findings:
                if finding.fingerprint in added or finding.rule in added_rules:
                    gone.setdefault(finding.fingerprint, "Ignored on this PR.")
            kept = [f for f in findings if f.fingerprint not in added and f.rule not in added_rules]
            if len(kept) < len(findings):
                findings = kept
                score = max(0, min(100, risk_score(findings)))
                conclusion = default_conclusion(findings, complete, policy, score)
            existing = latest  # edit the summary that's there now, never post a second one
    sorted_pr = triage(
        facts, findings, policy,
        matches_description=result.matches_description if result else "", mismatch=result.mismatch if result else "",
        record=record,
    )

    coverage = _large_coverage(large) + _coverage(result, incremental, changed - retried if changed is not None else None, state.head, files,
                                                 contents or {}, retried & read, engine.unread)
    if ctx.model is not None:
        coverage.append(f"Profile: `{engine.profile}` ({engine.profile_why}).")
    summary = publish.Summary(
        findings=findings, inline=set(), policy=policy, model_name=ctx.model.name if ctx.model else None,
        model_summary=(large.summary if large and large.synthesized else result.summary if result and result.ran else ""),
        matches_description=result.matches_description if result else "", mismatch=result.mismatch if result else "",
        triage=sorted_pr, notes=notes, coverage=coverage, problems=problems, muted=muted,
        **_walkthrough(large, findings),
    )
    # The next review reads what changed since this head, plus `unread`, so
    # it reads those again rather than calling a review it never finished
    # complete. A list too long for the summary comment keeps the old head
    # and its `unread` instead, so the next run reads everything since it.
    # (Whatever failed this time was either changed since the old head or
    # already in its `unread`; anything else was read at the old head.)
    keep_head = len(json.dumps(unread)) > MAX_UNREAD_BYTES
    new_state = publish.State(
        head=state.head if keep_head else head,
        unread=state.unread if keep_head else unread,
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
        # A check's finding on a moved file can be on a line the diff doesn't show: it's listed in the summary.
        posted = publish.publish_inline(gh, number, head, placeable(findings, files), threads, bot=ctx.bot,
                                        limit=policy.review.max_inline_comments, feedback=ctx.feedback)
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
        publish.publish_check(gh, head, findings, body.split("<details>")[0], conclusion=conclusion, name=policy.review.check_name)
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
        ctx.storage.record_review(gh.repo, number, head, score, summary.model_summary, findings)  # the summary as posted
    except Exception as exc:  # the review is on GitHub already; a broken database loses only the App's copy
        ctx.log(redact(f"couldn't store the review: {type(exc).__name__}: {exc}"))
    return Outcome(number, "published", findings, sorted_pr, result, body)


def _contents(ctx: Context, files: list[FileDiff], head: str, *, around: bool = False) -> dict[str, str] | None:
    """The full new text of the files where the checks need context:
    workflows and manifests always (they're small), Python files for Bandit
    when it's installed, and otherwise the Python files whose change touches
    async code, for the missing-await check. Files moved to where a check
    does more than where they were, since the diff shows none of their lines
    as added (at most MAX_MOVED_BYTES each; a bigger one is named as unread).
    With `around` (the profile shows code around each hunk), the biggest
    changes the model will read too."""
    from stratum_open.checks import bandit, moved_in
    from stratum_open.checks.awaits import touches_async
    from stratum_open.checks.common import looks_binary
    from stratum_open.checks.dependencies import manifest_kind

    def wanted(file: FileDiff) -> bool:
        path = file.path
        workflow = path.startswith(".github/workflows/") or path.rsplit("/", 1)[-1] in ("action.yml", "action.yaml")
        return workflow or manifest_kind(path) is not None

    live = [f for f in files if f.status != "deleted" and not f.binary]
    fetch = [f for f in live if wanted(f)][:MAX_CONTEXT_FILES]
    # The files Bandit reads first: a PR full of edited tests mustn't push the code out.
    python = sorted((f for f in live if f.path.endswith(".py")), key=lambda f: (not bandit.scope(f.path), -f.additions))
    fetch += (python if bandit.available() else [f for f in python if touches_async(f)])[:MAX_BANDIT_FILES]
    moved = [f for f in live if moved_in(f)]  # the size cap and the binary test apply however they're fetched
    fetch += [f for f in moved if f not in fetch][:MAX_MOVED_FILES]
    if around:
        reviewed = sorted((f for f in live if not ctx.policy.ignored(f.path) and f not in fetch), key=lambda f: -(f.additions + f.deletions))
        fetch += reviewed[:MAX_REVIEW_TEXT_FILES]
    contents: dict[str, str] = {}
    for file in fetch:
        try:
            text = ctx.gh.file_text(file.path, head, max_bytes=MAX_MOVED_BYTES if file in moved else None)
        except GitHubError:
            text = None
        if text is not None and file in moved and looks_binary(text):
            file.binary = True  # a moved file the diff couldn't call text or not
        elif text is not None:
            contents[file.path] = text
    return contents or None


def _history(ctx: Context, files: list[FileDiff], pull: dict) -> tuple[list[str], set[str], list[Finding], dict[str, str]]:
    """Notes, hotspot paths, boundary findings and per-file notes from the
    default branch's checkout. Without one (the CLI on a PR URL), boundaries
    still work from the diff alone."""
    from stratum_open.repo.boundaries import boundary_findings

    notes: list[str] = []
    hot: set[str] = set()
    file_notes: dict[str, str] = {}
    # The default branch knows a renamed file by its old path; the review, by its new one.
    current = [f.path for f in files if f.status != "deleted"]
    changed = current + [f.old_path for f in files if f.old_path]
    new_path = {f.old_path: f.path for f in files if f.old_path} | {path: path for path in current}
    if ctx.checkout:
        from stratum_open.repo import codeowners
        from stratum_open.repo.graph import importers, reach_note, related_note
        from stratum_open.repo.hotspots import hotspot_note, hotspot_paths, hotspots
        from stratum_open.repo.ownership import bus_factor_notes, owners

        def imports() -> None:
            found = importers(ctx.checkout, changed)
            notes.extend(reach_note(found))
            for path, importers_of in found.items():
                note = related_note(importers_of)
                if note and path in new_path:
                    file_notes[new_path[path]] = note

        window = ctx.policy.track_record.window_days
        for name, step in (
            ("imports", imports),
            ("codeowners", lambda: notes.extend(codeowners.owners_note(codeowners.parse(codeowners.read(ctx.checkout) or ""), current))),
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
            hot |= {new_path[path] for path in hot if path in new_path}  # a hotspot the PR renames stays one
            notes.extend(hotspot_note(found, changed))
        except Exception as exc:
            ctx.log(f"hotspots: {type(exc).__name__}: {exc}")
    try:
        boundary = boundary_findings(files, ctx.policy, ctx.checkout)
    except Exception as exc:
        ctx.log(f"boundaries: {type(exc).__name__}: {exc}")
        boundary = []
    return notes, hot, boundary, file_notes


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


def _coverage(result: ReviewResult | None, incremental: bool, changed: set[str] | None, previous_head: str, files: list[FileDiff],
              contents: dict[str, str], read_again: set[str] = frozenset(), unread: set[str] = frozenset()) -> list[str]:
    """`changed`: the files changed since `previous_head`; `contents`: the
    files' text, for what the checks couldn't read; `read_again`: the ones the
    last review couldn't read that the model read this time; `unread`: the
    ones whose call failed or was cut off this time."""
    from stratum_open.checks import bandit, moved_in

    lines = []
    if incremental and previous_head:
        new = len(changed or ())
        again = f"the {_files(len(read_again))} the last review couldn't read" if read_again else ""
        if new and again:
            lines.append(f"Reviewed only the {_files(new)} changed since {previous_head[:7]}, and {again}; findings elsewhere were kept.")
        elif again:
            lines.append(f"Read again {again}; findings elsewhere were kept.")
        elif new:
            lines.append(f"Reviewed only the {_files(new)} changed since {previous_head[:7]}; findings elsewhere were kept.")
        else:
            lines.append(f"No files changed since {previous_head[:7]}; the earlier findings were kept and the checks ran again.")
    if result and result.packed:
        packed = result.packed
        if packed.reviewed or packed.clipped:
            calls = len(packed.chunks)
            paths = packed.reviewed + packed.clipped
            sent = f"{_files(len(paths))} in {count(calls, 'call')}"
            read = len(set(paths) - unread)
            if result.ran and read == len(paths):
                lines.append(f"The model read {sent}.")
            elif result.ran:
                lines.append(f"Stratum sent the model {sent}; it read {read or 'none'} of them in full.")
            else:
                lines.append(f"Stratum sent the model {sent}, but it didn't answer, so they weren't reviewed.")
        if packed.clipped:
            lines.append("Read in part (too big for one call): " + ", ".join(f"`{p}`" for p in packed.clipped[:10]))
        if packed.omitted:
            lines.append(f"Not read by the model ({_files(len(packed.omitted))} past the review's size limit): " + ", ".join(f"`{p}`" for p in packed.omitted[:10]))
    lines.append(f"The checks ran on {'the' if len(files) == 1 else 'all'} {_files(len(files))} changed.")
    moved = [f.path for f in files if moved_in(f) and f.path in contents]
    if moved:
        lines.append("Checked in full, since they moved to where the checks do more: " + ", ".join(f"`{p}`" for p in moved[:10]))
    if bandit.available():
        _, left_out = bandit.plan(files, contents)
        if left_out:
            lines.append(f"Not read by Bandit ({_files(len(left_out))} past its limit of {bandit.MAX_FILES}, or without their text): "
                         + ", ".join(f"`{p}`" for p in left_out[:10]))
    return lines


def _walkthrough(large: LargeReview | None, findings: list[Finding]) -> dict[str, Any]:
    """The walkthrough's priorities and text for the summary. When its call
    failed, the fallbacks worked out from the findings still help, except as
    filler: no priorities without findings, no walkthrough without a file
    reviewed one at a time. (The summary sentence keeps the chunked review's,
    which beats the fallback's generic one.)"""
    if large is None:
        return {"priorities": [], "walkthrough": ""}
    return {
        "priorities": large.top_priorities if large.synthesized or findings else [],
        "walkthrough": large.walkthrough if large.synthesized or large.reviewed else "",
    }


def _large_coverage(large: LargeReview | None) -> list[str]:
    """What the large profile's per-file passes covered."""
    if large is None:
        return []
    reviewed = [f for f in large.files if f.status == "reviewed"]
    lines = []
    if reviewed:
        focused = [f for f in reviewed if f.lenses]
        lenses = sorted({lens for f in focused for lens in f.lenses})
        lines.append(f"The model reviewed {_files(len(reviewed))} one at a time"
                     + (f", {len(focused)} of them again for {_and(lenses)}." if focused else "."))
    failed = [f.path for f in large.files if f.status == "failed"]
    if failed:
        lines.append("Per-file review failed for: " + ", ".join(f"`{p}`" for p in failed[:10]))
    return lines


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _files(n: int) -> str:
    return count(n, "file")


def model_problems(errors: list[str]) -> list[str]:
    """The summary's lines for a model step that produced nothing: a bug of
    ours is named as one (as the Action does), the model not answering as an
    outage, and when the calls failed for both reasons, both are said."""
    bugs = [e for e in errors if e.startswith("Stratum hit a bug")]
    outages = [e for e in errors if not e.startswith("Stratum hit a bug")]
    lines = []
    if bugs:
        lines.append(f"{bugs[0].split(':', 1)[0]}, so this review has only the checks.")
    if outages or not bugs:
        lead = "The model also didn't answer" if bugs else "The model didn't answer, so this review has only the checks"
        lines.append(lead + (f": {outages[0]}" if outages else "."))
    return lines


def checkout_ref(checkout: str, default_branch: str) -> str:
    """The default branch inside a checkout: its remote-tracking ref when
    there is one, so a checked-out PR branch is never read as history."""
    for ref in (f"refs/remotes/origin/{default_branch}", f"refs/heads/{default_branch}"):
        found = subprocess.run(["git", "-C", checkout, "rev-parse", "--verify", "--quiet", ref], capture_output=True, text=True)
        if found.returncode == 0:
            return ref
    return "HEAD"
