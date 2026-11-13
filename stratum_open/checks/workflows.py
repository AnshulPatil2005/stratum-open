"""GitHub Actions changes that open a way in: `pull_request_target` with the
PR's own code checked out, script injection from issue and PR text, broad
token permissions, actions pinned to a tag their owner can move, and
self-hosted runners on pull requests.

The YAML is read line by line rather than parsed, since a diff shows only
parts of the file. With the file's full content (`contents`) the rules see all
of it. Without it they see what the diff shows, so a `run: |` block whose
first line is outside the diff's context isn't known to be a script, and a
trigger that isn't shown isn't known either.

A file moved here from a path that isn't a workflow or an action is checked
in full, since every line of it is new to where it now runs.
"""

from __future__ import annotations

import re

from stratum_open.checks.common import checked_lines, view
from stratum_open.diff import FileDiff
from stratum_open.findings import Finding

_WORKFLOW = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")  # GitHub runs only these
_ACTION = re.compile(r"(?:^|/)action\.ya?ml$")


def scope(path: str) -> frozenset[str]:
    """What this check reads in a file at `path`: an action's steps, or a
    workflow's steps and triggers."""
    if _WORKFLOW.match(path):
        return frozenset({"steps", "triggers"})
    return frozenset({"steps"}) if _ACTION.search(path) else frozenset()


