"""How much a review asks of its model: `small` for 7–14B models on the
maintainers' own hardware, `large` for big hosted ones.

A profile is a set of defaults, not separate code: it fills in the settings a
policy file didn't set, and anything the file sets explicitly wins. Callers
that embed the engine (Stratum) pass their settings explicitly, so changing a
profile's defaults here never changes them.

`review.profile: auto` picks the profile from the model the review runs on:
its size where it's known (from the server, or the "7b" in its name), else its
context window, else whether it runs locally.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Any

from stratum_open.policy import Policy

SMALL_MAX_BILLIONS = 20.0
SMALL_MAX_CONTEXT = 32_000

# Dotted policy keys each profile sets, unless the policy file set them.
PRESETS: dict[str, dict[str, Any]] = {
    "small": {
        "review.diff_format": "interleaved",
        "review.context_tokens": 16384,
        "review.context_lines": 0,
        "checks.bandit_low": False,
        "checks.heuristics": False,
        "review.file_reviews": 0,
        "review.lens_files": 0,
        "review.walkthrough": False,
        "review.parallel_calls": 1,
    },
    "large": {
        "review.diff_format": "split",
        "review.context_tokens": 32768,
        "review.context_lines": 10,
        "checks.bandit_low": True,
        "checks.heuristics": True,
        # About 29 calls a PR: 15 files one at a time, 5 of them again per
        # lens, one synthesis, then the chunked review.
        "review.file_reviews": 15,
        "review.lens_files": 5,
        "review.walkthrough": True,
        "review.parallel_calls": 4,
    },
}

_SIZE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)b(?![a-z])", re.IGNORECASE)


@dataclass(frozen=True)
class ModelInfo:
    billions: float | None = None  # parameters, in billions
    context: int | None = None  # the context window, in tokens
    local: bool = True  # runs on the maintainers' own hardware


def size_from_name(name: str) -> float | None:
    """The parameter count in a model's name: "qwen2.5-coder:7b" is 7,
    "nemotron-3-super-120b-a12b" is 120 (its total, not its active size)."""
    sizes = [float(match) for match in _SIZE.findall(name or "")]
    return max(sizes) if sizes else None


def choose(policy: Policy, info: ModelInfo) -> str:
    """The profile for a review: the policy's, unless it says `auto`."""
    if policy.review.profile != "auto":
        return policy.review.profile
    if info.billions is not None:
        return "small" if info.billions <= SMALL_MAX_BILLIONS else "large"
    if info.context is not None:
        return "small" if info.context < SMALL_MAX_CONTEXT else "large"
    return "small" if info.local else "large"


def apply(policy: Policy, profile: str, info: ModelInfo | None = None) -> Policy:
    """The policy with the profile's defaults filled in where the file was
    silent. The context window never goes past what the model has."""
    preset = dict(PRESETS[profile])
    if info is not None and info.context and "review.context_tokens" in preset:
        preset["review.context_tokens"] = min(preset["review.context_tokens"], info.context)
    for key, value in preset.items():
        if key not in policy.explicit:
            policy = _set(policy, key.split("."), value)
    return policy


def _set(obj: Any, path: list[str], value: Any) -> Any:
    head, rest = path[0], path[1:]
    if not rest:
        return replace(obj, **{head: value})
    return replace(obj, **{head: _set(getattr(obj, head), rest, value)})


def keys_of(cls: type, prefix: str = "") -> set[str]:
    """Every dotted settings key a policy dataclass has (used by the tests to
    check the presets name real keys)."""
    out = set()
    for spec in fields(cls):
        default = getattr(cls(), spec.name)
        if is_dataclass(default):
            out |= keys_of(type(default), f"{prefix}{spec.name}.")
        else:
            out.add(f"{prefix}{spec.name}")
    return out
