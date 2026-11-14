## The runs

All on GitHub's free runners (4 CPUs, 16 GB RAM, no GPU), 14 Nov 2026, on the 19 dev cases, each with the bug and as merged, with the Action's settings (#66).

| Run | Model | Variant |
| --- | --- | --- |
| [43548850195](https://github.com/stratum-org/stratum-open/actions/runs/43548850195) | `hf.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:UD-Q2_K_XL` | `review.verify: strict` |
| [43548853198](https://github.com/stratum-org/stratum-open/actions/runs/43548853198) | `hf.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:Q3_K_S` | `review.verify: strict` |
| [43551417975](https://github.com/stratum-org/stratum-open/actions/runs/43551417975) | `hf.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:Q3_K_S` | none: no verify pass |

`Qwen3-Coder-30B-A3B-Instruct` is a mixture of experts: 30.5B parameters in all, 3.3B used for each token. Ollama's library has it only at 4 bits and up (`qwen3-coder:30b`, 19 GB), so these are Unsloth's GGUF files from Hugging Face. Download sizes, from Hugging Face's manifest:

| Quantization | Size |
| --- | --- |
| `UD-Q2_K_XL` | 11.8 GB |
| `Q2_K` | 11.3 GB |
| `UD-IQ3_XXS` | 12.9 GB |
| `Q3_K_S` | 13.3 GB |
| `UD-Q3_K_XL` | 13.8 GB |
| `Q3_K_M` | 14.7 GB |
| `Q4_K_M` | 18.6 GB |

The two that were run were picked to leave room for the 8,192-token window (about 0.8 GB for this model) and the system in 16 GB. `Q3_K_M`, the issue's 3-bit pick, is 14.7 GB before the window, so it was left out. The model doesn't think, so `review.reasoning` was left unset.

Other mixtures of experts with about 3B parameters used per token, and their 4-bit sizes on Hugging Face: `ERNIE-4.5-21B-A3B` 13.3 GB, `SmallThinker-21B-A3B` 13.1 GB, `Ling-Coder-lite` (16.8B, 2.75B used) 11.2 GB, `Ling-mini-2.0` (16B, 1.4B used) 9.9 GB, and `Qwen3-30B-A3B-Instruct-2507`, the general model, at the same sizes as the coder. None of them was run.

`scripts/start-ollama.sh` now reads an `hf.co/` model's size from Hugging Face, which serves the same manifest as Ollama's registry. Each job said the model was "too big for the Actions cache" and kept it out of the cached folder. The disk had room for the pull, so no SDKs were removed.

## Memory, failed calls and time

- **No call was killed for memory, and none hit the 900 s limit.**
- **`Q3_K_S` filled the runner.** After each shard, 150–550 MB was left available and 0.1–1.6 GB had gone to swap. The model's process peaked at 14.4–14.7 GB resident. Besides the weights and the window, Ollama's server keeps earlier prompts' state for reuse, 1.5–1.8 GB by the end of a shard.
- **`UD-Q2_K_XL` fit.** 0.2–1.3 GB was left available, and one shard of eight used swap (0.3 GB).
- **Failed calls:** two of 150 for `UD-Q2_K_XL`, both on scrapy/scrapy#8431 (with the bug and as merged), where Ollama stopped the answer on its repeat limit. None for `Q3_K_S`.
- **Time:** a median of 232 s per PR for `UD-Q2_K_XL` and 186 s for `Q3_K_S` with verify, and 148 s for `Q3_K_S` without it. That's about `gpt-oss:20b`'s speed.

## How the findings were marked

Only the findings near a planted bug were marked, by the rule used for the earlier runs: right when the finding describes the planted bug. The other findings weren't read.

## What it shows

On the same 19 dev cases, with `gpt-oss:20b`'s earlier runs:

| Model | Verify | Found | Near only with the bug | Near the line when clean | Findings on the 19 clean PRs | Failed calls | Median time per PR |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gpt-oss:20b`, `reasoning: low` ([run](../2026-11-08-gpt-oss-actions-cpu/summary.md) 42415897860) | strict | 8 | 8 | 0 | 3 | 1 of 59 | 216 s |
| `gpt-oss:20b`, `reasoning: low` ([run](../2026-11-13-logic-pass-actions-cpu/summary.md) 43337430811) | strict | 9 | 9 | 1 | 4 | 0 of 67 | 219 s |
| `gpt-oss:20b`, `reasoning: low` ([run](../2026-11-08-moe-actions-cpu/summary.md) 42403403901) | none | 7 | 7 | 2 | 7 | 1 of 42 | 160 s |
| `Qwen3-Coder-30B-A3B`, `UD-Q2_K_XL` | strict | 1 | 0 | 2 | 3 | 2 of 150 | 232 s |
| `Qwen3-Coder-30B-A3B`, `Q3_K_S` | strict | 0 | 0 | 0 | 1 | 0 of 134 | 186 s |
| `Qwen3-Coder-30B-A3B`, `Q3_K_S` | none | 9 | 3 | 10 | 42 | 0 of 42 | 148 s |

- **With the strict verify pass, it finds almost nothing.** 1 of 19 at 2 bits and 0 of 19 at 3 bits, against 8 and 9 for `gpt-oss:20b` with the same settings.
- **The verify pass is what removes its catches.** Without the verify pass, `Q3_K_S` found 9 of 19, `gpt-oss:20b`'s number. With it, asked about each of its own findings, it said "not a real defect" to all but one of about 90 (the calls beyond the review's), and no finding near a planted bug was kept. `gpt-oss:20b` keeps its right findings through the same pass.
- **Without verify, it comments on the line whether it's wrong or not.** It was near the planted line on 10 of the 19 clean PRs, so only 3 of its catches were near the line only with the bug, and it made 42 comments on 19 clean PRs against `gpt-oss:20b`'s 3–7. That's the pattern of `deepseek-coder-v2:16b` ([run](../2026-11-08-moe-actions-cpu/summary.md)).
- **Neither quantization beats `gpt-oss:20b`, so neither was run on the 20 test cases.** `gpt-oss:20b` with `review.reasoning: low` and `review.verify: strict` stays the recommended model for the free runner. This closes the 30B question in #28 for the free runner: a 30B mixture of experts at 2–3 bits fits and runs at `gpt-oss:20b`'s speed, but it doesn't review better.

## What it doesn't show

- `review.verify: lenient` wasn't run. It can only drop findings from the 9 found and 42 comments without verify, and strict dropped nearly all of them.
- One run of each. Single-run differences of one to three are sampling, but 0–1 against 8–9 isn't.
- Unsloth's files only, at two quantizations. A 4-bit file doesn't fit the runner.
