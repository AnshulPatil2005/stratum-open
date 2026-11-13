"""What a review looks like on GitHub: one summary comment, inline comments
on the lines, a check run, and the triage label.

The summary comment also carries a small hidden copy of the last review
(head commit, findings, ignores), so the next push can review only what
changed without Stratum keeping any database. It's only ever read back from
a comment Stratum itself wrote, so a contributor can't forge it.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from stratum_open.engine import default_conclusion
from stratum_open.findings import Finding, counts, severity_rank
from stratum_open.github.api import GitHub, GitHubError
from stratum_open.policy import Policy
from stratum_open.review import COMMAND, MIN_REPEATED_FILES
from stratum_open.triage import COLORS, DESCRIPTIONS, KEYS, Triage, label_for

CHECK_NAME = "Stratum"
SUMMARY_MARKER = "<!-- stratum:summary -->"
RESOLVED_MARKER = "<!-- stratum:resolved -->"
_STATE = re.compile(r"<!-- stratum:state ([A-Za-z0-9+/=]+) -->")
_FINDING = re.compile(r"<!-- stratum:finding ([0-9a-f]{12}) (\S+) -->")
MAX_STATE_FINDINGS = 80
MAX_LISTED = 15
QUIET_SHOWN = 5  # `review.quiet`: the model's findings in view at most, on the lines or in the summary; the rest are folded
QUIET_NOTES = 3  # ... and the history notes shown; the rest go under "What was reviewed"
COMMENT_LIMIT = 65000
LABEL = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}
# The hint under each inline comment, and the one 0.1 and 0.2 posted.
HINT = "Reply `/stratum ignore` to hide it on this PR, or `/stratum explain` for more."
OLD_HINT = "Reply `@stratum ignore` to hide it on this PR, or `@stratum explain` for more."


@dataclass
class State:
    head: str = ""  # the last commit reviewed; the model read it all except `unread`
    findings: list[dict[str, Any]] = field(default_factory=list)  # model findings, with "snippet"
    ignored: list[str] = field(default_factory=list)  # fingerprints
    ignored_rules: list[str] = field(default_factory=list)
    triage: dict[str, Any] = field(default_factory=dict)
    unread: list[str] = field(default_factory=list)  # paths the model hasn't read at `head` (paths, not passes: each goes through every pass again)

    def encode(self) -> str:
        data = asdict(self)
        data["findings"] = data["findings"][:MAX_STATE_FINDINGS]
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return f"<!-- stratum:state {base64.b64encode(raw).decode('ascii')} -->"

    @classmethod
    def decode(cls, body: str) -> State:
        match = _STATE.search(body or "")
        if not match:
            return cls()
        try:
            data = json.loads(base64.b64decode(match.group(1)))
            return cls(
                head=str(data.get("head", "")),
                findings=[f for f in data.get("findings", []) if isinstance(f, dict)],
                ignored=[str(x) for x in data.get("ignored", [])],
                ignored_rules=[str(x) for x in data.get("ignored_rules", [])],
                triage=data.get("triage") if isinstance(data.get("triage"), dict) else {},
                unread=[str(x) for x in data.get("unread", []) if isinstance(x, str)],
            )
        except (ValueError, TypeError, AttributeError):
            return cls()


def replace_state(body: str, state: State) -> str:
    if _STATE.search(body):
        return _STATE.sub(lambda _: state.encode(), body)
    return body + "\n" + state.encode()


def state_finding(finding: Finding, snippet: str) -> dict[str, Any]:
    data = finding.to_dict()
    data.pop("replacement", None)
    data["detail"] = data["detail"][:500]
    data["snippet"] = snippet[:300]
    return data


def find_summary(comments: list[dict], bot: str) -> dict | None:
    """The latest summary comment Stratum wrote. Comments by anyone else are
    never trusted, even if they copy the marker."""
    mine = [c for c in comments if _is_bot(c, bot) and SUMMARY_MARKER in (c.get("body") or "")]
    return mine[-1] if mine else None


def _is_bot(comment: dict, bot: str) -> bool:
    return ((comment.get("user") or {}).get("login") or comment.get("author") or "").lower() == bot.lower()


# ── inline comments ──────────────────────────────────────────────────────


def comment_body(finding: Finding, *, feedback: bool) -> str:
    lines = [f"**{LABEL[finding.severity]} · {finding.category}:** {finding.title}", "", finding.detail]
    if finding.suggestion:
        lines += ["", f"**Fix:** {finding.suggestion}"]
    # Code to commit can't be put in backticks the way demention() does, so a
    # suggestion with a line that would read as a `/stratum` command is left
    # out, even one inside a fence of its own: the fences around it can change.
    if finding.replacement is not None and not COMMAND.search(finding.replacement):
        # The fence has to be longer than any run of backticks inside, or GitHub ends the block early.
        longest = max((len(run) for run in re.findall(r"`+", finding.replacement)), default=0)
        fence = "`" * max(3, longest + 1)
        lines += ["", f"{fence}suggestion", finding.replacement.rstrip("\n"), fence]
    source = f"a check (`{finding.rule}`)" if finding.source == "check" else "the model"
    hint = f"From {source}. {HINT}"
    if feedback:
        hint += " 👍 or 👎 tells Stratum whether this was useful."
    lines += ["", f"<sub>{hint}</sub>", f"<!-- stratum:finding {finding.fingerprint} {finding.rule} -->"]
    return "\n".join(lines)


def thread_marker(thread: dict, bot: str) -> tuple[str, str] | None:
    """(fingerprint, rule) of a thread Stratum started."""
    first = thread["comments"][0]
    if not _is_bot(first, bot):
        return None
    match = _FINDING.search(first["body"])
    return (match.group(1), match.group(2)) if match else None


def thread_fingerprint(thread: dict, bot: str) -> str | None:
    marker = thread_marker(thread, bot)
    return marker[0] if marker else None


def resolved_by_stratum(thread: dict, bot: str) -> bool:
    """Stratum closed this thread: its comment carries the resolved marker
    (or, before comments were edited, a reply did)."""
    return any(_is_bot(c, bot) and RESOLVED_MARKER in c["body"] for c in thread["comments"])


def is_open(thread: dict, bot: str) -> bool:
    return not thread["resolved"] and not resolved_by_stratum(thread, bot)


def closed_body(body: str, reason: str) -> str:
    """The comment, struck through with the reason, the original folded away.
    The finding marker stays, so Stratum still knows the thread."""
    marker = _FINDING.search(body)
    original = _FINDING.sub("", body).strip()
    title = original.split("\n", 1)[0].replace("**", "").strip()
    return (
        f"~~{title}~~\n\n**{reason}**\n\n<details><summary>The original comment</summary>\n\n{original}\n\n</details>\n"
        f"{RESOLVED_MARKER}\n{marker.group(0) if marker else ''}"
    )


def publish_inline(gh: GitHub, number: int, head_sha: str, findings: list[Finding], threads: list[dict], *, bot: str, limit: int, feedback: bool) -> list[str]:
    """Post the anchored findings that don't have a thread yet. A thread a
    person resolved stays resolved: the finding isn't posted again."""
    existing: dict[str, dict] = {}
    for thread in threads:
        fp = thread_fingerprint(thread, bot)
        if fp:
            existing[fp] = thread
    still_open = sum(1 for t in existing.values() if is_open(t, bot))
    def returned(fp: str) -> bool:  # Stratum closed it when its code changed, and it's back
        return resolved_by_stratum(existing[fp], bot)

    fresh = [f for f in findings if f.anchored and (f.fingerprint not in existing or returned(f.fingerprint))]
    fresh = fresh[: max(0, limit - still_open)]
    if not fresh:
        return []
    comments = [_inline(f, feedback) for f in fresh]
    try:
        gh.create_review(number, head_sha, comments)
        return [f.fingerprint for f in fresh]
    except GitHubError as exc:
        if exc.status != 422:
            raise
    posted = []  # one comment GitHub won't place shouldn't sink the rest
    for finding, comment in zip(fresh, comments):
        try:
            gh.create_review(number, head_sha, [comment])
            posted.append(finding.fingerprint)
        except GitHubError as exc:
            if exc.status != 422:
                raise
    return posted


