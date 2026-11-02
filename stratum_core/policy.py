"""The maintainers' rules: `.stratum/policy.yml`, read from the default branch.

A PR is always judged by the rules already on the default branch, never by a
policy it edits itself; its change applies once merged. No file means the
defaults below. A file that can't be used is never ignored silently: the run
falls back to the defaults and `Policy.errors` / `Policy.warnings` say why, for
the check run to show.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, is_dataclass, replace
from typing import Any, Callable

import yaml

POLICY_PATH = ".stratum/policy.yml"
VERSION = 1


@dataclass(frozen=True)
class Review:
    model: str = "local"  # local | hosted
    endpoint: str = "http://localhost:11434/v1"  # any OpenAI-compatible server; this is Ollama's
    model_name: str = ""  # empty = the model the benchmark picked
    max_inline_comments: int = 12
    log_prompts: bool = False


@dataclass(frozen=True)
class AlwaysHuman:
    paths: tuple[str, ...] = (".github/workflows/**", ".stratum/**")
    new_dependencies: bool = True


@dataclass(frozen=True)
class Labels:
    ready: str = "triage: ready"
    needs_work: str = "triage: needs work"
    low_effort: str = "triage: low effort"
    needs_human: str = "triage: needs a maintainer"


@dataclass(frozen=True)
class Triage:
    enabled: bool = False  # labels and closing happen only when true
    labels: Labels = field(default_factory=Labels)
    label_low_effort: bool = True
    require_linked_issue: bool = False
    close_without_issue: bool = False


@dataclass(frozen=True)
class TrackRecord:
    enabled: bool = True
    affects_triage: bool = False
    window_days: int = 180
    followup_days: int = 14
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class Dependencies:
    registry_lookup: bool = True
    min_package_age_days: int = 30


@dataclass(frozen=True)
class Data:
    findings_retention_days: int = 180


@dataclass(frozen=True)
class Policy:
    version: int = VERSION
    review: Review = field(default_factory=Review)
    always_human: AlwaysHuman = field(default_factory=AlwaysHuman)
    triage: Triage = field(default_factory=Triage)
    track_record: TrackRecord = field(default_factory=TrackRecord)
    dependencies: Dependencies = field(default_factory=Dependencies)
    data: Data = field(default_factory=Data)
    source: str = "defaults"  # "defaults" | "file" | "defaults (file unusable)"
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def needs_human(self, path: str) -> bool:
        """Whether a changed file falls under `always_human.paths`."""
        return any(_glob(pattern).fullmatch(path.lstrip("/")) for pattern in self.always_human.paths)


# Values that are checked beyond their type.
_CHOICES = {("review", "model"): ("local", "hosted")}
_MINIMUMS = {
    ("review", "max_inline_comments"): 0,
    ("track_record", "window_days"): 1,
    ("track_record", "followup_days"): 1,
    ("dependencies", "min_package_age_days"): 0,
    ("data", "findings_retention_days"): 1,
}
_META = {"source", "warnings", "errors"}


def parse_policy(text: str | None) -> Policy:
    """The policy for this file's text (None = no file)."""
    if text is None:
        return Policy()
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return _unusable(f"{POLICY_PATH} isn't valid YAML ({_yaml_problem(exc)}).")
    if raw is None:
        return Policy(source="file")
    if not isinstance(raw, dict):
        return _unusable(f"{POLICY_PATH} must be a mapping of keys, not a {type(raw).__name__}.")
    if raw.get("version", VERSION) != VERSION:
        return _unusable(f"{POLICY_PATH} has version {raw.get('version')!r}; this release reads version {VERSION}.")

    warnings: list[str] = []
    errors: list[str] = []
    policy = _build(Policy, raw, (), warnings, errors)
    if errors:
        return _unusable(*errors, warnings=warnings)
    return replace(policy, source="file", warnings=tuple(warnings))


def load_policy(read_default_branch_file: Callable[[str], str | None]) -> Policy:
    """`read_default_branch_file(path)` returns a file's text on the default
    branch, or None when it doesn't exist. Never pass the PR's head here."""
    return parse_policy(read_default_branch_file(POLICY_PATH))


def _unusable(*errors: str, warnings: list[str] | None = None) -> Policy:
    return Policy(source="defaults (file unusable)", errors=tuple(errors), warnings=tuple(warnings or ()))


def _build(cls: type, raw: dict, path: tuple[str, ...], warnings: list[str], errors: list[str]) -> Any:
    known = {f.name: f for f in fields(cls) if f.name not in _META}
    for key in raw:
        if key not in known:
            warnings.append(f"Unknown key {_dotted(path + (str(key),))} is ignored.")
    values: dict[str, Any] = {}
    defaults = cls()
    for name, spec in known.items():
        if name not in raw:
            continue
        key_path = path + (name,)
        default = getattr(defaults, name)
        value = raw[name]
        if is_dataclass(default):
            if not isinstance(value, dict):
                errors.append(f"{_dotted(key_path)} must be a mapping.")
                continue
            values[name] = _build(type(default), value, key_path, warnings, errors)
        else:
            checked = _check(key_path, value, default, errors)
            if checked is not _INVALID:
                values[name] = checked
    return replace(defaults, **values) if values else defaults


_INVALID = object()


def _check(path: tuple[str, ...], value: Any, default: Any, errors: list[str]) -> Any:
    name = _dotted(path)
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        errors.append(f"{name} must be true or false.")
    elif isinstance(default, int):
        if isinstance(value, int) and not isinstance(value, bool):
            minimum = _MINIMUMS.get(path)
            if minimum is not None and value < minimum:
                errors.append(f"{name} must be at least {minimum}.")
            else:
                return value
        else:
            errors.append(f"{name} must be a whole number.")
    elif isinstance(default, str):
        if isinstance(value, str):
            choices = _CHOICES.get(path)
            if choices and value not in choices:
                errors.append(f"{name} must be one of: {', '.join(choices)}.")
            else:
                return value
        else:
            errors.append(f"{name} must be text.")
    elif isinstance(default, tuple):
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return tuple(value)
        errors.append(f"{name} must be a list of text values.")
    return _INVALID


def _dotted(path: tuple[str, ...]) -> str:
    return ".".join(path)


def _yaml_problem(exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    return f"line {mark.line + 1}" if mark is not None else "unreadable"


_GLOB_CACHE: dict[str, re.Pattern] = {}


def _glob(pattern: str) -> re.Pattern:
    """`**` crosses folders, `*` and `?` stay inside one, like .gitignore."""
    compiled = _GLOB_CACHE.get(pattern)
    if compiled is None:
        out, i = [], 0
        pattern = pattern.lstrip("/")
        while i < len(pattern):
            if pattern.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
            elif pattern.startswith("**", i):
                out.append(".*")
                i += 2
            elif pattern[i] == "*":
                out.append("[^/]*")
                i += 1
            elif pattern[i] == "?":
                out.append("[^/]")
                i += 1
            else:
                out.append(re.escape(pattern[i]))
                i += 1
        compiled = _GLOB_CACHE[pattern] = re.compile("".join(out))
    return compiled
