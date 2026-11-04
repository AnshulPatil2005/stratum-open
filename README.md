# Stratum Open

PR review and triage for open-source maintainers, on a model you run
yourself.

Maintainers now get pull requests faster than they can read them, many of
them generated. Stratum Open reads each PR first and sorts the queue, so you
spend your review time on the ones worth it. It runs on small open models
(7–14B) on hardware you control, and your rules live in a file in your own
repo. Nothing is sent to Stratum: no account, no telemetry, no license check.

## What it does on each PR

- **Checks that need no model** catch what's risky whoever wrote it:
  - secrets in code, `eval`, shell commands and SQL built from strings, and CORS opened to everyone;
  - workflow changes that hand out a write token: `pull_request_target`, write permissions, unpinned third-party actions, and `${{ github.event... }}` inside `run:`;
  - new dependencies, packages published days ago, packages that don't exist (a typo, or a name an AI invented that someone could register), and look-alike names such as `reqeusts`;
  - edits to files that steer coding agents (`AGENTS.md`, `.cursorrules`, `.github/copilot-instructions.md`…), and hidden Unicode in any added line;
  - imports that break your module rules;
  - Python security issues via Bandit, when it's installed.
- **A review by the model:**
  - a summary of what the PR does, and whether that matches its description;
  - inline comments on the exact lines, with a suggested fix you can commit from GitHub;
  - each push reviews only what changed, and a finding stays until its code changes.
- **Context from the history:**
  - files that a lot of the code imports;
  - code that only one person has changed lately;
  - hotspots that change often and are complex, which get a stricter review.
- **One triage label:** `ready`, `needs work`, `low effort` or `needs a maintainer`, with the reasons spelled out. It judges what's in front of it: CI, tests, a linked issue, size, and whether the change matches its description. It doesn't try to guess whether AI wrote the PR, because detectors for that aren't reliable. Labels are off until your policy turns them on, and it closes nothing unless your policy says so.
- **A check run** that fails on a critical finding and is neutral on a high one. Make it required in branch protection if you want it to block merging.
- **A triage queue:** one issue listing every open PR, cheapest to review and best supported first.
- **Track records:** each author's merged PRs that were later reverted or fixed, read from git history. Every contributor can see their own, with `@stratum record`. A record affects nothing unless your policy says so.

## Quick start: the GitHub Action

Copy [examples/stratum.yml](examples/stratum.yml) to `.github/workflows/stratum.yml`. That's all.

That workflow runs `qwen2.5-coder:7b` with Ollama on GitHub's own runner, so you need no server. Ollama and the model go into the Actions cache on the first scheduled (or manual) run; PR runs read it, since GitHub doesn't let them write caches. GitHub's free runners have no GPU, though, so a review takes a few minutes. If your project has a machine with a GPU, run Ollama (or any OpenAI-compatible server) there instead. Then point `review.endpoint` in your policy file at it, and drop `ollama-model` from the workflow.

The workflow uses `pull_request_target` so it can comment on PRs from forks. It's safe here because the workflow checks out your default branch, never the PR, and Stratum reads the PR only through GitHub's API: no code from the PR ever runs. Keep it that way: don't add a step that checks out the PR.

## Your rules: `.stratum/policy.yml`

Every key, with its default, is in [policy.example.yml](policy.example.yml).

