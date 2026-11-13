"""The false-alarm corpus outside Python: pick its merged PRs, and sum up
what the checks found on them.

    python benchmark/corpus.py pick [--out benchmark/planted]
    python benchmark/planted.py checks --risks benchmark/planted/risks.yml \\
        --prs benchmark/planted/clean-go.json --out benchmark/results/<name>/go    # and js, rust, infra
    # mark the verdict column of each checks.csv: right | wrong | unsure
    python benchmark/corpus.py summary benchmark/results/<name>

planted/clean.json is merged PRs from Python projects only. `pick` writes four
more lists in the same form, each PR pinned by its head commit:

- clean-js.json, clean-go.json, clean-rust.json: for each project below, its
  PER_PROJECT newest merged PRs that add at most MAX_ADDED lines and add a line
  to a source file of the language that the checks don't take for a test.
- clean-infra.json: for each of the same projects, from the PRs not picked
  above and under the same size limit: the newest by a person that adds a
  line to a workflow, the newest by a person that adds a line to a dependency
  manifest, and the newest by a bot (Dependabot, Renovate, a release action)
  that does either.

"Newest" is by PR number. Only PRs merged into the project's default branch
before CUTOFF count (a backport to a release branch repeats a PR already
there), and only the LOOK_BACK newest of those are read for each project. So a
later run picks the same PRs, and a slot stays empty when nothing in that
window fits it. `main` and `master` count as the default branch too: a
project that renamed it has older PRs under the old name.

The PRs are listed with GitHub's GraphQL API, where one request and one point
of its quota list 50 PRs with their files: about 130 points for the 18
projects. It uses GITHUB_TOKEN or `gh auth token`, and keeps what it read in
benchmark/.cache.

`summary` reads a folder holding one `planted.py checks` run per list (js/,
go/, rust/, infra/) and writes its summary.md: the findings on the merged PRs
by group and by rule, the ones marked wrong or unsure, then the folder's
notes.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import CACHE, _token  # noqa: E402
from stratum_open.checks.common import extension, is_test_path  # noqa: E402
from stratum_open.checks.dependencies import manifest_kind  # noqa: E402

CUTOFF = "2026-11-13T00:00:00Z"
PER_PROJECT = 10
MAX_ADDED = 400
LOOK_BACK = 300
PAGE = 50
MAX_FILES = 100  # what one request lists for a PR; a PR changing more is skipped

# Well-known projects with merged PRs every month, of different kinds for each
# language: web frameworks and servers, tools that run commands, and one
# database library, where the SQL rule has the most to misread.
PROJECTS = {
    "js": ["expressjs/express", "fastify/fastify", "vitejs/vite", "sveltejs/svelte", "eslint/eslint", "typeorm/typeorm"],
    "go": ["gin-gonic/gin", "go-gorm/gorm", "caddyserver/caddy", "prometheus/prometheus", "cli/cli", "etcd-io/etcd"],
    "rust": ["tokio-rs/tokio", "tokio-rs/axum", "clap-rs/clap", "rust-lang/cargo", "launchbadge/sqlx", "nushell/nushell"],
}
SOURCE = {
    "js": {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"},
    "go": {".go"},
    "rust": {".rs"},
}
GROUPS = {"js": "JavaScript and TypeScript", "go": "Go", "rust": "Rust", "infra": "Workflows and manifests"}
VERDICTS = ("right", "wrong", "unsure")

QUERY = """
query($owner: String!, $name: String!, $first: Int!, $files: Int!, $after: String) {
  rateLimit { cost remaining }
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name }
    pullRequests(states: MERGED, first: $first, after: $after, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number mergedAt additions changedFiles headRefOid baseRefName
        author { login __typename }
        files(first: $files) { nodes { path additions } }
      }
    }
  }
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    pick = sub.add_parser("pick")
    pick.add_argument("--out", default=str(Path(__file__).resolve().parent / "planted"))
    summary = sub.add_parser("summary")
    summary.add_argument("out")
    args = parser.parse_args()
    return _pick(Path(args.out)) if args.command == "pick" else _summary(Path(args.out))


# ── picking the PRs ──────────────────────────────────────────────────────


def _pick(out: Path) -> int:
    infra: list[dict] = []
    for language, repos in PROJECTS.items():
        picked: list[dict] = []
        for repo in repos:
            pulls = merged_pulls(repo)
            mine = [p for p in pulls if fits(p) and adds_source(p, language)][:PER_PROJECT]
            picked += [entry(repo, p) for p in mine]
            taken = {p["number"] for p in mine}
            slots = infra_slots([p for p in pulls if fits(p) and p["number"] not in taken])
            infra += [entry(repo, p, slot) for slot, p in slots.items()]
            oldest = min((p["mergedAt"][:10] for p in mine), default="-")
            print(f"{repo}: {len(mine)} {language} PR(s) back to {oldest}; infra: {', '.join(slots) or 'none'}", flush=True)
        write(out / f"clean-{language}.json", picked)
    write(out / "clean-infra.json", infra)
    return 0


def fits(pull: dict) -> bool:
    return pull["additions"] <= MAX_ADDED and pull["changedFiles"] <= MAX_FILES


def added_paths(pull: dict) -> list[str]:
    return [f["path"] for f in pull["files"]["nodes"] if f["additions"]]


def adds_source(pull: dict, language: str) -> bool:
    return any(extension(path) in SOURCE[language] and not is_test_path(path) for path in added_paths(pull))


def is_bot(pull: dict) -> bool:
    author = pull.get("author") or {}
    return author.get("__typename") == "Bot" or (author.get("login") or "").endswith("[bot]")


