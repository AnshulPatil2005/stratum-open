## The run

- **What it is:** the same 40 cases as [2026-11-07-planted-actions-cpu](../2026-11-07-planted-actions-cpu/summary.md), reviewed by two larger open models served through OpenRouter, as a reference for the small ones. Workflow runs: [qwen3-coder](https://github.com/AnshulPatil2005/stratum-open/actions/runs/42324332131) and [qwen-2.5-coder-32b-instruct](https://github.com/AnshulPatil2005/stratum-open/actions/runs/42324338865), 2026-11-07. Both together cost US$0.14 on an evaluation key.
- **Settings:** the same prompt and settings as the small models: the `small` profile and an 8,192-token window. A model this size would normally get the `large` profile (a pass per file, a walkthrough), so these are like-for-like numbers, not each model's best.
- **Times:** the times are a hosted GPU service's, so they don't compare with the free runner's.

## How the findings were marked

The same way as the free-runner run (see its notes).

- **Found, with the consequence off:** two of Qwen3-Coder's found and two of the 32B's name the planted change as wrong but say what breaks, or how to fix it, inaccurately.
- **The planted bug on the wrong line:** two of Qwen3-Coder's other findings marked right describe the planted bug in httpx#3670 (`close()` without `await`), but on lines 199 and 217, outside the 3-line window, so they don't count as found. That case was later found not to be a bug (see its `invalid` in bugs.json), so it's left out of the scores, these two findings with it.
- **Real bugs in the merged PRs:** both models found that jinja#2098's `__version__` returned werkzeug's version (fixed later in pallets/jinja@9c11dd6). Qwen3-Coder also flagged a debug `print` left in a textual test, and the 32B two docstring typos in textual#6455. Those three are real but trivial.
