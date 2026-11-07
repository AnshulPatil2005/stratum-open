# Planted-bug benchmark

39 real merged PRs, each with one bug planted on a line it adds. Each model reviewed
them as the Action does: the `small` profile and an 8,192-token window, unless a variant says
otherwise. Only the model's own findings count. A finding is near the bug when it's on that file
within 3 lines; found means it was read and it describes the bug. Where the PRs were also
reviewed as merged (the clean control), *near only with the bug* counts the cases whose clean
version got no finding on that line: a model that comments on every changed line doesn't score.

| Model | Variant | PRs | Near the bug | Near it when clean | Near only with the bug | Found (dev + test) | Other findings | Other findings marked right | Findings on the clean PRs | Failed calls | Median time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `qwen2.5-coder:1.5b` | - | 39 | 0 of 39 | - | - | 0 of 39 (0 + 0) | 4 | 0 of 4 marked | - | 1 of 43 | 43s |
| `qwen2.5-coder:3b` | - | 39 | 11 of 39 | - | - | 0 of 39 (0 + 0) | 37 | 0 of 37 marked | - | 0 of 43 | 101s |
| `qwen2.5-coder:7b` | - | 39 | 1 of 39 | - | - | 1 of 39 (0 + 1) | 13 | 0 of 13 marked | - | 0 of 43 | 169s |
| `qwen2.5-coder:14b` | - | 39 | 16 of 39 | - | - | 10 of 39 (7 + 3) | 33 | 1 of 33 marked | - | 7 of 43 | 409s |

Found, by kind of bug:

| Model | Variant | flipped-comparison | inverted-return | missing-await | negated-condition | not-in | off-by-one | wrong-boolean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `qwen2.5-coder:1.5b` | - | 0 of 11 | 0 of 4 | 0 of 3 | 0 of 9 | 0 of 1 | 0 of 4 | 0 of 7 |
| `qwen2.5-coder:3b` | - | 0 of 11 | 0 of 4 | 0 of 3 | 0 of 9 | 0 of 1 | 0 of 4 | 0 of 7 |
| `qwen2.5-coder:7b` | - | 1 of 11 | 0 of 4 | 0 of 3 | 0 of 9 | 0 of 1 | 0 of 4 | 0 of 7 |
| `qwen2.5-coder:14b` | - | 5 of 11 | 0 of 4 | 0 of 3 | 1 of 9 | 1 of 1 | 1 of 4 | 2 of 7 |

Left out: encode/httpx#3670. Not a bug: the stream's file comes from the builtin open(), so close() is synchronous and dropping its await fixes the line rather than breaking it. The merged PR awaits that file's read() and close() (lines 199, 205, 217), which raises TypeError on httpx's v1 branch.

## The run

- **Machine:** GitHub-hosted `ubuntu-latest` runners (4 CPUs, 16 GB RAM, no GPU), the machine the Action reviews on. One job per model and shard. [Workflow run](https://github.com/AnshulPatil2005/stratum-open/actions/runs/42314995839), 2026-11-07.
- **Settings:** Ollama 0.40.13 with its default Q4_K_M weights, the `small` profile, an 8,192-token window, and a 900 s limit per model call, as the Action has.
- **Shown:** every planted line was in the prompt the model got, so no miss comes from a clipped diff.

## How the findings were marked

Each finding was read against its planted bug and the PR's code. Its verdict and the reason are in the `verdict` and `note` columns of each shard's findings.csv.

- **Found:** the finding names the planted change as wrong. A finding that is only on the planted line, or that names the line in passing ("the same applies to line 469"), is near but not found.
- **Wrong consequence:** two of the 14B's ten found say what breaks wrongly. `rich#4006` calls the off-by-one an infinite loop. `scrapy#8431` adds a point about the secure flag that the line above already handles.
- **Other findings:** `right` means a real problem in the PR as merged, and `unsure` counts neither way.

## What it shows

- **The 14B timed out:** 7 of its 44 calls hit the 900 s limit, one in each of 7 PRs. Those calls gave no findings, as they would in the Action. Its 10 of 40 counts them as misses.
- **Bugs no model found:** no model found an inverted return or a dropped `await`.
- **A real bug in a merged PR:** one of the 14B's other findings is a bug in the merged PR itself. jinja#2098's `__version__` shim returned `importlib.metadata.version("werkzeug")`, and the maintainers fixed it later in pallets/jinja@9c11dd6. Every other finding outside the planted bugs was wrong or unsure. Most restate what the PR does as a problem, or ask for a timeout on a call that takes none.