def infra_slots(pulls: list[dict]) -> dict[str, dict]:
    """The newest PR for each slot, each PR in one slot only."""
    slots: dict[str, dict] = {}
    for pull in pulls:
        paths = added_paths(pull)
        workflow = any(path.startswith(".github/workflows/") for path in paths)
        manifest = any(manifest_kind(path) is not None for path in paths)
        if is_bot(pull):
            wanted = ["bot"] if workflow or manifest else []
        else:
            wanted = [name for name, fit in (("workflow", workflow), ("manifest", manifest)) if fit]
        for name in wanted:
            if name not in slots:
                slots[name] = pull
                break
    return slots


def entry(repo: str, pull: dict, slot: str | None = None) -> dict:
    item = {"repo": repo, "pr": pull["number"], "head": pull["headRefOid"]}
    return {**item, "slot": slot} if slot else item


def write(path: Path, prs: list[dict]) -> None:
    path.write_text(json.dumps(prs, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {path}: {len(prs)} PR(s)", flush=True)


def merged_pulls(repo: str) -> list[dict]:
    """The repo's LOOK_BACK newest PRs merged into its default branch before
    CUTOFF, newest first."""
    CACHE.mkdir(exist_ok=True)
    cached = CACHE / f"corpus__{repo.replace('/', '__')}__{CUTOFF[:10]}.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    owner, name = repo.split("/")
    pulls: list[dict] = []
    after = None
    while len(pulls) < LOOK_BACK:
        data = graphql(owner=owner, name=name, first=PAGE, files=MAX_FILES, after=after)
        page = data["repository"]["pullRequests"]
        mainline = {data["repository"]["defaultBranchRef"]["name"], "main", "master"}
        pulls += [p for p in page["nodes"] if p["mergedAt"] < CUTOFF and p["baseRefName"] in mainline]
        if data["rateLimit"]["remaining"] < 500:
            raise SystemExit(f"Stopping at {repo}: {data['rateLimit']['remaining']} GraphQL points left.")
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    pulls = pulls[:LOOK_BACK]
    cached.write_text(json.dumps(pulls), encoding="utf-8")
    return pulls


def graphql(**variables) -> dict:
    response = requests.post(
        "https://api.github.com/graphql", json={"query": QUERY, "variables": variables},
        headers={"Authorization": f"Bearer {_token()}"}, timeout=60,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise SystemExit(f"GitHub: {body['errors'][0].get('message')}")
    return body["data"]


# ── the summary ──────────────────────────────────────────────────────────


def _summary(out: Path) -> int:
    runs: dict[str, tuple[list[dict], list[dict]]] = {}
    for group in GROUPS:
        if (out / group / "checks.csv").exists():
            with (out / group / "checks.csv").open(encoding="utf-8", newline="") as handle:
                rows = [row for row in csv.DictReader(handle) if row["set"] == "merged"]
            runs[group] = (json.loads((out / group / "prs.json").read_text(encoding="utf-8")), rows)
    if not runs:
        raise SystemExit(f"Nothing to sum up in {out}: no js/, go/, rust/ or infra/ with a checks.csv")
    every = [row for _, rows in runs.values() for row in rows]
    lines = [
        "# Checks that need no model, beyond Python",
        "",
        f"{sum(len(prs) for prs, _ in runs.values())} real merged PRs as they are, where every finding is a possible false alarm. "
        "Each finding was read and marked: right when what it says is true of the PR, wrong when the code is fine and "
        "the rule misread it (a false alarm), unsure otherwise.",
        "",
        "| Group | PRs | PRs with a finding | Findings | Right | Wrong | Unsure |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for group, (prs, rows) in runs.items():
        lines.append(f"| [{GROUPS[group]}]({group}/checks.csv) | {len(prs)} | {len({r['case'] for r in rows})} | {_counts(rows)} |")
    lines.append(f"| **All** | **{sum(len(prs) for prs, _ in runs.values())}** | **{len({r['case'] for r in every})}** | "
                 + " | ".join(f"**{cell}**" for cell in _counts(every).split(" | ")) + " |")
    unmarked = sum(1 for row in every if row["verdict"] not in VERDICTS)
    if unmarked:
        lines += ["", f"**{unmarked} finding(s) not marked yet**, so the counts are incomplete."]
    for group, (prs, rows) in runs.items():
        by_rule: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_rule[row["rule"]].append(row)
        lines += ["", f"## {GROUPS[group]}", "", f"{len(prs)} PRs, {len(rows)} finding(s) on {len({r['case'] for r in rows})} of them."]
        if rows:
            lines += ["", "| Rule | PRs | Findings | Right | Wrong | Unsure |", "| --- | --- | --- | --- | --- | --- |"]
            lines += [f"| `{rule}` | {len({r['case'] for r in mine})} | {_counts(mine)} |" for rule, mine in sorted(by_rule.items())]
    doubted = [row for row in every if row["verdict"] in ("wrong", "unsure")]
    if doubted:
        lines += ["", "## Marked wrong or unsure", "", "| PR | Rule | Where | Finding | Verdict | Why |", "| --- | --- | --- | --- | --- | --- |"]
        lines += [f"| {r['case']} | `{r['rule']}` | `{r['path']}`{':' + r['line'] if r['line'] else ''} | {r['title']} | {r['verdict']} | {r['note']} |"
                  for r in doubted]
    notes = out / "notes.md"
    if notes.exists():
        lines += ["", notes.read_text(encoding="utf-8").strip()]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print((out / "summary.md").read_text(encoding="utf-8"))
    return 0


def _counts(rows: list[dict]) -> str:
    return " | ".join(str(n) for n in [len(rows)] + [sum(r["verdict"] == verdict for r in rows) for verdict in VERDICTS])


if __name__ == "__main__":
    raise SystemExit(main())
