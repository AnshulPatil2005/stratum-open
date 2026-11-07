# Small-model benchmark

Can a 7–14B open model running on a maintainer's own hardware review PRs well
enough to help? This folder holds the script that measures it and the results.

## Method

1. Take real merged PRs ([prs.json](prs.json)). Merged PRs passed their
   maintainers' review, so most findings on them should be wrong, and a model
   that flags little on them is doing its job.
2. Review each one with every model under test, with the same prompt, policy
   defaults and context window. Only the model's findings are counted: the
   checks that need no model are the same whatever the model.
3. Maintainers mark each finding in `findings.csv` as `right`, `wrong` or
   `unsure`.
4. `bench.py score` then reports, per model: findings marked right and wrong,
   failed calls, and the median time per PR.

```sh
python benchmark/bench.py run --prs benchmark/prs.json --out benchmark/results/<name> \
    --model qwen2.5-coder:7b@http://localhost:11434/v1 \
    --model qwen2.5-coder:14b@http://localhost:11434/v1
python benchmark/bench.py score benchmark/results/<name>
```

To measure on GitHub's free runners (4 CPUs, no GPU), the machine the Action
reviews on, start the **Benchmark** workflow from the Actions tab. Its results
come back as an artifact.

A hosted model can be compared the same way: pass its OpenAI-compatible
endpoint, with the key in `STRATUM_MODEL_API_KEY`.

## Planted bugs

Marking findings on merged PRs needs a maintainer for every row, so
[planted.py](planted.py) also measures against an answer known in advance.

- **The model's review.** [bugs.json](planted/bugs.json) is 40 real merged PRs (one since found not to be a bug, and left out: see its `invalid`)
  from 12 Python projects, each with one bug planted on a line it adds: a
  flipped comparison, a negated condition, `and` for `or`, an inverted return,
  a dropped `await`, an off-by-one. Each case says what the bug breaks. Each
  model reviews them as the Action does (the `small` profile, an 8,192-token
  window). A finding within 3 lines of the bug is near it; each finding is
  then read and marked: whether it describes the bug (`found`), and whether
  each other finding is a real problem, with the reason in its `note`. Start the **Planted-bug benchmark** workflow to run it on
  GitHub's free runners, one job per model and shard.
- **The checks that need no model.** [risks.yml](planted/risks.yml) is 44
  planted risks (secrets, code that runs input, workflow holes, typosquatted
  and made-up packages, coding-agent files, hidden Unicode), after real
  incidents where there was one. [clean.json](planted/clean.json) is 184
  merged PRs as they are, where every finding is a possible false alarm.

```sh
python benchmark/planted.py checks --risks benchmark/planted/risks.yml --prs benchmark/planted/clean.json \
    --out benchmark/results/<name>
python benchmark/planted.py run --bugs benchmark/planted/bugs.json --out benchmark/results/<name>/raw/7b \
    --model qwen2.5-coder:7b@http://localhost:11434/v1
python benchmark/planted.py score benchmark/results/<name>   # after marking the verdict columns
```

**Trying a change.** Each case is in the `dev` or the `test` set (in bugs.json, stratified by kind):
- **Tune on dev only.** Use `--set dev`, and run `test` once at the end, so the result isn't tuned to its cases.
- **Add the clean control.** `--clean` reviews the PRs as merged, without the bug. A method that comments on every changed condition flags the planted line in both, so the summary counts **near only with the bug**: the cases whose clean version got no finding on that line.
- **Name what you change.** `--policy change.yml --variant <name>` merges a policy file over the Action's settings and keeps the name with the results.

```sh
python benchmark/planted.py run --bugs benchmark/planted/bugs.json --set dev --out benchmark/results/<name>/raw/7b-planted \
    --model qwen2.5-coder:7b@http://localhost:11434/v1 --policy change.yml --variant change
python benchmark/planted.py run --bugs benchmark/planted/bugs.json --set dev --clean --out benchmark/results/<name>/raw/7b-clean \
    --model qwen2.5-coder:7b@http://localhost:11434/v1 --policy change.yml --variant change
```

## Results

Each run is a folder in [results/](results/), holding its `summary.md`, every
finding and every call. The machine each run used is noted in its summary.

| Run | Machine | Models |
| --- | --- | --- |
| [2026-11-02-actions-cpu](results/2026-11-02-actions-cpu/summary.md) | GitHub runner, 4 CPUs, no GPU | `qwen2.5-coder:7b`, `qwen2.5-coder:14b` |
| [2026-11-07-checks](results/2026-11-07-checks/summary.md) | Any (no model) | The checks: 43 of 44 planted risks caught, no false alarms on 184 merged PRs |
| [2026-11-07-planted-actions-cpu](results/2026-11-07-planted-actions-cpu/summary.md) | GitHub runner, 4 CPUs, no GPU | Planted bugs found: `qwen2.5-coder` 1.5B 0, 3B 0, 7B 1, 14B 10, of 40 |
| [2026-11-07-planted-hosted](results/2026-11-07-planted-hosted/summary.md) | OpenRouter (reference) | Planted bugs found: `qwen-2.5-coder-32b` 12, `qwen3-coder` 15, of 40, with the same prompt |
