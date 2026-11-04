from dataclasses import asdict, replace
from pathlib import Path

import pytest
import yaml

from stratum_open.policy import POLICY_PATH, Policy, forbidden_pairs, load_policy, parse_policy


def test_no_file_means_defaults():
    policy = parse_policy(None)
    assert policy == Policy()
    assert policy.source == "defaults"
    assert policy.review.model == "local"
    assert policy.triage.enabled is False
    assert policy.track_record.affects_triage is False
    assert policy.triage.labels.needs_human == "triage: needs a maintainer"


def test_the_example_file_lists_every_key_with_its_default():
    example = (Path(__file__).parent.parent / "policy.example.yml").read_text(encoding="utf-8")
    parsed = parse_policy(example)
    assert replace(parsed, explicit=frozenset()) == replace(Policy(), source="file")

    def keys(value, prefix=""):
        if not isinstance(value, dict):
            return set()
        return {prefix + k for k in value} | {key for k, v in value.items() for key in keys(v, f"{prefix}{k}.")}

    expected = {k: v for k, v in asdict(Policy()).items() if k not in ("source", "warnings", "errors", "explicit")}
    assert keys(yaml.safe_load(example)) == keys(expected)


def test_the_file_records_which_keys_it_set():
    policy = parse_policy("review:\n  diff_format: split\ncommands:\n  explain: write\n")
    assert policy.explicit == frozenset({"review.diff_format", "commands.explain"})
    assert parse_policy("review:\n  events: [opened, closed]\n").errors == (
        "review.events can't include 'closed'; it can include: opened, synchronize, reopened, ready_for_review.",
    )
    assert parse_policy("review:\n  fail_at: 40\n  warn_at: 60\n").errors == ("review.warn_at must not be above review.fail_at.",)
    assert parse_policy("commands:\n  review: everyone\n").errors == ("commands.review must be one of: anyone, author, write.",)


def test_empty_file_means_defaults_from_the_file():
    policy = parse_policy("")
    assert policy.source == "file"
    assert policy.errors == policy.warnings == ()


def test_values_override_only_what_they_name():
    policy = parse_policy(
        """
version: 1
review:
  max_inline_comments: 5
triage:
  labels:
    ready: "good to go"
track_record:
  exclude: [dependabot, renovate]
"""
    )
    assert policy.source == "file"
    assert policy.review.max_inline_comments == 5
    assert policy.review.endpoint == "http://localhost:11434/v1"
    assert policy.triage.labels.ready == "good to go"
    assert policy.triage.labels.needs_work == "triage: needs work"
    assert policy.track_record.exclude == ("dependabot", "renovate")


def test_unknown_keys_are_warnings_not_errors():
    policy = parse_policy("review:\n  modle: hosted\nextra: 1\n")
    assert policy.source == "file"
    assert policy.warnings == ("Unknown key extra is ignored.", "Unknown key review.modle is ignored.")


@pytest.mark.parametrize(
    "text, error",
    [
        ("review: [1]", "review must be a mapping."),
        ("review:\n  model: cloud", "review.model must be one of: local, hosted."),
        ("review:\n  log_prompts: 1", "review.log_prompts must be true or false."),
        ("review:\n  max_inline_comments: -1", "review.max_inline_comments must be at least 0."),
        ("review:\n  max_inline_comments: true", "review.max_inline_comments must be a whole number."),
        ("review:\n  endpoint: 8080", "review.endpoint must be text."),
        ("track_record:\n  followup_days: 0", "track_record.followup_days must be at least 1."),
        ("always_human:\n  paths: .github/**", "always_human.paths must be a list of text values."),
        ("version: 2", f"{POLICY_PATH} has version 2; this release reads version 1."),
        ("- a\n- b", f"{POLICY_PATH} must be a mapping of keys, not a list."),
        ("review:\n  model: [", f"{POLICY_PATH} isn't valid YAML (line 2)."),
    ],
)
def test_an_unusable_file_falls_back_to_defaults_and_says_why(text, error):
    policy = parse_policy(text)
    assert policy.source == "defaults (file unusable)"
    assert policy.errors == (error,)
    assert policy.review == Policy().review


