# Planted-bug benchmark

40 real merged PRs, each with one bug planted on a line it adds. Each model reviewed
them as the Action does: the `small` profile and an 8,192-token window, unless a variant says
otherwise. Only the model's own findings count. A finding is near the bug when it's on that file
within 3 lines; found means it was read and it describes the bug. Where the PRs were also
reviewed as merged (the clean control), *near only with the bug* counts the cases whose clean
version got no finding on that line: a model that comments on every changed line doesn't score.

| Model | Variant | PRs | Near the bug | Near it when clean | Near only with the bug | Found (dev + test) | Other findings | Other findings marked right | Findings on the clean PRs | Failed calls | Median time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `qwen/qwen-2.5-coder-32b-instruct` | - | 40 | 20 of 40 | - | - | 12 of 40 (6 + 6) | 38 | 3 of 38 marked | - | 0 of 44 | 13s |
| `qwen/qwen3-coder` | - | 40 | 18 of 40 | - | - | 15 of 40 (7 + 8) | 32 | 5 of 30 marked | - | 0 of 44 | 3s |

Found, by kind of bug:

| Model | Variant | flipped-comparison | inverted-return | missing-await | negated-condition | not-in | off-by-one | wrong-boolean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `qwen/qwen-2.5-coder-32b-instruct` | - | 6 of 11 | 1 of 4 | 0 of 4 | 2 of 9 | 0 of 1 | 1 of 4 | 2 of 7 |
| `qwen/qwen3-coder` | - | 6 of 11 | 2 of 4 | 1 of 4 | 3 of 9 | 1 of 1 | 0 of 4 | 2 of 7 |

## The run

- **What it is:** the same 40 cases as [2026-11-07-planted-actions-cpu](../2026-11-07-planted-actions-cpu/summary.md), reviewed by two larger open models served through OpenRouter, as a reference for the small ones. Workflow runs: [qwen3-coder](https://github.com/AnshulPatil2005/stratum-open/actions/runs/42324332131) and [qwen-2.5-coder-32b-instruct](https://github.com/AnshulPatil2005/stratum-open/actions/runs/42324338865), 2026-11-07. Both together cost US$0.14 on an evaluation key.
- **Settings:** the same prompt and settings as the small models: the `small` profile and an 8,192-token window. A model this size would normally get the `large` profile (a pass per file, a walkthrough), so these are like-for-like numbers, not each model's best.
- **Times:** the times are a hosted GPU service's, so they don't compare with the free runner's.

## How the findings were marked

The same way as the free-runner run (see its notes).

- **Found, with the consequence off:** two of Qwen3-Coder's found and two of the 32B's name the planted change as wrong but say what breaks, or how to fix it, inaccurately.
- **The planted bug on the wrong line:** two of Qwen3-Coder's other findings marked right describe the planted bug in httpx#3670 (`close()` without `await`), but on lines 199 and 217, outside the 3-line window, so they don't count as found.
- **Real bugs in the merged PRs:** both models found that jinja#2098's `__version__` returned werkzeug's version (fixed later in pallets/jinja@9c11dd6). Qwen3-Coder also flagged a debug `print` left in a textual test, and the 32B two docstring typos in textual#6455. Those three are real but trivial.
