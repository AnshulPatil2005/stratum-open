## The run

[Run 43349208178](https://github.com/stratum-org/stratum-open/actions/runs/43349208178), 13 Nov 2026, on GitHub's free runners (4 CPUs, 16 GB RAM, no GPU): the 19 dev cases, with the bug and as merged, in four shards each.

| Tool | Settings |
| --- | --- |
| PR-Agent 0.50.0 | its `review` and `improve` commands, set up as [others/README.md](../../others/README.md) says |
| stratum-open | the Action's settings with `review.reasoning: low` and `review.verify: strict` |

Both used `gpt-oss:20b` through the same pinned Ollama, an 8,192-token window and the same reduced thinking. The runner checked that Ollama had loaded the model with that window before PR-Agent's first call. The planted line was in a PR-Agent prompt for all 38 reviews, and none of its 108 model calls failed or was cut short.

## How the findings were marked

Only the findings near a planted bug were marked, by the rule used for stratum-open's own runs: right when the finding describes the planted bug. PR-Agent's were marked the same way, and where one could be read either way it was marked right.

## What it shows

| | Found | Near the line on a clean PR | Findings on the 19 clean PRs | Median time per PR |
| --- | --- | --- | --- | --- |
| PR-Agent, `review` only | 7 of 19 | 5 | 7 | |
| PR-Agent, `review` and `improve` | 10 of 19 | 9 | 20 | 599 s |
| stratum-open | 6 of 19 | 2 | 8 | 219 s |

- **Bugs found: no clear difference.** stratum-open with these settings found 8 of the same 19 cases on 8 Nov ([2026-11-08-gpt-oss-actions-cpu](../2026-11-08-gpt-oss-actions-cpu/summary.md)) and 6 here. On 19 cases a gap of two or three between single runs is what sampling gives, so PR-Agent's 10 can't be told apart from it.
- **Where they differ is what they say about code with nothing planted in it.** PR-Agent left 20 findings on the 19 merged PRs and had one near the planted line on 9 of them. stratum-open left 8, and was near the line on 2.
- **`improve` earns its catches and its noise.** Three of PR-Agent's ten catches came only from `improve` (pydantic#14244, scrapy#8461, textual#6464), and so did 13 of its 20 findings on clean PRs. `improve` is built to suggest something for every PR, so a suggestion on a clean PR is the tool working as designed, not a fault. It is still what a maintainer reads.
- **Time.** PR-Agent took 2.7 times as long per PR: three or more model calls, each with a longer prompt.
- **Neither found** either inverted `return`, or either off-by-one.

## What it doesn't show

- **One run of each on 19 cases.** Nothing here is a ranking.
- **A small local model on a CPU.** PR-Agent is built for hosted models with windows many times this size. With 8,192 tokens it drops its second `improve` pass on the two largest diffs, as its own token budget tells it to. This says nothing about PR-Agent on the models it is meant for.
- **Planted one-line logic bugs only.** PR-Agent's other output (the PR description, test suggestions, effort estimates) isn't measured, and nor are stratum-open's checks.
- **The findings away from the planted line weren't read,** so how many of either tool's other comments are real problems isn't known. PR-Agent's own 0 to 10 score is in each `improve` finding's detail and wasn't used to filter.
