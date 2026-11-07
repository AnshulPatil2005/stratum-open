"""How the missing-await check does on awaits it wasn't written for.

The check (stratum_open/checks/awaits.py) was written after looking at the
planted-bug benchmark's four dropped-await cases, so those prove nothing. This
takes every `await` the merged PRs in clean.json add, outside the benchmark's
own 40 PRs, drops it one at a time, and asks whether the check flags that line.
It also runs the check on every PR as merged, where any finding is a possible
false alarm.

    python benchmark/dropped_awaits.py --out benchmark/results/<name>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import planted  # noqa: E402
from bench import _fetch  # noqa: E402
from stratum_open.checks import awaits  # noqa: E402
from stratum_open.diff import parse_diff  # noqa: E402

HERE = Path(__file__).resolve().parent
AWAIT = re.compile(r"\bawait\s+")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    benchmark = {(b["repo"], int(b["pr"])) for b in json.loads((HERE / "planted/bugs.json").read_text(encoding="utf-8"))}
    prs = json.loads((HERE / "planted/clean.json").read_text(encoding="utf-8"))
    cases, alarms = [], []
    for item in prs:
        repo, number = item["repo"], int(item["pr"])
        _, text = _fetch(repo, number)
        files = parse_diff(text)
        contents = planted._head_contents(repo, number, files)
        alarms += [{"pr": f"{repo}#{number}", "path": f.path, "line": f.line_start, "title": f.title} for f in awaits.check(files, contents)]
        if (repo, number) in benchmark:
            continue
        for file in files:
            if not file.path.endswith(".py"):
                continue
            for line_number, line in file.added():
                match = AWAIT.search(line)
                if not match or line.lstrip().startswith("#"):
                    continue
                dropped = line[:match.start()] + line[match.end():]
                bug = {"path": file.path, "line": line_number, "find": line, "replace": dropped, "repo": repo, "pr": number}
                changed = parse_diff(text)
                planted.plant(bug, changed)
                hits = [f for f in awaits.check(changed, planted.with_line(contents, bug, dropped))
                        if f.path == file.path and f.line_start == line_number]
                cases.append({"pr": f"{repo}#{number}", "path": file.path, "line": line_number, "code": line.strip(), "caught": bool(hits)})
    caught = sum(c["caught"] for c in cases)
    (out / "cases.json").write_text(json.dumps(cases, indent=1) + "\n", encoding="utf-8")
    (out / "alarms.json").write_text(json.dumps(alarms, indent=1) + "\n", encoding="utf-8")
    summary = [
        "# The missing-await check on awaits it wasn't written for",
        "",
        f"- **Dropped awaits caught:** {caught} of {len(cases)}, each an `await` added by a merged PR in clean.json "
        "(outside the planted-bug benchmark's 40), dropped one at a time.",
        f"- **Findings on the {len(prs)} PRs as merged:** {len(alarms)}.",
        "",
        "Missed: " + ("; ".join(f"`{c['code'][:60]}` ({c['pr']})" for c in cases if not c["caught"]) or "none") + ".",
    ]
    (out / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
