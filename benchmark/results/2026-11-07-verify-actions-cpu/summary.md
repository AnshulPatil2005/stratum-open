# Planted-bug benchmark

39 real merged PRs, each with one bug planted on a line it adds. Each model reviewed
them as the Action does: the `small` profile and an 8,192-token window, unless a variant says
otherwise. Only the model's own findings count. A finding is near the bug when it's on that file
within 3 lines; found means it was read and it describes the bug. Where the PRs were also
reviewed as merged (the clean control), *near only with the bug* counts the cases whose clean
version got no finding on that line: a model that comments on every changed line doesn't score.

| Model | Variant | PRs | Near the bug | Near it when clean | Near only with the bug | Found (dev + test) | Other findings | Other findings marked right | Findings on the clean PRs | Failed calls | Median time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `qwen2.5-coder:7b` | - | 39 | 1 of 39 | 0 of 39 | 1 of 39 | 0 of 39 (0 + 0) | 17 | 0 of 0 marked | 14 | 0 of 86 | 148s |
| `qwen2.5-coder:7b` | verify | 39 | 0 of 39 | 0 of 39 | 0 of 39 | 0 of 39 (0 + 0) | 3 | 0 of 0 marked | 6 | 0 of 110 | 126s |
| `qwen2.5-coder:14b` | - | 39 | 15 of 39 | 13 of 39 | 6 of 39 | 0 of 39 (0 + 0) | 34 | 0 of 0 marked | 57 | 5 of 86 | 353s |
| `qwen2.5-coder:14b` | verify | 39 | 2 of 39 | 2 of 39 | 2 of 39 | 0 of 39 (0 + 0) | 8 | 0 of 0 marked | 7 | 13 of 180 | 434s |

Found, by kind of bug:

| Model | Variant | flipped-comparison | inverted-return | missing-await | negated-condition | not-in | off-by-one | wrong-boolean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `qwen2.5-coder:7b` | - | 0 of 11 | 0 of 4 | 0 of 3 | 0 of 9 | 0 of 1 | 0 of 4 | 0 of 7 |
| `qwen2.5-coder:7b` | verify | 0 of 11 | 0 of 4 | 0 of 3 | 0 of 9 | 0 of 1 | 0 of 4 | 0 of 7 |
| `qwen2.5-coder:14b` | - | 0 of 11 | 0 of 4 | 0 of 3 | 0 of 9 | 0 of 1 | 0 of 4 | 0 of 7 |
| `qwen2.5-coder:14b` | verify | 0 of 11 | 0 of 4 | 0 of 3 | 0 of 9 | 0 of 1 | 0 of 4 | 0 of 7 |

**82 finding(s) not marked yet**, so Found and the marked counts are incomplete.

Left out: encode/httpx#3670. Not a bug: the stream's file comes from the builtin open(), so close() is synchronous and dropping its await fixes the line rather than breaking it. The merged PR awaits that file's read() and close() (lines 199, 205, 217), which raises TypeError on httpx's v1 branch.

## The run

- **What it is:** the 39 planted-bug cases, each reviewed with the bug and as merged (the clean control), on GitHub's free runners (4 CPUs, no GPU) with the Action's settings.
- **The two runs:** one with the current method (variant `-`, [run](https://github.com/AnshulPatil2005/stratum-open/actions/runs/42383821042)) and one with `review.verify: strict` (variant `verify`, [run](https://github.com/AnshulPatil2005/stratum-open/actions/runs/42383815523)), both on 7 Nov 2026.
- **Unmarked:** these findings haven't been marked, so the comparison rests on the clean control and the counts. **Near only with the bug** is a case where the planted line got a finding with the bug and none without it.

## What it shows

- **7B:** strict verify cuts the comments on the clean PRs from 14 to 6, and the other comments on the bugged PRs from 17 to 3, with no slower median (126 s against 148 s). The 7B had nothing real to lose: its one near-only hit doesn't describe its bug.
- **14B:** strict verify also removes most of what the 14B finds. Near only with the bug falls from 6 to 2. The 14B, unlike the 7B, can judge findings, so it gets the `lenient` prompt instead.
- **The 14B and the clean control:** it comments on the planted line even in the clean version in 13 of 39 cases.

## The verify step replayed on marked findings

`benchmark/verify_replay.py` shows each finding already marked right or wrong (in [2026-11-07-planted-actions-cpu](../2026-11-07-planted-actions-cpu/summary.md)) to the model that made it, the way the verify step does. These counts are from a local GPU with the same Ollama weights:

| Model | `strict`: right kept | `strict`: wrong dropped | `lenient`: right kept | `lenient`: wrong dropped |
| --- | --- | --- | --- | --- |
| `qwen2.5-coder:7b` | 0 of 1 | 9 of 15 | 1 of 1 | 1 of 15 |
| `qwen2.5-coder:14b` | 6 of 11 | 31 of 38 | 11 of 11 | 11 of 38 |

The 7B's answers follow the prompt's tone rather than the finding, so only `strict` thins its comments. The 14B can tell right from wrong; `lenient` keeps all its right findings. A third prompt, neutral and without "most findings are wrong", kept 8 of 11 and dropped 22 of 38 for the 14B, and isn't shipped. Counts move by a finding or two between replays: the model samples at temperature 0.1.
