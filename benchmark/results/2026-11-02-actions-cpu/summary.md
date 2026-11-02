# Small-model benchmark

10 merged PR(s), reviewed by every model with the same prompt and settings.
Only the model's findings are counted; Stratum's checks don't depend on the model.

| Model | Median time per PR | Findings per PR | Failed calls | Marked right | Marked wrong | Not marked |
| --- | --- | --- | --- | --- | --- | --- |
| `qwen2.5-coder:7b` | 224s | 0.1 | 0 of 10 | 0 | 0 | 1 |
| `qwen2.5-coder:14b` | 667s | 0.9 | 2 of 10 | 0 | 0 | 9 |

No findings have been marked yet. Precision needs maintainers to mark each row of findings.csv right or wrong, then `bench.py score`.
