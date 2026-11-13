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
- **The checks that need no model.** [risks.yml](planted/risks.yml) is 107
  planted risks (secrets, code that runs input, a dropped promise, workflow holes, typosquatted
  and made-up packages, versions with known advisories, coding-agent files,
  hidden Unicode), after real incidents where there was one, an error for
  each rule of the Ruff check, and 4 controls a rule must leave alone.
  [clean.json](planted/clean.json) is 184 merged PRs as they are, where every
  finding is a possible false alarm. `--policy` takes a policy file, for a
  rule that is off by default: the six known-advisory cases are missed
  without [known-vulnerabilities.yml](planted/variants/known-vulnerabilities.yml).
  [whole_files.py](whole_files.py) runs the Ruff check's rules, and the ones
  left out of it, on every line of those PRs' Python files, since on the
  added lines alone they find next to nothing to judge.
  Those PRs are from Python projects. [corpus.py](corpus.py) picks 233 more from
  JavaScript and TypeScript, Go and Rust projects (`clean-js.json`,
  `clean-go.json`, `clean-rust.json`) and from PRs that change workflows or
  dependency manifests (`clean-infra.json`), and sums up a run on them.
  `--policy` takes a policy file, for a rule that is off by default: the two
  dropped-promise cases are missed without
  [floating-promises.yml](planted/variants/floating-promises.yml) and the
  `js` extra.
- **The floating-promise check.** [floating_promises.py](floating_promises.py)
  drops every `await` the PRs in `clean-js.json` add, one at a time, and asks
  whether the check flags the line. It also runs the check on those PRs as
  merged, and on merged code as if every line were new: the files the PRs
  touch, and the 18 repositories pinned in
  [repos-js.json](planted/repos-js.json), six of them kept back until the
  rule was settled. Each finding is then read and marked.

```sh
python benchmark/planted.py checks --risks benchmark/planted/risks.yml --prs benchmark/planted/clean.json \
    --out benchmark/results/<name> [--policy benchmark/planted/variants/known-vulnerabilities.yml]
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

### A second set, with other kinds of bug

The first set's bugs are all one-line logic flips, and a review tuned on seven kinds of flip can look better than it is. [bugs-2.json](planted/bugs-2.json) is 32 more cases in the same format: eight of each of four kinds, half `dev` and half `test`.

- **`swapped-arguments`:** two arguments of a call exchanged, where both are of the same type, like `urljoin(target, url)`.
- **`wrong-variable`:** a neighbouring variable or attribute of a fitting type where the right one should be.
- **`wrong-constant`:** an index, a key, a limit or a mask changed to another plausible one.
- **`dropped-statement`:** a statement the PR wrote, left out. Its `replace` is `null`. The diff is then numbered as if the line was never written, and a finding is near within 3 lines of the gap.

How the cases were chosen:
- **The PRs:** merged PRs from 11 of the first set's 12 projects (requests had too few that fit). Each changes Python outside the tests, adds at most 300 lines and touches at most 10 files. None is in the first set or in clean.json.
- **The bugs:** each is on a line the PR adds and changes what the code does, while the file still parses and every name on the line is defined and of a fitting type. Where there was a choice, the bug goes against a test, a docstring or a comment of the same PR, which is how a careful reviewer would catch it.
- **What a diff can show:** the planted line is never one the PR's diff already holds. A dropped line has the code that needs it right beside it, and another added line in its hunk: a hunk that only held the dropped line wouldn't be in the diff at all.

`validate` checks every case against its PR: the line is one the PR adds and reads `find`, and the file parses with the bug in it. With `--strict` it also refuses what it otherwise only notes, such as a `find` that several added lines match (three cases of the first set).

```sh
python benchmark/planted.py fetch --bugs benchmark/planted/bugs-2.json     # cache the PRs
python benchmark/planted.py validate --bugs benchmark/planted/bugs-2.json --strict
python benchmark/planted.py run --bugs benchmark/planted/bugs-2.json --set dev --out benchmark/results/<name>/raw/7b \
    --model qwen2.5-coder:7b@http://localhost:11434/v1
