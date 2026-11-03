"""The engine: one call from a diff to ranked findings, a score and a check
conclusion. The Action, the App and the CLI call it through `runner`; Stratum
calls it directly, passing its own settings and hooks.

`analyze()` does, in order:
1. the profile: the policy's defaults filled in for the model (`profiles`);
2. the checks, then the model's review of the files in scope;
3. placing findings on the diff's lines, fingerprinting and de-duplicating;
4. carrying over the last review's findings whose code is still there;
5. dropping excluded findings, then the caller's `weigh`, ranking,
   the score and the conclusion.

Everything an embedder needs to change is an argument; nothing here reads
the environment or calls GitHub.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from stratum_open import __version__
from stratum_open.diff import FileDiff
from stratum_open.findings import Finding, anchor_to_diff, assign_fingerprints, dedupe, rank, risk_score
from stratum_open.policy import Policy
from stratum_open.review import ReviewResult, _locate, review, shown


@dataclass
class EngineResult:
    findings: list[Finding]
    gone: dict[str, str]  # fingerprint -> why a finding from the last review is no longer reported
    excluded: list[Finding]  # findings dropped by exclude_fingerprints / exclude_rules
    review: ReviewResult | None  # the model's part; None when it didn't run
    policy: Policy  # the policy as applied, profile defaults included
    profile: str
    profile_why: str
    score: int
    conclusion: str  # success | neutral | failure, for the check run
    complete: bool  # every model call was read in full
    engine_version: str = __version__


def analyze(
    *,
    title: str,
    description: str,
    files: list[FileDiff],
    policy: Policy,
    model: Any | None,
    contents: dict[str, str] | None = None,
    lookup: Any | None = None,
    only_paths: set[str] | None = None,
    previous: list[dict[str, Any]] | None = None,
    notes: Sequence[str] = (),
    hotspots: set[str] | frozenset[str] = frozenset(),
    guidance: str = "",
    extra_findings: Sequence[Finding] = (),
    exclude_fingerprints: set[str] | frozenset[str] = frozenset(),
    exclude_rules: set[str] | frozenset[str] = frozenset(),
    weigh: Callable[[list[Finding]], tuple[list[Finding], int]] | None = None,
    score: Callable[[list[Finding], int], int] | None = None,
    conclusion: Callable[[list[Finding], int, bool], str] | None = None,
    log: Callable[[str, str], None] | None = None,
) -> EngineResult:
    """Review a diff.

    - `model`: anything with `.name` and `.complete(system, user, *,
      max_tokens, json_mode) -> Reply`; `.describe()` too for `profile: auto`.
      None runs the checks only.
    - `only_paths`: on an incremental review, the files changed since the last
      one; an empty set means nothing new for the model to read.
    - `previous`: the last review's findings (as saved by `state_finding`, with
      their `snippet`); those whose code is still there are carried over.
    - `notes`, `guidance`: context for the prompt (history notes; the team's
      own docs and examples).
    - `extra_findings`: the caller's own findings, merged before de-duplication.
    - `weigh(findings) -> (findings, risk_bonus)`, `score(findings, risk_bonus)`,
      `conclusion(findings, score, complete)`: replace the defaults.
    """
    from stratum_open.checks import run_checks

    policy, profile, profile_why = effective_policy(policy, model)
    registry = lookup if policy.dependencies.registry_lookup else None
    checks = run_checks(files, policy, lookup=registry, contents=contents)

    result: ReviewResult | None = None
    if model is not None and (only_paths is None or only_paths):
        try:
            result = review(
                title=title, description=description, files=files, policy=policy, model=model, only_paths=only_paths,
                context=list(notes), hotspots=set(hotspots), guidance=guidance, log=log,
                previous=still_there(previous or [], files) if only_paths is not None else None,
            )
        except Exception as exc:  # the model step is the one part of a review that may never stop the rest
            # Reported like any other failed call, so the summary says the model didn't answer and why;
            # the traceback rides along for the caller's log and never reaches a comment.
            result = ReviewResult(failed_calls=1, calls=[{
                "model": str(getattr(model, "name", "")), "seconds": 0, "ok": False, "prompt_tokens": None, "output_tokens": None,
                "error": f"the model step failed ({type(exc).__name__}): {exc}", "traceback": traceback.format_exc(),
            }])

    found = anchor_to_diff(checks + list(extra_findings) + (result.findings if result else []), files)
    assign_fingerprints(found, files)
    found = dedupe(found)

    # A model finding stays until the code it's on changes; the model not
    # repeating it on the next push doesn't make it fixed.
    reviewed_now = set(result.packed.reviewed + result.packed.clipped) if result and result.packed and result.ran else set()
    carried, gone = carry(previous or [], files, {f.fingerprint for f in found}, reviewed_now)
    for finding in [f for f in carried if not shown(f, policy)]:  # the policy's bar may have risen since
        carried.remove(finding)
        gone[finding.fingerprint] = f"Hidden: this project shows the model's findings from {policy.review.min_severity} severity up."
    found = dedupe(found + carried)

    excluded = [f for f in found if f.fingerprint in exclude_fingerprints or f.rule in exclude_rules]
    dropped = {id(f) for f in excluded}
    kept = [f for f in found if id(f) not in dropped]
    bonus = 0
    if weigh is not None:
        kept, bonus = weigh(kept)
    kept = rank(kept)

    complete = result.complete if result is not None else True  # no new code to read is a complete review
    total = score(kept, bonus) if score is not None else max(0, min(100, risk_score(kept) + bonus))
    verdict = conclusion(kept, total, complete) if conclusion is not None else default_conclusion(kept, complete, policy, total)
    return EngineResult(kept, gone, excluded, result, policy, profile, profile_why, total, verdict, complete)


def effective_policy(policy: Policy, model: Any | None) -> tuple[Policy, str, str]:
    """The policy with its profile's defaults filled in for this model, the
    profile, and a line saying why. The model's context window follows."""
    from stratum_open.profiles import ModelInfo, apply, choose

    # A model that can't describe itself is treated as small and local, the
    # cheaper guess; embedders set review.profile to say otherwise.
    describe = getattr(model, "describe", None)
    try:
        info = describe() if callable(describe) else ModelInfo()
    except Exception:  # a model that can't be asked is still a model that can be called; guess, as the docstring says
        info = ModelInfo()
    profile = choose(policy, info) if model is not None else "small"
    policy = apply(policy, profile, info)
    if model is not None:
        model.context_tokens = policy.review.context_tokens
    if policy.review.profile != "auto":
        why = "set in the policy"
    elif info.billions is not None:
        why = f"{info.billions:g}B parameters"
    elif info.context is not None:
        why = f"a {info.context:,}-token context window"
    else:
        why = "runs locally" if info.local else "a hosted model of unknown size"
    return policy, profile, why