def _inline(finding: Finding, feedback: bool) -> dict[str, Any]:
    comment: dict[str, Any] = {"path": finding.path, "line": finding.line_end, "side": "RIGHT", "body": comment_body(finding, feedback=feedback)}
    if finding.line_start != finding.line_end:
        comment.update(start_line=finding.line_start, start_side="RIGHT")
    return comment


def _new_hint(body: str) -> str:
    """The comment with the hint from before 0.3.0 rewritten, and nothing else
    changed. The hint is in the last <sub>, after any suggestion, whose code
    has to stay as it was posted."""
    head, sub, tail = body.rpartition("<sub>")
    return head + sub + tail.replace(OLD_HINT, HINT, 1) if sub else body


def update_threads(gh: GitHub, number: int, threads: list[dict], gone: dict[str, str], *, bot: str,
                   log=lambda line: None) -> int:
    """Close Stratum's open threads whose finding is gone, saying why, and
    rewrite the hint 0.2 posted in the open ones, so it names `/stratum`, the
    form that's read now. `gone` maps a fingerprint to the reason. A comment
    is edited at most once per review; once no open thread has the old hint,
    only closing edits anything. Returns how many were closed.

    The comment itself is edited to say it's closed, because GitHub refuses
    to let an Actions token resolve threads, even its own; resolving is tried
    too, for tokens that may."""
    closed = 0
    for thread in threads:
        fp = thread_fingerprint(thread, bot)
        if not fp or not is_open(thread, bot):
            continue
        root = thread["comments"][0]
        body = _new_hint(root["body"])
        if fp in gone:
            try:
                gh.edit_review_comment(root["id"], closed_body(body, gone[fp]))
            except GitHubError as exc:
                log(f"couldn't mark a finding's comment as closed: {exc}")
                continue
            closed += 1
            try:
                gh.resolve_thread(thread["id"])
            except GitHubError:
                pass  # the edit already says it's closed
        elif body != root["body"]:
            try:
                gh.edit_review_comment(root["id"], body)
            except GitHubError as exc:
                log(f"couldn't update the hint in a finding's comment: {exc}")
    return closed