def check(files: list[FileDiff], contents: dict[str, str] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for file in files:
        if not scope(file.path):
            continue
        added = checked_lines(file, contents, scope)
        if added:
            findings.extend(_check_file(file, contents, bool(_WORKFLOW.match(file.path)), added))
    return findings


def _check_file(file: FileDiff, contents: dict[str, str] | None, workflow: bool, added_lines: list[tuple[int, str]]) -> list[Finding]:
    lines = view(file, contents)
    added = {number for number, _ in added_lines}
    findings: list[Finding] = []
    if workflow:
        findings.extend(_pull_request_target(file.path, lines, added))
    scripts = _script_lines(lines)
    pr_triggered = workflow and any(_triggers(text) for _, text in lines)
    for number, text in added_lines:
        if _yaml_comment(text) and number not in scripts:
            continue
        findings.extend(_permissions(file.path, number, text))
        uses = _USES.match(text)
        if uses:
            findings.extend(_unpinned(file.path, number, uses.group("target")))
        if number in scripts:
            findings.extend(_injection(file.path, number, text))
        if pr_triggered and _SELF_HOSTED.search(text):
            findings.append(_self_hosted(file.path, number))
    return findings


def _finding(path: str, number: int | None, rule: str, title: str, detail: str, suggestion: str,
             severity: str, needs_human: bool = False, category: str = "security") -> Finding:
    return Finding(
        title=title, detail=detail, severity=severity, category=category, path=path,
        line_start=number, line_end=number, suggestion=suggestion, source="check", rule=rule,
        confidence=0.9, needs_human=needs_human,
    )


def _yaml_comment(text: str) -> bool:
    return text.lstrip().startswith("#")


def _strip_comment(text: str) -> str:
    return re.sub(r"\s+#.*$", "", text)


# ── pull_request_target ──────────────────────────────────────────────────

_ON = re.compile(r"""^\s*["']?on["']?\s*:(?P<rest>.*)$""")
_TRIGGER_KEY = re.compile(
    r"""^\s*(?:-\s+)?["']?(?P<name>pull_request(?:_target)?)["']?\s*(?::\s*(?:\{.*|null|~)?)?\s*$"""
)
_HEAD = (
    r"github\.event\.pull_request\.head\.(?:sha|ref|repo\.full_name)|github\.head_ref"
    r"|github\.event\.pull_request\.(?:number|merge_commit_sha)|github\.event\.number|refs/pull/"
)
# Checking out the PR's code: a `ref:`/`repository:` input naming its head,
# or a git command fetching it. A bare `github.head_ref` elsewhere (often in
# `concurrency:`) isn't a checkout.
_HEAD_CHECKOUT = re.compile(
    rf"^\s*(?:-\s+)?(?:ref|repository)\s*:.*(?:{_HEAD})"
    rf"|\bgit\s+(?:checkout|fetch|switch|pull)\b.*(?:{_HEAD})|\bgh\s+pr\s+checkout\b"
)


def _triggers(text: str) -> set[str]:
    """The pull request triggers a line of `on:` names, if it is one."""
    if _yaml_comment(text):
        return set()
    text = _strip_comment(text)
    on = _ON.match(text)
    if on:
        return set(re.findall(r"\bpull_request(?:_target)?\b", on.group("rest")))
    key = _TRIGGER_KEY.match(text)
    return {key.group("name")} if key else set()


def _pull_request_target(path: str, lines: list[tuple[int, str]], added: set[int]) -> list[Finding]:
    targets = [number for number, text in lines if "pull_request_target" in _triggers(text)]
    if not targets:
        return []
    checkouts = [number for number, text in lines if not _yaml_comment(text) and _HEAD_CHECKOUT.search(text)]
    new_targets = [n for n in targets if n in added]
    new_checkouts = [n for n in checkouts if n in added]
    if checkouts and (new_targets or new_checkouts):
        anchor = (new_checkouts or new_targets)[0]
        return [_finding(
            path, anchor, "workflow/pwn-request",
            title="pull_request_target runs the PR's own code",
            detail=(
                "This workflow runs on `pull_request_target`, so it gets a write token and the repo's secrets "
                f"even for pull requests from forks, and line {checkouts[0]} checks out the pull request's own "
                "code. Anything in that code (a build script, a test, an install hook) then runs with those "
                "secrets, so anyone who opens a PR can take them. This is known as a \"pwn request\"."
            ),
            suggestion=(
                "Build and test PR code in a separate `pull_request` workflow with no secrets. If this one needs "
                "the results, pass them as an artifact and treat them as untrusted."
            ),
            severity="critical", needs_human=True,
        )]
    if not new_targets:
        return []
    return [_finding(
        path, new_targets[0], "workflow/pull-request-target",
        title="Workflow now runs on pull_request_target",
        detail=(
            "`pull_request_target` gives runs for pull requests from forks a write token and the repo's "
            "secrets. That's safe only while the workflow never checks out, builds or runs code from the PR."
        ),
        suggestion=(
            "Use `pull_request` unless the job needs secrets or write access. If it does, keep the PR's code "
            "out of it: no checkout of the head, no installs or builds from it."
        ),
        severity="high", needs_human=True,
    )]


# ── permissions ──────────────────────────────────────────────────────────

_WRITE_ALL = re.compile(r"\bpermissions\s*:\s*[\"']?write-all\b")
_SCOPES = (
    "actions", "attestations", "checks", "contents", "deployments", "discussions", "id-token", "issues",
    "models", "packages", "pages", "pull-requests", "repository-projects", "security-events", "statuses",
)
_WRITE_SCOPE = re.compile(rf"(?<![\w-])({'|'.join(map(re.escape, _SCOPES))})\s*:\s*[\"']?write\b")


def _permissions(path: str, number: int, text: str) -> list[Finding]:
    if _WRITE_ALL.search(text):
        return [_finding(
            path, number, "workflow/write-all",
            title="Workflow token can write to everything",
            detail=(
                "`permissions: write-all` gives this workflow's token write access to everything it can reach in "
                "the repo: code, releases, packages, issues. Any step, including third-party actions, can use it."
            ),
            suggestion="Grant only the scopes the jobs need, for example `permissions: { contents: read }`.",
            severity="high", needs_human=True,
        )]
    scopes = _WRITE_SCOPE.findall(text)
    if not scopes:
        return []
    named = ", ".join(f"`{scope}: write`" for scope in scopes)
    return [_finding(
        path, number, "workflow/write-permission",
        title="Workflow token gets write access",
        detail=f"This gives the workflow's token {named}. Any step in the job, including third-party actions, can use that access.",
        suggestion="Keep it if a job needs it, but grant it on that job only, and use `read` everywhere else.",
        severity="medium", needs_human=True,
    )]


# ── actions pinned to something movable ──────────────────────────────────

_USES = re.compile(r"""^\s*(?:-\s+)?uses\s*:\s*["']?(?P<target>[^\s"'#]+)""")
_SHA = re.compile(r"[0-9a-fA-F]{40}")


def _unpinned(path: str, number: int, target: str) -> list[Finding]:
    if target.startswith(("./", "../")) or "${{" in target:
        return []
    if target.startswith("docker://"):
        if "@sha256:" in target:
            return []
        what, first_party = f"the Docker image `{target[len('docker://'):]}` by tag", False
    else:
        if "@" not in target:
            return []
        action, ref = target.rsplit("@", 1)
        if _SHA.fullmatch(ref):
            return []
        first_party = action.split("/", 1)[0].lower() in ("actions", "github")
        what = f"`{action}` at `{ref}`, a tag or branch"
    detail = (
        f"This runs {what}, which its owner can point at different code at any time. "
        "The workflow would then run that code with its token and secrets."
    )
    if first_party:
        detail += " GitHub maintains this one, so the risk is lower, but a pinned SHA still protects against a moved tag."
    return [_finding(
        path, number, "workflow/unpinned-action",
        title="Action not pinned to a commit",
        detail=detail,
        suggestion=(
            "Pin the full 40-character commit SHA and keep the version in a comment, like "
            "`uses: owner/repo@<sha> # v4`. Dependabot and Renovate keep pinned SHAs up to date."
            if not target.startswith("docker://") else "Pin the image by digest: `docker://image@sha256:<digest>`."
        ),
        severity="low" if first_party else "medium", category="supply-chain",
    )]


# ── script injection ─────────────────────────────────────────────────────

_RUN_BLOCK = re.compile(r"^\s*(?:-\s+)?(?P<key>run|script)\s*:\s*(?:[|>][-+0-9]*)?\s*(?:#.*)?$")
_RUN_INLINE = re.compile(r"^\s*(?:-\s+)?(?:run|script)\s*:\s*(?![|>])\S")
_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}")
# Text anyone can set by opening an issue or a PR, or pushing a branch.
_UNTRUSTED = re.compile(
    r"\bgithub\.event\.(?:issue|pull_request|comment|review|review_comment|head_commit|commits|pages|discussion"
    r"|discussion_comment|workflow_run)(?:\.[\w-]+|\[[^\]]*\]|\.\*)*?"
    r"\.(?:title|body|message|name|label|ref|email|head_ref|page_name|head_branch|display_title|default_branch)(?![\w.-])"
    r"|\bgithub\.head_ref\b"
)
_BASE_REPO = re.compile(r"\.(?:base|repository)\.")


