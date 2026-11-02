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

## Results

Each run is a folder in [results/](results/), holding its `summary.md`, every
finding and every call. The machine each run used is noted in its summary.
