"""Replay only `review.verify` on findings already marked right or wrong.

A full benchmark run takes hours on a CPU and its findings change from run to
run. This takes the marked findings of an earlier run (their `verdict`), shows
each to the model the way the verify step does, and counts how many right
findings it keeps and how many wrong ones it drops.

    python benchmark/verify_replay.py --results benchmark/results/2026-11-07-planted-actions-cpu \\
        --model qwen2.5-coder:14b@http://localhost:11434/v1 --verify lenient
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import planted  # noqa: E402
from bench import _fetch  # noqa: E402
from stratum_open import diff as diffs  # noqa: E402
from stratum_open.diff import parse_diff  # noqa: E402
from stratum_open.model import Model, parse_json  # noqa: E402
from stratum_open.review import VERIFY_CHARS, VERIFY_LINES, VERIFY_PROMPTS  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", required=True, help="a run folder whose findings.csv files have verdicts")
    parser.add_argument("--model", required=True, help="name@endpoint; the model whose own findings are replayed")
    parser.add_argument("--verify", choices=sorted(VERIFY_PROMPTS), required=True)
    args = parser.parse_args()
    name, _, endpoint = args.model.partition("@")
    model = Model(endpoint or "http://localhost:11434/v1", name, api_key=os.environ.get("STRATUM_MODEL_API_KEY"), context_tokens=8192)
    system, max_tokens = VERIFY_PROMPTS[args.verify]
    bugs = {f"{b['repo']}#{b['pr']}": b for b in json.loads(planted.BUGS.read_text(encoding="utf-8"))}
    kept = {"right": 0, "wrong": 0}
    total = {"right": 0, "wrong": 0}
    for path in sorted(Path(args.results).rglob("findings.csv")):
        for row in csv.DictReader(path.open(encoding="utf-8")):
            bug = bugs.get(row["case"])
            if row["model"] != name or row["verdict"] not in kept or not bug or bug.get("invalid") or not row["line"]:
                continue
            _, text = _fetch(bug["repo"], int(bug["pr"]))
            files = parse_diff(text)
            planted.plant(bug, files)
            file = next((f for f in files if f.path == row["path"]), None)
            if file is None:
                continue
            line = int(row["line"])
            near = [h for h in file.hunks if any(l.new is not None and abs(l.new - line) <= VERIFY_LINES for l in h.lines)]
            code = diffs.render(file, near or file.hunks)[:VERIFY_CHARS]
            reply = model.complete(system, f"The changed code:\n{code}\n\nThe finding, on line {line}:\n{row['title']}: {row['detail']}", max_tokens=max_tokens)
            data = parse_json(reply.text) if reply.ok else None
            total[row["verdict"]] += 1
            kept[row["verdict"]] += not (isinstance(data, dict) and str(data.get("real", "")).strip().lower() == "no")
    print(f"{name} verify={args.verify}: right kept {kept['right']} of {total['right']}, "
          f"wrong dropped {total['wrong'] - kept['wrong']} of {total['wrong']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
