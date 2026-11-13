# Results

Every number the benchmarks give: its sample size, its 95% interval, how many runs it comes from, and how much of it a person has marked. How each benchmark works is in the [README](README.md).

This page is generated from the files in [results/](results/), so don't edit it. After adding or marking a run, run `python benchmark/results_page.py`. A test fails when the page is out of date, or when a README quotes a figure the results don't give.

**The intervals** are 95% Wilson score intervals, which hold up for small samples and for counts near none or all. Each takes the cases as a sample of the PRs a project might get, and says how far a count this small can be trusted. It can't say how well these cases stand for yours.

## The model's review: planted bugs

Real merged PRs, each with one bug planted on a line it adds. Where a run has the clean control, the same PRs were reviewed as merged too. Each row is one run of one model and variant: no row pools runs.

- **Variant:** the run's name for what it changed in the Action's settings. `-` is the settings as they come.
- **Cases:** how many PRs the run reviewed with the bug. *(dev)* is a run on the dev set alone, the cases a change is tuned on.
- **Found:** a finding within 3 lines of the bug, which a person read and marked as describing it.
- **At least:** a finding near a bug isn't marked yet, so the count is a lower bound. Its interval runs up to where it would end if every such case were marked found.
- **Near the line when clean:** clean PRs with a finding within 3 lines of where the bug goes.
- **Comments on the clean PRs:** the model's findings on the PRs as merged. Each is a possible false alarm, and all are counted, marked or not.
- **Failed calls** and **Median time per PR** cover the reviews with the bug and the clean ones together.
- **Not marked:** findings on the PRs with the bug that have no verdict yet, and how many of those are near a bug.
- **Runs:** how many runs of that model and variant are on this page. Where there's more than one, the next table sets them side by side.

