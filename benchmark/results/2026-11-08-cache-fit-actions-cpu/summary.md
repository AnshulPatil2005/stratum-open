# Planted-bug benchmark

19 real merged PRs, each with one bug planted on a line it adds. Each model reviewed
them as the Action does: the `small` profile and an 8,192-token window, unless a variant says
otherwise. Only the model's own findings count. A finding is near the bug when it's on that file
within 3 lines; found means it was read and it describes the bug. Where the PRs were also
reviewed as merged (the clean control), *near only with the bug* counts the cases whose clean
version got no finding on that line: a model that comments on every changed line doesn't score.

| Model | Variant | PRs | Near the bug | Near it when clean | Near only with the bug | Found (dev + test) | Other findings | Other findings marked right | Findings on the clean PRs | Failed calls | Median time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gemma3:12b` | reasoning-off | 19 | 4 of 19 | 3 of 19 | 2 of 19 | 1 of 19 (1 + 0) | 9 | 0 of 0 marked | 11 | 5 of 42 | 537s |
| `phi4:14b` | reasoning-off | 19 | 7 of 19 | 7 of 19 | 2 of 19 | 3 of 19 (3 + 0) | 19 | 0 of 0 marked | 37 | 8 of 42 | 572s |
| `qwen3:14b` | reasoning-off | 19 | 7 of 19 | 6 of 19 | 5 of 19 | 4 of 19 (4 + 0) | 29 | 0 of 0 marked | 27 | 6 of 42 | 476s |

Found, by kind of bug:

| Model | Variant | flipped-comparison | inverted-return | missing-await | negated-condition | not-in | off-by-one | wrong-boolean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gemma3:12b` | reasoning-off | 0 of 6 | 0 of 2 | 0 of 1 | 0 of 4 | 1 of 1 | 0 of 2 | 0 of 3 |
| `phi4:14b` | reasoning-off | 2 of 6 | 0 of 2 | 0 of 1 | 0 of 4 | 0 of 1 | 0 of 2 | 1 of 3 |
| `qwen3:14b` | reasoning-off | 0 of 6 | 0 of 2 | 0 of 1 | 0 of 4 | 1 of 1 | 1 of 2 | 2 of 3 |

**57 finding(s) not marked yet**, so Found and the marked counts are incomplete.

Left out: encode/httpx#3670. Not a bug: the stream's file comes from the builtin open(), so close() is synchronous and dropping its await fixes the line rather than breaking it. The merged PR awaits that file's read() and close() (lines 199, 205, 217), which raises TypeError on httpx's v1 branch.

## The run

[42434564099](https://github.com/AnshulPatil2005/stratum-open/actions/runs/42434564099) ran on GitHub's free runners (4 CPUs, 16 GB RAM, no GPU) on 8 Nov 2026. It covered the 19 dev cases, each with the bug and as merged, using the Action's settings and `review.reasoning: off`.

The question was whether a model that fits the repo's 10 GB Actions cache does better than `qwen2.5-coder:14b`. Download sizes come from Ollama's registry:

| Model | Size |
| --- | --- |
| `qwen3:14b` | 9.3 GB |
| `phi4:14b` | 9.1 GB |
| `gemma3:12b` | 8.2 GB |

`qwen3` thinks by default, so it ran with `reasoning: off`. The setting makes no difference to the other two, which don't think.

## How the findings were marked

Only the findings near a planted bug were marked; the rule is the same as before.

## What it shows

On the same 19 dev cases:

| Model | Found | Near the line when clean | Findings on the 19 clean PRs | Failed calls | Median time per PR |
| --- | --- | --- | --- | --- | --- |
| `qwen2.5-coder:14b` ([verify run](../2026-11-07-verify-actions-cpu/summary.md), no verify) | 7 | 7 | 28 | 0 of 21 | 350 s |
| `qwen3:14b` | 4 | 6 | 27 | 6 of 42 | 476 s |
| `phi4:14b` | 3 | 7 | 37 | 8 of 42 | 572 s |
| `gemma3:12b` | 1 | 3 | 11 | 5 of 42 | 537 s |
| `gpt-oss:20b`, `reasoning: low`, `verify: strict` ([run](../2026-11-08-gpt-oss-actions-cpu/summary.md)) | 8 | 0 | 7 on all 39 | 3 of 122 on all 39 | 210 s |

- **None of the three beats `qwen2.5-coder:14b`.** Each found fewer bugs and was slower.
- **Their failed calls were timeouts.** A dense 12–14B model on 4 CPUs sometimes needs more than the 900 s limit; one other `gemma3` call stopped on Ollama's repeat limit.
- **The models that fit the cache are dense, so they run at about the 14B's speed.** `gpt-oss:20b` is fast because it's a mixture of experts with few active parameters. Of the mixtures of experts checked, only `deepseek-coder-v2:16b` is under 9 GB, and it found 4 of 39 ([run](../2026-11-08-moe-actions-cpu/summary.md)).
- **No model measured so far fits the cache and beats the 14B.** For a cached model, `qwen2.5-coder:14b` stays the best; overall, `gpt-oss:20b` is.

