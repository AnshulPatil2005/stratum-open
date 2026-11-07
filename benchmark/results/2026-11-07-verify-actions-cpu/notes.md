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