| Model | Variant | Run | Cases | Found | 95% interval | Near the line when clean | Comments on the clean PRs | Per clean PR | Failed calls | Median time per PR | Not marked | Runs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `qwen2.5-coder:1.5b` | - | [2026-11-07-planted-actions-cpu](results/2026-11-07-planted-actions-cpu/) | 39 | 0 of 39 | 0% to 9.0% | - | - | - | 1 of 43 | 43s | 0 | 1 |
| `qwen2.5-coder:3b` | - | [2026-11-07-planted-actions-cpu](results/2026-11-07-planted-actions-cpu/) | 39 | 0 of 39 | 0% to 9.0% | - | - | - | 0 of 43 | 101s | 0 | 1 |
| `qwen2.5-coder:7b` | - | [2026-11-07-planted-actions-cpu](results/2026-11-07-planted-actions-cpu/) | 39 | 1 of 39 | 0.5% to 13% | - | - | - | 0 of 43 | 169s | 0 | 2 |
| `qwen2.5-coder:7b` | - | [2026-11-07-verify-actions-cpu](results/2026-11-07-verify-actions-cpu/) | 39 | at least 0 of 39 | 0% to 13% | 0 of 39 | 14 on 39 | 0.36 | 0 of 86 | 148s | 18 (1 near a bug) | 2 |
| `qwen2.5-coder:7b` | reasoning-low | [2026-11-08-gpt-oss-actions-cpu](results/2026-11-08-gpt-oss-actions-cpu/) | 19 (dev) | 0 of 19 | 0% to 17% | - | - | - | 0 of 21 | 98s | 6 | 1 |
| `qwen2.5-coder:7b` | verify | [2026-11-07-verify-actions-cpu](results/2026-11-07-verify-actions-cpu/) | 39 | 0 of 39 | 0% to 9.0% | 0 of 39 | 6 on 39 | 0.15 | 0 of 110 | 126s | 3 | 1 |
| `gemma3:12b` | reasoning-off | [2026-11-08-cache-fit-actions-cpu](results/2026-11-08-cache-fit-actions-cpu/) | 19 (dev) | 1 of 19 | 0.9% to 25% | 3 of 19 | 11 on 19 | 0.58 | 5 of 42 | 537s | 9 | 1 |
| `phi4:14b` | reasoning-off | [2026-11-08-cache-fit-actions-cpu](results/2026-11-08-cache-fit-actions-cpu/) | 19 (dev) | 3 of 19 | 5.5% to 38% | 7 of 19 | 37 on 19 | 1.95 | 8 of 42 | 572s | 19 | 1 |
| `qwen2.5-coder:14b` | - | [2026-11-07-planted-actions-cpu](results/2026-11-07-planted-actions-cpu/) | 39 | 10 of 39 | 15% to 41% | - | - | - | 7 of 43 | 409s | 0 | 2 |
| `qwen2.5-coder:14b` | - | [2026-11-07-verify-actions-cpu](results/2026-11-07-verify-actions-cpu/) | 39 | at least 0 of 39 | 0% to 54% | 13 of 39 | 57 on 39 | 1.46 | 5 of 86 | 353s | 51 (17 near a bug) | 2 |
| `qwen2.5-coder:14b` | verify | [2026-11-07-verify-actions-cpu](results/2026-11-07-verify-actions-cpu/) | 39 | at least 0 of 39 | 0% to 17% | 2 of 39 | 7 on 39 | 0.18 | 13 of 180 | 434s | 10 (2 near a bug) | 1 |
| `qwen3:14b` | reasoning-off | [2026-11-08-cache-fit-actions-cpu](results/2026-11-08-cache-fit-actions-cpu/) | 19 (dev) | 4 of 19 | 8.5% to 43% | 6 of 19 | 27 on 19 | 1.42 | 6 of 42 | 476s | 29 | 1 |
| `deepseek-coder-v2:16b` | - | [2026-11-08-moe-actions-cpu](results/2026-11-08-moe-actions-cpu/) | 39 | 4 of 39 | 4.1% to 24% | 12 of 39 | 90 on 39 | 2.31 | 0 of 86 | 143s | 78 | 1 |
| `gpt-oss:20b` | - | [2026-11-08-moe-actions-cpu](results/2026-11-08-moe-actions-cpu/) | 39 | 9 of 39 | 13% to 38% | 0 of 39 | 4 on 39 | 0.10 | 64 of 86 | 508s | 2 | 1 |
| `gpt-oss:20b` | reasoning-low | [2026-11-08-gpt-oss-actions-cpu](results/2026-11-08-gpt-oss-actions-cpu/) | 19 (dev) | 6 of 19 | 15% to 54% | - | - | - | 0 of 21 | 186s | 3 | 2 |
| `gpt-oss:20b` | reasoning-low | [2026-11-08-moe-actions-cpu](results/2026-11-08-moe-actions-cpu/) | 39 | 12 of 39 | 19% to 46% | 2 of 39 | 16 on 39 | 0.41 | 4 of 86 | 166s | 11 | 2 |
| `gpt-oss:20b` | reasoning-low-verify-lenient | [2026-11-08-gpt-oss-actions-cpu](results/2026-11-08-gpt-oss-actions-cpu/) | 39 | 16 of 39 | 27% to 57% | 2 of 39 | 10 on 39 | 0.26 | 4 of 131 | 222s | 11 | 1 |
| `gpt-oss:20b` | reasoning-low-verify-strict | [2026-11-08-gpt-oss-actions-cpu](results/2026-11-08-gpt-oss-actions-cpu/) | 39 | 16 of 39 | 27% to 57% | 0 of 39 | 7 on 39 | 0.18 | 3 of 122 | 210s | 8 | 1 |
| `qwen/qwen-2.5-coder-32b-instruct` | - | [2026-11-07-planted-hosted](results/2026-11-07-planted-hosted/) | 39 | 12 of 39 | 19% to 46% | - | - | - | 0 of 43 | 13s | 0 | 1 |
| `qwen/qwen3-coder` | - | [2026-11-07-planted-hosted](results/2026-11-07-planted-hosted/) | 39 | 15 of 39 | 25% to 54% | - | - | - | 0 of 43 | 3s | 0 | 1 |