def test_boundaries():
    policy = parse_policy("boundaries:\n  modules: {api: ['server/api/**'], db: ['server/db/**']}\n  forbid: ['api -> db']\n")
    assert policy.boundaries.modules == (("api", ("server/api/**",)), ("db", ("server/db/**",)))
    assert forbidden_pairs(policy.boundaries) == [("api", "db")]

    assert parse_policy("boundaries:\n  forbid: ['api to db']\n").errors == ('boundaries.forbid entry "api to db" must look like "a -> b", or "a -> b: why".',)
    assert parse_policy("boundaries:\n  modules: {api: ['x/**']}\n  forbid: ['api -> db']\n").errors == (
        "boundaries.forbid entry \"api -> db\" names db, which boundaries.modules doesn't define.",
    )
    assert parse_policy("boundaries:\n  modules: {api: x}\n").errors == ("boundaries.modules must map names to lists of text values.",)


def test_one_bad_value_rejects_the_whole_file():
    policy = parse_policy("review:\n  max_inline_comments: 3\n  model: cloud\n")
    assert policy.review.max_inline_comments == 12


def test_load_policy_reads_only_the_policy_path():
    asked = []

    def read(path):
        asked.append(path)
        return "review:\n  model: hosted\n"

    assert load_policy(read).review.model == "hosted"
    assert asked == [POLICY_PATH]
    assert load_policy(lambda path: None).source == "defaults"


@pytest.mark.parametrize(
    "path, expected",
    [
        (".github/workflows/ci.yml", True),
        (".github/workflows/nested/ci.yml", True),
        ("/.github/workflows/ci.yml", True),
        (".github/dependabot.yml", False),
        (".stratum/policy.yml", True),
        ("src/.stratum/policy.yml", False),
    ],
)
def test_needs_human_default_paths(path, expected):
    assert Policy().needs_human(path) is expected


def test_glob_rules():
    policy = parse_policy("always_human:\n  paths: ['**/*.lock', '*.md', 'docs/?.txt']\n")
    assert policy.needs_human("poetry.lock")
    assert policy.needs_human("a/b/Cargo.lock")
    assert policy.needs_human("README.md")
    assert not policy.needs_human("docs/README.md")
    assert policy.needs_human("docs/a.txt")
    assert not policy.needs_human("docs/ab.txt")


def test_profiles_fill_in_only_what_the_file_left_out():
    from stratum_open.profiles import PRESETS, ModelInfo, apply, choose, keys_of, size_from_name

    for preset in PRESETS.values():
        assert set(preset) <= keys_of(Policy)  # presets only name real keys
    assert size_from_name("qwen2.5-coder:7b") == 7
    assert size_from_name("nvidia/nemotron-3-super-120b-a12b:free") == 120
    assert size_from_name("north-mini-code") is None
    policy = parse_policy(None)
    assert choose(policy, ModelInfo(billions=7.6)) == "small"
    assert choose(policy, ModelInfo(billions=31)) == "large"
    assert choose(policy, ModelInfo(context=8192, local=False)) == "small"
    assert choose(policy, ModelInfo(local=False)) == "large"
    assert choose(policy, ModelInfo(local=True)) == "small"
    assert choose(parse_policy("review:\n  profile: small\n"), ModelInfo(billions=405)) == "small"

    large = apply(policy, "large", ModelInfo(context=20_000))
    assert (large.review.diff_format, large.checks.bandit_low, large.review.context_tokens) == ("split", True, 20_000)
    pinned = apply(parse_policy("review:\n  diff_format: interleaved\n"), "large")
    assert pinned.review.diff_format == "interleaved" and pinned.checks.bandit_low is True


def test_score_based_conclusions():
    from stratum_open.findings import Finding
    from stratum_open.github.publish import conclusion

    scored = parse_policy("review:\n  conclusion: score\n  fail_at: 70\n  warn_at: 50\n")
    high = [Finding("t", "d", "high")]
    assert conclusion(high, True) == "neutral"  # by severity: high is neutral
    assert conclusion(high, True, scored, score=75) == "failure"
    assert conclusion(high, True, scored, score=55) == "neutral"
    assert conclusion([], True, scored, score=10) == "success"
    assert conclusion([], False, scored, score=10) == "neutral"
