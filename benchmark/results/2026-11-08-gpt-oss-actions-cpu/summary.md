# Planted-bug benchmark

39 real merged PRs, each with one bug planted on a line it adds. Each model reviewed
them as the Action does: the `small` profile and an 8,192-token window, unless a variant says
otherwise. Only the model's own findings count. A finding is near the bug when it's on that file
within 3 lines; found means it was read and it describes the bug. Where the PRs were also
reviewed as merged (the clean control), *near only with the bug* counts the cases whose clean
version got no finding on that line: a model that comments on every changed line doesn't score.

| Model | Variant | PRs | Near the bug | Near it when clean | Near only with the bug | Found (dev + test) | Other findings | Other findings marked right | Findings on the clean PRs | Failed calls | Median time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `qwen2.5-coder:7b` | reasoning-low | 19 | 0 of 19 | - | - | 0 of 19 (0 + 0) | 6 | 0 of 0 marked | - | 0 of 21 | 98s |
| `gpt-oss:20b` | reasoning-low | 19 | 7 of 19 | - | - | 6 of 19 (6 + 0) | 3 | 0 of 0 marked | - | 0 of 21 | 186s |
| `gpt-oss:20b` | reasoning-low-verify-lenient | 39 | 17 of 39 | 2 of 39 | 17 of 39 | 16 of 39 (9 + 7) | 11 | 0 of 0 marked | 10 | 4 of 131 | 222s |
| `gpt-oss:20b` | reasoning-low-verify-strict | 39 | 17 of 39 | 0 of 39 | 17 of 39 | 16 of 39 (8 + 8) | 8 | 0 of 0 marked | 7 | 3 of 122 | 210s |

Found, by kind of bug:

| Model | Variant | flipped-comparison | inverted-return | missing-await | negated-condition | not-in | off-by-one | wrong-boolean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `qwen2.5-coder:7b` | reasoning-low | 0 of 6 | 0 of 2 | 0 of 1 | 0 of 4 | 0 of 1 | 0 of 2 | 0 of 3 |
| `gpt-oss:20b` | reasoning-low | 2 of 6 | 0 of 2 | 0 of 1 | 2 of 4 | 1 of 1 | 0 of 2 | 1 of 3 |
| `gpt-oss:20b` | reasoning-low-verify-lenient | 7 of 11 | 1 of 4 | 0 of 3 | 4 of 9 | 1 of 1 | 0 of 4 | 3 of 7 |
| `gpt-oss:20b` | reasoning-low-verify-strict | 6 of 11 | 0 of 4 | 1 of 3 | 6 of 9 | 1 of 1 | 0 of 4 | 2 of 7 |

**28 finding(s) not marked yet**, so Found and the marked counts are incomplete.

Left out: encode/httpx#3670. Not a bug: the stream's file comes from the builtin open(), so close() is synchronous and dropping its await fixes the line rather than breaking it. The merged PR awaits that file's read() and close() (lines 199, 205, 217), which raises TypeError on httpx's v1 branch.

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
