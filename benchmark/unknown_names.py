"""The unknown-names check on real code, where every finding is a possible
false alarm.

The check (stratum_open/repo/unknown_names.py) reads the repository, not only
the diff, so it needs each project's files:

    python benchmark/unknown_names.py clone --repos <folder>
    python benchmark/planted.py checks --risks benchmark/planted/risks.yml --prs benchmark/planted/clean.json \\
        --out benchmark/results/<name> --policy benchmark/planted/variants/unknown-names.yml --repos <folder>
    python benchmark/unknown_names.py source --repos <folder> --out benchmark/results/<name>

`clone` clones each project of clean.json with git (not the API), fetching a
file's contents only when a checkout needs them. Keep the folder outside this
repository. With `--repos`, `planted.py checks` checks out, for each merged
PR, the default branch as it was just before the PR merged (the first parent
of the merge commit, asked from GitHub once per project and cached), and runs
the check on the PR's diff there, as the Action would have.

Merged PRs add few imports, so `source` asks the same of far more code: it
reads every line of each project's default branch as if a PR had just added
it. It writes source.csv, one row per finding to read and mark (marks are
kept when it's run again), and adds to names.json how many names were looked
up and how each came out.

Nothing in the clones is imported or run.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import CACHE, _token, _write  # noqa: E402
from stratum_open.diff import FileDiff, Hunk, Line  # noqa: E402
from stratum_open.findings import Finding  # noqa: E402
from stratum_open.github.api import GitHub  # noqa: E402
from stratum_open.policy import Policy  # noqa: E402
from stratum_open.repo import unknown_names as check  # noqa: E402

HERE = Path(__file__).resolve().parent
PRS = HERE / "planted" / "clean.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("clone", "source"):
        command = sub.add_parser(name)
        command.add_argument("--repos", required=True, help="a folder for one clone per project, outside this repository")
        command.add_argument("--prs", default=str(PRS))
        if name == "source":
            command.add_argument("--out", required=True)
    args = parser.parse_args()
    projects = sorted({item["repo"] for item in json.loads(Path(args.prs).read_text(encoding="utf-8"))})
    return _clone(Path(args.repos), projects) if args.command == "clone" else _source(Path(args.repos), projects, Path(args.out))


def _clone(repos: Path, projects: list[str]) -> int:
    repos.mkdir(parents=True, exist_ok=True)
    for repo in projects:
        where = folder(repos, repo)
        if not (where / ".git").exists():
            subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout", f"https://github.com/{repo}.git", str(where)], check=True)
        print(f"{repo}: {_git(where, 'rev-parse', 'origin/HEAD').strip()}", flush=True)
    return 0


def folder(repos: Path, repo: str) -> Path:
    return repos / repo.replace("/", "__")


def _git(where: Path, *args: str, check: bool = True) -> str:
    # Long paths and names Windows won't take must not stop a checkout of everything else.
    return subprocess.run(["git", "-C", str(where), "-c", "core.longpaths=true", "-c", "core.protectNTFS=false", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=check).stdout


def checkout(where: Path, commit: str) -> bool:
    _git(where, "checkout", "--quiet", "--force", "--detach", commit, check=False)
    return _git(where, "rev-parse", "HEAD", check=False).strip() == commit


def bases(repo: str, number: int) -> dict:
    """For a merged PR, its merge commit and that commit's first parent: the
    default branch just before the PR went in. One GraphQL call per project
    covers its PRs in clean.json; the answers are cached."""
    path = CACHE / f"{repo.replace('/', '__')}__bases.json"
    known = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if str(number) not in known:
        numbers = sorted({number} | {int(item["pr"]) for item in json.loads(PRS.read_text(encoding="utf-8")) if item["repo"] == repo})
        owner, name = repo.split("/", 1)
        asked = " ".join(f"p{n}: pullRequest(number: {n}) {{ mergeCommit {{ oid parents(first: 1) {{ nodes {{ oid }} }} }} }}"
                         for n in numbers if str(n) not in known)
        data = GitHub(_token(), repo).graphql("query($owner:String!,$name:String!){repository(owner:$owner,name:$name){" + asked + "}}",
                                              owner=owner, name=name).get("repository") or {}
        for n in numbers:
            merge = (data.get(f"p{n}") or {}).get("mergeCommit") or {}
            parents = (merge.get("parents") or {}).get("nodes") or []
            known.setdefault(str(n), {"merge": merge.get("oid", ""), "base": parents[0]["oid"] if parents else ""})
        CACHE.mkdir(exist_ok=True)
        path.write_text(json.dumps(known, indent=1) + "\n", encoding="utf-8")
    return known[str(number)]


def at_base(repos: Path, repo: str, number: int, files: list[FileDiff], policy: Policy, out: Path) -> list[Finding]:
    """The check's findings on one merged PR, read against the project as it
    was just before the PR merged. What it looked up goes into names.json."""
    if not policy.checks.unknown_names:
        return []
    where, base = folder(repos, repo), bases(repo, number)["base"]
    if not base or not checkout(where, base):
        print(f"{repo}#{number}: no checkout of its base ({base or 'not merged'}), so the unknown-names check didn't see it", flush=True)
        _record(out, "merged", f"{repo}#{number}", {"base": base, "tally": {"no checkout": 1}})
        return []
    found, tally = check._check(files, str(where), {}, False)
    _record(out, "merged", f"{repo}#{number}", {"base": base, "tally": dict(sorted(tally.items()))})
    return found


def _record(out: Path, part: str, key: str, value: dict) -> None:
    path = out / "names.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.setdefault(part, {})[key] = value
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _source(repos: Path, projects: list[str], out: Path) -> int:
    import planted  # here, not at the top: it imports this module

    out.mkdir(parents=True, exist_ok=True)
    marks = {(r["case"], r["path"], r["title"]): r for r in (planted._read(out / "source.csv") if (out / "source.csv").exists() else [])}
    check.MAX_PARSED = 10 ** 9  # the cap is for one PR; here the whole project is the change
    asked = check._lookup

    def lookup(repo, where, attrs):
        """Each name found defined is asked for again under a name that isn't
        there: would a made-up name in its place have been caught?"""
        answer = asked(repo, where, attrs)
        name, line = attrs[answer[1]]
        if answer[0] == "defined" and not check._dunder(name):
            again = asked(repo, where, attrs[:answer[1]] + [(f"{name}_made_up", line)])
            repo.tally["a made-up name in its place: " + ("caught" if again[0] == "missing" else "not caught")] += 1
        return answer

    check._lookup = lookup
    rows = []
    for repo in projects:
        where = folder(repos, repo)
        commit = _git(where, "rev-parse", "origin/HEAD").strip()
        if not checkout(where, commit):
            print(f"{repo}: couldn't check out {commit}", flush=True)
            continue
        project = check._Repo(str(where), [], {}, True)
        missing = []
        for path in sorted(project.python) if project.complete else []:
            text = project.text(path)
            if text is not None:
                lines = [Line("+", line, None, number) for number, line in enumerate(text.split("\n"), start=1)]
                missing += check._file_missing(project, FileDiff(path=path, status="added", hunks=[Hunk(0, 1, "", lines)]))
        found = check._report(project, missing)
        for f in found:
            kept = marks.get((repo, f.path, f.title), {})
            rows.append({"set": "source", "case": repo, "rule": f.rule, "path": f.path, "line": f.line_start or "", "title": f.title,
                         "expected": "", "verdict": kept.get("verdict", ""), "note": kept.get("note", "")})
        _record(out, "source", repo, {"commit": commit, "files": len(project.python), "tally": dict(sorted(project.tally.items()))})
        print(f"{repo} at {commit[:10]}: {len(project.python)} files, {len(found)} finding(s)", flush=True)
    _write(out / "source.csv", planted.CHECK_COLUMNS, rows)
    print(_table(out))
    return 0


def _table(out: Path) -> str:
    """names.json as the lines notes.md quotes."""
    data = json.loads((out / "names.json").read_text(encoding="utf-8"))
    lines = []
    for part, title in (("merged", "On the lines the merged PRs add"), ("source", "On every line of the projects")):
        total: Counter[str] = Counter()
        for entry in data.get(part, {}).values():
            total.update(entry["tally"])
        lines += [f"{title} ({len(data.get(part, {}))}):", ""] + [f"- {key}: {count}" for key, count in sorted(total.items())] + [""]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
