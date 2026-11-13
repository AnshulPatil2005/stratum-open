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
  - new dependencies, packages published days ago, packages that don't exist (a typo, or a name an AI invented that someone could register), look-alike names such as `reqeusts`, and, when your policy turns it on, a dependency pinned to a version with published security advisories in the [OSV](https://osv.dev) database;
  - edits to files that steer coding agents (`AGENTS.md`, `.cursorrules`, `.github/copilot-instructions.md`…), and hidden Unicode in any added line;
  - imports that break your module rules;
  - in Python, when your policy sets `checks.unknown_names: true`, an import or a call of a name its module doesn't define: `from app.utils import normalize_path` when `utils.py` has no such function, or `os.path.makedirs`. It reads your own modules and 47 of the standard library's, and says nothing where it can't be sure;
  - when your policy turns it on (`checks.weakened_tests`), tests a PR weakens, on the line: a test switched off with nothing to decide when (`@pytest.mark.skip`, `it.skip`, `t.Skip()`, `#[ignore]`, a stray `.only`), assertions removed with nothing in their place, and an assertion's tolerance made wider. On [782 merged PRs](benchmark/results/2026-11-13-weakened-tests/summary.md) it reported 15 removed assertions, none misread, 11 of them plainly fine because the PR said why; the skip and tolerance rules found nothing there, and are proven on planted cases only;
  - in Python, when your policy sets `checks.removed_names: true`, a function, class or constant the PR removes or renames while another file still imports or uses it: `parse_cookie` becomes `parse_cookies` in `http.py`, and `request.py`, which the PR doesn't touch, still has `from app.http import parse_cookie`. The comment goes on the changed module and names the lines that will fail. It follows imports and module attributes only, and says nothing where it can't be sure. [Measured](benchmark/results/2026-11-13-removed-names/summary.md) on 17 projects: nothing on 184 merged PRs or on 5,100 commits as they were merged, and, with each commit cut down to the one file that lost a name, 144 of the 320 removed definitions the commit also took out of another file, and every line it named was one that commit changed;
  - in Python, a coroutine called without `await` inside an `async def`, when the same call is awaited elsewhere in the file, and `await` on a method of a plain file from `open()`;
  - in JavaScript and TypeScript, when your policy turns it on (`checks.floating_promises`) and the `js` extra is installed, a call left unawaited as a statement of its own in an `async` function, when the file awaits the same call elsewhere or declares it `async`. It caught a third of the awaits dropped from merged PRs and said nothing on the 60 PRs as merged ([how it was measured](benchmark/results/2026-11-13-floating-promises/summary.md));
  - Python security issues via Bandit, when it's installed;
  - Python errors Ruff can see without running the code (a name nothing defines, a format string that doesn't fit its arguments, `return` outside a function), when it's installed and your policy sets `checks.ruff: true`.
- **A review by the model:**
  - a summary of what the PR does, and whether that matches its description;
  - inline comments on the exact lines, with a suggested fix you can commit from GitHub;
  - each push reviews only what changed, and a finding stays until its code changes.
- **Context from the history:**
  - files that a lot of the code imports;
  - code that only one person has changed lately;
  - hotspots that change often and are complex, or keep needing reverts and hotfixes, which get a stricter review;
  - who owns the changed files, from CODEOWNERS.
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
  - `review.quiet: true` posts less of what the model says, and keeps it apart from what the checks found;
  - `always_human.paths` lists paths that always need a maintainer;
  - `boundaries` holds module rules such as `api -> db`;
  - `track_record.affects_triage` lets an author's record move their PR's label.
- `services` is used by hosted Stratum's service map (which service calls, uses, publishes to or consumes from which); Stratum Open ignores it, so the same file works for both. The example file shows it commented out.

## Commands

Comment on a PR:

| Command | Who | What it does |
| --- | --- | --- |
| `@stratum review` | write access | Review the whole PR again |
| `@stratum ignore` | write access | As a reply to an inline comment: hide that finding on this PR |
| `@stratum ignore <rule>` | write access | Hide a rule on this PR |
| `@stratum mute <rule>` / `unmute` | write access | Hide a rule on every PR (GitHub App only, since it stores it) |
| `@stratum explain` | the PR's author or write access, a few a day | As a reply to an inline comment: explain that finding in more detail |
| `@stratum triage` | anyone | Why the PR got its label |
| `@stratum record` | anyone | Your own track record in this repo |

## Other ways to run it

**GitHub App.** One container on your server, for projects that would rather install an App than add a workflow to every repo.
- It's the only mode that stores anything: findings for 180 days, 👍/👎 votes from people with write access (which mute rules your maintainers keep rejecting), and a 14-day job log. Uninstalling deletes a repo's rows at once.
- **Create the App:**
  - Permissions: contents read; pull requests, issues and checks read & write.
  - Events: pull request, issue comment, pull request review comment.
- **Run it:** `docker build -t stratum-open .` and run it with `STRATUM_APP_ID`, `STRATUM_APP_PRIVATE_KEY_FILE`, `STRATUM_WEBHOOK_SECRET` and `STRATUM_MODEL_ENDPOINT` set (see the [Dockerfile](Dockerfile)). Point the App's webhook at `https://your-host/webhook`.

**CLI.** `pip install git+https://github.com/stratum-org/stratum-open` for:
- `stratum-open review --local main`: review your branch against `main` before you open a PR. Handy for contributors too.
- `stratum-open review owner/repo#123`: a dry run on any PR; `--post` publishes it.
- `stratum-open record <login>`: a record from a local clone's history.
- `stratum-open policy check`: validate a policy file.
- `stratum-open replay owner/repo`: the label each of your last 50 closed PRs would have got, beside whether it was merged. No model, and nothing is posted.
- `stratum-open triage owner/repo`: the sorted queue.
- `stratum-open data …`: see or delete what the App stored.

## Models

The target is a 7–14B open coding model. Defaults:
- `qwen2.5-coder:7b` through Ollama;
- any OpenAI-compatible server works;
- a hosted model works too, if your project prefers one (`review.model: hosted`, with the key in `STRATUM_MODEL_API_KEY`).

On GitHub's free runner (4 CPUs, 16 GB RAM, no GPU) you can choose between two models. The numbers are from the [planted-bug benchmark](benchmark/README.md): 39 real PRs, each with one bug planted. Every number, with its sample size and 95% interval, is on the [results page](benchmark/RESULTS.md).

| `ollama-model` | Policy setting | Bugs found | Wrong comments per clean PR | Time per PR | Download |
| --- | --- | --- | --- | --- | --- |
| `qwen2.5-coder:7b` (the example's) | | 1 of 39 | about 0.4 | about 2.5 min | 4.7 GB, kept in the Actions cache |
| `gpt-oss:20b` | `review.reasoning: low` and `review.verify: strict` | 16 of 39 (12 to 18 over four runs) | about 0.2 | about 3.5 min | 13.8 GB, downloaded each run |

`gpt-oss:20b` finds far more, at about the same speed and with fewer wrong comments. With the logic pass and ten lines of code around each change it finds more again, for about three times the wait ([a more thorough review](#a-more-thorough-review)). Two costs of `gpt-oss:20b` either way:
- **Disk:** it doesn't fit the runner's disk as it comes, so the Action removes preinstalled SDKs it doesn't need to make room.
- **Cache:** it's bigger than a repository's 10 GB Actions cache, so it's downloaded each run, which adds a couple of minutes. Ollama itself is still cached.

It's a thinking model, so it needs `review.reasoning: low`. Without it, the thinking uses up the output budget and most replies come back empty.

`review.verify: strict` costs it no real catches and halves its wrong comments. On the 7B, by contrast, it removes noise only because there were no real catches to lose.

The same review can catch different bugs from one run to the next, so expect the counts to move by a few.

### A more thorough review

If you'd rather wait longer for more catches, turn on the logic pass and show the model ten lines of code around each change. In `.stratum/policy.yml`, with `ollama-model: gpt-oss:20b`:

```yaml
review:
  reasoning: low
  verify: strict
  logic_pass: true      # one more call per chunk, for logic errors only
  context_lines: 10     # unchanged lines shown around each change
```

On the planted-bug benchmark this found 27 of 39 bugs, against 18 for the same model without the two settings, including inverted `return`s and off-by-ones the plain review never caught. What it costs:

- **Time:** a review takes about three times as long, a median of about 11 minutes a PR on the free runner, and a large PR can take most of an hour.
- **Comments on code with nothing wrong:** about 0.4 a PR, against 0.2.

The two settings only help together: each on its own found one or two more. Neither is on by default, because of the wait. [How it was measured](benchmark/results/2026-11-13-logic-pass-actions-cpu/notes.md).

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

## Upgrading to 0.3.0

The command is now `stratum-open`, or `python -m stratum_open`; it was `stratum`. Hosted Stratum's client also installs a `stratum` command, and the new name lets the two sit side by side. Change any script, cron job or container command that runs `stratum`, such as `stratum serve` or `stratum data purge`. Nothing else is renamed: the Action, the `@stratum` comment commands, `.stratum/policy.yml` and the `STRATUM_*` settings work as before.

If your code embeds the engine (`stratum_open.analyze`): a check's finding can now have a line the diff doesn't show, when a file moves to where a check does more (out of `tests/`, or into `.github/workflows/`) and the check reads all of it. GitHub refuses an inline comment on such a line. Post inline only the findings `stratum_open.placeable(findings, files)` returns, and list the rest in the summary.

## Stratum Open and hosted Stratum

Stratum Open is the engine and the ways to run it yourself. Hosted Stratum runs the same engine as a service, with managed models and features that need a database (incidents, Drift, Insights). [How Stratum and Stratum Open fit together](docs/stratum-and-stratum-open.md) explains which does what, the commands in each, and which repository owns what.

## Status

Early. It works end to end on GitHub. Expect rough edges, and please open issues.

## License

[GNU AGPL-3.0](LICENSE). Contributions are under the [CLA](CLA.md), which a bot asks you to sign on your first PR. See [CONTRIBUTING.md](CONTRIBUTING.md).

Copyright © 2026 Anshul Patil.
