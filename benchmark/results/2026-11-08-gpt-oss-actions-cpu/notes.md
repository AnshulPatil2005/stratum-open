## The runs

All three ran on GitHub's free runners (4 CPUs, 16 GB RAM, no GPU) with the Action's settings and `review.reasoning: low`, on 7–8 Nov 2026.

| Run | Models | Variant | Cases |
| --- | --- | --- | --- |
| [42415897860](https://github.com/AnshulPatil2005/stratum-open/actions/runs/42415897860) | `gpt-oss:20b` | `review.verify: strict` | all 39, with the bug and as merged |
| [42415904158](https://github.com/AnshulPatil2005/stratum-open/actions/runs/42415904158) | `gpt-oss:20b` | `review.verify: lenient` | all 39, with the bug and as merged |
| [42416456748](https://github.com/AnshulPatil2005/stratum-open/actions/runs/42416456748) | `gpt-oss:20b`, `qwen2.5-coder:7b` | none | the 19 dev cases, with the bug only |

The third run was also the first to use `scripts/start-ollama.sh`, the Action's own setup script. It kept `gpt-oss:20b` out of the Actions cache folder ("too big for the Actions cache"). The disk had room for the pull, so no SDKs were removed. `qwen2.5-coder:7b` stayed in the cache folder. The 7B took `reasoning: low` with no failed calls, which shows the setting is safe on a model that doesn't think.

## How the findings were marked

Only the findings near a planted bug were marked; the rule is the same as before.

## What it shows

With the [mixture-of-experts run](../2026-11-08-moe-actions-cpu/summary.md), there are three full runs of `gpt-oss:20b` with `reasoning: low`:

| Verify | Found | Near the line when clean | Comments on the 39 clean PRs | Median time per PR |
| --- | --- | --- | --- | --- |
| none | 12 of 39 | 2 | 16 | 166 s |
| lenient | 16 of 39 | 2 | 10 | 222 s |
| strict | 16 of 39 | 0 | 7 | 210 s |

- **Verify didn't add the catches.** It can only drop findings, so the spread from 12 to 16 comes from the review sampling at temperature 0.1. On the 19 dev cases, two runs without verify caught 7 and 8, but only 5 of those were the same cases, and the two found 10 between them.
- **What verify does do:**
  - **Wrong comments:** they halve, from 16 to 7.
  - **Real catches:** none are lost. Unlike the 14B, `gpt-oss` can tell a right finding from a wrong one.
- **Recommended for the free runner:** `gpt-oss:20b` with `review.reasoning: low` and `review.verify: strict`.
