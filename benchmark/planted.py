"""The planted-bug benchmark: does a review find a bug we know is there?

`bench.py` reviews merged PRs as they are, so judging its findings needs a
maintainer. Here each case is a real merged PR with one bug planted on a line
it adds (a flipped comparison, a dropped `await`, an off-by-one), so the
answer is known before the model reads it.

    python benchmark/planted.py run --bugs benchmark/planted/bugs.json --out benchmark/results/<name>/raw/7b-1 \\
        --model qwen2.5-coder:7b@http://localhost:11434/v1 [--shard 1/8]
    python benchmark/planted.py score benchmark/results/<name>

    python benchmark/planted.py checks --risks benchmark/planted/risks.yml --prs benchmark/planted/clean.json \\
        --out benchmark/results/<name>-checks [--policy benchmark/planted/variants/known-vulnerabilities.yml]

    python benchmark/planted.py validate --bugs benchmark/planted/bugs.json --bugs benchmark/planted/bugs-2.json

`run` reviews each case the way the Action does (the `small` profile, an
8,192-token window) and writes reviews.csv and findings.csv. A finding counts
as near the bug when it's on the planted file within LINES_NEAR lines of it.
A case whose `replace` is null drops its line instead of changing it (a
statement the PR should have written): the diff is numbered as if the line
were never there, and a finding is near within LINES_NEAR lines of the gap.
Mark the `verdict` column of findings.csv by hand: `right` for a finding near
the bug that describes it, or for any other finding that's a real problem;
`wrong` or `unsure` otherwise. `score` reads every reviews.csv under the run
folder (one per shard), so shards run as separate jobs.

Some checks read the repository, not only the PR. For those a planted case
can have `repo`, the files already on the default branch (a file the case has
too is then a change to it, not a new file), and `checks --repos <folder>`
reads each merged PR against a clone of its project, as it was just before
the PR merged (unknown_names.py makes the clones).

`checks` runs the checks that need no model: on planted risks (risks.yml),
each of which should get its rule's finding, and on merged PRs as they are,
where every finding is a possible false alarm for a person to mark. A case
marked `control: true` is the other way round: files its rule must leave
alone. `--policy` is a policy file, for a rule that is off until a key
turns it on. `checks.ruff` is turned on as well, so Ruff's rules are
measured when Ruff is installed.

`validate` reads every case of a case file against its PR, before a model
does: the fields are in order, the line is one the PR adds and reads `find`,
and the file still parses with the bug in it. It notes, without failing, a
`find` that several added lines match and a `replace` the PR's diff already
holds; `--strict` fails on those too.
"""

from __future__ import annotations

import argparse
import dataclasses
import ast
import csv
import difflib
import json
import os
import random
import re
import statistics
import string
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from bench import CACHE, _fetch, _token, _write  # noqa: E402
import removed_names  # noqa: E402
import unknown_names  # noqa: E402
from stratum_open.checks import run_checks  # noqa: E402
from stratum_open.checks.bandit import available as bandit_available  # noqa: E402
from stratum_open.checks.dependencies import PackageLookup, manifest_kind  # noqa: E402
from stratum_open.checks.ruff import available as ruff_available  # noqa: E402
from stratum_open.diff import FileDiff, Hunk, Line, parse_diff  # noqa: E402
from stratum_open.engine import analyze  # noqa: E402
from stratum_open.github.api import GitHub, GitHubError  # noqa: E402
from stratum_open.runner import MAX_BANDIT_FILES, MAX_CONTEXT_FILES  # noqa: E402
from stratum_open.model import Model  # noqa: E402
from stratum_open.policy import parse_policy  # noqa: E402
from stratum_open.repo.removed_names import RULE as REMOVED_NAME, removed_name_findings  # noqa: E402
from stratum_open.repo.unknown_names import RULE as UNKNOWN_NAME, unknown_name_findings  # noqa: E402

