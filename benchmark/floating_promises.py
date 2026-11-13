"""How the floating-promise check does on merged JavaScript and TypeScript.

The questions dropped_awaits.py asks of the Python check, on the PRs in
planted/clean-js.json:

- every `await` those PRs add inside a function is dropped, one at a time:
  does the check (stratum_open/checks/promises.py) flag that line?
- the PRs as merged: any finding is a possible false alarm.

60 PRs add few lines, so the check also reads far more merged code as if every
line of it were new. A finding there is a promise a project merged unawaited:
a bug, or a call it means to leave running, which is a false alarm.

- every file those PRs touch;
- every JavaScript and TypeScript file of the repositories pinned in
  planted/repos-js.json. Its `dev` ones were read while the rule was being
  narrowed; the `test` ones only once it was settled.

    python benchmark/floating_promises.py --out benchmark/results/<name>
    # mark the verdict column of findings.csv: right | wrong | unsure
    python benchmark/floating_promises.py --out benchmark/results/<name>   # again, for the summary

A second run keeps the verdicts and notes of the rows it finds again. A PR's
files are read at its pinned head with GITHUB_TOKEN or `gh auth token`, one
request a file, and fetching stops when fewer than STOP_BELOW requests are left
of the hour's quota. A repository comes as GitHub's archive of the commit,
which costs none, and is read from the archive: nothing is unpacked or run.
Both are kept in benchmark/.cache. Needs the `js` extra.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import tarfile
from pathlib import Path
from typing import Iterator

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import CACHE, _fetch, _token, _write  # noqa: E402
from stratum_open.checks import promises  # noqa: E402
from stratum_open.checks.common import is_test_path  # noqa: E402
from stratum_open.diff import FileDiff, Hunk, Line, parse_diff  # noqa: E402
from stratum_open.github.api import GitHub, GitHubError  # noqa: E402

HERE = Path(__file__).resolve().parent
STOP_BELOW = 1000
COLUMNS = ["set", "case", "path", "line", "code", "title", "evidence", "verdict", "note"]
VERDICTS = ("right", "wrong", "unsure")
SETS = {"merged": "The PRs as merged", "whole": "Every line of the files they touch", "repos": "Whole repositories"}
# Folders of code a project didn't write or doesn't keep.
SKIPPED = {"node_modules", "dist", "build", "vendor", "coverage"}
SHAPES = {
    "statement, call by name": "A whole statement awaiting a call by name: `await save(x);`",
    "statement, other": "Another whole statement: `await pending;`, `await a().b();`",
    "value used": "A value the code goes on to use: `const x = await f()`, `return await f()`, `g(await f())`",
}
EVIDENCE = {"is awaited on": "Awaited elsewhere in the file", "is declared `async`": "Declared `async` in the file", "removes": "Awaited on a removed line"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    parser.add_argument("--prs", default=str(HERE / "planted" / "clean-js.json"))
    parser.add_argument("--repos", default=str(HERE / "planted" / "repos-js.json"))
    args = parser.parse_args()
    if not promises.available():
        raise SystemExit('The parser isn\'t installed: pip install -e ".[js]"')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    prs = json.loads(Path(args.prs).read_text(encoding="utf-8"))
    cases, rows, seen = [], [], set()
    touched = {"prs": len(prs), "files": 0, "unparsed": 0, "lines": 0, "statements": 0}
    for item in prs:
        repo, number = item["repo"], int(item["pr"])
        case = f"{repo}#{number}"
        _, text = _fetch(repo, number)
        files = parse_diff(text)
        contents = head_contents(repo, number, item["head"], files)
        rows += [row("merged", case, f, contents[f.path]) for f in promises.check(files, contents)]
        for file in files:
            if file.path in contents and (repo, file.path) not in seen:
                seen.add((repo, file.path))
                rows += [row("whole", case, f, contents[file.path]) for f in as_new(file.path, contents[file.path], touched)]
            cases += [{"pr": case, **dropped} for dropped in drop_each_await(text, file, contents)]
        print(f"{case}: {sum(c['pr'] == case for c in cases)} await(s) dropped, "
              f"{sum(r['case'] == case and r['set'] == 'merged' for r in rows)} finding(s) as merged", flush=True)
    repos = []
    for item in json.loads(Path(args.repos).read_text(encoding="utf-8")):
        stats = {**item, "files": 0, "unparsed": 0, "lines": 0, "statements": 0}
        for path, source in repo_files(item["repo"], item["commit"]):
            rows += [row("repos", item["repo"], f, source) for f in as_new(path, source, stats)]
        repos.append(stats)
        print(f"{item['repo']}: {stats['lines']:,} lines, {sum(r['case'] == item['repo'] and r['set'] == 'repos' for r in rows)} finding(s)", flush=True)
    keep_marks(out / "findings.csv", rows)
    _write(out / "findings.csv", COLUMNS, rows)
    (out / "cases.json").write_text(json.dumps(cases, indent=1) + "\n", encoding="utf-8")
    (out / "read.json").write_text(json.dumps({"touched": touched, "repos": repos}, indent=1) + "\n", encoding="utf-8")
    numbers, findings = summarize(cases, rows, touched, repos)
    notes = out / "notes.md"  # goes between the numbers and the list of every finding, which is long
    parts = [numbers] + ([notes.read_text(encoding="utf-8").strip()] if notes.exists() else []) + [findings]
    (out / "summary.md").write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    print(numbers)
    return 0


# ── what is read ─────────────────────────────────────────────────────────


def head_contents(repo: str, number: int, head: str, files: list[FileDiff]) -> dict[str, str]:
    """The full text, at the PR's head, of the JavaScript and TypeScript
    files it changes, cached."""
    path = CACHE / f"{repo.replace('/', '__')}__{number}__js.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    gh = GitHub(_token(), repo)
    left = gh.get("/rate_limit")["resources"]["core"]["remaining"]  # asking doesn't count against the quota
    if left < STOP_BELOW:
        raise SystemExit(f"Stopping at {repo}#{number}: {left} GitHub requests left this hour.")
    contents = {}
    for file in files:
        if not promises.reads(file.path) or file.status == "deleted" or file.binary:
            continue
        try:
            text = gh.file_text(file.path, head)
        except GitHubError:
            text = None
        if text is not None:
            contents[file.path] = text
    path.write_text(json.dumps(contents), encoding="utf-8")
    return contents


def repo_files(repo: str, commit: str) -> Iterator[tuple[str, str]]:
    """Each JavaScript and TypeScript file a repository holds at a commit,
    read out of GitHub's archive of it. Minified files, type declarations and
    SKIPPED folders are left out: no code of the project's to read there."""
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"{repo.replace('/', '__')}__{commit[:12]}.tar.gz"
    if not path.exists():
        response = requests.get(f"https://codeload.github.com/{repo}/tar.gz/{commit}", timeout=600)
        response.raise_for_status()
        path.write_bytes(response.content)
    with tarfile.open(path) as archive:
        for member in archive:
            name = member.name.partition("/")[2]  # the archive holds one folder, named after the repo and commit
            if not member.isfile() or not promises.reads(name) or name.endswith((".min.js", ".d.ts")) or SKIPPED & set(name.split("/")[:-1]):
                continue
            yield name, archive.extractfile(member).read().decode("utf-8", errors="replace")