def _script_lines(lines: list[tuple[int, str]]) -> set[int]:
    """Lines that are script text: an inline `run: cmd`, or a line inside a
    `run: |` block. `script:` (github-script, ssh actions) counts too."""
    inside: set[int] = set()
    block_column: int | None = None
    previous: int | None = None
    for number, text in lines:
        if previous is not None and number != previous + 1:
            block_column = None  # a gap in what the diff shows
        previous = number
        if block_column is not None:
            if not text.strip() or len(text) - len(text.lstrip()) > block_column:
                inside.add(number)
                continue
            block_column = None
        block = _RUN_BLOCK.match(text)
        if block:
            block_column = block.start("key")
        elif _RUN_INLINE.match(text):
            inside.add(number)
    return inside


def _injection(path: str, number: int, text: str) -> list[Finding]:
    untrusted = []
    for expression in _EXPRESSION.finditer(text):
        match = _UNTRUSTED.search(expression.group(1))
        # `.base.` and `.repository.` (not `.head_repository.`) name the base repo, which its maintainers control.
        if match and not _BASE_REPO.search(match.group(0)):
            untrusted.append(match.group(0))
    if not untrusted:
        return []
    shown = untrusted[0]
    return [_finding(
        path, number, "workflow/script-injection",
        title="Untrusted text pasted into a script",
        detail=(
            f"This puts `${{{{ {shown} }}}}` straight into a script. Whoever opens the issue or PR, or names the "
            "branch, controls that text, and GitHub pastes it in before the script runs, so something like "
            "`$(...)` in it runs as a command with this workflow's token and secrets."
        ),
        suggestion=(
            f"Pass it through an environment variable and quote it: `env: VALUE: ${{{{ {shown} }}}}` on the "
            'step, then `"$VALUE"` in the script.'
        ),
        severity="high",
    )]


# ── self-hosted runners ──────────────────────────────────────────────────

_SELF_HOSTED = re.compile(
    r"^\s*(?:-\s+)?runs-on\s*:.*\bself-hosted\b|^\s*labels\s*:.*\bself-hosted\b|^\s*-\s+[\"']?self-hosted[\"']?\s*$"
)


def _self_hosted(path: str, number: int) -> Finding:
    return _finding(
        path, number, "workflow/self-hosted-pr",
        title="Self-hosted runner on pull requests",
        detail=(
            "This job runs on a self-hosted runner, and the workflow runs on pull requests. On a public repo a "
            "PR from a fork can run anything on that machine and leave something behind for later jobs."
        ),
        suggestion=(
            "Use GitHub-hosted runners for pull request jobs, or require approval for fork PRs and use "
            "ephemeral self-hosted runners."
        ),
        severity="medium",
    )