LINES_NEAR = 3
POLICY = "review:\n  profile: small\n  context_tokens: {tokens}\n"  # what the Action runs with Ollama
REVIEW_COLUMNS = ["case", "set", "kind", "path", "line", "model", "variant", "version", "seconds", "shown", "calls", "failed_calls", "findings", "near", "error"]
FINDING_COLUMNS = ["case", "model", "variant", "version", "severity", "category", "path", "line", "near", "title", "detail", "verdict", "note"]
BUGS = Path(__file__).resolve().parent / "planted" / "bugs.json"
CASE_KEYS = ("repo", "pr", "path", "line", "kind", "set", "find", "replace", "why")  # and `invalid`, on a case found not to be a bug
CHECK_COLUMNS = ["set", "case", "rule", "path", "line", "title", "expected", "verdict", "note"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--bugs", required=True)
    run.add_argument("--model", required=True, help="name@endpoint")
    run.add_argument("--out", required=True)
    run.add_argument("--shard", default="1/1", help="k/n: run every n-th case, starting at the k-th")
    run.add_argument("--context-tokens", type=int, default=8192)
    run.add_argument("--set", choices=("dev", "test", "all"), default="all",
                     help="tune on dev; keep test for the result (each case's set is in bugs.json)")
    run.add_argument("--clean", action="store_true", help="review the PRs as merged, without the bug: the control")
    run.add_argument("--policy", help="a YAML file merged over the Action's settings, for trying a change")
    run.add_argument("--variant", default="", help="a name for what --policy changes, kept with the results")
    fetch = sub.add_parser("fetch", help="fill the cache, so parallel jobs needn't each call GitHub")
    fetch.add_argument("--bugs", required=True)
    validate = sub.add_parser("validate", help="check every case against its PR, before a model reads any of them")
    validate.add_argument("--bugs", required=True, action="append", help="a case file; repeat for several")
    validate.add_argument("--strict", action="store_true", help="also fail on the notes, such as a `find` that several added lines match")
    score = sub.add_parser("score")
    score.add_argument("out")
    checks = sub.add_parser("checks")
    checks.add_argument("--risks", required=True)
    checks.add_argument("--prs", required=True)
    checks.add_argument("--repos", help="a folder with a clone of each PR's project, for the checks that read the repository")
    checks.add_argument("--out", required=True)
    checks.add_argument("--policy", help="a YAML policy file, for a rule that a key turns on")
    args = parser.parse_args()
    if args.command == "fetch":
        for bug in json.loads(Path(args.bugs).read_text(encoding="utf-8")):
            _, text = _fetch(bug["repo"], int(bug["pr"]))
            _head_contents(bug["repo"], int(bug["pr"]), parse_diff(text))
        return 0
    return {"run": _run, "score": lambda a: _score(Path(a.out)), "checks": _checks, "validate": _validate}[args.command](args)


# ── the model review ─────────────────────────────────────────────────────


def plant(bug: dict, files: list[FileDiff]) -> Line:
    """Swap the planted line into the parsed diff; fails loudly if the PR's
    line isn't what bugs.json says, so a stale case can't pass for a miss.

    A case whose `replace` is null drops the line instead, as if the PR had
    never written it. The line returned is then the one beside the gap, which
    is what `shown` can look for."""
    for file in files:
        if file.path != bug["path"]:
            continue
        for hunk in file.hunks:
            for index, line in enumerate(hunk.lines):
                if line.kind == "+" and line.new == bug["line"]:
                    if line.text != bug["find"]:
                        raise SystemExit(f"{bug['repo']}#{bug['pr']} {bug['path']}:{bug['line']} reads {line.text!r}, not {bug['find']!r}")
                    if bug["replace"] is None:
                        return _drop(bug, file, hunk, index)
                    line.text = bug["replace"]
                    return line
    raise SystemExit(f"{bug['repo']}#{bug['pr']}: no added line {bug['path']}:{bug['line']}")


def _drop(bug: dict, file: FileDiff, hunk: Hunk, index: int) -> Line:
    """Take an added line out of the diff. Every later line of the file moves
    up by one, so the diff still numbers the new file as it would be without
    the line, and `bug["line"]` is now the number of the line that followed it:
    the gap is just above. Returns that line, or the one above the gap when
    the dropped line ended its hunk."""
    del hunk.lines[index]
    if not any(line.kind == "+" for line in hunk.lines):
        # The PR wouldn't have this hunk, or would only remove lines there,
        # which the model is told of in a note and can't comment on.
        raise SystemExit(f"{bug['repo']}#{bug['pr']} {bug['path']}:{bug['line']} is the only line its hunk adds, so without it the diff wouldn't show the place")
    after = False
    for other in file.hunks:
        if other is hunk:
            after, moved = True, hunk.lines[index:]
        elif after:
            other.new_start -= 1
            moved = other.lines
        else:
            continue
        for line in moved:
            if line.new is not None:
                line.new -= 1
    below = [line for line in hunk.lines[index:] if line.new is not None]
    return below[0] if below else [line for line in hunk.lines[:index] if line.new is not None][-1]


def near(finding, bug: dict) -> bool:
    if finding.path != bug["path"] or finding.line_start is None:
        return False
    return finding.line_start - LINES_NEAR <= bug["line"] <= (finding.line_end or finding.line_start) + LINES_NEAR


def shown(chunks: list[str], path: str, line: Line) -> bool:
    """Whether the planted line, numbered as `render` numbers it, is in the
    planted file's part of a prompt: its text alone (`return True`) could be
    anywhere else in a clipped diff. For a dropped line it's the line beside
    the gap, which the PR may have added or only left as it was."""
    numbered = f"{line.new:>6} {line.kind}  {line.text}"
    return any(
        section.startswith(f"{path} (") and numbered in section.split("\n")
        for chunk in chunks for section in ("\n" + chunk).split("\n## ")[1:]
    )


def policy_for(tokens: int, extra: str | None = None):
    """The Action's settings, with a --policy file's keys merged over them."""
    settings = yaml.safe_load(POLICY.format(tokens=tokens))
    for section, values in ((yaml.safe_load(extra) if extra else None) or {}).items():
        settings.setdefault(section, {}).update(values or {})
    return parse_policy(yaml.safe_dump(settings))


def original(bug: dict, files: list[FileDiff]) -> Line:
    """The line a bug would be planted on, left as merged (the clean control)."""
    for file in files:
        for hunk in file.hunks if file.path == bug["path"] else []:
            for line in hunk.lines:
                if line.kind == "+" and line.new == bug["line"]:
                    return line
    raise SystemExit(f"{bug['repo']}#{bug['pr']}: no added line {bug['path']}:{bug['line']}")


def with_line(contents: dict[str, str], bug: dict, text: str | None) -> dict[str, str]:
    """The files' full text as the Action reads it, with the planted line in
    it too (or out of it, when `text` is None: a dropped line), so code shown
    around a hunk (`review.context_lines`) agrees with the diff."""
    contents = dict(contents)
    lines = contents.get(bug["path"], "").split("\n")
    if len(lines) >= bug["line"] and lines[bug["line"] - 1] in (bug["find"], bug["replace"]):
        lines[bug["line"] - 1:bug["line"]] = [] if text is None else [text]
        contents[bug["path"]] = "\n".join(lines)
    return contents


def _run(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    k, n = (int(part) for part in args.shard.split("/"))
    bugs = [b for b in json.loads(Path(args.bugs).read_text(encoding="utf-8"))
            if args.set in ("all", b.get("set")) and not b.get("invalid")][k - 1::n]
    name, _, endpoint = args.model.partition("@")
    model = Model(endpoint or "http://localhost:11434/v1", name, api_key=os.environ.get("STRATUM_MODEL_API_KEY"), context_tokens=args.context_tokens)
    policy = policy_for(args.context_tokens, Path(args.policy).read_text(encoding="utf-8") if args.policy else None)
    version = "clean" if args.clean else "planted"
    reviews, findings = [], []
    for bug in bugs:
        case = f"{bug['repo']}#{bug['pr']}"
        pull, text = _fetch(bug["repo"], int(bug["pr"]))
        files = parse_diff(text)
        planted = original(bug, files) if args.clean else plant(bug, files)
        dropped = bug["replace"] is None and not args.clean
        contents = with_line(_head_contents(bug["repo"], int(bug["pr"]), files), bug, None if dropped else planted.text)
        started = time.monotonic()
        result = analyze(title=pull.get("title") or "", description=pull.get("body") or "", files=files, policy=policy, model=model,
                         contents=contents)
        seconds = time.monotonic() - started
        calls = result.review.calls if result.review else []
        chunks = result.review.packed.chunks if result.review and result.review.packed else []
        mine = [f for f in result.findings if f.source == "model"]
        hits = [f for f in mine if near(f, bug)]
        reviews.append({
            "case": case, "set": bug.get("set", ""), "kind": bug["kind"], "path": bug["path"], "line": bug["line"], "model": name,
            "variant": args.variant, "version": version, "seconds": round(seconds, 1), "shown": "yes" if shown(chunks, bug["path"], planted) else "no",
            "calls": len(calls), "failed_calls": result.review.failed_calls if result.review else 0,
            "findings": len(mine), "near": len(hits),
            "error": "; ".join(sorted({c["error"] for c in calls if c.get("error")}))[:300],
        })
        for f in mine:
            findings.append({
                "case": case, "model": name, "variant": args.variant, "version": version, "severity": f.severity, "category": f.category, "path": f.path,
                "line": f.line_start or "", "near": "yes" if f in hits else "", "title": f.title,
                "detail": " ".join(f.detail.split()), "verdict": "", "note": "",
            })
        print(f"{case} {name} {args.variant} {version}: {len(mine)} finding(s), {len(hits)} near the bug, {seconds:.0f}s"
              + (f", error: {reviews[-1]['error']}" if reviews[-1]["error"] else ""), flush=True)
        _write(out / "reviews.csv", REVIEW_COLUMNS, reviews)
        _write(out / "findings.csv", FINDING_COLUMNS, findings)
    return 0


def case_format(bug: object) -> list[str]:
    """What is wrong with a case's fields, before its PR is read."""
    if not isinstance(bug, dict):
        return ["isn't an object"]
    problems = [f"has no `{key}`" for key in CASE_KEYS if key not in bug]
    problems += [f"has an unknown key `{key}`" for key in bug if key not in (*CASE_KEYS, "invalid")]
    if problems:
        return problems
    if not (isinstance(bug["repo"], str) and re.fullmatch(r"[\w.-]+/[\w.-]+", bug["repo"])):
        problems.append("`repo` isn't owner/name")
    for key in ("pr", "line"):
        if type(bug[key]) is not int or bug[key] < 1:
            problems.append(f"`{key}` isn't a positive whole number")
    for key in ("path", "kind", "find", "why", *(["invalid"] if "invalid" in bug else [])):
        if not (isinstance(bug[key], str) and bug[key].strip()):
            problems.append(f"`{key}` is empty or isn't text")
    if bug["set"] not in ("dev", "test"):
        problems.append("`set` is neither dev nor test")
    if bug["replace"] is not None and not isinstance(bug["replace"], str):
        problems.append("`replace` is neither text nor null (null drops the line)")
    elif bug["replace"] == bug["find"]:
        problems.append("`replace` is the same as `find`")
    return problems


def case_problems(bug: dict, files: list[FileDiff], contents: dict[str, str]) -> tuple[list[str], list[str]]:
    """A well-formed case against its PR's diff and the files at its head:
    what makes it unusable, and notes. The bug has to be on a line the PR
    adds, since only those are reviewed, and the file has to parse with the
    bug in it, so that a finding there isn't just a syntax error's."""
    where = f"{bug['path']}:{bug['line']}"
    file = next((f for f in files if f.path == bug["path"]), None)
    added = file.added() if file else []
    here = [text for number, text in added if number == bug["line"]]
    if not here:
        return [f"the PR adds no line {where}"], []
    if here[0] != bug["find"]:
        return [f"{where} reads {here[0]!r}, not `find`"], []
    problems, notes = [], []
    same = [number for number, text in added if text == bug["find"]]
    if len(same) > 1:
        notes.append(f"`find` is on {len(same)} added lines of {bug['path']} ({', '.join(map(str, same))}): only the line number tells them apart")
    if bug["replace"] in file.removed():
        notes.append(f"`replace` is a line the PR removes from {bug['path']}: planted, the diff would show it removed and added back")
    if bug["replace"] in [text for number, text in added if number != bug["line"]]:
        notes.append(f"`replace` is already an added line of {bug['path']}: planted, the diff would hold it twice")
    hunk = next(h for h in file.hunks if any(line.kind == "+" and line.new == bug["line"] for line in h.lines))
    if bug["replace"] is None and not any(line.kind == "+" and line.new != bug["line"] for line in hunk.lines):
        problems.append(f"{where} is the only line its hunk adds, so without it the diff wouldn't show the place")
    text = contents.get(bug["path"])
    planted = with_line(contents, bug, bug["replace"]).get(bug["path"])
    if text is None:
        problems.append(f"the cache has no text of {bug['path']} at the PR's head, so it can't be parsed")
    elif planted == text:
        problems.append(f"{where} at the PR's head doesn't read `find`")
    elif error := _syntax_error(text):
        problems.append(f"{bug['path']} doesn't parse as merged, on Python {sys.version_info.major}.{sys.version_info.minor}: {error}")
    elif error := _syntax_error(planted):
        problems.append(f"{bug['path']} doesn't parse with the bug planted: {error}")
    elif bug["replace"] is None and not _is_statement(text, bug["line"]):
        problems.append(f"{where} isn't a whole statement, and only a whole one can be dropped")
    return problems, notes


def _syntax_error(text: str) -> str:
    try:
        ast.parse(text)
    except (SyntaxError, ValueError) as error:
        return f"{getattr(error, 'msg', error)} (line {getattr(error, 'lineno', '?')})"
    return ""


def _is_statement(text: str, line: int) -> bool:
    """Whether the line holds one statement, all of it."""
    return any(isinstance(node, ast.stmt) and node.lineno == line == node.end_lineno for node in ast.walk(ast.parse(text)))


def validate(bugs: object, load: Callable[[dict], tuple[list[FileDiff], dict[str, str]]]) -> list[tuple[str, list[str], list[str]]]:
    """Every case of a case file checked: (case, problems, notes) for each one
    that has either. `load` gives a case's parsed diff and its files' text."""
    if not isinstance(bugs, list):
        return [("the file", ["isn't a list of cases"], [])]
    found, seen = [], set()
    for index, bug in enumerate(bugs, start=1):
        problems, notes = case_format(bug), []
        case = f"{bug['repo']}#{bug['pr']}" if not problems else f"case {index}"
        if not problems and case in seen:
            problems.append("is in the file twice: a PR holds one planted bug")
        seen.add(case)
        if not problems:
            problems, notes = case_problems(bug, *load(bug))
        if problems or notes:
            found.append((case, problems, notes))
    return found


def _validate(args) -> int:
    def load(bug: dict) -> tuple[list[FileDiff], dict[str, str]]:
        files = parse_diff(_fetch(bug["repo"], int(bug["pr"]))[1])
        return files, _head_contents(bug["repo"], int(bug["pr"]), files)

    failed = False
    for name in args.bugs:
        bugs = json.loads(Path(name).read_text(encoding="utf-8"))
        found = validate(bugs, load)
        for case, problems, notes in found:
            for problem in problems:
                print(f"{case}: {problem}")
            for note in notes:
                print(f"{case}: note: {note}")
        wrong = sum(len(problems) for _, problems, _ in found)
        noted = sum(len(notes) for _, _, notes in found)
        failed = failed or bool(wrong) or (args.strict and bool(noted))
        print(f"{name}: {len(bugs) if isinstance(bugs, list) else 0} case(s), {wrong} problem(s), {noted} note(s)", flush=True)
    return 1 if failed else 0


def _score(out: Path) -> int:
    """summary.md for whatever the run folder holds: the checks' results,
    the model reviews (every reviews.csv under it, one per shard), or both."""
    parts = []
    if (out / "checks.csv").exists():
        parts.append(_checks_summary(out))
    if any(out.rglob("reviews.csv")):
        parts.append(_model_summary(out))
    if not parts:
        raise SystemExit(f"Nothing to score in {out}: no checks.csv, and no reviews.csv under it")
    notes = out / "notes.md"
    if notes.exists():
        parts.append(notes.read_text(encoding="utf-8").strip())
    (out / "summary.md").write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    print((out / "summary.md").read_text(encoding="utf-8"))
    return 0


def _cases_of(reviews: list[dict]) -> list[dict]:
    """Every case of the case files (planted/bugs*.json) a run's cases come
    from. No PR is in two of them, so a case names its file."""
    ran = {row["case"] for row in reviews}
    files = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(BUGS.parent.glob("bugs*.json"))]
    return [bug for bugs in files if any(f"{b['repo']}#{b['pr']}" in ran for b in bugs) for bug in bugs]


def _model_summary(out: Path) -> str:
    reviews = [row for path in sorted(out.rglob("reviews.csv")) for row in _read(path)]
    every = _cases_of(reviews)
    sets = {f"{b['repo']}#{b['pr']}": b.get("set", "") for b in every}
    invalid = {f"{b['repo']}#{b['pr']}": b["invalid"] for b in every if b.get("invalid")}
    # A case found not to be a bug after a run is left out of its scores too.
    reviews = [row for row in reviews if row["case"] not in invalid]
    findings = [row for path in sorted(out.rglob("findings.csv")) for row in _read(path) if row["case"] not in invalid]
    for row in reviews + findings:  # runs from before the clean control and the split
        row["variant"] = row.get("variant") or ""
        row["version"] = row.get("version") or "planted"
        row["set"] = row.get("set") or sets.get(row["case"], "")
    runs: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in reviews:
        runs[(row["model"], row["variant"])][row["version"]].append(row)
    marks: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for row in findings:
        marks[(row["model"], row["variant"], row["version"], row["case"])].append(row)
    cases = {row["case"] for row in reviews}
    lines = [
        "# Planted-bug benchmark",
        "",
        f"{len(cases)} real merged PRs, each with one bug planted on a line it adds. Each model reviewed",
        "them as the Action does: the `small` profile and an 8,192-token window, unless a variant says",
        "otherwise. Only the model's own findings count. A finding is near the bug when it's on that file",
        f"within {LINES_NEAR} lines; found means it was read and it describes the bug. Where the PRs were also",
        "reviewed as merged (the clean control), *near only with the bug* counts the cases whose clean",
        "version got no finding on that line: a model that comments on every changed line doesn't score.",
        "",
        "| Model | Variant | PRs | Near the bug | Near it when clean | Near only with the bug | Found (dev + test) "
        "| Other findings | Other findings marked right | Findings on the clean PRs | Failed calls | Median time |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    order = sorted(runs, key=lambda key: (_size(key[0]), key[0], key[1]))
    for model, variant in order:
        planted, clean = runs[(model, variant)]["planted"], runs[(model, variant)]["clean"]
        if not planted:
            continue
        seen = [r for r in planted if r["shown"] == "yes"]
        near = {r["case"] for r in seen if int(r["near"])}
        near_clean = {r["case"] for r in clean if int(r["near"])}
        found = [r for r in seen if any(m["near"] == "yes" and m["verdict"] == "right" for m in marks[(model, variant, "planted", r["case"])])]
        dev = sum(r["set"] == "dev" for r in found)
        other = [m for r in planted for m in marks[(model, variant, "planted", r["case"])] if m["near"] != "yes"]
        other_marked = sum(m["verdict"] in ("right", "wrong") for m in other)
        on_clean = sum(int(r["findings"]) for r in clean)
        rows = planted + clean
        lines.append(
            f"| `{model}` | {variant or '-'} | {len(planted)} | {len(near)} of {len(seen)} "
            f"| {f'{len(near_clean)} of {len(clean)}' if clean else '-'} "
            f"| {f'{len(near - near_clean)} of {len(seen)}' if clean else '-'} "
            f"| {len(found)} of {len(seen)} ({dev} + {len(found) - dev}) "
            f"| {len(other)} | {sum(m['verdict'] == 'right' for m in other)} of {other_marked} marked "
            f"| {on_clean if clean else '-'} | {sum(int(r['failed_calls']) for r in rows)} of {sum(int(r['calls']) for r in rows)} "
            f"| {statistics.median(float(r['seconds']) for r in rows):.0f}s |"
        )
    kinds = sorted({r["kind"] for r in reviews})
    lines += ["", "Found, by kind of bug:", "", "| Model | Variant | " + " | ".join(kinds) + " |", "| --- | --- |" + " --- |" * len(kinds)]
    for model, variant in order:
        planted = runs[(model, variant)]["planted"]
        if not planted:
            continue
        cells = []
        for kind in kinds:
            mine = [r for r in planted if r["kind"] == kind and r["shown"] == "yes"]
            hit = sum(any(m["near"] == "yes" and m["verdict"] == "right" for m in marks[(model, variant, "planted", r["case"])]) for r in mine)
            cells.append(f"{hit} of {len(mine)}")
        lines.append(f"| `{model}` | {variant or '-'} | " + " | ".join(cells) + " |")
    unmarked = sum(1 for m in findings if m["version"] == "planted" and not m["verdict"])
    if unmarked:
        lines += ["", f"**{unmarked} finding(s) not marked yet**, so Found and the marked counts are incomplete."]
    for case, why in sorted(invalid.items()):
        lines += ["", f"Left out: {case}. {why}"]
    return "\n".join(lines)


def _size(model: str) -> float:
    """Models in order of size, where the name says it (qwen2.5-coder:7b)."""
    tail = model.rsplit(":", 1)[-1].rstrip("bB")
    try:
        return float(tail)
    except ValueError:
        return float("inf")


# ── the checks that need no model ────────────────────────────────────────


def _checks(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    policy = parse_policy(Path(args.policy).read_text(encoding="utf-8") if args.policy else "")
    if policy.errors or policy.warnings:  # a mistyped key would leave its rule off, and the run would measure nothing
        raise SystemExit(f"{args.policy}: " + " ".join(policy.errors + policy.warnings))
    policy = dataclasses.replace(policy, checks=dataclasses.replace(policy.checks, ruff=True))  # off by default, and measured here like the rest
    lookup = PackageLookup()
    rows, results = [], []
    for case in yaml.safe_load(Path(args.risks).read_text(encoding="utf-8")):
        files, contents = _case_files(case)
        found = run_checks(files, policy, lookup=lookup, contents=contents, pr_text=case.get("title", ""))
        found += _repo_checks(case, files, policy, contents)
        rules = case["rule"] if isinstance(case["rule"], list) else [case["rule"]]
        control = bool(case.get("control"))
        caught = any(f.rule in rules for f in found)
        results.append({"id": case["id"], "rule": " or ".join(rules), "caught": caught, "found": sorted({f.rule for f in found})} | ({"control": True} if control else {}))
        for f in found:
            expected = f.rule in rules and not control
            flagged = f.rule in rules and control
            rows.append({"set": "planted", "case": case["id"], "rule": f.rule, "path": f.path, "line": f.line_start or "",
                         "title": f.title, "expected": "yes" if expected else "", "verdict": "right" if expected else "wrong" if flagged else "",
                         "note": "A control: its rule should have left this alone." if flagged else ""})
        outcome = ("FLAGGED" if caught else "left alone") if control else ("caught" if caught else "MISSED")
        print(f"{case['id']}: {outcome} ({', '.join(sorted({f.rule for f in found})) or 'no findings'})", flush=True)
    prs = json.loads(Path(args.prs).read_text(encoding="utf-8"))
    if not policy.checks.unknown_names:
        print("checks.unknown_names is off, so its cases are missed: --policy benchmark/planted/variants/unknown-names.yml.")
    elif not args.repos:
        print("No --repos, so the unknown-names check doesn't see the merged PRs: benchmark/unknown_names.py clone makes the clones.")
    if not policy.checks.removed_names:
        print("checks.removed_names is off, so its cases are missed: --policy benchmark/planted/variants/removed-names.yml.")
    elif not args.repos:
        print("No --repos, so the removed-names check doesn't see the merged PRs: benchmark/unknown_names.py clone makes the clones.")
    for item in prs:
        repo, number = item["repo"], int(item["pr"])
        pull, text = _fetch(repo, number)
        files = parse_diff(text)
        found = run_checks(files, policy, lookup=lookup, contents=_head_contents(repo, number, files), pr_text=f"{pull.get('title') or ''}\n{pull.get('body') or ''}")
        if args.repos:
            found += unknown_names.at_base(Path(args.repos), repo, number, files, policy, out)
            found += removed_names.at_base(Path(args.repos), repo, number, files, policy, out)
        for f in found:
            rows.append({"set": "merged", "case": f"{repo}#{number}", "rule": f.rule, "path": f.path, "line": f.line_start or "",
                         "title": f.title, "expected": "", "verdict": "", "note": ""})
        print(f"{repo}#{number}: {len(found)} finding(s)", flush=True)
    _write(out / "checks.csv", CHECK_COLUMNS, rows)
    (out / "planted.json").write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    (out / "prs.json").write_text(json.dumps(prs, indent=1) + "\n", encoding="utf-8")
    if not policy.checks.weakened_tests:
        print("checks.weakened_tests is off, so its cases are missed: --policy benchmark/planted/variants/weakened-tests.yml.")
    if not bandit_available():
        print("Bandit isn't installed, so its cases are missed: pip install bandit.")
    if not policy.dependencies.known_vulnerabilities:
        print("dependencies.known_vulnerabilities is off, so its cases are missed: --policy benchmark/planted/variants/known-vulnerabilities.yml.")
    if not ruff_available():
        print("Ruff isn't installed, so its cases are missed: pip install ruff.")
    return _score(out)


def _checks_summary(out: Path) -> str:
    cases = json.loads((out / "planted.json").read_text(encoding="utf-8"))
    planted, controls = [c for c in cases if not c.get("control")], [c for c in cases if c.get("control")]
    rows = _read(out / "checks.csv")
    merged = [r for r in rows if r["set"] == "merged"]
    prs = json.loads((out / "prs.json").read_text(encoding="utf-8"))
    groups: dict[str, list[dict]] = defaultdict(list)
    for case in planted:
        groups[case["rule"].split("/")[0].split(" ")[0]].append(case)
    names = {"code": "Secrets and risky code", "bandit": "Bandit (optional extra)", "ruff": "Ruff (optional extra, off by default)",
             "workflow": "GitHub workflows",
             "deps": "Dependencies", "agents": "Coding-agent files", "unicode": "Hidden Unicode", "tests": "Weakened tests"}
    # The rules that are off by default are each measured on their own, so each gets its own line.
    for rule, title in ((UNKNOWN_NAME, "Names their module doesn't define (off by default)"),
                        (REMOVED_NAME, "Names a PR removes that are still used (off by default)")):
        own = [case for case in groups["code"] if case["rule"] == rule]
        if own:
            groups["code"] = [case for case in groups["code"] if case["rule"] != rule]
            groups[rule], names[rule] = own, title
    lines = [
        "# Checks that need no model",
        "",
        f"{len(planted)} planted risks, each a small PR with one problem in it, and {len(prs)} real merged PRs as they are.",
        "",
        "| Planted risk | Caught by its rule |",
        "| --- | --- |",
    ]
    for group, cases in groups.items():
        lines.append(f"| {names.get(group, group)} | {sum(c['caught'] for c in cases)} of {len(cases)} |")
    lines.append(f"| **All** | **{sum(c['caught'] for c in planted)} of {len(planted)}** |")
    missed = [c for c in planted if not c["caught"]]
    if missed:
        lines += ["", "Missed: " + "; ".join(f"`{c['id']}` (found: {', '.join(c['found']) or 'nothing'})" for c in missed) + "."]
    if controls:
        flagged = [c for c in controls if c["caught"]]
        lines += ["", f"Controls, files a rule must leave alone: {len(controls) - len(flagged)} of {len(controls)} left alone."
                  + (" Flagged: " + "; ".join(f"`{c['id']}`" for c in flagged) + "." if flagged else "")]
    marked = {v: sum(r["verdict"] == v for r in merged) for v in ("right", "wrong", "unsure", "")}
    lines += [
        "",
        f"On the merged PRs: {len(merged)} finding(s) on {len({r['case'] for r in merged})} of {len(prs)} PRs. "
        f"Marked: {marked['right']} right, {marked['wrong']} wrong (false alarms), {marked['unsure']} unsure"
        + (f", {marked['']} not marked yet." if marked[""] else "."),
    ]
    if merged:
        lines += ["", "| PR | Rule | Finding | Verdict | Why |", "| --- | --- | --- | --- | --- |"]
        lines += [f"| {r['case']} | `{r['rule']}` | {r['title']} | {r['verdict'] or '?'} | {r['note']} |" for r in merged]
    return "\n".join(lines)


_ALNUM = string.ascii_letters + string.digits
_FAKES = {
    "github-token": lambda r: "gh" + "p_" + _pick(r, _ALNUM, 36),
    "aws-key": lambda r: "AK" + "IA" + _pick(r, string.ascii_uppercase + string.digits, 16),
    "aws-secret": lambda r: _pick(r, _ALNUM + "/+", 40),
    "stripe-key": lambda r: "sk" + "_live_" + _pick(r, _ALNUM, 24),
    "slack-token": lambda r: "xo" + "xb-" + _pick(r, string.digits, 12) + "-" + _pick(r, _ALNUM, 24),
    "google-key": lambda r: "AI" + "za" + _pick(r, _ALNUM + "_-", 35),
    "openai-key": lambda r: "sk" + "-proj-" + _pick(r, _ALNUM, 48),
    "password": lambda r: _pick(r, _ALNUM, 18),
    "private-key": lambda r: "\n".join(["-----BEGIN RSA " + "PRIVATE KEY-----"] + [_pick(r, _ALNUM + "+/", 64) for _ in range(6)] + ["-----END RSA " + "PRIVATE KEY-----"]),
}


def _pick(rng: random.Random, alphabet: str, length: int) -> str:
    return "".join(rng.choice(alphabet) for _ in range(length))


def _fill(text: str, seed: str) -> str:
    """risks.yml's placeholders: a random value shaped like a secret, the
    same on every run, and Unicode characters written as U+XXXX."""
    rng = random.Random(seed)
    text = re.sub(r"<<fake:([a-z-]+)>>", lambda m: _FAKES[m.group(1)](rng), text)
    return re.sub(r"<<U\+([0-9A-F]{4,6})>>", lambda m: chr(int(m.group(1), 16)), text)


def _case_files(case: dict) -> tuple[list[FileDiff], dict[str, str]]:
    """A planted case's PR: the new files it adds, or its `diff` as written,
    for a rule that reads what a PR removes."""
    if "diff" in case:
        return parse_diff(case["diff"]), {}
    return _added_files({path: _fill(text, case["id"]) for path, text in case["files"].items()}, case.get("repo"))


def _repo_checks(case: dict, files: list[FileDiff], policy, contents: dict[str, str]) -> list:
    """The checks that read the repository and not only the PR, against a
    case's `repo`: the files already on the default branch."""
    with tempfile.TemporaryDirectory() as root:
        for path, text in (case.get("repo") or {}).items():
            target = Path(root, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return unknown_name_findings(files, policy, root, contents=contents) + removed_name_findings(files, policy, root, contents=contents)


def _added_files(spec: dict[str, str], before: dict[str, str] | None = None) -> tuple[list[FileDiff], dict[str, str]]:
    """New files as a diff that adds every line, with their full text. A
    file the default branch already has (`before`) is a change to it."""
    files = []
    for path, text in spec.items():
        if path in (before or {}):
            changed = difflib.unified_diff(before[path].splitlines(), text.splitlines(), f"a/{path}", f"b/{path}", lineterm="")
            files += parse_diff("\n".join([f"diff --git a/{path} b/{path}", *changed]) + "\n")
            continue
        lines = [Line("+", t, None, i) for i, t in enumerate(text.splitlines(), start=1)]
        files.append(FileDiff(path=path, status="added", hunks=[Hunk(0, 1, f"@@ -0,0 +1,{len(lines)} @@", lines)]))
    return files, dict(spec)


def _head_contents(repo: str, number: int, files: list[FileDiff]) -> dict[str, str]:
    """The files the Action reads in full for the checks (manifests,
    workflows, and Python for Bandit and Ruff), at the PR's head, cached."""
    path = CACHE / f"{repo.replace('/', '__')}__{number}__contents.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    gh = GitHub(_token(), repo)
    head = gh.pull(number)["head"]["sha"]
    live = [f for f in files if f.status != "deleted" and not f.binary]
    wanted = [f for f in live if f.path.startswith(".github/workflows/") or manifest_kind(f.path) is not None][:MAX_CONTEXT_FILES]
    wanted += sorted((f for f in live if f.path.endswith(".py")), key=lambda f: -f.additions)[:MAX_BANDIT_FILES]
    contents = {}
    for file in wanted:
        try:
            text = gh.file_text(file.path, head)
        except GitHubError:
            text = None
        if text is not None:
            contents[file.path] = text
    path.write_text(json.dumps(contents), encoding="utf-8")
    return contents


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


if __name__ == "__main__":
    raise SystemExit(main())
