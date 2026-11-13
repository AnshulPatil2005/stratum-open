"""How the weakened-tests check does on merged PRs.

The check (stratum_open/checks/tests_weakened.py) is off unless
`checks.weakened_tests` is on. This runs it alone on every PR of the corpus
lists as merged, and on its planted cases in risks.yml. Maintainers reviewed
and merged those PRs, so a finding on one is either a false alarm or a test
they agreed to weaken.

    python benchmark/weakened_tests.py run --out benchmark/results/<name>
    python benchmark/weakened_tests.py score benchmark/results/<name>

`run` writes checks.csv, one row per finding, and markers.csv, one row per
added line that names a skip marker, reported or not: the lines the rule for
a test switched off had to tell apart. Mark both by hand, and `run` keeps the
marks when it's repeated:

- checks.csv: `verdict` is `right` when the PR does what the finding says,
  `wrong` when the rule misread the diff, `unsure` otherwise. For a right one,
  `fine` is `yes` when nobody needs it pointed out (the PR's title or
  description says it, or it follows from what they say the PR does), and
  `no` when the finding is the only notice a maintainer gets.
- markers.csv: `kind` is what the line is (`conditional`, `not a marker`, ...).

It also writes planted.json, the planted cases and whether each was caught,
and lists.json, what each list gave the rules to read: its PRs that change a
test file, the assertion lines they remove, their lines with a tolerance.

`score` writes summary.md: the planted cases, a table per rule and per list,
every marked row, then the folder's notes.md. A dropped.csv in the folder
holds the marked findings of a rule that was measured and then taken out of
the check: `run` leaves it alone, and `score` gives it a table of its own.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

import planted  # noqa: E402
from bench import _fetch, _write  # noqa: E402
from stratum_open.checks import run_checks, tests_weakened  # noqa: E402
from stratum_open.checks.common import extension, is_test_path  # noqa: E402
from stratum_open.diff import FileDiff, parse_diff  # noqa: E402
from stratum_open.policy import parse_policy  # noqa: E402

HERE = Path(__file__).resolve().parent
LISTS = ("clean", "clean-js", "clean-go", "clean-rust", "clean-infra", "clean-tests", "clean-flaky")
NAMES = {"clean": "Python", "clean-js": "JavaScript and TypeScript", "clean-go": "Go", "clean-rust": "Rust",
         "clean-infra": "Workflows and manifests", "clean-tests": "PRs that remove test lines",
         "clean-flaky": "PRs about a flaky or skipped test"}
POLICY = "checks:\n  weakened_tests: true\n"
COLUMNS = ["set", "list", "case", "rule", "path", "line", "title", "detail", "expected", "verdict", "fine", "note"]
MARKER_COLUMNS = ["list", "case", "path", "line", "text", "reported", "kind", "note"]
COUNTS = ("prs", "touch_tests", "trim_tests", "assertions_removed", "tolerance_lines")
# Any line that names a way to switch a test off, whether or not something decides when.
MARKER = re.compile(
    r"mark\.(?:skip|xfail)|\b(?:pytest|self)\.(?:skip|skipTest|xfail|importorskip)\(|\bSkipTest\b|unittest\.skip|expectedFailure"
    r"|\b(?:it|test|describe|context|suite)\.(?:skip|only|todo|skipIf|runIf)\b|\b(?:xit|xtest|xdescribe|fit|fdescribe)\(|\bthis\.skip\(|\bskip:"
    r"|\b(?:t|b|tb|f)\.Skip(?:Now|f)?\(|#\[ignore\b|#\[cfg_attr\(.*\bignore\b"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--out", required=True)
    run.add_argument("--risks", default=str(HERE / "planted" / "risks.yml"))
    run.add_argument("--lists", nargs="+", default=list(LISTS), help="names of lists in benchmark/planted, without .json")
    score = sub.add_parser("score")
    score.add_argument("out")
    args = parser.parse_args()
    return _run(args) if args.command == "run" else _score(Path(args.out))


def _run(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    policy = parse_policy(POLICY)
    rows, markers, cases, lists = [], [], [], []
    for case in yaml.safe_load(Path(args.risks).read_text(encoding="utf-8")):
        rules = case["rule"] if isinstance(case["rule"], list) else [case["rule"]]
        if not any(rule.startswith("tests/") for rule in rules):
            continue
        files, contents = planted._case_files(case)
        found = [f for f in run_checks(files, policy, contents=contents) if f.rule.startswith("tests/")]
        control = bool(case.get("control"))
        caught = any(f.rule in rules for f in found)
        cases.append({"id": case["id"], "rule": " or ".join(rules), "caught": caught, "found": sorted({f.rule for f in found})}
                     | ({"control": True} if control else {}))
        for f in found:
            expected = f.rule in rules and not control
            rows.append(_row("planted", "", case["id"], f) | {"expected": "yes" if expected else "", "verdict": "right" if expected else "wrong"})
        print(f"{case['id']}: {('FLAGGED' if caught else 'left alone') if control else ('caught' if caught else 'MISSED')}", flush=True)
    seen: set[str] = set()
    for name in args.lists:
        prs = json.loads((HERE / "planted" / f"{name}.json").read_text(encoding="utf-8"))
        stats = {"list": name} | {key: 0 for key in COUNTS}
        for item in prs:
            case = f"{item['repo']}#{item['pr']}"
            if case in seen:
                continue  # in an earlier list too
            seen.add(case)
            _, text = _fetch(item["repo"], int(item["pr"]))
            files = parse_diff(text)
            tests = [f for f in files if _is_test(f)]
            stats["prs"] += 1
            stats["touch_tests"] += bool(tests)
            stats["trim_tests"] += any(f.deletions and f.status != "deleted" for f in tests)
            assertions, tolerances = _read_by_the_rules(tests)
            stats["assertions_removed"] += assertions
            stats["tolerance_lines"] += tolerances
            found = tests_weakened.check(files)
            rows += [_row("merged", name, case, f) for f in found]
            reported = {(f.path, f.line_start) for f in found if f.rule in (tests_weakened.SKIPPED, tests_weakened.ONLY)}
            markers += [{"list": name, "case": case, "path": f.path, "line": number, "text": " ".join(line.split())[:200],
                         "reported": "yes" if (f.path, number) in reported else "", "kind": "", "note": ""}
                        for f in files if _is_test(f) or extension(f.path) == ".rs" for number, line in f.added() if MARKER.search(line)]
            if found:
                print(f"{case}: {len(found)} finding(s)", flush=True)
        lists.append(stats)
        print(f"{name}: {stats['prs']} PR(s), {stats['touch_tests']} change a test file", flush=True)
    _keep_marks(out / "checks.csv", rows, ("set", "case", "rule", "path", "line", "title"), ("verdict", "fine", "note"))
    _keep_marks(out / "markers.csv", markers, ("case", "path", "line", "text"), ("kind", "note"))
    _write(out / "checks.csv", COLUMNS, rows)
    _write(out / "markers.csv", MARKER_COLUMNS, markers)
    (out / "planted.json").write_text(json.dumps(cases, indent=1) + "\n", encoding="utf-8")
    (out / "lists.json").write_text(json.dumps(lists, indent=1) + "\n", encoding="utf-8")
    return _score(out)


def _is_test(file: FileDiff) -> bool:
    return is_test_path(file.path) and extension(file.path) in tests_weakened._LANGUAGES and not file.binary


def _read_by_the_rules(tests: list[FileDiff]) -> tuple[int, int]:
    """What the PR gave the rules to read, by their own reading: the
    assertion lines it removes from test files it keeps, and the changed
    lines of those files that carry a tolerance."""
    assertions = tolerances = 0
    for file in tests:
        if file.status == "deleted":
            continue
        read = tests_weakened._Reading(file, tests_weakened._LANGUAGES[extension(file.path)])
        changed = [line for hunk in file.hunks for line in hunk.lines if line.kind != " "]
        assertions += sum(line.kind == "-" and read.assertion(line) for line in changed)
        tolerances += sum(bool(tests_weakened._compares(read, line) and tests_weakened._tolerances(line.text)[1]) for line in changed)
    return assertions, tolerances


def _row(group: str, name: str, case: str, finding) -> dict:
    return {"set": group, "list": name, "case": case, "rule": finding.rule, "path": finding.path, "line": finding.line_start or "",
            "title": finding.title, "detail": " ".join(finding.detail.split()), "expected": "", "verdict": "", "fine": "", "note": ""}


def _keep_marks(path: Path, rows: list[dict], key: tuple[str, ...], marks: tuple[str, ...]) -> None:
    """Copy the hand-made marks of an earlier run onto the same rows of this one."""
    if not path.exists():
        return
    earlier = {tuple(str(row[k]) for k in key): row for row in planted._read(path)}
    for row in rows:
        old = earlier.get(tuple(str(row[k]) for k in key))
        for mark in marks if old else ():
            row[mark] = row[mark] or old.get(mark, "")


def _score(out: Path) -> int:
    rows = planted._read(out / "checks.csv")
    markers = planted._read(out / "markers.csv")
    cases = json.loads((out / "planted.json").read_text(encoding="utf-8"))
    lists = json.loads((out / "lists.json").read_text(encoding="utf-8"))
    dropped = planted._read(out / "dropped.csv") if (out / "dropped.csv").exists() else []
    merged = [row for row in rows if row["set"] == "merged"]
    risks, controls = [c for c in cases if not c.get("control")], [c for c in cases if c.get("control")]
    total = {key: sum(entry[key] for entry in lists) for key in COUNTS}
    lines = [
        "# Weakened tests: the check on merged PRs",
        "",
        f"{len(risks)} planted cases, {len(controls)} controls the check must leave alone, and {total['prs']} real merged PRs as they are, "
        f"{total['touch_tests']} of which change a test file and {total['trim_tests']} of which remove a line from one. "
        "Each finding was read against the PR's diff, title and description and marked: right when the PR does what the finding says, "
        "wrong when the rule misread the diff. A right one is plainly fine when the PR says it, or it follows from what the PR says it does; "
        "it's worth pointing out when the finding is the only notice a maintainer gets.",
        "",
        f"Planted: {sum(c['caught'] for c in risks)} of {len(risks)} caught, {sum(not c['caught'] for c in controls)} of {len(controls)} controls left alone.",
    ]
    missed = [c["id"] for c in risks if not c["caught"]] + [c["id"] for c in controls if c["caught"]]
    if missed:
        lines += ["", "Missed, or flagged though a control: " + ", ".join(f"`{name}`" for name in missed) + "."]
    header = ["| Rule | Findings | PRs | Right, worth pointing out | Right, plainly fine | Wrong | Unsure |", "| --- | --- | --- | --- | --- | --- | --- |"]
    by_rule = _by_rule(merged)
    for case in risks:  # a rule with a planted case and no finding gets its row of zeros
        by_rule.setdefault(case["rule"], [])
    lines += ["", "## By rule", "", *header]
    lines += [f"| `{rule}` | {_counts(mine)} |" for rule, mine in sorted(by_rule.items())]
    lines.append("| **All** | " + " | ".join(f"**{cell}**" for cell in _counts(merged).split(" | ")) + " |")
    unmarked = sum(1 for row in merged + dropped if row["verdict"] not in ("right", "wrong", "unsure") or (row["verdict"] == "right" and row["fine"] not in ("yes", "no")))
    if unmarked:
        lines += ["", f"**{unmarked} finding(s) not marked yet**, so the counts are incomplete."]
    if dropped:
        lines += ["", "Measured, then taken out of the check ([dropped.csv](dropped.csv)):", "", *header]
        lines += [f"| `{rule}` | {_counts(mine)} |" for rule, mine in sorted(_by_rule(dropped).items())]
    lines += ["", "## By list", "",
              "| List | PRs | Change a test file | Remove a line from one | Assertion lines removed | Changed lines with a tolerance | Skip markers added | Findings | PRs with a finding |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for entry in lists:
        mine = [row for row in merged if row["list"] == entry["list"]]
        marks = sum(marker["list"] == entry["list"] for marker in markers)
        lines.append(f"| [{NAMES.get(entry['list'], entry['list'])}](../../planted/{entry['list']}.json) | " + " | ".join(str(entry[key]) for key in COUNTS)
                     + f" | {marks} | {len(mine)} | {len({row['case'] for row in mine})} |")
    lines.append("| **All** | " + " | ".join(f"**{total[key]}**" for key in COUNTS)
                 + f" | **{len(markers)}** | **{len(merged)}** | **{len({row['case'] for row in merged})}** |")
    kinds: dict[str, int] = defaultdict(int)
    for marker in markers:
        kinds["reported" if marker["reported"] == "yes" else marker["kind"] or "not marked"] += 1
    if markers:
        lines += ["", "The added lines that name a skip marker ([markers.csv](markers.csv)), by what they are: "
                  + ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items(), key=lambda item: -item[1])) + "."]
    for title, group in (("Every finding", merged), ("Every finding of a rule taken out", dropped)):
        if group:
            lines += ["", f"## {title}", "", "| PR | Rule | Where | Finding | Verdict | Why |", "| --- | --- | --- | --- | --- | --- |"]
            lines += [f"| {row['case']} | `{row['rule']}` | `{row['path']}`{':' + row['line'] if row['line'] else ''} | {row['title']} | {_verdict(row)} | {row['note']} |"
                      for row in sorted(group, key=lambda row: (row["rule"], row["case"]))]
    notes = out / "notes.md"
    if notes.exists():
        lines += ["", notes.read_text(encoding="utf-8").strip()]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print((out / "summary.md").read_text(encoding="utf-8"))
    return 0


def _by_rule(rows: list[dict]) -> dict[str, list[dict]]:
    by_rule: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_rule[row["rule"]].append(row)
    return dict(by_rule)


def _verdict(row: dict) -> str:
    if row["verdict"] != "right":
        return row["verdict"] or "?"
    return {"yes": "right, plainly fine", "no": "right, worth pointing out"}.get(row["fine"], "right")


def _counts(rows: list[dict]) -> str:
    right = [row for row in rows if row["verdict"] == "right"]
    cells = [len(rows), len({row["case"] for row in rows}), sum(row["fine"] == "no" for row in right), sum(row["fine"] == "yes" for row in right),
             sum(row["verdict"] == "wrong" for row in rows), sum(row["verdict"] == "unsure" for row in rows)]
    return " | ".join(str(cell) for cell in cells)


if __name__ == "__main__":
    raise SystemExit(main())