### Run more than once

The same review can catch different bugs from one run to the next, so one run's count isn't the last word. These models and variants have more than one:

| Model | Variant | Runs | Found in each run |
| --- | --- | --- | --- |
| `qwen2.5-coder:7b` | - | 2 | 1 of 39 in [2026-11-07-planted-actions-cpu](results/2026-11-07-planted-actions-cpu/); at least 0 of 39 in [2026-11-07-verify-actions-cpu](results/2026-11-07-verify-actions-cpu/) |
| `qwen2.5-coder:14b` | - | 2 | 10 of 39 in [2026-11-07-planted-actions-cpu](results/2026-11-07-planted-actions-cpu/); at least 0 of 39 in [2026-11-07-verify-actions-cpu](results/2026-11-07-verify-actions-cpu/) |
| `gpt-oss:20b` | reasoning-low | 2 | 6 of 19 in [2026-11-08-gpt-oss-actions-cpu](results/2026-11-08-gpt-oss-actions-cpu/); 12 of 39 in [2026-11-08-moe-actions-cpu](results/2026-11-08-moe-actions-cpu/) |

Left out of every run: encode/httpx#3670. Not a bug: the stream's file comes from the builtin open(), so close() is synchronous and dropping its await fixes the line rather than breaking it. The merged PR awaits that file's read() and close() (lines 199, 205, 217), which raises TypeError on httpx's v1 branch.

## The checks that need no model

- **Planted risks caught:** each risk is a PR adding files with one problem in them. Caught means the rule written for it fired.
- **Merged PRs:** real merged PRs as they are, where every finding is a possible false alarm. A person marks each one right (what it says is true) or wrong (a false alarm).
- **PRs with a wrong finding:** the false-alarm rate. It reads *at least* while a finding is still not marked.

| Run | Planted risks caught | 95% interval | Missed | Merged PRs | Findings on them | Marked right | Marked wrong | Unsure | Not marked | PRs with a wrong finding | 95% interval |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [2026-11-07-checks](results/2026-11-07-checks/) | 43 of 44 | 88% to 99.6% | `dep-jellyfish` | 184 | 3 on 2 PRs | 3 | 0 | 0 | 0 | 0 of 184 | 0% to 2.0% |

## The missing-await check

- **Dropped awaits caught:** every `await` the merged PRs add, dropped one at a time. Caught means the check flagged that line.
- **PRs with a finding:** the same PRs as merged, where any finding is a possible false alarm. Nobody marks these, so each one counts.

| Run | Dropped awaits caught | 95% interval | Merged PRs | Findings on them | PRs with a finding | 95% interval |
| --- | --- | --- | --- | --- | --- | --- |
| [2026-11-07-missing-await](results/2026-11-07-missing-await/) | 42 of 56 | 62% to 84% | 184 | 0 | 0 of 184 | 0% to 2.0% |

## The model's review: merged PRs as they are

Real merged PRs with nothing planted, so each finding needs a maintainer to mark it. Until one does, a run has times and counts but no share right.

- **Right, of those marked:** findings marked right, of those marked right or wrong.
- **Not marked:** findings with no verdict yet, or marked unsure.

| Model | Run | PRs | Findings | Per PR | Right, of those marked | 95% interval | Not marked | Failed calls | Median time per PR |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `qwen2.5-coder:7b` | [2026-11-02-actions-cpu](results/2026-11-02-actions-cpu/) | 10 | 1 | 0.1 | - | - | 1 | 0 of 10 | 224s |
| `qwen2.5-coder:14b` | [2026-11-02-actions-cpu](results/2026-11-02-actions-cpu/) | 10 | 9 | 0.9 | - | - | 9 | 2 of 10 | 667s |

## Not included

Nothing: every folder under results/ is in a table above.
