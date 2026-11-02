Machine: a GitHub-hosted `ubuntu-latest` runner (4 CPUs, 16 GB RAM, no GPU), the one the Action reviews on. Ollama 0.40.13, Q4_K_M weights, a 16,384-token context, and a 900 s limit per model call. [Workflow run](https://github.com/AnshulPatil2005/stratum-open/actions/runs/41428313841).

What this run shows so far, before any marking:

- **The 7B fits the free runner:** every call finished, with a median of under 4 minutes per PR. It flagged 1 finding across the 10 merged PRs.
- **The 14B is about three times slower on the same machine.** 2 of its 10 calls hit the 900 s limit, so for the Action it needs a GPU server rather than the runner. It flagged 9 findings.
- These PRs were all merged, so how many of the findings are right is the open question. That's what the marking answers.
