"""Which of the model's findings `review.quiet` may leave out, going by the
findings people marked.

Every findings.csv of a model review under benchmark/results/ has a
`verdict` column, and a few hundred of their rows are marked right or wrong.
The rows from another reviewer (PR-Agent, under a `pr-agent` variant) are left
out: `review.quiet` only reads what stratum-open's prompt gets back. This tries each candidate
pattern on those rows and counts the wrong findings it would remove and the
right ones it would lose. A pattern that loses a right finding doesn't ship;
`stratum_open.review.NOT_DEFECTS` holds the ones that did. It also counts how
often one title lands on several files of a review, which quiet mode keeps
once.

    python benchmark/quiet_filter.py
    python benchmark/quiet_filter.py --out benchmark/results/<name>   # writes summary.md there
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stratum_open.findings import slug  # noqa: E402
from stratum_open.review import MIN_REPEATED_FILES, NOT_DEFECTS, SPARED  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"


@dataclass(frozen=True)
class Candidate:
    name: str
    where: str  # title | detail | either | category
    pattern: str
    left_out: str = ""  # why it doesn't ship although it loses nothing marked right
    spared: str = ""  # a category whose findings it never removes


# Why a pattern ships although no marked finding has such a title.
UNBACKED = {
    "asks for docs": "nothing marked matches, so the benchmark is silent on it; the prompt rules these out, "
                     "and Stratum's review of its own PR #55 had \"Missing docstring for new parameter\"",
}

# The patterns tried and not shipped. Several are the first idea for a kind of
# comment, which the shipped pattern then narrows; they stay here so the table
# shows what the narrowing was for.
TRIED = [
    Candidate("mentions docs or a docstring", "either", r"\b(docstrings?|documentation|documented|undocumented)\b"),
    Candidate("mentions a comment", "either", r"\bcomments?\b"),
    Candidate("mentions a name", "title", r"\bnames?\b"),
    Candidate("redundant or unnecessary", "title", r"\b(redundant|unnecessary|unneeded|unused|duplicate)\b"),
    Candidate("says could, may or might", "detail", r"\b(could|may|might)\b"),
    Candidate("about tests", "title", r"\btests?\b"),
    Candidate("about a deprecation", "title", r"\bdeprecat\w+"),
    Candidate("starts with Deprecated or Deprecation", "title", r"^deprecat\w+",
              "it can't tell a PR that deprecates something, which is all its matches are, from code that calls something deprecated"),
    Candidate("says consider", "detail", r"\bconsider\b",
              "its one marked match uses the word in another sense (\"should also consider the case where\")"),
    Candidate("starts with Consider or Suggest", "title", r"^(consider|suggest\w*|recommend\w*)\b", "no finding in the benchmark has such a title"),
    Candidate("type hints", "title", r"\btype (hints?|hinting|annotations?)\b",
              "nothing marked matches, and code that reads annotations when it runs can break on a wrong one"),
    Candidate("formatting", "title", r"\b(indentation|whitespace|formatting|blank lines?)\b",
              "its matches are about what a formatter or a console prints and about Python indentation, each a defect if the claim were true"),
    Candidate("says may, might or could", "title", r"\b(may|might|could)\b",
              "three marked findings are too few to tell, and the prompt doesn't rule the wording out"),
    Candidate("missing timeout", "title", r"\btimeout\b",
              "a missing timeout is a defect the prompt asks for; the benchmark plants logic bugs, so it can't say how often one is right"),
    Candidate("category is performance", "category", r"^performance$",
              "a kind of defect, not a kind of comment; the benchmark plants logic bugs, so it can't say how often one is right"),
]


def marked(results: Path) -> list[dict[str, str]]:
    """Every row of every findings.csv of stratum-open's model reviews, marked
    or not: the files with a `model` and a `verdict` column, less the rows
    from another reviewer."""
    rows = []
    for path in sorted(results.rglob("findings.csv")):
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not {"model", "verdict"} <= set(reader.fieldnames or ()):  # a check's findings, or not marked at all
                continue
            for row in reader:
                if (row.get("variant") or "").startswith("pr-agent"):
                    continue
                row["pr"] = row.get("case") or f"{row.get('repo')}#{row.get('pr')}"
                row["review"] = "|".join((str(path), row["pr"], row["model"], row.get("variant") or ""))  # one model's review of one PR
                rows.append(row)
    return rows


def matches(candidate: Candidate, row: dict[str, str]) -> bool:
    if candidate.spared and row.get("category") == candidate.spared:
        return False
    fields = ("title", "detail") if candidate.where == "either" else (candidate.where,)
    return any(re.search(candidate.pattern, row.get(field) or "", re.IGNORECASE) for field in fields)


def tally(candidates: list[Candidate], rows: list[dict[str, str]]) -> dict[str, int]:
    """How many rows of each verdict any of `candidates` matches, and in how
    many PRs the wrong ones are: a pattern whose matches all come from one or
    two PRs has found those PRs' subject, not a kind of comment."""
    hit = [row for row in rows if any(matches(c, row) for c in candidates)]
    count = {verdict: sum(1 for row in hit if row["verdict"] == verdict) for verdict in ("wrong", "right", "unsure", "")}
    count["cases"] = len({row["pr"] for row in hit if row["verdict"] == "wrong"})
    return count


