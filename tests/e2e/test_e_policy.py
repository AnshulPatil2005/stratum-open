"""E. The policy file: whatever is in `.stratum/policy.yml`, the review runs
with usable rules and says which ones."""

from __future__ import annotations

import pytest

from stratum_open.github import publish
from stratum_open.policy import POLICY_PATH, Policy, parse_policy
from stratum_open.runner import review_pull

import diffs
from conftest import DIFF, head_of
from contract import assert_contract, assert_idempotent
from fake_model import Response, finding, json_reply

HEAD = head_of(7)


@pytest.mark.parametrize("text, source", [
    (None, "defaults"), ("", "file"), ("# only comments\n# version: 2\n", "file"), ("version: 1\n", "file"),
    ("review: [not, a, mapping]\n", "defaults (file unusable)"), ("- a list\n", "defaults (file unusable)"), ("just text", "defaults (file unusable)"),
    ("version: 1\nreview:\n  min_severity: : :\n", "defaults (file unusable)"), ("review:\n  max_chunks: null\n", "defaults (file unusable)"),
    ("review:\n  min_severity: null\n", "defaults (file unusable)"), ("triage:\n  enabled: 'yes'\n", "defaults (file unusable)"),
    ("version: 2\n", "defaults (file unusable)"), ("unknown_key: 1\nreview:\n  typo_key: 2\n", "file"),
    ("\ufeffversion: 1\ntriage:\n  enabled: true\n", "file"), ("review:\n  rules: [1, 2]\n", "defaults (file unusable)"),
    ("boundaries:\n  modules: {a: ['x/**']}\n  forbid: ['a -> b']\n", "defaults (file unusable)"),
    ("boundaries:\n  modules: {a: ['x/**']}\n  forbid: ['a -> a']\n", "file"),
    ("boundaries:\n  modules: {a: ['x/**'], b: ['y/**']}\n  forbid: ['a -> b', 'b -> a']\n", "file"),
    ("boundaries:\n  modules: {a: ['nothing/matches/this/**']}\n  forbid: []\n", "file"),
    ("boundaries:\n  forbid: ['a b c']\n", "defaults (file unusable)"),
    ("always_human:\n  paths: ['/abs/**', 'abs/**', 'abs/x/**']\n", "file"),
    ("services:\n  web: {calls: [api]}\n  api: 7\n", "file"),
], ids=["no-file", "empty", "comments", "version-only", "wrong-type", "list", "text", "invalid-yaml", "null-int", "null-str", "string-bool", "version-2",
        "unknown-keys", "bom", "rules-not-text", "forbid-unknown-module", "self-reference", "cycle", "glob-matches-nothing", "bad-forbid", "overlapping-paths",
        "hosted-services"])
def test_every_policy_shape_gives_a_usable_policy(text, source):
    policy = parse_policy(text)
    assert isinstance(policy, Policy) and policy.source == source
    if source.endswith("unusable)"):
        assert policy.errors and all(error.endswith(".") for error in policy.errors)
        assert policy.review == Policy().review
    if text and "unknown_key" in text:
        assert len(policy.warnings) == 2
    if text and text.startswith("services:"):  # hosted Stratum's section: no warning here
        assert policy.warnings == ()
    assert isinstance(policy.ignored("any/path.py"), bool)  # the globs compile and answer
    assert isinstance(policy.needs_human("abs/x/y.py"), bool)


@pytest.mark.parametrize("level", ["low", "medium", "high", "critical"])
def test_min_severity_at_every_level(level):
    assert parse_policy(f"review:\n  min_severity: {level}\n").review.min_severity == level


def test_min_severity_invalid_falls_back_and_says_why():
    policy = parse_policy("review:\n  min_severity: urgent\n")
    assert list(policy.errors) == ["review.min_severity must be one of: low, medium, high, critical."]


@pytest.mark.parametrize("key, value, ok", [
    ("review.context_tokens", 2048, True), ("review.context_tokens", 2047, False), ("review.max_chunks", 1, True), ("review.max_chunks", 0, False),
    ("review.max_inline_comments", 0, True), ("review.max_inline_comments", -1, False), ("track_record.window_days", 1, True),
    ("track_record.window_days", 0, False), ("dependencies.min_package_age_days", 0, True), ("data.findings_retention_days", 1, True),
    ("data.findings_retention_days", 0, False), ("review.context_tokens", 10**12, True), ("review.context_tokens", True, False),
])
def test_integers_at_their_boundaries(key, value, ok):
    section, name = key.split(".")
    policy = parse_policy(f"{section}:\n  {name}: {str(value).lower() if isinstance(value, bool) else value}\n")
    assert (not policy.errors) is ok, policy.errors