```

On GitHub's runners, give the **Planted-bug benchmark** workflow the file as its `bugs` input. Results on the second set are reported separately from the first set's, in a run folder of their own, so every "of 39" stays comparable.

## Results

Each run is a folder in [results/](results/), holding its `summary.md`, every
finding and every call. The machine each run used is noted in its summary.

| Run | Machine | Models |
| --- | --- | --- |
| [2026-11-02-actions-cpu](results/2026-11-02-actions-cpu/summary.md) | GitHub runner, 4 CPUs, no GPU | `qwen2.5-coder:7b`, `qwen2.5-coder:14b` |
| [2026-11-07-checks](results/2026-11-07-checks/summary.md) | Any (no model) | The checks: 43 of 44 planted risks caught, no false alarms on 184 merged PRs |
| [2026-11-07-planted-actions-cpu](results/2026-11-07-planted-actions-cpu/summary.md) | GitHub runner, 4 CPUs, no GPU | Planted bugs found: `qwen2.5-coder` 1.5B 0, 3B 0, 7B 1, 14B 10, of 40 |
| [2026-11-07-planted-hosted](results/2026-11-07-planted-hosted/summary.md) | OpenRouter (reference) | Planted bugs found: `qwen-2.5-coder-32b` 12, `qwen3-coder` 15, of 40, with the same prompt |
| [2026-11-07-verify-actions-cpu](results/2026-11-07-verify-actions-cpu/summary.md) | GitHub runner, 4 CPUs, no GPU | `review.verify` against the current method, 7B and 14B, with the clean control |
| [2026-11-08-moe-actions-cpu](results/2026-11-08-moe-actions-cpu/summary.md) | GitHub runner, 4 CPUs, no GPU | Mixtures of experts: `gpt-oss:20b` finds 12 of 39 with `review.reasoning: low`, `deepseek-coder-v2:16b` 4 |
| [2026-11-08-gpt-oss-actions-cpu](results/2026-11-08-gpt-oss-actions-cpu/summary.md) | GitHub runner, 4 CPUs, no GPU | `gpt-oss:20b` with `reasoning: low` and each `verify`: 16 of 39 found with strict verify, 7 comments on 39 clean PRs |
| [2026-11-08-cache-fit-actions-cpu](results/2026-11-08-cache-fit-actions-cpu/summary.md) | GitHub runner, 4 CPUs, no GPU | Models that fit the Actions cache, on the 19 dev cases: `qwen3:14b` 4, `phi4:14b` 3, `gemma3:12b` 1, against 7 for `qwen2.5-coder:14b` |
| [2026-11-13-osv](results/2026-11-13-osv/summary.md) | Any (no model) | `dependencies.known_vulnerabilities` on: 6 of 6 pins with known advisories caught, 3 of 3 controls left alone. No pinned version in the 184 merged PRs; in 47 more that change a manifest, 3 of 18 pins flagged, all 3 right |
| [2026-11-13-ruff](results/2026-11-13-ruff/summary.md) | Any (no model) | The Ruff check (`checks.ruff`, off by default): 55 of 55 planted errors caught and no finding on the lines 184 merged PRs add; 53 of its 55 rules fire nowhere in those files, so their false alarms aren't measured |
| [2026-11-13-checks-beyond-python](results/2026-11-13-checks-beyond-python/summary.md) | Any (no model) | The checks on 233 merged PRs from JavaScript and TypeScript, Go and Rust projects: 87 findings, none plainly wrong, 2 unsure. The rules for code had almost nothing to read there |
| [2026-11-13-floating-promises](results/2026-11-13-floating-promises/summary.md) | Any (no model) | `checks.floating_promises` on JavaScript and TypeScript: 42 of 126 dropped awaits caught, nothing on 60 merged PRs. On 1.56 million lines of six other projects read as new, 28 findings, 6 of them real mistakes and 16 false alarms |
