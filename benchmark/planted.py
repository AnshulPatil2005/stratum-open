"""The planted-bug benchmark: does a review find a bug we know is there?

`bench.py` reviews merged PRs as they are, so judging its findings needs a
maintainer. Here each case is a real merged PR with one bug planted on a line
it adds (a flipped comparison, a dropped `await`, an off-by-one), so the
answer is known before the model reads it.

    python benchmark/planted.py run --bugs benchmark/planted/bugs.json --out benchmark/results/<name>/raw/7b-1 \\
        --model qwen2.5-coder:7b@http://localhost:11434/v1 [--shard 1/8]
    python benchmark/planted.py score benchmark/results/<name>

    python benchmark/planted.py checks --risks benchmark/planted/risks.yml --prs benchmark/planted/clean.json \\
        --out benchmark/results/<name>-checks

`run` reviews each case the way the Action does (the `small` profile, an
8,192-token window) and writes reviews.csv and findings.csv. A finding counts
as near the bug when it's on the planted file within LINES_NEAR lines of it.
Mark the `verdict` column of findings.csv by hand: `right` for a finding near
the bug that describes it, or for any other finding that's a real problem;
`wrong` or `unsure` otherwise. `score` reads every reviews.csv under the run
folder (one per shard), so shards run as separate jobs.

`checks` runs the checks that need no model: on planted risks (risks.yml),
each of which should get its rule's finding, and on merged PRs as they are,
where every finding is a possible false alarm for a person to mark.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import statistics
import string
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from bench import CACHE, _fetch, _token, _write  # noqa: E402
from stratum_open.checks import run_checks  # noqa: E402
from stratum_open.checks.bandit import available as bandit_available  # noqa: E402
from stratum_open.checks.dependencies import PackageLookup, manifest_kind  # noqa: E402
from stratum_open.diff import FileDiff, Hunk, Line, parse_diff  # noqa: E402
from stratum_open.engine import analyze  # noqa: E402
from stratum_open.github.api import GitHub, GitHubError  # noqa: E402
from stratum_open.runner import MAX_BANDIT_FILES, MAX_CONTEXT_FILES  # noqa: E402
from stratum_open.model import Model  # noqa: E402
from stratum_open.policy import parse_policy  # noqa: E402

LINES_NEAR = 3
POLICY = "review:\n  profile: small\n  context_tokens: {tokens}\n"  # what the Action runs with Ollama
REVIEW_COLUMNS = ["case", "kind", "path", "line", "model", "seconds", "shown", "calls", "failed_calls", "findings", "near", "error"]
FINDING_COLUMNS = ["case", "model", "severity", "category", "path", "line", "near", "title", "detail", "verdict", "note"]
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
    fetch = sub.add_parser("fetch", help="fill the cache, so parallel jobs needn't each call GitHub")
    fetch.add_argument("--bugs", required=True)
    score = sub.add_parser("score")
    score.add_argument("out")
    checks = sub.add_parser("checks")
    checks.add_argument("--risks", required=True)
    checks.add_argument("--prs", required=True)
    checks.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.command == "fetch":
        for bug in json.loads(Path(args.bugs).read_text(encoding="utf-8")):
            _fetch(bug["repo"], int(bug["pr"]))
        return 0
    return {"run": _run, "score": lambda a: _score(Path(a.out)), "checks": _checks}[args.command](args)


# ── the model review ─────────────────────────────────────────────────────


def plant(bug: dict, files: list[FileDiff]) -> Line:
    """Swap the planted line into the parsed diff; fails loudly if the PR's
    line isn't what bugs.json says, so a stale case can't pass for a miss."""
    for file in files:
        if file.path != bug["path"]:
            continue
        for hunk in file.hunks:
            for line in hunk.lines:
                if line.kind == "+" and line.new == bug["line"]:
                    if line.text != bug["find"]:
                        raise SystemExit(f"{bug['repo']}#{bug['pr']} {bug['path']}:{bug['line']} reads {line.text!r}, not {bug['find']!r}")
                    line.text = bug["replace"]
                    return line
    raise SystemExit(f"{bug['repo']}#{bug['pr']}: no added line {bug['path']}:{bug['line']}")


def near(finding, bug: dict) -> bool:
    if finding.path != bug["path"] or finding.line_start is None:
        return False
    return finding.line_start - LINES_NEAR <= bug["line"] <= (finding.line_end or finding.line_start) + LINES_NEAR


def shown(chunks: list[str], path: str, line: Line) -> bool:
    """Whether the planted line, numbered as `render` numbers it, is in the
    planted file's part of a prompt: its text alone (`return True`) could be
    anywhere else in a clipped diff."""
    numbered = f"{line.new:>6} +  {line.text}"
    return any(
        section.startswith(f"{path} (") and numbered in section.split("\n")
        for chunk in chunks for section in ("\n" + chunk).split("\n## ")[1:]
    )


def _run(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    k, n = (int(part) for part in args.shard.split("/"))
    bugs = json.loads(Path(args.bugs).read_text(encoding="utf-8"))[k - 1::n]
    name, _, endpoint = args.model.partition("@")
    model = Model(endpoint or "http://localhost:11434/v1", name, api_key=os.environ.get("STRATUM_MODEL_API_KEY"), context_tokens=args.context_tokens)
    policy = parse_policy(POLICY.format(tokens=args.context_tokens))
    reviews, findings = [], []
    for bug in bugs:
        case = f"{bug['repo']}#{bug['pr']}"
        pull, text = _fetch(bug["repo"], int(bug["pr"]))
        files = parse_diff(text)
        planted = plant(bug, files)
        started = time.monotonic()
        result = analyze(title=pull.get("title") or "", description=pull.get("body") or "", files=files, policy=policy, model=model)
        seconds = time.monotonic() - started
        calls = result.review.calls if result.review else []
        chunks = result.review.packed.chunks if result.review and result.review.packed else []
        mine = [f for f in result.findings if f.source == "model"]
        hits = [f for f in mine if near(f, bug)]
        reviews.append({
            "case": case, "kind": bug["kind"], "path": bug["path"], "line": bug["line"], "model": name,
            "seconds": round(seconds, 1), "shown": "yes" if shown(chunks, bug["path"], planted) else "no",
            "calls": len(calls), "failed_calls": result.review.failed_calls if result.review else 0,
            "findings": len(mine), "near": len(hits),
            "error": "; ".join(sorted({c["error"] for c in calls if c.get("error")}))[:300],
        })
        for f in mine:
            findings.append({
                "case": case, "model": name, "severity": f.severity, "category": f.category, "path": f.path,
                "line": f.line_start or "", "near": "yes" if f in hits else "", "title": f.title,
                "detail": " ".join(f.detail.split()), "verdict": "", "note": "",
            })
        print(f"{case} {name}: {len(mine)} finding(s), {len(hits)} near the bug, {seconds:.0f}s"
              + (f", error: {reviews[-1]['error']}" if reviews[-1]["error"] else ""), flush=True)
        _write(out / "reviews.csv", REVIEW_COLUMNS, reviews)
        _write(out / "findings.csv", FINDING_COLUMNS, findings)
    return 0


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


def _model_summary(out: Path) -> str:
    reviews = [row for path in sorted(out.rglob("reviews.csv")) for row in _read(path)]
    findings = [row for path in sorted(out.rglob("findings.csv")) for row in _read(path)]
    by_model: dict[str, list[dict]] = defaultdict(list)
    for row in reviews:
        by_model[row["model"]].append(row)
    marks: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in findings:
        marks[(row["model"], row["case"])].append(row)
    cases = {row["case"] for row in reviews}
    lines = [
        "# Planted-bug benchmark",
        "",
        f"{len(cases)} real merged PRs, each with one bug planted on a line it adds. Each model reviewed",
        "them as the Action does: the `small` profile and an 8,192-token window. Only the model's own",
        "findings count. A finding is near the bug when it's on that file within "
        f"{LINES_NEAR} lines; found means it was read and it describes the bug.",
        "",
        "| Model | PRs | Bug shown to the model | Near the bug | Found | Other findings | Other findings marked right | Failed calls | Median time |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for model, rows in sorted(by_model.items(), key=lambda item: _size(item[0])):
        seen = [r for r in rows if r["shown"] == "yes"]
        near_hits = [r for r in seen if int(r["near"])]
        found = [r for r in seen if any(m["near"] == "yes" and m["verdict"] == "right" for m in marks[(model, r["case"])])]
        other = [m for r in rows for m in marks[(model, r["case"])] if m["near"] != "yes"]
        other_right = sum(m["verdict"] == "right" for m in other)
        other_marked = sum(m["verdict"] in ("right", "wrong") for m in other)
        failed = sum(int(r["failed_calls"]) for r in rows)
        calls = sum(int(r["calls"]) for r in rows)
        median = statistics.median(float(r["seconds"]) for r in rows)
        lines.append(
            f"| `{model}` | {len(rows)} | {len(seen)} | {len(near_hits)} of {len(seen)} | {len(found)} of {len(seen)} "
            f"| {len(other)} | {other_right} of {other_marked} marked | {failed} of {calls} | {median:.0f}s |"
        )
    kinds = sorted({r["kind"] for r in reviews})
    lines += ["", "Found, by kind of bug:", "", "| Model | " + " | ".join(kinds) + " |", "| --- |" + " --- |" * len(kinds)]
    for model, rows in sorted(by_model.items(), key=lambda item: _size(item[0])):
        cells = []
        for kind in kinds:
            mine = [r for r in rows if r["kind"] == kind and r["shown"] == "yes"]
            hit = sum(any(m["near"] == "yes" and m["verdict"] == "right" for m in marks[(model, r["case"])]) for r in mine)
            cells.append(f"{hit} of {len(mine)}")
        lines.append(f"| `{model}` | " + " | ".join(cells) + " |")
    unmarked = sum(1 for m in findings if not m["verdict"])
    if unmarked:
        lines += ["", f"**{unmarked} finding(s) not marked yet**, so Found and the marked counts are incomplete."]
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
    policy = parse_policy("")
    lookup = PackageLookup()
    rows, results = [], []
    for case in yaml.safe_load(Path(args.risks).read_text(encoding="utf-8")):
        files, contents = _added_files({path: _fill(text, case["id"]) for path, text in case["files"].items()})
        found = run_checks(files, policy, lookup=lookup, contents=contents, pr_text=case.get("title", ""))
        rules = case["rule"] if isinstance(case["rule"], list) else [case["rule"]]
        caught = any(f.rule in rules for f in found)
        results.append({"id": case["id"], "rule": " or ".join(rules), "caught": caught, "found": sorted({f.rule for f in found})})
        for f in found:
            expected = f.rule in rules
            rows.append({"set": "planted", "case": case["id"], "rule": f.rule, "path": f.path, "line": f.line_start or "",
                         "title": f.title, "expected": "yes" if expected else "", "verdict": "right" if expected else "", "note": ""})
        print(f"{case['id']}: {'caught' if caught else 'MISSED'} ({', '.join(sorted({f.rule for f in found})) or 'no findings'})", flush=True)
    prs = json.loads(Path(args.prs).read_text(encoding="utf-8"))
    for item in prs:
        repo, number = item["repo"], int(item["pr"])
        pull, text = _fetch(repo, number)
        files = parse_diff(text)
        found = run_checks(files, policy, lookup=lookup, contents=_head_contents(repo, number, files), pr_text=f"{pull.get('title') or ''}\n{pull.get('body') or ''}")
        for f in found:
            rows.append({"set": "merged", "case": f"{repo}#{number}", "rule": f.rule, "path": f.path, "line": f.line_start or "",
                         "title": f.title, "expected": "", "verdict": "", "note": ""})
        print(f"{repo}#{number}: {len(found)} finding(s)", flush=True)
    _write(out / "checks.csv", CHECK_COLUMNS, rows)
    (out / "planted.json").write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    (out / "prs.json").write_text(json.dumps(prs, indent=1) + "\n", encoding="utf-8")
    if not bandit_available():
        print("Bandit isn't installed, so its cases are missed: pip install bandit.")
    return _score(out)


def _checks_summary(out: Path) -> str:
    planted = json.loads((out / "planted.json").read_text(encoding="utf-8"))
    rows = _read(out / "checks.csv")
    merged = [r for r in rows if r["set"] == "merged"]
    prs = json.loads((out / "prs.json").read_text(encoding="utf-8"))
    groups: dict[str, list[dict]] = defaultdict(list)
    for case in planted:
        groups[case["rule"].split("/")[0].split(" ")[0]].append(case)
    names = {"code": "Secrets and risky code", "bandit": "Bandit (optional extra)", "workflow": "GitHub workflows",
             "deps": "Dependencies", "agents": "Coding-agent files", "unicode": "Hidden Unicode"}
    lines = [
        "# Checks that need no model",
        "",
        f"{len(planted)} planted risks, each a PR adding files with one problem in them, and {len(prs)} real merged PRs as they are.",
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


def _added_files(spec: dict[str, str]) -> tuple[list[FileDiff], dict[str, str]]:
    """New files as a diff that adds every line, with their full text."""
    files = []
    for path, text in spec.items():
        lines = [Line("+", t, None, i) for i, t in enumerate(text.splitlines(), start=1)]
        files.append(FileDiff(path=path, status="added", hunks=[Hunk(0, 1, f"@@ -0,0 +1,{len(lines)} @@", lines)]))
    return files, dict(spec)


def _head_contents(repo: str, number: int, files: list[FileDiff]) -> dict[str, str]:
    """The files the Action reads in full for the checks (manifests,
    workflows, and Python for Bandit), at the PR's head, cached."""
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
