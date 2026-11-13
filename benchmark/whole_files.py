"""Ruff's rules on every line of the merged PRs' Python files, not only the
lines the PRs add.

`planted.py checks` counts findings on added lines, and on those the rules of
stratum_open/checks/ruff.py report next to nothing: too little to tell a rule
that is right from one that never had the chance to be wrong. This reads the
whole text of each file the check would read for a PR in clean.json, as if
the PR had added every line. It runs the check's own rules and the candidates
left out of it (`--also`), so a rule that fires on working code shows up.

    python benchmark/whole_files.py --out benchmark/results/<name>

Writes whole.csv, one row for each place a rule fires (a file many PRs touch
is counted once). `reported` says whether the check as it is would report it.
Mark the `verdict` column by hand: `right` for a real error, `wrong` for code
that works.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import planted  # noqa: E402
from bench import _fetch, _write  # noqa: E402
from stratum_open.checks import ruff  # noqa: E402
from stratum_open.diff import FileDiff, Hunk, Line, parse_diff  # noqa: E402

HERE = Path(__file__).resolve().parent
# The candidates tried and left out of the check, where this Ruff has them.
LEFT_OUT = "F504,F522,F523,PLE0604,PLE0605,PLE0704,PLE1300,PLE1310,PLE1519,B006,B008,B018,B020,B023"
COLUMNS = ["rule", "case", "prs", "path", "line", "code", "reported", "verdict", "note"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    parser.add_argument("--also", default=LEFT_OUT, help="rules to try besides the check's own")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rules = [*ruff.RULES, *[code for code in args.also.split(",") if code and code not in ruff.RULES]]
    rows: dict[tuple[str, str, str, str], dict] = {}
    seen: dict[tuple[str, str], int] = {}
    for item in json.loads((HERE / "planted/clean.json").read_text(encoding="utf-8")):
        repo, number = item["repo"], int(item["pr"])
        _, text = _fetch(repo, number)
        files = parse_diff(text)
        contents = planted._head_contents(repo, number, files)
        sources = {path: contents[path] for path in ruff.plan(files, contents)[0]}
        if not sources:
            continue
        reported = {(f.path, f.line_start, f.rule) for f in ruff.check([_all_added(p, t) for p, t in sources.items()], sources)}
        for path, source in sources.items():
            seen[(repo, path)] = source.count("\n") + 1
        for issue in ruff.run_ruff(sources, rules=rules):
            lines = sources[issue["path"]].split("\n")
            code = lines[issue["line"] - 1].strip() if issue["line"] <= len(lines) else ""
            rule = f"ruff/{issue['code']}"
            row = rows.setdefault((repo, issue["path"], rule, code), {
                "rule": rule, "case": f"{repo}#{number}", "prs": 0, "path": issue["path"], "line": issue["line"], "code": code,
                "reported": "yes" if (issue["path"], issue["line"], rule) in reported else "", "verdict": "", "note": "",
            })
            row["prs"] += 1
    found = sorted((r for r in rows.values() if r["rule"] != "ruff/invalid-syntax"), key=lambda r: (r["rule"], r["case"], r["path"], r["line"]))
    _write(out / "whole.csv", COLUMNS, found)
    places, kept = Counter(r["rule"] for r in found), Counter(r["rule"] for r in found if r["reported"])
    print(f"{len(seen)} files, {sum(seen.values())} lines, {len(rules)} rules ({len(ruff.RULES)} in the check).")
    for rule in sorted(places):
        print(f"{rule}: {places[rule]} place(s), {kept[rule]} the check would report")
    print("No place: " + ", ".join(code for code in rules if f"ruff/{code}" not in places))
    return 0


def _all_added(path: str, text: str) -> FileDiff:
    """The file as a diff that adds every line, numbered as the checks number them."""
    lines = [Line("+", t, None, i) for i, t in enumerate(text.split("\n"), start=1)]
    return FileDiff(path=path, status="added", hunks=[Hunk(0, 1, f"@@ -0,0 +1,{len(lines)} @@", lines)])


if __name__ == "__main__":
    raise SystemExit(main())
