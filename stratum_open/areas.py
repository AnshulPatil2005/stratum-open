"""The part of a project a changed file is in, from its path alone: the areas
where a small mistake costs the most. The `large` profile reviews these files
first and tells the model why each one matters."""

from __future__ import annotations

# Each area's markers, matched anywhere in the lowercased path. A marker that
# starts with "/" must start a folder or file name.
AREAS: dict[str, tuple[str, ...]] = {
    "auth": ("auth", "login", "permission", "policy", "jwt", "token"),
    "config": ("config", ".env", "settings", "docker", "compose", "nginx", "vercel"),
    "migration": ("migrat", "schema", "model", "sql"),
    "workflow": (".github/workflows", ".gitlab-ci", ".circleci", "jenkinsfile", "/ci/", "/ci.", "pipeline", "deploy"),
    "dependency": ("package.json", "package-lock", "requirements", "poetry.lock", "pyproject"),
    "tests": ("/test", "_test", "test_", ".test.", ".spec."),  # not latest.py or attestation.py
}

WHY = {
    "auth": "Touches authentication or access control paths.",
    "config": "Changes shared runtime configuration or environment-sensitive behavior.",
    "migration": "Modifies schema or data migration behavior.",
    "workflow": "Affects CI or deployment workflow behavior.",
    "dependency": "Changes dependency versions or supply-chain surface area.",
}

GENERATED_NAMES = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", ".min.js", ".min.css")
GENERATED_DIRS = ("dist/", "build/", "coverage/", "node_modules/")
CODE_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".jsx", ".sql", ".yml", ".yaml")


def areas(path: str) -> list[str]:
    lowered = "/" + path.lower().lstrip("/")
    return [name for name, markers in AREAS.items() if any(marker in lowered for marker in markers)]


def why_it_matters(path: str, found: list[str]) -> str:
    for name in found:
        if name in WHY:
            return WHY[name]
    if path.endswith((".md", ".txt")):
        return "Mostly documentation impact unless the PR depends on process or API contract changes here."
    return "Carries application behavior or review-relevant code changes."


def generated(path: str) -> str:
    """Why a file is noise to review line by line ("" when it isn't)."""
    lowered = path.lower()
    if any(name in lowered for name in GENERATED_NAMES):
        return "a lockfile or minified build output"
    if any("/" + folder in "/" + lowered for folder in GENERATED_DIRS):
        return "a generated build artifact"
    return ""


def priority(path: str, changed_lines: int, found: list[str]) -> int:
    """Higher is reviewed first: the size of the change (up to 40), code over
    prose, and the risky areas above all."""
    score = min(changed_lines, 40)
    if path.lower().endswith(CODE_EXTENSIONS):
        score += 8
    if found:
        score += 20
    if "tests" in found:
        score += 4
    return score