def repeats(rows: list[dict[str, str]]) -> str:
    """One title on several files of one review, which `review.quiet` keeps
    once from MIN_REPEATED_FILES files up: how often it happens at each number
    of files, and how the copies were marked."""
    claims: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        claims.setdefault((row["review"], slug(row["title"])), []).append(row)
    lines = [
        f"One title on several files, in the {len({row['review'] for row in rows})} reviews that have findings:",
        "",
        "| One title on | Times | Findings it would fold away | Copies marked wrong | Copies marked right |",
        "| --- | --- | --- | --- | --- |",
    ]
    for files in (2, 3, 4):
        hit = [copies for copies in claims.values() if len({row["path"] for row in copies}) >= files]
        verdicts = [row["verdict"] for copies in hit for row in copies]
        ships = " (what ships)" if files == MIN_REPEATED_FILES else ""
        lines.append(f"| {files} files or more{ships} | {len(hit)} | {sum(len({row['path'] for row in copies}) - 1 for copies in hit)} "
                     f"| {verdicts.count('wrong')} | {verdicts.count('right')} |")
    return "\n".join(lines) + "\n"


def shipped_candidates() -> list[Candidate]:
    """The patterns `review.quiet` uses, each with the category it spares."""
    return [Candidate(name, "title", pattern, spared=SPARED.get(name, "")) for name, pattern in NOT_DEFECTS.items()]


def table(rows: list[dict[str, str]]) -> str:
    shipped = shipped_candidates()
    total = {verdict: sum(1 for row in rows if row["verdict"] == verdict) for verdict in ("wrong", "right", "unsure", "")}
    lines = [
        f"{len(rows)} model findings in all: {total['wrong']} marked wrong, {total['right']} marked right, "
        f"{total['unsure']} unsure, {total['']} not marked.",
        "",
        "| Pattern | Looks at | Wrong removed | In PRs | Right lost | Unsure | Not marked | Ships |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for candidate in shipped + TRIED:
        count = tally([candidate], rows)
        if candidate in shipped:
            ships = "yes" if count["wrong"] else f"yes: {UNBACKED[candidate.name]}"
        elif count["right"]:
            ships = f"no: loses {count['right']} marked right"
        else:
            ships = f"no: {candidate.left_out}"
        pattern = candidate.pattern.replace("|", "\\|")  # a bare | would end the table's cell
        where = candidate.where + (f" (not in {candidate.spared} findings)" if candidate.spared else "")
        lines.append(f"| {candidate.name}: `{pattern}` | {where} | {count['wrong']} | {count['cases']} "
                     f"| {count['right']} | {count['unsure']} | {count['']} | {ships} |")
    count = tally(shipped, rows)
    lines += [
        "",
        f"**The shipped patterns together** remove {count['wrong']} of the {total['wrong']} findings marked wrong (in {count['cases']} PRs) "
        f"and {count['right']} of the {total['right']} marked right. They also match {count['unsure']} marked unsure and "
        f"{count['']} of the {total['']} nobody marked.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", default=str(RESULTS), help="the folder searched for findings.csv files")
    parser.add_argument("--out", help="a folder to write summary.md to; printed when left out")
    args = parser.parse_args()
    rows = marked(Path(args.results))
    text = "# What `review.quiet` may leave out, tried on the marked findings\n\n" + table(rows) + "\n" + repeats(rows)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.md").write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
