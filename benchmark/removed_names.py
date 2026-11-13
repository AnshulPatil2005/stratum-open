"""The removed-names check on real code, where every finding is a possible
false alarm.

The check (stratum_open/repo/removed_names.py) reads the repository, not only
the diff, so it needs each project's files. It uses the clones
unknown_names.py makes:

    python benchmark/unknown_names.py clone --repos <folder>
    python benchmark/planted.py checks --risks benchmark/planted/risks.yml --prs benchmark/planted/clean.json \\
        --out benchmark/results/<name> --policy benchmark/planted/variants/removed-names.yml --repos <folder>
    python benchmark/removed_names.py commits --repos <folder> --out benchmark/results/<name> [--commits 300]

With `--repos`, `planted.py checks` reads each merged PR against its project
as it was just before the PR merged.

Merged PRs remove few names, so `commits` reads far more of them: the last
`--commits` commits on each project's default branch (first parents only:
what was merged, not what a merge brought in), each as a PR against its
parent. Every commit that takes a name out of a module is read twice:

- As it was merged. Its author ran the tests, so a finding is either a false
  alarm or a break that got merged. commits.csv has a row per finding, to
  read and mark (marks are kept when it's run again).
- One file at a time, for each file that loses a function, a class or an
  assignment: that file's change alone, as if the author had made it and
  forgotten the rest. Every line that used a removed name is back, so this
  counts what the rule sees of them. Each use it names is then held against
  the real commit: did the commit change that line, or leave it?

Both write removed.json, the counts, and removed.csv, a row for each removed
name that wasn't simply unused: every definition that came out any other
way, and every import that was found still used.

Nothing in the clones is imported or run.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import _write  # noqa: E402
from stratum_open.diff import FileDiff, parse_diff  # noqa: E402
from stratum_open.findings import Finding  # noqa: E402
from stratum_open.policy import Policy  # noqa: E402
from stratum_open.repo import removed_names as check  # noqa: E402
from unknown_names import PRS, _git, bases, checkout, folder  # noqa: E402

ALONE = 25  # files of one commit read as its only change, the biggest removals first
# What `review --local` asks of git, so the diff is the one a PR would show.
DIFF = ("diff", "--no-color", "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/", "-U3", "--find-renames")
NAME_COLUMNS = ["set", "case", "module", "name", "kind", "outcome", "why", "uses", "where", "left"]
_SHOWN = 3  # uses listed in a row of removed.csv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    commits = sub.add_parser("commits")
    commits.add_argument("--repos", required=True, help="the folder of clones benchmark/unknown_names.py clone made")
    commits.add_argument("--prs", default=str(PRS), help="the projects are the ones these PRs are from")
    commits.add_argument("--out", required=True)
    commits.add_argument("--commits", type=int, default=300, help="how many commits of each default branch to read, newest first")
    commits.add_argument("--project", action="append", help="only this owner/name (may be given more than once)")
    commits.add_argument("--jobs", type=int, default=4, help="projects read at a time")
    args = parser.parse_args()
    projects = sorted({item["repo"] for item in json.loads(Path(args.prs).read_text(encoding="utf-8"))})
    return _commits(Path(args.repos), [p for p in projects if not args.project or p in args.project], Path(args.out), args.commits, args.jobs)


def at_base(repos: Path, repo: str, number: int, files: list[FileDiff], policy: Policy, out: Path) -> list[Finding]:
    """The check's findings on one merged PR, read against the project as it
    was just before the PR merged. What the PR removed goes into
    removed.json and removed.csv."""
    if not policy.checks.removed_names:
        return []
    case = f"{repo}#{number}"
    where, base = folder(repos, repo), bases(repo, number)["base"]
    if not base or not checkout(where, base):
        print(f"{case}: no checkout of its base ({base or 'not merged'}), so the removed-names check didn't see it", flush=True)
        _record(out, "merged", case, {"base": base, "tally": {"no checkout": 1}})
        return []
    # As the review would: a use on a line the PR adds is left to the unknown-names check when that is on too.
    removed, tally = check._decide(files, str(where), {}, False, added=not policy.checks.unknown_names)
    _record(out, "merged", case, {"base": base, "tally": dict(sorted(tally.items())), "names": _by_kind(removed)})
    _rows(out, lambda row: row["set"] == "merged" and row["case"] == case, [_row("merged", case, item) for item in removed if _listed(item)])
    by_path = {file.path: file for file in files}
    return [check._finding(item, by_path[item.module]) for item in removed if item.outcome == check.REPORTED]


def _by_kind(removed: list) -> dict[str, int]:
    """How the names came out: definitions (a function, a class, an
    assignment) apart from imports, which a module drops all the time
    without anything having imported them from it."""
    return dict(sorted(Counter(f"{'imports' if item.kind == 'import' else 'definitions'}: {item.outcome}" for item in removed).items()))


def _listed(item) -> bool:
    """Whether a name gets a row in removed.csv: not the ones that were
    simply unused, and of the imports only the ones found still used."""
    return item.outcome in (check.REPORTED, check.SET_ELSEWHERE) or (item.kind != "import" and item.outcome != check.UNUSED)


def _row(part: str, case: str, item, left: int | None = None) -> dict:
    uses = sorted({(use.path, use.line) for use in item.uses})
    return {"set": part, "case": case, "module": item.module, "name": item.name, "kind": item.kind, "outcome": item.outcome, "why": item.why,
            "uses": len(uses) or "", "where": " ".join(f"{path}:{line}" for path, line in uses[:_SHOWN]), "left": "" if left is None else left}


def _record(out: Path, part: str, key: str, value: dict) -> None:
    path = out / "removed.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.setdefault(part, {})[key] = value
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _rows(out: Path, replaced, rows: list[dict]) -> None:
    """Put these rows into removed.csv in place of the ones `replaced` picks
    (an earlier run's rows for the same PR or projects)."""
    import planted  # here, not at the top: it imports this module

    path = out / "removed.csv"
    kept = [row for row in (planted._read(path) if path.exists() else []) if not replaced(row)]
    _write(path, NAME_COLUMNS, sorted(kept + rows, key=lambda row: (row["set"] != "merged", row["set"], row["case"], row["module"], row["name"])))


def _commits(repos: Path, projects: list[str], out: Path, limit: int, jobs: int) -> int:
    import planted

    out.mkdir(parents=True, exist_ok=True)
    kept = planted._read(out / "commits.csv") if (out / "commits.csv").exists() else []
    marks = {(r["case"], r["path"], r["title"]): r for r in kept}
    rows = [r for r in kept if r["case"].partition("@")[0] not in projects]  # another run's projects stay as they are
    names: list[dict] = []
    # Each project has its own clone, and most of the time goes to git: threads are enough.
    with ThreadPoolExecutor(max_workers=max(jobs, 1)) as pool:
        for repo, (entry, found, listed) in zip(projects, pool.map(lambda repo: _project(repos, repo, limit), projects)):
            _record(out, "commits", repo, entry)
            names += listed
            for commit, f in found:
                mark = marks.get((f"{repo}@{commit[:10]}", f.path, f.title), {})
                rows.append({"set": "commits", "case": f"{repo}@{commit[:10]}", "rule": f.rule, "path": f.path, "line": f.line_start or "", "title": f.title,
                             "expected": "", "verdict": mark.get("verdict", ""), "note": mark.get("note", "")})
    _write(out / "commits.csv", planted.CHECK_COLUMNS, rows)
    _rows(out, lambda row: row["set"] != "merged" and row["case"].partition("@")[0] in projects, names)
    print(_table(out))
    return 0


def _project(repos: Path, repo: str, limit: int) -> tuple[dict, list[tuple[str, Finding]], list[dict]]:
    """One project's commits: its counts for removed.json, the findings on
    the commits as they were merged, and its rows for removed.csv."""
    where = folder(repos, repo)
    head = _git(where, "rev-parse", "origin/HEAD").strip()
    seen: Counter[str] = Counter()
    tally: Counter[str] = Counter()
    merged: Counter[str] = Counter()
    alone: Counter[str] = Counter()
    found: list[tuple[str, Finding]] = []
    listed: list[dict] = []
    for commit, *parents in (line.split() for line in _git(where, "rev-list", "--first-parent", "--parents", f"--max-count={limit}", head).splitlines()):
        if not parents:
            continue
        seen["commits"] += 1
        try:
            files = parse_diff(_diff(where, parents[0], commit))
        except subprocess.CalledProcessError:
            seen["commits git couldn't give the diff of"] += 1
            continue
        if not any(f.status == "modified" and f.path.endswith(".py") and f.deletions for f in files):
            continue
        seen["commits that take lines out of a Python file"] += 1
        if not checkout(where, parents[0]):
            seen["commits whose parent couldn't be checked out"] += 1
            continue
        removed, counts = check._decide(files, str(where), {}, False)
        tally.update(counts)
        if not removed:
            continue
        seen["commits that remove a name from a module"] += 1
        case = f"{repo}@{commit[:10]}"
        by_path = {file.path: file for file in files}
        merged.update(_by_kind(removed))
        found += [(commit, check._finding(item, by_path[item.module])) for item in removed if item.outcome == check.REPORTED]
        listed += [_row("commits", case, item) for item in removed if _listed(item)]
        # The same commit, one file that loses a definition at a time: every other file, and so every line that
        # used the name, is as it was. (Not the files that only lose imports: those are the ones that used it.)
        modules = sorted({item.module for item in removed if item.kind != "import"}, key=lambda path: (-by_path[path].deletions, path))
        seen["files that lose a definition"] += len(modules)
        seen["files that lose a definition, read alone"] += len(modules[:ALONE])
        reported = 0
        for module in modules[:ALONE]:
            names = check._decide([by_path[module]], str(where), {}, False)[0]
            alone.update(_by_kind(names))
            for item in names:
                left, elsewhere = _held(item, files)
                if item.kind != "import" and elsewhere:
                    alone["definitions the commit also took out of a line of another Python file"] += 1
                    alone["definitions the commit also took out of a line of another Python file, reported"] += item.outcome == check.REPORTED
                if item.outcome == check.REPORTED:
                    reported += 1
                    alone["uses named in the findings"] += len({(use.path, use.line) for use in item.uses})
                    alone["uses named in the findings that the commit left as they were"] += left
                if _listed(item):
                    listed.append(_row("alone", case, item, left if item.uses else None))
        print(f"{case}: {len(removed)} name(s) removed, {sum(item.outcome == check.REPORTED for item in removed)} finding(s) as merged, "
              f"{reported} with one file changed at a time", flush=True)
    print(f"{repo} at {head[:10]}: {dict(seen)}", flush=True)
    checkout(where, head)
    entry = {"head": head, "seen": dict(sorted(seen.items())), "tally": dict(sorted(tally.items())), "names": dict(sorted(merged.items())),
             "alone": dict(sorted(alone.items()))}
    return entry, found, listed


def _diff(where: Path, parent: str, commit: str) -> str:
    """The commit as a PR's diff. A clone made without file contents fetches
    them here, which can fail for a moment: tried twice."""
    try:
        return _git(where, *DIFF, parent, commit)
    except subprocess.CalledProcessError:
        return _git(where, *DIFF, parent, commit)


def _held(item, files: list[FileDiff]) -> tuple[int, bool]:
    """A name of a file read as the commit's only change, held against what
    the commit really did: how many of the uses the rule names the commit
    left as they were, and whether the commit took the name out of a line of
    another Python file at all (a sign it was used, whatever the rule saw)."""
    word = re.compile(rf"(?<!\w){re.escape(item.name)}(?!\w)")
    by_old = {file.old_path or file.path: file for file in files}
    left = set()
    for use in item.uses:
        file = by_old.get(use.path)
        if file is None or (file.status != "deleted" and use.line not in {line.old for hunk in file.hunks for line in hunk.lines if line.kind == "-"}):
            left.add((use.path, use.line))
    elsewhere = any(word.search(line.text) for file in files if file.path != item.module and (file.old_path or file.path).endswith(".py")
                    for hunk in file.hunks for line in hunk.lines if line.kind == "-")
    return len(left), elsewhere


def _table(out: Path) -> str:
    """removed.json as the lines notes.md quotes."""
    data = json.loads((out / "removed.json").read_text(encoding="utf-8"))
    lines = []
    for part, title in (("merged", "The merged PRs"), ("commits", "The commits, as merged")):
        entries = data.get(part, {}).values()
        lines += [f"{title} ({len(entries)}):", ""] + _counts(entries, "seen") + _counts(entries, "tally") + _counts(entries, "names") + [""]
    entries = data.get("commits", {}).values()
    lines += ["The same commits, one file that loses a definition at a time:", ""] + _counts(entries, "alone") + [""]
    return "\n".join(lines)


def _counts(entries, key: str) -> list[str]:
    total: Counter[str] = Counter()
    for entry in entries:
        total.update(entry.get(key, {}))
    return [f"- {name}: {count}" for name, count in sorted(total.items())]


if __name__ == "__main__":
    raise SystemExit(main())