def as_new(path: str, source: str, stats: dict) -> list:
    """The check's findings on a file as if a PR added every line of it, with
    what was read counted into `stats`."""
    root = promises.parse(path, source) if len(source) <= promises.MAX_CHARS else None
    stats["files"] += 1
    if root is None:
        stats["unparsed"] += 1
        return []
    stats["lines"] += source.count("\n")
    stats["statements"] += sum(map(len, promises._read(root).calls.values()))
    lines = [Line("+", line.rstrip("\r"), None, number) for number, line in enumerate(source.split("\n"), start=1)]
    file = FileDiff(path=path, status="added", hunks=[Hunk(0, 1, f"@@ -0,0 +1,{len(lines)} @@", lines)])
    return promises.check([file], {path: source})


def row(group: str, case: str, finding, source: str) -> dict:
    code = source.split("\n")[finding.line_start - 1].strip()
    return {"set": group, "case": case, "path": finding.path, "line": finding.line_start, "code": code, "title": finding.title,
            "evidence": finding.detail.split(", so it returns")[0], "verdict": "", "note": ""}


# ── dropped awaits ───────────────────────────────────────────────────────


def drop_each_await(diff: str, file: FileDiff, contents: dict[str, str]) -> list[dict]:
    """One case for each `await` on a line the PR adds to this file, inside a
    function: the line without it, and whether the check then flags it.
    `shape` says what the await was: a whole statement awaiting a call by
    name (the one the rule reads), another whole statement, or a value the
    code goes on to use."""
    source = contents.get(file.path)
    root = promises.parse(file.path, source) if source is not None else None
    if root is None:
        return []
    added = dict(file.added())
    cases = []
    for node, in_function in awaits(root):
        number, column = node.start_point[0] + 1, node.start_point[1]
        if number not in added or not in_function:
            continue
        line = added[number].encode("utf-8")
        if line[column:column + 5] != b"await":
            raise SystemExit(f"{file.path}:{number} reads {added[number]!r}: no `await` at column {column}")
        dropped = (line[:column] + line[column + 5:].lstrip()).decode("utf-8")
        named, call = promises.awaited_call(node)
        whole = call if call is not None and call.start_byte <= node.start_byte else node  # `await f<T>(x)`: see awaited_call
        statement = whole.parent is not None and whole.parent.type == "expression_statement"
        shape = "statement, call by name" if statement and named else "statement, other" if statement else "value used"
        changed = parse_diff(diff)
        for hunk in next(f for f in changed if f.path == file.path).hunks:
            for changed_line in hunk.lines:
                if changed_line.kind == "+" and changed_line.new == number:
                    changed_line.text = dropped
        lines = source.split("\n")
        ending = "\r" if lines[number - 1].endswith("\r") else ""
        if lines[number - 1] != added[number] + ending:
            raise SystemExit(f"{file.path}:{number}: the diff and the file at the PR's head disagree")
        lines[number - 1] = dropped + ending
        hits = [f for f in promises.check(changed, {**contents, file.path: "\n".join(lines)}) if f.path == file.path and f.line_start == number]
        cases.append({"path": file.path, "line": number, "code": added[number].strip(), "shape": shape,
                      "test": is_test_path(file.path), "caught": bool(hits)})
    return cases