def default_conclusion(findings: list[Finding], complete: bool, policy: Policy | None = None, score: int | None = None) -> str:
    """With `review.conclusion: severity` (the default): failure for a
    critical finding, neutral for a high one or an incomplete review, success
    otherwise. With `score`: failure at or above `review.fail_at`, neutral at
    or above `review.warn_at` or when incomplete. Make the check required in
    branch protection to block merging on `failure`."""
    if policy is not None and policy.review.conclusion == "score":
        score = risk_score(findings) if score is None else score
        if score >= policy.review.fail_at:
            return "failure"
        return "neutral" if score >= policy.review.warn_at or not complete else "success"
    if any(f.severity == "critical" for f in findings):
        return "failure"
    if any(f.severity == "high" for f in findings) or not complete:
        return "neutral"
    return "success"


def still_there(prior: list[dict], files: list[FileDiff]) -> list[dict]:
    """The prior model findings `carry` will keep whatever the model says:
    anchored ones whose code is still on the new side of the diff. Only these
    may be named to the model as "already found". One whose code was edited,
    or one about a whole file, is dropped when its file is reviewed again, so
    the model must be free to report it again."""
    by_path = {f.path: f for f in files}
    kept = []
    for data in prior:
        file = by_path.get(data.get("path", ""))
        if (data.get("source") == "model" and file is not None and isinstance(data.get("line_start"), int)
                and isinstance(data.get("snippet"), str) and data["snippet"]
                and _locate(data["snippet"], file, near=data["line_start"]) is not None):
            kept.append(data)
    return kept


def carry(prior: list[dict], files: list[FileDiff], current: set[str], reviewed_now: set[str]) -> tuple[list[Finding], dict[str, str]]:
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
        try:
            finding = Finding.from_dict(data)
        except (TypeError, ValueError):  # state written by an older release, or damaged
            continue
        snippet = data.get("snippet") if isinstance(data.get("snippet"), str) else ""
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


def snippet_of(finding: Finding, files: list[FileDiff]) -> str:
    """The code a finding is on, saved with it so the next review can find it."""
    file = next((f for f in files if f.path == finding.path), None)
    return file.snippet(finding.line_start, finding.line_end) if file and finding.line_start else ""
