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
from stratum_open.findings import Finding, counts
from stratum_open.github.api import GitHub, GitHubError
from stratum_open.policy import Policy
from stratum_open.triage import COLORS, DESCRIPTIONS, KEYS, Triage, label_for

CHECK_NAME = "Stratum"
SUMMARY_MARKER = "<!-- stratum:summary -->"
RESOLVED_MARKER = "<!-- stratum:resolved -->"
_STATE = re.compile(r"<!-- stratum:state ([A-Za-z0-9+/=]+) -->")
_FINDING = re.compile(r"<!-- stratum:finding ([0-9a-f]{12}) (\S+) -->")
MAX_STATE_FINDINGS = 80
MAX_LISTED = 15
COMMENT_LIMIT = 65000
LABEL = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}


@dataclass
class State:
    head: str = ""
    findings: list[dict[str, Any]] = field(default_factory=list)  # model findings, with "snippet"
    ignored: list[str] = field(default_factory=list)  # fingerprints
    ignored_rules: list[str] = field(default_factory=list)
    triage: dict[str, Any] = field(default_factory=dict)

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
    if finding.replacement is not None:
        fence = "````" if "```" in finding.replacement else "```"
        lines += ["", f"{fence}suggestion", finding.replacement.rstrip("\n"), fence]
    source = f"a check (`{finding.rule}`)" if finding.source == "check" else "the model"
    hint = f"From {source}. Reply `@stratum ignore` to hide it on this PR, or `@stratum explain` for more."
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


def resolve_threads(gh: GitHub, number: int, threads: list[dict], gone: dict[str, str], *, bot: str,
                    log=lambda line: None) -> int:
    """Close Stratum's open threads whose finding is gone, saying why. `gone`
    maps a fingerprint to the reason.

    The comment itself is edited to say it's closed, because GitHub refuses
    to let an Actions token resolve threads, even its own; resolving is tried
    too, for tokens that may."""
    closed = 0
    for thread in threads:
        fp = thread_fingerprint(thread, bot)
        if fp not in gone or not is_open(thread, bot):
            continue
        root = thread["comments"][0]
        try:
            gh.edit_review_comment(root["id"], closed_body(root["body"], gone[fp]))
        except GitHubError as exc:
            log(f"couldn't mark a finding's comment as closed: {exc}")
            continue
        closed += 1
        try:
            gh.resolve_thread(thread["id"])
        except GitHubError:
            pass  # the edit already says it's closed
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
        for f in listed:
            where = f"`{f.path}`" + (f" line {f.line_start}" if f.line_start else "") if f.path else "the PR"
            lines.append(f"- **{LABEL[f.severity]} · {f.category}** in {where}: {f.title}. {_short(f.detail, 220)}")
    lines.append("")

    if summary.notes:
        lines += ["**From the project's history:**"] + [f"- {note}" for note in summary.notes] + [""]
    if summary.walkthrough:
        lines += ["<details><summary>Walkthrough</summary>", "", summary.walkthrough, "", "</details>", ""]
    if summary.problems:
        lines += ["**Couldn't do everything:**"] + [f"- {p}" for p in summary.problems] + [""]

    details = list(summary.coverage)
    details.append(f"Model: `{summary.model_name}`, chosen by this project's maintainers." if summary.model_name else "Model: none configured; checks only.")
    policy = summary.policy
    details.append("Rules: `.stratum/policy.yml` on the default branch." if policy.source == "file" else "Rules: the defaults (no `.stratum/policy.yml`)." if policy.source == "defaults" else "Rules: the defaults, because `.stratum/policy.yml` couldn't be used.")
    details += [f"Policy problem: {e}" for e in policy.errors] + [f"Policy warning: {w}" for w in policy.warnings]
    if summary.muted:
        details.append("Muted by maintainers' 👎: " + ", ".join(f"`{r}`" for r in summary.muted))
    if state.ignored or state.ignored_rules:
        details.append(f"Ignored on this PR: {len(state.ignored)} finding(s)" + (", rules " + ", ".join(f"`{r}`" for r in state.ignored_rules) if state.ignored_rules else "") + ".")
    lines += ["<details><summary>What was reviewed</summary>", ""] + [f"- {d}" for d in details] + ["", "</details>", ""]
    lines.append("<sub>Stratum Open: review and triage on a model the maintainers run. Comment `@stratum help` for commands.</sub>")
    body = "\n".join(lines)
    encoded = state.encode()
    while len(body) + len(encoded) > COMMENT_LIMIT and state.findings:
        state.findings = state.findings[: len(state.findings) // 2]
        encoded = state.encode()
    return body[: COMMENT_LIMIT - len(encoded) - 1] + "\n" + encoded


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
                  name: str = CHECK_NAME) -> None:
    tally = counts(findings)
    title = ", ".join(f"{n} {s}" for s, n in tally.items() if n) or "No findings"
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