def awaits(root):
    """Every `await` expression, and whether it's inside a function (one at
    the top of a module isn't)."""
    stack = [(root, False)]
    while stack:
        node, in_function = stack.pop()
        if node.type == "await_expression":
            yield node, in_function
        in_function = in_function or (node.type in promises._FUNCTIONS and node.is_named)
        stack.extend((child, in_function) for child in node.children)


# ── the summary ──────────────────────────────────────────────────────────


def keep_marks(path: Path, rows: list[dict]) -> None:
    """Carry the verdicts and notes of an earlier run over to the same rows."""
    if not path.exists():
        return
    with path.open(encoding="utf-8", newline="") as handle:
        marks = {tuple(old[c] for c in COLUMNS[:6]): (old["verdict"], old["note"]) for old in csv.DictReader(handle)}
    for new in rows:
        new["verdict"], new["note"] = marks.get(tuple(str(new[c]) for c in COLUMNS[:6]), ("", ""))


def interval(hits: int, total: int) -> str:
    """The 95% Wilson interval of a share, in percent."""
    if not total:
        return "-"
    z, p = 1.96, hits / total
    centre, half = p + z * z / (2 * total), z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    low, high = (centre - half) / (1 + z * z / total), (centre + half) / (1 + z * z / total)
    return f"{max(0.0, low):.0%} to {min(1.0, high):.0%}"


def counts(rows: list[dict]) -> str:
    return " | ".join(str(n) for n in [len(rows)] + [sum(r["verdict"] == verdict for r in rows) for verdict in VERDICTS])