- The policy is read from your **default branch**, never from the PR. A PR that edits it is judged by the rules already merged.
- No file means the defaults. A broken file falls back to the defaults and says why in the review.
- Things you'll likely want to set:
  - `triage.enabled: true` turns the labels on;
  - `review.rules` holds your project's own review rules, in plain words;
  - `review.min_severity` sets how serious a model finding must be to be shown (default `medium`; the checks' findings always show);
  - `always_human.paths` lists paths that always need a maintainer;
  - `boundaries` holds module rules such as `api -> db`;
  - `track_record.affects_triage` lets an author's record move their PR's label.

## Commands

Comment on a PR:

| Command | Who | What it does |
| --- | --- | --- |
| `@stratum review` | write access | Review the whole PR again |
| `@stratum ignore` | write access | As a reply to an inline comment: hide that finding on this PR |
| `@stratum ignore <rule>` | write access | Hide a rule on this PR |
| `@stratum mute <rule>` / `unmute` | write access | Hide a rule on every PR (GitHub App only, since it stores it) |
| `@stratum explain` | anyone | As a reply to an inline comment: explain that finding in more detail |
| `@stratum triage` | anyone | Why the PR got its label |
| `@stratum record` | anyone | Your own track record in this repo |

## Other ways to run it

**GitHub App.** One container on your server, for projects that would rather install an App than add a workflow to every repo.
- It's the only mode that stores anything: findings for 180 days, 👍/👎 votes from people with write access (which mute rules your maintainers keep rejecting), and a 14-day job log. Uninstalling deletes a repo's rows at once.
- **Create the App:**
  - Permissions: contents read; pull requests, issues and checks read & write.
  - Events: pull request, issue comment, pull request review comment.
- **Run it:** `docker build -t stratum-open .` and run it with `STRATUM_APP_ID`, `STRATUM_APP_PRIVATE_KEY_FILE`, `STRATUM_WEBHOOK_SECRET` and `STRATUM_MODEL_ENDPOINT` set (see the [Dockerfile](Dockerfile)). Point the App's webhook at `https://your-host/webhook`.

**CLI.** `pip install git+https://github.com/AnshulPatil2005/stratum-open` for:
- `stratum review --local main`: review your branch against `main` before you open a PR. Handy for contributors too.
- `stratum review owner/repo#123`: a dry run on any PR; `--post` publishes it.
- `stratum record <login>`: a record from a local clone's history.
- `stratum policy check`: validate a policy file.
- `stratum triage owner/repo`: the sorted queue.
- `stratum data …`: see or delete what the App stored.

## Models

The target is a 7–14B open coding model. Defaults:
- `qwen2.5-coder:7b` through Ollama;
- any OpenAI-compatible server works;
- a hosted model works too, if your project prefers one (`review.model: hosted`, with the key in `STRATUM_MODEL_API_KEY`).

How much a review asks of the model depends on the model. `review.profile: auto` asks the model's server for its size:
- **`small`**, for models up to about 20B: a compact diff, 1–3 model calls.
- **`large`**, for bigger ones: the new and old code shown apart, a bigger context window, and Bandit's low-severity results too. It also reviews the 15 most important files one at a time, the top 5 of them again for security, performance and logic, and writes a walkthrough. That's up to about 29 model calls a PR, 4 at a time, instead of 1–3, so check the cost on a paid API. Set `review.file_reviews: 0` to keep the rest of `large` without them.

A profile only fills in settings your policy file doesn't set, so anything you set stays as you set it. Each review says which profile it used and why. `review.mode` (`general`, `security`, `performance` or `maintainability`) sets what the per-file passes look for first.

Small models make more mistakes than large ones, so Stratum checks their answers:
- a finding goes on the line whose code the model quoted, not the line number it guessed;
- a suggested fix is offered only where the model showed which lines it replaces;
- anything outside the diff is dropped;
- a finding that only describes the change ("New function") is dropped, and one vague worry repeated down a file becomes one finding.

The [benchmark](benchmark/) reviews real merged PRs with each model, and maintainers mark each finding right or wrong. Its results are published in `benchmark/results/` as each run finishes.

## Data

[DATA.md](DATA.md) lists every field stored, where it lives and for how long, and everything that leaves your infrastructure. A test fails when a stored field is added without a line there.

## Status

Early. It works end to end on GitHub. Expect rough edges, and please open issues.

## License

[GNU AGPL-3.0](LICENSE). Contributions are under the [CLA](CLA.md), which a bot asks you to sign on your first PR. See [CONTRIBUTING.md](CONTRIBUTING.md).

Copyright © 2026 Anshul Patil.
