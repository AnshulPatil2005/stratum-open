## The runs

All three ran on GitHub's free runners (4 CPUs, 16 GB RAM, no GPU) with the Action's settings, on the 39 planted-bug cases, each with the bug and as merged. The model's disk was cleared first (#34) so a 14 GB model fits.

| Run | Models | Variant |
| --- | --- | --- |
| [42396746900](https://github.com/AnshulPatil2005/stratum-open/actions/runs/42396746900) | `deepseek-coder-v2:16b`, `gpt-oss:20b` | none |
| [42403403901](https://github.com/AnshulPatil2005/stratum-open/actions/runs/42403403901) | `gpt-oss:20b` | `reasoning-low` (`review.reasoning: low`, #35) |

Both models are mixtures of experts: 16B and 21B parameters in all, but only 2.4B and 3.6B used for each token. That's why they fit the runner's 16 GB and run at small-model speed. A 30B mixture of experts at Q4 needs about 19 GB, so it doesn't fit.

## How the findings were marked

Only the findings near a planted bug were marked, plus one other right finding. The rule is the same as in the earlier runs: found means the finding names the planted change as wrong. The other findings are counted but not marked.

## What it shows

- **`gpt-oss:20b` with its default thinking:** 57 of its 86 calls came back without the JSON asked for. It spends its 2,048-token output budget thinking, and on a CPU the answer doesn't fit. The answers that did arrive found 9 of 39 bugs, never flagged a clean line, and made 4 comments on the 39 clean PRs.
- **`gpt-oss:20b` with `reasoning: low`:** 4 of 86 calls failed, at 166 s per PR, against 508 s. It found **12 of 39**: more than the 14B on the same runner (10), level with the hosted 32B (12), and close to the hosted 480B (15), all with the same prompt. It made 16 comments on the 39 clean PRs, the 7B's rate. It also flagged the real bug in pallets/jinja#2098 again.
- **`deepseek-coder-v2:16b`:** the fastest after the 7B's small-model peers, at 143 s per PR with no failed calls. But it comments on lines whether they're wrong or not: 12 near the planted line on the clean versions against 11 with the bug. It found 4 of 39 and made 90 comments on the clean PRs.
