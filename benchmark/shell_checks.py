"""How the ShellCheck and actionlint checks do on merged PRs.

Both checks are off unless `checks.shellcheck` and `checks.actionlint` are on,
and need the programs on PATH. This picks merged PRs that change a shell
script or a workflow, runs the two programs on each, and writes down every
issue they report on a line the PR adds, kept by the checks or not, so the
list of what is kept can be judged on them.

    python benchmark/shell_checks.py pick [--out benchmark/planted]
    python benchmark/shell_checks.py run --out benchmark/results/<name>
    python benchmark/shell_checks.py whole --out benchmark/results/<name>
    # mark the verdict column of issues.csv and whole.csv: right | wrong | unsure
    python benchmark/shell_checks.py score benchmark/results/<name>

Run it on Linux, the system of GitHub's runners: on Windows actionlint hangs
handing ShellCheck a long script, so `run:` scripts go unchecked there.

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
well. actionlint is run with ShellCheck on the `run:` scripts, which the check
doesn't do, to measure what that would add. It keeps the marks when it's
repeated. It also writes kept.csv, the checks' findings as a review gets them,
and prs.json, what each PR gave the programs to read.

Merged PRs add few lines, so `whole` reads every line of the same scripts and
workflows as if a PR had just added it, each file once, as the newest picked
PR left it, and writes whole.csv: what the checks keep, and every other error,
on any line, and whole.json, how much it read.

`score` writes summary.md: for each check, what it kept on added lines and on
whole files, with the marks, then every issue by program and code, kept or
not, then the folder's notes.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import corpus  # noqa: E402
from bench import CACHE, _fetch, _token, _write  # noqa: E402
from stratum_open.checks import actionlint, shellcheck  # noqa: E402
from stratum_open.checks.common import checked_lines, extension, find_program, is_test_path  # noqa: E402
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
# How actionlint reports what ShellCheck found in a `run:` script, and the `run:` line it puts it on.
_SCRIPT = re.compile(r"^shellcheck reported issue in this script: SC(\d+):(\w+):(\d+):\d+: (.*)$", re.DOTALL)
_RUN = re.compile(r"^\s*(?:-\s+)?run\s*:\s*(?P<rest>.*)$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    pick = sub.add_parser("pick")
    pick.add_argument("--out", default=str(HERE / "planted"))
    run = sub.add_parser("run")
    run.add_argument("--out", required=True)
    run.add_argument("--prs", default=str(HERE / "planted" / "clean-shell.json"))
    whole = sub.add_parser("whole")
    whole.add_argument("--out", required=True)
    whole.add_argument("--prs", default=str(HERE / "planted" / "clean-shell.json"))
    score = sub.add_parser("score")
    score.add_argument("out")
    args = parser.parse_args()
    if args.command == "pick":
        return _pick(Path(args.out))
    if args.command == "whole":
        return _whole(Path(args.out), Path(args.prs))
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
            # SC1107, an unknown directive, is the `# shellcheck disable=` the check switched off.
            if lines and issue["code"] != 1107:
                rows.append(_row("shellcheck", f"SC{issue['code']}", issue["level"], issue["path"], lines[0], added[issue["path"]][lines[0]],
                                 issue["message"], shellcheck.kept(issue["code"], issue["level"]) is not None))
    if flows:
        try:
            found = actionlint_rows({path: contents[path] for path in flows})
        except actionlint.ActionlintError as exc:
            errors.append(str(exc))
            found = []
        for row in found:
            mine = added[row["path"]]
            if row["line"] in mine and row["level"] != "info" and row["level"] != "style":
                row["text"] = mine[row["line"]].strip()[:200]
                rows.append(row)
    read = {
        "scripts": len(scripts), "script_lines": sum(len(added[p]) for p in scripts),
        "workflows": len(flows), "workflow_lines": sum(len(added[p]) for p in flows), "errors": errors,
        "unread": unread_scripts + unread_flows,
    }
    return rows, read


def actionlint_rows(sources: dict[str, str]) -> list[dict]:
    """A row for each of actionlint's issues, on any line, with ShellCheck run
    on the `run:` scripts too (except on Windows, where actionlint hangs on a
    long one): the check turns that off, and this measures what it would add.
    A script's issue goes on the line of the script it's about."""
    checker = find_program("shellcheck") if os.name != "nt" else None
    rows = []
    for issue in actionlint.run_actionlint(sources, scripts=checker or ""):
        script = _SCRIPT.match(issue["message"])
        if issue["kind"] == "shellcheck" and script:
            line = script_line(sources[issue["path"]], issue["line"], int(script.group(3)))
            if line is not None:
                rows.append(_row("actionlint+shellcheck", f"SC{script.group(1)}", script.group(2), issue["path"], line, "", script.group(4), False))
        elif issue["kind"] != "shellcheck":
            every = set(range(1, sources[issue["path"]].count("\n") + 2))
            rows.append(_row("actionlint", issue["kind"], "", issue["path"], issue["line"], "", issue["message"],
                             actionlint.to_finding(issue, every) is not None))
    return rows


def script_line(text: str, run_line: int, offset: int) -> int | None:
    """The workflow's line for line `offset` of the script at `run_line`: the
    same line for `run: echo`, the lines below it for `run: |`. A folded
    `run: >` joins its lines, so only its first is known."""
    lines = text.split("\n")
    found = _RUN.match(lines[run_line - 1]) if 0 < run_line <= len(lines) else None
    if found is None:
        return None
    rest = re.sub(r"\s+#.*$", "", found.group("rest")).strip()
    if rest.startswith("|"):
        return run_line + offset
    if rest.startswith(">"):
        return run_line + 1 if offset == 1 else None
    return run_line if offset == 1 else None


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
    _write(out / "kept.csv", FINDING_COLUMNS, findings)
    (out / "prs.json").write_text(json.dumps(listed, indent=1) + "\n", encoding="utf-8")
    return 0


def _whole(out: Path, prs_path: Path) -> int:
    """Every line of the scripts and workflows `run` read, as if a PR had
    just added it, each file once (as the newest PR left it). whole.csv gets
    what the checks keep, and every other ShellCheck error, on any line."""
    if not (shellcheck.available() and actionlint.available()):
        raise SystemExit("Put shellcheck and actionlint on PATH first.")
    marks = {_key(row): row for row in _read(out / "whole.csv")}
    seen: set[tuple[str, str]] = set()
    rows: list[dict] = []
    lines = {"scripts": 0, "script_lines": 0, "workflows": 0, "workflow_lines": 0, "errors": []}
    for item in json.loads(prs_path.read_text(encoding="utf-8")):
        repo, number = item["repo"], int(item["pr"])
        _, text = _fetch(repo, number)
        files = parse_diff(text)
        contents = head_contents(repo, number, item["head"], files)
        for tool, (read, _) in (("shellcheck", shellcheck.plan(files, contents)), ("actionlint", actionlint.plan(files, contents))):
            fresh = {path: contents[path] for path in read if (repo, path) not in seen}
            seen |= {(repo, path) for path in fresh}
            if not fresh:
                continue
            kind = "scripts" if tool == "shellcheck" else "workflows"
            lines[kind] += len(fresh)
            lines[kind[:-1] + "_lines"] += sum(len(t.split("\n")) for t in fresh.values())
            try:
                if tool == "shellcheck":
                    found = [_row(tool, f"SC{issue['code']}", issue["level"], issue["path"], issue["line"], "", issue["message"],
                                  shellcheck.kept(issue["code"], issue["level"]) is not None) for issue in shellcheck.run_shellcheck(fresh)]
                else:
                    found = actionlint_rows(fresh)
            except (shellcheck.ShellCheckError, actionlint.ActionlintError) as exc:
                lines["errors"].append(f"{repo}: {exc}")
                continue
            # Kept issues, and every other error, which is what the kept list is judged against.
            for row in found:
                if row["kept"] == "yes" or row["level"] in ("error", ""):
                    body = fresh[row["path"]].split("\n")
                    row["case"] = repo
                    row["text"] = body[row["line"] - 1].strip()[:200] if 0 < row["line"] <= len(body) else ""
                    rows.append(row)
    for row in rows:
        old = marks.get(_key(row))
        if old:
            row["verdict"], row["note"] = old["verdict"], old["note"]
    _write(out / "whole.csv", ISSUE_COLUMNS, rows)
    (out / "whole.json").write_text(json.dumps(lines, indent=1) + "\n", encoding="utf-8")
    print(f"{lines}; {len(rows)} row(s), {sum(1 for r in rows if r['kept'] == 'yes')} kept", flush=True)
    return 0


def _key(row: dict) -> tuple:
    return row["case"], row["tool"], row["code"], row["path"], str(row["line"]), row["message"]


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


# ── the summary ──────────────────────────────────────────────────────────

TOOLS = {"shellcheck": "ShellCheck", "actionlint": "actionlint"}


def scores(out: Path) -> list[dict]:
    """Per check, what a run adds up to, for its summary and the results page
    alike: the PRs and the lines it read, and the issues it keeps on added
    lines and on whole files, with their marks."""
    prs = json.loads((out / "prs.json").read_text(encoding="utf-8"))
    whole = json.loads((out / "whole.json").read_text(encoding="utf-8"))
    added, every = _read(out / "issues.csv"), _read(out / "whole.csv")
    rows = []
    for tool, name in TOOLS.items():
        files = "scripts" if tool == "shellcheck" else "workflows"
        kept = [row for row in added if row["kept"] == "yes" and row["tool"] == tool]
        kept_whole = [row for row in every if row["kept"] == "yes" and row["tool"] == tool]
        rows.append({
            "run": out.name, "check": name, "setting": f"checks.{tool}",
            "prs": sum(1 for p in prs if p[files]), "lines": sum(p[files[:-1] + "_lines"] for p in prs),
            "findings": len(kept), **_marks(kept),
            "files": whole[files], "file_lines": whole[files[:-1] + "_lines"],
            "whole": len(kept_whole), **{f"whole_{verdict}": count for verdict, count in _marks(kept_whole).items()},
        })
    return rows


def _marks(rows: list[dict]) -> dict:
    return {verdict: sum(1 for row in rows if row["verdict"] == verdict) for verdict in VERDICTS}


def _score(out: Path) -> int:
    prs = json.loads((out / "prs.json").read_text(encoding="utf-8"))
    lines = [
        "# ShellCheck and actionlint on merged PRs", "",
        f"{len(prs)} merged PRs as they are, and the scripts and workflows they change read whole, as if every line were new. "
        "Each issue was read and marked: right when what it says is true of the code, wrong when the code works as meant (a false alarm), unsure otherwise.", "",
        "| Check | PRs it read | Added lines | Findings | Right | Wrong | Unsure | Files read whole | Lines | Findings | Right | Wrong | Unsure |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in scores(out):
        lines.append(
            f"| `{row['setting']}` | {row['prs']} | {row['lines']:,} | {row['findings']} | {row['right']} | {row['wrong']} | {row['unsure']} "
            f"| {row['files']} | {row['file_lines']:,} | {row['whole']} | {row['whole_right']} | {row['whole_wrong']} | {row['whole_unsure']} |"
        )
    unread = sorted({path for p in prs for path in p.get("unread", [])})
    errors = [error for p in prs for error in p.get("errors", [])]
    lines += ["", f"On {sum(1 for p in prs if p.get('unread'))} PRs, {len(unread)} files weren't read (past the limit of files, too long, or without their text): "
              + ", ".join(f"`{path}`" for path in unread) + "." if unread else "", f"A program ran out of time {len(errors)} times." if errors else ""]
    for title, name in (("On added lines", "issues.csv"), ("On whole files", "whole.csv")):
        rows = _read(out / name)
        lines += ["", f"## {title}: every issue, kept or not", "", "| Program | Code or kind | Kept | Issues | Right | Wrong | Unsure |", "| --- | --- | --- | --- | --- | --- | --- |"]
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for row in rows:
            groups[(row["tool"], row["code"], row["kept"])].append(row)
        for (tool, code, kept), group in sorted(groups.items(), key=lambda item: (item[0][2] != "yes", item[0][0], -len(item[1]), item[0][1])):
            lines.append(f"| {tool} | `{code}` | {kept} | {len(group)} | " + " | ".join(str(sum(1 for r in group if r["verdict"] == v)) for v in VERDICTS) + " |")
        kept = [row for row in rows if row["kept"] == "yes"]
        if kept:
            lines += ["", "What the checks keep:", "", "| Where | Code or kind | Line | Verdict | Why |", "| --- | --- | --- | --- | --- |"]
            lines += [f"| {row['case']} `{row['path']}`:{row['line']} | `{row['code']}` | `{_cell(row['text'][:80])}` | {row['verdict']} | {_cell(row['note'])} |" for row in kept]
    notes = out / "notes.md"
    if notes.exists():
        lines += ["", notes.read_text(encoding="utf-8").strip()]
    (out / "summary.md").write_text("\n".join(line for line in lines) + "\n", encoding="utf-8")
    print(f"Wrote {out / 'summary.md'}")
    return 0


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
