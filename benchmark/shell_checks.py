"""How the ShellCheck and actionlint checks do on merged PRs.

Both checks are off unless `checks.shellcheck` and `checks.actionlint` are on,
and need the programs on PATH. This picks merged PRs that change a shell
script or a workflow, runs the two programs on each, and writes down every
issue they report on a line the PR adds, kept by the checks or not, so the
list of what is kept can be judged on them.

    python benchmark/shell_checks.py pick [--out benchmark/planted]
    python benchmark/shell_checks.py run --out benchmark/results/<name>
    # mark the verdict column of issues.csv: right | wrong | unsure
    python benchmark/shell_checks.py score benchmark/results/<name>

`pick` writes planted/clean-shell.json. For each project of corpus.py, and the
shell-heavy ones in SHELL_PROJECTS, from the same window of merged PRs and
under the same size limit: the SHELL_PER_PROJECT newest that add a line to a
`.sh` or `.bash` file outside the tests, and the WORKFLOWS_PER_PROJECT newest
that add a line to a workflow. A PR can be both. A script with no extension
can't be told from a listing, so none is picked for one, though the check
reads one a picked PR changes.

`run` fetches each PR's diff, and its scripts and workflows at its head (in
benchmark/.cache), and writes issues.csv: one row per issue on an added line,
with whether the checks keep it. ShellCheck's info and style notes aren't
asked for (`--severity=warning`), and in `run:` scripts they're left out as
well. It keeps the marks when it's repeated. It also writes checks.csv, the
checks' findings as a review gets them, and prs.json, what each PR gave the
programs to read.

`score` writes summary.md: the issues by tool and code, kept and left out,
with their marks, then the folder's notes.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import corpus  # noqa: E402
from bench import CACHE, _fetch, _token, _write  # noqa: E402
from stratum_open.checks import actionlint, shellcheck  # noqa: E402
from stratum_open.checks.common import checked_lines, extension, is_test_path  # noqa: E402
from stratum_open.diff import FileDiff, parse_diff  # noqa: E402
from stratum_open.github.api import GitHub, GitHubError  # noqa: E402

HERE = Path(__file__).resolve().parent
SHELL_PER_PROJECT = 10
WORKFLOWS_PER_PROJECT = 5
# Projects with much of their code in shell, of different kinds: package
# build scripts, a tool written in shell, shell plugins, and the build
# scripts of two big Go projects. Some run ShellCheck in their CI, some don't.
SHELL_PROJECTS = [
    "termux/termux-packages", "acmesh-official/acme.sh", "pyenv/pyenv", "dokku/dokku",
    "kubernetes/kubernetes", "Homebrew/brew", "nvm-sh/nvm", "moby/moby",
]
ISSUE_COLUMNS = ["case", "tool", "code", "level", "path", "line", "text", "message", "kept", "verdict", "note"]
FINDING_COLUMNS = ["case", "rule", "severity", "path", "line", "title"]
VERDICTS = ("right", "wrong", "unsure")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    pick = sub.add_parser("pick")
    pick.add_argument("--out", default=str(HERE / "planted"))
    run = sub.add_parser("run")
    run.add_argument("--out", required=True)
    run.add_argument("--prs", default=str(HERE / "planted" / "clean-shell.json"))
    score = sub.add_parser("score")
    score.add_argument("out")
    args = parser.parse_args()
    if args.command == "pick":
        return _pick(Path(args.out))
    return _run(Path(args.out), Path(args.prs)) if args.command == "run" else _score(Path(args.out))


# ── picking the PRs ──────────────────────────────────────────────────────


def is_script_path(path: str) -> bool:
    return extension(path) in shellcheck.EXTENSIONS and not is_test_path(path)


def _pick(out: Path) -> int:
    picked = []
    for repo in [repo for repos in corpus.PROJECTS.values() for repo in repos] + SHELL_PROJECTS:
        pulls = [p for p in corpus.merged_pulls(repo) if corpus.fits(p)]
        scripts = [p for p in pulls if any(is_script_path(path) for path in corpus.added_paths(p))][:SHELL_PER_PROJECT]
        flows = [p for p in pulls if any(actionlint.scope(path) for path in corpus.added_paths(p))][:WORKFLOWS_PER_PROJECT]
        numbers = sorted({p["number"] for p in scripts + flows}, reverse=True)
        by_number = {p["number"]: p for p in pulls}
        for number in numbers:
            slot = "+".join(name for name, group in (("shell", scripts), ("workflow", flows)) if by_number[number] in group)
            picked.append(corpus.entry(repo, by_number[number], slot))
        print(f"{repo}: {len(scripts)} that change a script, {len(flows)} a workflow", flush=True)
    corpus.write(out / "clean-shell.json", picked)
    return 0


# ── running the programs ─────────────────────────────────────────────────


def head_contents(repo: str, number: int, head: str, files: list[FileDiff]) -> dict[str, str]:
    """The text at the PR's head of every file the two checks might read:
    workflows, and what shellcheck.worth_reading would fetch. Cached."""
    path = CACHE / f"{repo.replace('/', '__')}__{number}__shell_contents.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    wanted = [f for f in files if f.status != "deleted" and not f.binary and (actionlint.scope(f.path) or shellcheck.worth_reading(f))]
    contents = {}
    gh = GitHub(_token(), repo) if wanted else None
    for file in wanted:
        try:
            text = gh.file_text(file.path, head)
        except GitHubError:
            text = None
        if text is not None:
            contents[file.path] = text
    path.write_text(json.dumps(contents), encoding="utf-8")
    return contents


def issues_on_added(files: list[FileDiff], contents: dict[str, str]) -> tuple[list[dict], dict]:
    """Every issue the two programs report on a line the PR adds, and what
    the PR gave them to read."""
    rows: list[dict] = []
    errors: list[str] = []
    scripts, unread_scripts = shellcheck.plan(files, contents)
    flows, unread_flows = actionlint.plan(files, contents)
    by_path = {file.path: file for file in files}
    added = {path: dict(checked_lines(by_path[path], contents, shellcheck.scope if path in scripts else actionlint.scope)) for path in scripts + flows}
    if scripts:
        sources = {path: shellcheck.without_disables(contents[path], set(added[path]))[0] for path in scripts}
        try:
            found = shellcheck.run_shellcheck(sources)
        except shellcheck.ShellCheckError as exc:  # the review gets a checks/error finding instead
            errors.append(str(exc))
            found = []
        for issue in found:
            lines = [n for n in range(issue["line"], issue["end_line"] + 1) if n in added[issue["path"]]]
            if lines:
                rows.append(_row("shellcheck", f"SC{issue['code']}", issue["level"], issue["path"], lines[0], added[issue["path"]][lines[0]],
                                 issue["message"], shellcheck.kept(issue["code"], issue["level"]) is not None))
    if flows:
        try:
            found = actionlint.run_actionlint({path: contents[path] for path in flows})
        except actionlint.ActionlintError as exc:
            errors.append(str(exc))
            found = []
        for issue in found:
            mine = added[issue["path"]]
            script = actionlint._SHELLCHECK.match(issue["message"])
            if issue["kind"] == "shellcheck" and script:
                code, level = int(script.group(1)), script.group(2)
                line = actionlint.script_line(contents[issue["path"]], issue["line"], int(script.group(3)))
                if level in ("error", "warning") and line in mine:
                    rows.append(_row("actionlint+shellcheck", f"SC{code}", level, issue["path"], line, mine[line], script.group(4),
                                     shellcheck.kept(code, level) is not None))
            elif issue["kind"] != "shellcheck" and issue["line"] in mine:
                kept = actionlint._finding(issue, contents[issue["path"]], set(mine)) is not None
                rows.append(_row("actionlint", issue["kind"], "", issue["path"], issue["line"], mine[issue["line"]], issue["message"], kept))
    read = {
        "scripts": len(scripts), "script_lines": sum(len(added[p]) for p in scripts),
        "workflows": len(flows), "workflow_lines": sum(len(added[p]) for p in flows), "errors": errors,
        "unread": unread_scripts + unread_flows,
    }
    return rows, read


def _row(tool: str, code: str, level: str, path: str, line: int, text: str, message: str, kept: bool) -> dict:
    return {"tool": tool, "code": code, "level": level, "path": path, "line": line, "text": text.strip()[:200],
            "message": message.replace("\n", " ")[:300], "kept": "yes" if kept else "no", "verdict": "", "note": ""}


def _run(out: Path, prs_path: Path) -> int:
    if not (shellcheck.available() and actionlint.available()):
        raise SystemExit("Put shellcheck and actionlint on PATH first.")
    out.mkdir(parents=True, exist_ok=True)
    marks = {_key(row): row for row in _read(out / "issues.csv")}
    prs = json.loads(prs_path.read_text(encoding="utf-8"))
    rows, findings, listed = [], [], []
    for item in prs:
        repo, number = item["repo"], int(item["pr"])
        _, text = _fetch(repo, number)
        files = parse_diff(text)
        contents = head_contents(repo, number, item["head"], files)
        found, read = issues_on_added(files, contents)
        case = f"{repo}#{number}"
        for row in found:
            row["case"] = case
            old = marks.get(_key(row))
            if old:
                row["verdict"], row["note"] = old["verdict"], old["note"]
        rows += found
        checked = [] if read["errors"] else shellcheck.check(files, contents) + actionlint.check(files, contents)
        for finding in checked:
            findings.append({"case": case, "rule": finding.rule, "severity": finding.severity, "path": finding.path,
                             "line": finding.line_start or "", "title": finding.title})
        listed.append({**item, **read})
        print(f"{case}: {read['scripts']} script(s), {read['workflows']} workflow(s), {len(found)} issue(s) on added lines"
              + "".join(f"; {error}" for error in read["errors"]), flush=True)
    _write(out / "issues.csv", ISSUE_COLUMNS, rows)
    _write(out / "checks.csv", FINDING_COLUMNS, findings)
    (out / "prs.json").write_text(json.dumps(listed, indent=1) + "\n", encoding="utf-8")
    return 0


def _key(row: dict) -> tuple:
    return row["case"], row["tool"], row["code"], row["path"], str(row["line"]), row["message"]


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


# ── the summary ──────────────────────────────────────────────────────────


def scores(out: Path) -> dict:
    """What a run adds up to, for its summary and the results page alike."""
    prs = json.loads((out / "prs.json").read_text(encoding="utf-8"))
    rows = _read(out / "issues.csv")
    kept = [row for row in rows if row["kept"] == "yes"]
    return {
        "prs": len(prs),
        "script_prs": sum(1 for p in prs if p["scripts"]), "workflow_prs": sum(1 for p in prs if p["workflows"]),
        "script_lines": sum(p["script_lines"] for p in prs), "workflow_lines": sum(p["workflow_lines"] for p in prs),
        "errors": sum(1 for p in prs if p.get("errors")),
        "unread": sorted({path for p in prs for path in p.get("unread", [])}), "unread_prs": sum(1 for p in prs if p.get("unread")),
        "issues": len(rows), "kept": len(kept), "kept_prs": len({row["case"] for row in kept}),
        **{verdict: sum(1 for row in kept if row["verdict"] == verdict) for verdict in VERDICTS},
    }


def _score(out: Path) -> int:
    s = scores(out)
    rows = _read(out / "issues.csv")
    lines = [
        "# ShellCheck and actionlint on merged PRs", "",
        f"{s['prs']} merged PRs: {s['script_prs']} gave ShellCheck a script to read ({s['script_lines']:,} added lines), "
        f"{s['workflow_prs']} gave actionlint a workflow ({s['workflow_lines']:,} added lines). "
        f"On {s['errors']} a program didn't finish in its time limit, which a review reports as a check that didn't run. "
        f"{len(s['unread'])} files on {s['unread_prs']} PRs weren't read (too long, or without their text): "
        + ", ".join(f"`{path}`" for path in s["unread"]) + ".", "",
        f"The checks keep {s['kept']} of the {s['issues']} issues the programs report on added lines, on {s['kept_prs']} PRs: "
        f"{s['right']} right, {s['wrong']} wrong (false alarms), {s['unsure']} unsure.", "",
        "| Tool | Code | Kept | Issues | PRs | Right | Wrong | Unsure |", "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["tool"], row["code"], row["kept"])].append(row)
    for (tool, code, kept), group in sorted(groups.items(), key=lambda item: (item[0][2] != "yes", item[0][0], -len(item[1]), item[0][1])):
        counts = " | ".join(str(sum(1 for r in group if r["verdict"] == v)) for v in VERDICTS)
        lines.append(f"| {tool} | `{code}` | {kept} | {len(group)} | {len({r['case'] for r in group})} | {counts} |")
    marked = [row for row in rows if row["kept"] == "yes"]
    if marked:
        lines += ["", "## Every issue the checks keep", "", "| PR | Code | Where | Message | Verdict | Why |", "| --- | --- | --- | --- | --- | --- |"]
        for row in marked:
            lines.append(f"| {row['case']} | `{row['code']}` | `{row['path']}`:{row['line']} | {_cell(row['message'])} | {row['verdict']} | {_cell(row['note'])} |")
    notes = out / "notes.md"
    if notes.exists():
        lines += ["", notes.read_text(encoding="utf-8").strip()]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out / 'summary.md'}")
    return 0


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