def table(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    return ["", "| Where | Code | Evidence | Verdict | Why |", "| --- | --- | --- | --- | --- |"] + [
        f"| {r['case']} `{r['path']}`:{r['line']} | `{r['code'][:60]}` | {r['evidence']} | {r['verdict'] or '?'} | {r['note']} |" for r in rows
    ]


def load(out: Path) -> tuple[list[dict], list[dict], dict, list[dict]]:
    """A run's dropped awaits, its findings, and what it read, from the files it wrote."""
    with (out / "findings.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    read = json.loads((out / "read.json").read_text(encoding="utf-8"))
    return json.loads((out / "cases.json").read_text(encoding="utf-8")), rows, read["touched"], read["repos"]


def scores(cases: list[dict], rows: list[dict], touched: dict, repos: list[dict]) -> dict:
    """What a run adds up to, for its summary and the results page alike. `read`
    is the merged code read as if it were new: the files the PRs touch, then
    the whole repositories of each set."""
    read = [{"read": "The files the PRs touch", "files": touched["files"] - touched["unparsed"], "lines": touched["lines"],
             "statements": touched["statements"], "findings": [r for r in rows if r["set"] == "whole"]}]
    for group in sorted({r.get("set", "") for r in repos}):
        mine = [r for r in repos if r.get("set", "") == group]
        read.append({"read": f"{len(mine)} whole repositories, `{group}`", "files": sum(m["files"] - m["unparsed"] for m in mine),
                     "lines": sum(m["lines"] for m in mine), "statements": sum(m["statements"] for m in mine),
                     "findings": [r for r in rows if r["set"] == "repos" and r["case"] in {m["repo"] for m in mine}]})
    return {"caught": sum(c["caught"] for c in cases), "cases": len(cases), "prs": touched["prs"],
            "merged": [r for r in rows if r["set"] == "merged"], "read": read}


def summarize(cases: list[dict], rows: list[dict], touched: dict, repos: list[dict]) -> tuple[str, str]:
    """The run's numbers, and the list of every finding with its verdict."""
    numbers = scores(cases, rows, touched, repos)
    caught = numbers["caught"]
    tests = [c for c in cases if c["test"]]
    lines = [
        "# The floating-promise check on merged JavaScript and TypeScript",
        "",
        "## Dropped awaits",
        "",
        f"Every `await` that {touched['prs']} merged PRs from six projects ([clean-js.json](../../planted/clean-js.json)) add inside a "
        f"function, dropped one at a time: **{caught} of {len(cases)} caught** (95% interval {interval(caught, len(cases))}).",
        "",
        "| What the await was | Caught | Of | 95% interval |",
        "| --- | --- | --- | --- |",
    ]
    for shape, words in SHAPES.items():
        mine = [c for c in cases if c["shape"] == shape]
        lines.append(f"| {words} | {sum(c['caught'] for c in mine)} | {len(mine)} | {interval(sum(c['caught'] for c in mine), len(mine))} |")
    lines += ["", f"{len(tests)} of the {len(cases)} are in test files ({sum(c['caught'] for c in tests)} caught), "
              f"{len(cases) - len(tests)} in other code ({caught - sum(c['caught'] for c in tests)} caught)."]

    merged = numbers["merged"]
    lines += ["", f"## {SETS['merged']}", "",
              f"**{len(merged)} finding(s) on the {touched['prs']} PRs** (95% interval for a PR getting one: "
              f"{interval(len({r['case'] for r in merged}), touched['prs'])})."] + table(merged)

    whole = [r for r in rows if r["set"] == "whole"]
    lines += [
        "", "## Merged code read as if it were new", "",
        "Right: a promise nothing waits for, by mistake. Wrong: a false alarm, mostly a call the project means to leave running. "
        "Unsure: can't tell from the code.",
        "",
        "| Read | Files | Lines | Whole-statement calls in async functions | Findings | Right | Wrong | Unsure |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [f"| {r['read']} | {r['files']:,} | {r['lines']:,} | {r['statements']:,} | {counts(r['findings'])} |" for r in numbers["read"]]
    unparsed = touched["unparsed"] + sum(m["unparsed"] for m in repos)
    lines += ["", f"{unparsed} more file(s) didn't parse cleanly or were over the size limit, and were skipped as the check skips them."]
    every = [r for r in rows if r["set"] in ("whole", "repos")]
    unmarked = sum(r["verdict"] not in VERDICTS for r in every)
    if unmarked:
        lines += ["", f"**{unmarked} finding(s) not marked yet**, so the counts are incomplete."]
    lines += ["", "By what the finding rests on:", "", "| Evidence | Findings | Right | Wrong | Unsure |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| {words} | {counts([r for r in every if marker in r['evidence']])} |" for marker, words in EVIDENCE.items()]
    listed = ["## Every finding", "", f"### {SETS['whole']}"] + (table(whole) or ["", "No findings."])
    for item in repos:
        found = [r for r in rows if r["set"] == "repos" and r["case"] == item["repo"]]
        listed += ["", f"### {item['repo']} at `{item['commit'][:7]}` ({item.get('set', '')})", "",
                   f"{item['files'] - item['unparsed']:,} files, {item['lines']:,} lines, {item['statements']:,} whole-statement calls in async "
                   f"functions: {len(found)} finding(s)."] + table(found)
    return "\n".join(lines), "\n".join(listed)


if __name__ == "__main__":
    raise SystemExit(main())