# ── summary comment ──────────────────────────────────────────────────────


@dataclass
class Summary:
    findings: list[Finding]
    inline: set[str]  # fingerprints with an inline comment
    policy: Policy
    model_name: str | None
    model_summary: str = ""
    matches_description: str = ""
    mismatch: str = ""
    triage: Triage | None = None
    notes: list[str] = field(default_factory=list)
    coverage: list[str] = field(default_factory=list)  # what was and wasn't reviewed
    problems: list[str] = field(default_factory=list)  # the model failed, the policy was broken...
    muted: list[str] = field(default_factory=list)
    priorities: list[str] = field(default_factory=list)  # what to check first (the large profile's walkthrough call)
    walkthrough: str = ""


def render_summary(summary: Summary, state: State) -> str:
    lines = [SUMMARY_MARKER, "### Stratum review", ""]
    if summary.model_summary:
        lines.append(f"**What this PR does:** {summary.model_summary}")
    if summary.matches_description in ("partly", "no"):
        word = "Partly" if summary.matches_description == "partly" else "No"
        lines.append(f"**Matches its description:** {word}" + (f". {summary.mismatch}" if summary.mismatch else "."))
    if summary.priorities:
        lines += ["", "**Check first:**"] + [f"- {p}" for p in summary.priorities]
    if lines[-1] != "":
        lines.append("")

    if summary.triage is not None:
        label = label_for(summary.policy, summary.triage.key)
        state_word = "" if summary.policy.triage.enabled else " (labels are off in this project's policy, so nothing was applied)"
        lines.append(f"**Triage:** `{label}`{state_word}")
        lines += [f"- {reason}" for reason in summary.triage.reasons]
        if summary.triage.evidence:
            lines.append("- In its favour: " + " ".join(summary.triage.evidence))
        lines.append("")

    quiet = summary.policy.review.quiet
    folded: list[Finding] = []  # `review.quiet`: the model's findings past QUIET_SHOWN, for a block of their own further down
    if quiet:
        folded = _two_lists(summary, lines)
    else:
        tally = counts(summary.findings)
        found = ", ".join(f"{n} {LABEL[s].lower()}" for s, n in tally.items() if n)
        shown = sum(1 for f in summary.findings if f.fingerprint in summary.inline)
        if summary.findings:
            lines.append(f"**Findings:** {found}." + (f" {shown} on the changed lines below." if shown else ""))
        else:
            lines.append("**Findings:** none.")
        listed = [f for f in summary.findings if f.fingerprint not in summary.inline][:MAX_LISTED]
        if listed:
            lines += ["", "Not on a changed line:"] if shown else [""]
            lines += [_listed(f) for f in listed]
    lines.append("")

    notes, more_notes = _lead_notes(summary.notes) if quiet else (summary.notes, [])
    if notes:
        lines += ["**From the project's history:**"] + [f"- {note}" for note in notes]
        if more_notes:
            lines.append(f'- {len(more_notes)} more under "What was reviewed".')
        lines.append("")
    if summary.walkthrough:
        lines += ["<details><summary>Walkthrough</summary>", "", summary.walkthrough, "", "</details>", ""]
    if summary.problems:
        lines += ["**Couldn't do everything:**"] + [f"- {p}" for p in summary.problems] + [""]
    if folded:
        # Below the problems, so the check run's summary (the text before the first folded block) doesn't lose them to this one.
        lines += [f"<details><summary>{len(folded)} more from the model</summary>", ""] + _capped(folded) + ["", "</details>", ""]

    details = list(summary.coverage)
    details.append(f"Model: `{summary.model_name}`, chosen by this project's maintainers." if summary.model_name else "Model: none configured; checks only.")
    policy = summary.policy
    details.append("Rules: `.stratum/policy.yml` on the default branch." if policy.source == "file" else "Rules: the defaults (no `.stratum/policy.yml`)." if policy.source == "defaults" else "Rules: the defaults, because `.stratum/policy.yml` couldn't be used.")
    details += [f"Policy problem: {e}" for e in policy.errors] + [f"Policy warning: {w}" for w in policy.warnings]
    if summary.muted:
        details.append("Muted by maintainers' 👎: " + ", ".join(f"`{r}`" for r in summary.muted))
    if state.ignored or state.ignored_rules:
        details.append(f"Ignored on this PR: {len(state.ignored)} finding(s)" + (", rules " + ", ".join(f"`{r}`" for r in state.ignored_rules) if state.ignored_rules else "") + ".")
    if quiet:  # what was left out is never listed, so the reader is at least told that something may have been
        details.append(f"Quiet review: comments from the model that name no defect are left out, and one it repeats on {MIN_REPEATED_FILES} "
                       "or more files is kept once.")
    if more_notes:
        details.append("More from the project's history:" + "".join(f"\n  - {note}" for note in more_notes))
    lines += ["<details><summary>What was reviewed</summary>", ""] + [f"- {d}" for d in details] + ["", "</details>", ""]
    lines.append("<sub>Stratum Open: review and triage on a model the maintainers run. Comment `/stratum help` for commands.</sub>")
    body = "\n".join(lines)
    encoded = state.encode()
    while len(body) + len(encoded) > COMMENT_LIMIT and state.findings:
        state.findings = state.findings[: len(state.findings) // 2]
        encoded = state.encode()
    return body[: COMMENT_LIMIT - len(encoded) - 1] + "\n" + encoded


def _two_lists(summary: Summary, lines: list[str]) -> list[Finding]:
    """`review.quiet`: the checks' findings and the model's as two lists, each
    with its own count, so a rule that matched is never added up with a
    model's reading. Of the model's, QUIET_SHOWN at most are in view: the
    ones on the lines, then the ones it's surest of. Returns the rest, which
    go in a folded block."""
    checks = [f for f in summary.findings if f.source != "model"]
    model = [f for f in summary.findings if f.source == "model"]
    on_lines = sum(1 for f in checks if f.fingerprint in summary.inline)
    if checks:
        lines.append(f"**Found by the checks:** {_tally(checks)}." + (f" {on_lines} on the changed lines below." if on_lines else "")
                     + " These need no model: each is a rule that matched the diff.")
    else:
        lines.append("**Found by the checks:** nothing.")
    listed = [f for f in checks if f.fingerprint not in summary.inline]
    if listed:
        lines += ["", "Listed here instead:"] if on_lines else [""]
        lines += _capped(listed)
    if not model:
        if summary.model_name is not None:  # with no model there is nobody to have suggested nothing
            lines += ["", "**Suggested by the model:** nothing."]
        return []

    on_lines = sum(1 for f in model if f.fingerprint in summary.inline)
    rest = surest([f for f in model if f.fingerprint not in summary.inline])
    room = max(0, QUIET_SHOWN - on_lines)
    listed, folded = rest[:room], rest[room:]
    lines.append("")
    where = ([f"{on_lines} on the changed lines below"] if on_lines else []) + ([f"{len(folded)} folded at the end of this comment"] if folded else [])
    lines.append(f"**Suggested by the model:** {_tally(model)}." + (f" {', '.join(where)}." if where else "")
                 + " The model can be wrong, so check each one before acting on it.")
    if listed:
        lines += ["", "Listed here instead:"] if on_lines else [""]
        lines += [_listed(f) for f in listed]
    return folded


def _lead_notes(notes: list[str]) -> tuple[list[str], list[str]]:
    """`review.quiet`: the QUIET_NOTES history notes to show, and the rest,
    which go under "What was reviewed". The first note of each kind is shown
    before a second of any (the file most of the code imports, who owns the
    files, one hotspot): ten lines of one kind say less than one of each.
    Notes are of a kind when they read the same but for their names and
    numbers."""
    def kind(note: str) -> str:
        return " ".join(re.sub(r"`[^`]*`|\d+", "", note).split()[:3])

    first: dict[str, int] = {}
    for index, note in enumerate(notes):
        first.setdefault(kind(note), index)
    lead = sorted(first.values())[:QUIET_NOTES]
    lead = sorted(lead + [i for i in range(len(notes)) if i not in lead][:QUIET_NOTES - len(lead)])
    return [notes[i] for i in lead], [note for i, note in enumerate(notes) if i not in lead]


def surest(findings: list[Finding]) -> list[Finding]:
    """The findings the model is surest of first: one that two of its answers
    agreed on (the review and `review.logic_pass`) before one it said
    once, whatever severity each was given. `review.quiet` fills its places
    in this order."""
    return sorted(findings, key=lambda f: (-f.confidence, -severity_rank(f.severity), f.path, f.line_start or 0))


def quiet_inline(findings: list[Finding], threads: list[dict], *, bot: str) -> list[Finding]:
    """`review.quiet`: the findings to post inline. All of the checks', and of
    the model's as many as bring its open threads to QUIET_SHOWN, the ones
    it's surest of first. An open thread keeps its place, so a push never
    swaps one comment for another; one a person resolved isn't offered again
    (`publish_inline` wouldn't post it), so it doesn't hold a place either."""
    started = {thread_fingerprint(thread, bot): thread for thread in threads}
    open_now = {fp for fp, thread in started.items() if fp and is_open(thread, bot)}
    model = [f for f in findings if f.source == "model"]
    room = max(0, QUIET_SHOWN - sum(1 for f in model if f.fingerprint in open_now))
    fresh = [f for f in model if f.fingerprint not in started or resolved_by_stratum(started[f.fingerprint], bot)]
    chosen = {id(f) for f in surest(fresh)[:room]}
    return [f for f in findings if f.source != "model" or id(f) in chosen]


def _tally(findings: list[Finding]) -> str:
    return ", ".join(f"{n} {LABEL[s].lower()}" for s, n in counts(findings).items() if n)


def _listed(finding: Finding) -> str:
    where = f"`{finding.path}`" + (f" line {finding.line_start}" if finding.line_start else "") if finding.path else "the PR"
    return f"- **{LABEL[finding.severity]} · {finding.category}** in {where}: {finding.title}. {_short(finding.detail, 220)}"


def _capped(findings: list[Finding]) -> list[str]:
    """At most MAX_LISTED findings as lines, and a line saying how many were left out."""
    more = len(findings) - MAX_LISTED
    return [_listed(f) for f in findings[:MAX_LISTED]] + ([f"- and {more} more."] if more > 0 else [])


def upsert_summary(gh: GitHub, number: int, existing: dict | None, body: str) -> None:
    if existing:
        gh.edit_comment(existing["id"], body)
    else:
        gh.comment(number, body)


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


# ── check run and labels ─────────────────────────────────────────────────

_LEVEL = {"critical": "failure", "high": "failure", "medium": "warning", "low": "notice"}


# The rule lives in the engine, which computes it for every review.
conclusion = default_conclusion


def publish_check(gh: GitHub, head_sha: str, findings: list[Finding], summary_text: str, *, conclusion: str,
                  name: str = CHECK_NAME, quiet_inline: set[str] | None = None) -> None:
    """`quiet_inline`: with `review.quiet`, the fingerprints that have an
    inline comment. The title then counts the checks' findings and the
    model's apart, as the summary does, and of the model's findings only
    those are annotated: GitHub shows an annotation on its line too, so one
    for each folded finding would unfold them all."""
    tally = counts(findings)
    title = ", ".join(f"{n} {s}" for s, n in tally.items() if n) or "No findings"
    if quiet_inline is not None and findings:
        by_model = [f for f in findings if f.source == "model"]
        title = f"Checks: {_tally([f for f in findings if f.source != 'model']) or 'nothing'}. Model: {_tally(by_model) or 'nothing'}."
        findings = [f for f in findings if f.source != "model" or f.fingerprint in quiet_inline]
    annotations = [
        {
            "path": f.path, "start_line": f.line_start, "end_line": f.line_end,
            "annotation_level": _LEVEL[f.severity], "title": f.title[:255], "message": _short(f.detail, 1000),
        }
        for f in findings if f.anchored
    ]
    gh.check_run(head_sha, name, conclusion, title, summary_text[:2000], "", annotations)


def apply_label(gh: GitHub, number: int, policy: Policy, key: str, current: list[str]) -> None:
    """Make the PR carry exactly one triage label."""
    wanted = label_for(policy, key)
    for other in KEYS:
        name = label_for(policy, other)
        if name != wanted and name in current:
            gh.remove_label(number, name)
    if wanted not in current:
        gh.ensure_label(wanted, COLORS[key], DESCRIPTIONS[key])
        gh.add_labels(number, [wanted])