def test_huge_and_non_utf8_policy_files():
    huge = "version: 1\n" + "# padding\n" * 200_000 + "triage:\n  enabled: true\n"
    assert parse_policy(huge).triage.enabled is True
    garbage = b"version: 1\nreview:\n  rules: ['caf\xe9 \xff\xfe']\n".decode("utf-8", errors="replace")
    assert parse_policy(garbage).review.rules[0].startswith("caf")


def test_policy_edited_in_the_pr_is_ignored_and_the_merged_one_wins(fake_github, make_context, fake_model, logs):
    fake_github.set_policy("triage:\n  enabled: false\nreview:\n  min_severity: critical\n")
    diff = diffs.modified(POLICY_PATH, ["  enabled: false"], ["  enabled: true", "  close_without_issue: true", "  require_linked_issue: true"]) + DIFF
    fake_github.add_pull(7, diff=diff)
    from stratum_open.runner import load_context_policy
    from stratum_open.github.api import GitHub
    gh = GitHub(fake_github.token, fake_github.repo, api=fake_github.url)
    policy = load_context_policy(gh, fake_github.default_branch)
    assert policy.triage.enabled is False and policy.review.min_severity == "critical"
    ctx = make_context(policy_text=None)
    ctx.policy = policy
    outcome = review_pull(ctx, 7)
    assert fake_github.labels_on(7) == [] and fake_github.closed == []
    assert any(f.rule == "paths/always-human" and f.path == POLICY_PATH for f in outcome.findings)
    assert_contract(fake_github, number=7, head=HEAD, logs=logs)


def test_a_broken_policy_falls_back_to_defaults_and_says_why_in_the_review(fake_github, make_context, logs):
    fake_github.add_pull(7, diff=DIFF)
    outcome = review_pull(make_context(policy_text="review:\n  model: cloud\n  min_severity: urgent\n"), 7)
    body = fake_github.summary_comments(7)[0]["body"]
    assert "Rules: the defaults, because `.stratum/policy.yml` couldn't be used." in body
    assert "Policy problem: review.model must be one of: local, hosted." in body
    assert_contract(fake_github, number=7, head=HEAD, logs=logs)


@pytest.mark.parametrize("enabled", [False, True])
def test_triage_enabled_posts_exactly_one_label_or_none(fake_github, make_context, logs, enabled):
    fake_github.add_pull(7, diff=DIFF, labels=["triage: ready"] if enabled else [])
    review_pull(make_context(policy_text=f"triage:\n  enabled: {str(enabled).lower()}\n"), 7)
    labels = [name for name in fake_github.labels_on(7) if name.startswith("triage:")]
    assert len(labels) == (1 if enabled else 0)
    if enabled:
        assert labels == ["triage: needs work"]  # the stale "ready" label went
    assert_contract(fake_github, number=7, head=HEAD, logs=logs)


def test_quiet_keeps_the_checks_and_the_model_apart_and_a_second_run_posts_nothing_new(fake_github, make_context, fake_model, logs):
    fake_github.add_pull(7, diff=DIFF)
    fake_model.queue(Response.ok(json_reply([
        finding("app/users.py", "return cur.fetchone()", line=6),
        finding("app/users.py", 'print("unreachable")', line=7, title="Potential dead code", detail="This line may never run.", severity="medium"),
    ])))
    ctx = make_context(policy_text="review:\n  quiet: true\n")
    outcome = review_pull(ctx, 7)
    body = fake_github.summary_comments(7)[0]["body"]
    assert [f.title for f in outcome.findings if f.source == "model"] == ["Unknown id"]  # a guess isn't a finding
    assert any(f.source == "check" for f in outcome.findings)  # the SQL built from a string
    assert "**Found by the checks:** " in body and "**Found by the checks:** nothing." not in body
    assert "**Suggested by the model:** 1 high. 1 on the changed lines below." in body
    assert "**Findings:**" not in body and "Potential dead code" not in fake_github.posted_text()
    assert publish.State.decode(body).head == HEAD  # the next run finds this one's state where it always was
    assert_contract(fake_github, number=7, head=HEAD, logs=logs)
    assert_idempotent(fake_github, 7, lambda: review_pull(ctx, 7))
