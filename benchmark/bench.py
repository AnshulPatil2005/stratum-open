"""The small-model benchmark: review real merged PRs with several models,
then let maintainers mark each finding right or wrong.

    python benchmark/bench.py run --prs benchmark/prs.json --out benchmark/results/2026-11-pilot \\
        --model qwen2.5-coder:7b@http://localhost:11434/v1 --model qwen2.5-coder:14b@http://localhost:11434/v1
    # fill in the verdict column of findings.csv: right | wrong | unsure
    python benchmark/bench.py score benchmark/results/2026-11-pilot

`run` writes findings.csv (one row per finding, verdict blank), calls.csv (one
row per PR and model: time, tokens, failures) and summary.md. `score` rebuilds
summary.md once verdicts are in. Only the model's findings are compared: the
checks are the same for every model.

PR diffs are fetched with GITHUB_TOKEN (or `gh auth token`) and cached in
benchmark/.cache, which isn't committed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stratum_open.diff import parse_diff  # noqa: E402
from stratum_open.findings import anchor_to_diff, assign_fingerprints, dedupe, rank  # noqa: E402
from stratum_open.github.api import GitHub  # noqa: E402
from stratum_open.model import Model  # noqa: E402
from stratum_open.policy import parse_policy  # noqa: E402
from stratum_open.review import review  # noqa: E402

CACHE = Path(__file__).resolve().parent / ".cache"
FINDING_COLUMNS = ["repo", "pr", "model", "severity", "category", "path", "line", "title", "detail", "verdict", "note"]
CALL_COLUMNS = ["repo", "pr", "model", "seconds", "prompt_tokens", "output_tokens", "calls", "failed_calls", "files_read", "findings", "dropped", "error"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--prs", required=True, help='JSON list of {"repo": "owner/name", "pr": 123}')
    run.add_argument("--model", action="append", required=True, help="name@endpoint, repeatable")
    run.add_argument("--out", required=True)
    run.add_argument("--context-tokens", type=int, default=16384)
    score = sub.add_parser("score")
    score.add_argument("out")
    args = parser.parse_args()
    return _run(args) if args.command == "run" else _score(Path(args.out))


def _run(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    prs = json.loads(Path(args.prs).read_text(encoding="utf-8"))
    policy = parse_policy(f"review:\n  context_tokens: {args.context_tokens}\n")
    models = []
    for spec in args.model:
        name, _, endpoint = spec.partition("@")
        models.append(Model(endpoint or "http://localhost:11434/v1", name, api_key=os.environ.get("STRATUM_MODEL_API_KEY"), context_tokens=args.context_tokens))

    # One model at a time, so a small machine holds only one in memory, and
    # the files are rewritten after every PR so a stopped run keeps its results.
    pulls = {(item["repo"], int(item["pr"])): _fetch(item["repo"], int(item["pr"])) for item in prs}
    finding_rows, call_rows = [], []
    (out / "prs.json").write_text(json.dumps(prs, indent=2) + "\n", encoding="utf-8")
    for model in models:
        for (repo, number), (pull, text) in pulls.items():
            files = parse_diff(text)
            started = time.monotonic()
            result = review(title=pull.get("title") or "", description=pull.get("body") or "", files=files, policy=policy, model=model)
            findings = anchor_to_diff(result.findings, files)
            assign_fingerprints(findings, files)
            findings = rank(dedupe(findings))
            seconds = time.monotonic() - started
            call_rows.append({
                "repo": repo, "pr": number, "model": model.name, "seconds": round(seconds, 1),
                "prompt_tokens": sum(c.get("prompt_tokens") or 0 for c in result.calls),
                "output_tokens": sum(c.get("output_tokens") or 0 for c in result.calls),
                "calls": len(result.calls), "failed_calls": result.failed_calls,
                "files_read": len(result.packed.reviewed) + len(result.packed.clipped) if result.packed else 0,
                "findings": len(findings), "dropped": result.dropped,
                "error": "; ".join(sorted({c["error"] for c in result.calls if c.get("error")}))[:300],
            })
            for f in findings:
                finding_rows.append({
                    "repo": repo, "pr": number, "model": model.name, "severity": f.severity, "category": f.category,
                    "path": f.path, "line": f.line_start or "", "title": f.title, "detail": " ".join(f.detail.split()),
                    "verdict": "", "note": "",
                })
            print(f"{repo}#{number} {model.name}: {len(findings)} finding(s) in {seconds:.0f}s"
                  + (f", {result.failed_calls} failed call(s): {call_rows[-1]['error']}" if result.failed_calls else ""), flush=True)
            _write(out / "findings.csv", FINDING_COLUMNS, finding_rows)
            _write(out / "calls.csv", CALL_COLUMNS, call_rows)
            (out / "summary.md").write_text(_summary(call_rows, finding_rows, args), encoding="utf-8")
    print(f"Wrote {out}/summary.md")
    return 0


def _score(out: Path) -> int:
    calls = list(csv.DictReader((out / "calls.csv").open(encoding="utf-8")))
    findings = list(csv.DictReader((out / "findings.csv").open(encoding="utf-8")))
    (out / "summary.md").write_text(_summary(calls, findings, None), encoding="utf-8")
    print((out / "summary.md").read_text(encoding="utf-8"))
    return 0


def _summary(calls: list[dict], findings: list[dict], args) -> str:
    by_model: dict[str, list[dict]] = defaultdict(list)
    for row in calls:
        by_model[row["model"]].append(row)
    verdicts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in findings:
        verdicts[row["model"]][(row.get("verdict") or "").strip().lower() or "unmarked"] += 1
    prs = {(r["repo"], r["pr"]) for r in calls}
    lines = [
        "# Small-model benchmark",
        "",
        f"{len(prs)} merged PR(s), reviewed by each model with the same prompt and settings.",
        "Only the model's findings are counted; Stratum's checks don't depend on the model.",
        "",
        "| Model | Median time per PR | Findings per PR | Failed calls | Marked right | Marked wrong | Not marked |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for model, rows in by_model.items():
        seconds = statistics.median(float(r["seconds"]) for r in rows)
        per_pr = sum(int(r["findings"]) for r in rows) / len(rows)
        failed = sum(int(r["failed_calls"]) for r in rows)
        total = sum(int(r["calls"]) for r in rows)
        v = verdicts[model]
        lines.append(f"| `{model}` | {seconds:.0f}s | {per_pr:.1f} | {failed} of {total} | {v['right']} | {v['wrong']} | {v['unmarked'] + v['unsure']} |")
    marked = sum(v["right"] + v["wrong"] for v in verdicts.values())
    lines += [""]
    if not marked:
        lines.append("No findings have been marked yet. Precision needs maintainers to mark each row of findings.csv right or wrong, then `bench.py score`.")
    lines.append("")
    return "\n".join(lines)


def _fetch(repo: str, number: int) -> tuple[dict, str]:
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"{repo.replace('/', '__')}__{number}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["pull"], data["diff"]
    gh = GitHub(_token(), repo)
    pull = gh.pull(number)
    data = {"pull": {k: pull.get(k) for k in ("title", "body", "merged_at", "additions", "deletions")}, "diff": gh.pull_diff(number)}
    path.write_text(json.dumps(data), encoding="utf-8")
    return data["pull"], data["diff"]


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
    if not token:
        sys.exit("Set GITHUB_TOKEN to fetch PR diffs.")
    return token


def _write(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
