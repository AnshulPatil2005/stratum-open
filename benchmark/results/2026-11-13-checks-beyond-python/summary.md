# Checks that need no model, beyond Python

233 real merged PRs as they are, where every finding is a possible false alarm. Each finding was read and marked: right when what it says is true of the PR, wrong when the code is fine and the rule misread it (a false alarm), unsure otherwise.

| Group | PRs | PRs with a finding | Findings | Right | Wrong | Unsure |
| --- | --- | --- | --- | --- | --- | --- |
| [JavaScript and TypeScript](js/checks.csv) | 60 | 4 | 5 | 4 | 0 | 1 |
| [Go](go/checks.csv) | 60 | 1 | 2 | 2 | 0 | 0 |
| [Rust](rust/checks.csv) | 60 | 6 | 7 | 6 | 0 | 1 |
| [Workflows and manifests](infra/checks.csv) | 53 | 30 | 73 | 73 | 0 | 0 |
| **All** | **233** | **41** | **87** | **85** | **0** | **2** |

## JavaScript and TypeScript

60 PRs, 5 finding(s) on 4 of them.

| Rule | PRs | Findings | Right | Wrong | Unsure |
| --- | --- | --- | --- | --- | --- |
| `deps/new` | 1 | 1 | 0 | 0 | 1 |
| `paths/always-human` | 3 | 4 | 4 | 0 | 0 |

## Go

60 PRs, 2 finding(s) on 1 of them.

| Rule | PRs | Findings | Right | Wrong | Unsure |
| --- | --- | --- | --- | --- | --- |
| `paths/always-human` | 1 | 2 | 2 | 0 | 0 |

## Rust

60 PRs, 7 finding(s) on 6 of them.

| Rule | PRs | Findings | Right | Wrong | Unsure |
| --- | --- | --- | --- | --- | --- |
| `deps/new` | 1 | 1 | 0 | 0 | 1 |
| `paths/always-human` | 6 | 6 | 6 | 0 | 0 |

## Workflows and manifests

53 PRs, 73 finding(s) on 30 of them.

| Rule | PRs | Findings | Right | Wrong | Unsure |
| --- | --- | --- | --- | --- | --- |
| `paths/always-human` | 30 | 51 | 51 | 0 | 0 |
| `workflow/unpinned-action` | 4 | 20 | 20 | 0 | 0 |
| `workflow/write-permission` | 2 | 2 | 2 | 0 | 0 |

## Marked wrong or unsure

| PR | Rule | Where | Finding | Verdict | Why |
| --- | --- | --- | --- | --- | --- |
| vitejs/vite#24079 | `deps/new` | `playground/package.json`:14 | New dependency: `tinyexec` | unsure | The PR does add `tinyexec` to `playground/package.json`, but `packages/vite` and `packages/create-vite` in the same workspace already depend on it, and the lock file gains no package: new to that manifest, not to the repo. |
| tokio-rs/tokio#8753 | `deps/new` | `tokio-util/Cargo.toml`:70 | New dependency: `tracing-mock` | unsure | The PR does add `tracing-mock` to tokio-util's dev-dependencies, but `tokio/Cargo.toml` in the same workspace already has it at the same version: new to that manifest, not to the repo. |

## What was run

Run on 2026-11-13 on a laptop, on `main` at e71328f, with the default policy: the checks give the same answer on any machine. Bandit 1.9.4 was installed. Package registries were asked about each new dependency's name, as the Action does. No model was run, and nothing from the PRs was executed.

```sh
python benchmark/corpus.py pick
python benchmark/planted.py checks --risks benchmark/planted/risks.yml --prs benchmark/planted/clean-js.json \
    --out benchmark/results/2026-11-13-checks-beyond-python/js    # and go, rust, infra
python benchmark/corpus.py summary benchmark/results/2026-11-13-checks-beyond-python
```

Each group's folder is one run of `planted.py checks`, so it holds the 44 planted risks again (43 of 44 caught each time, as on 2026-11-07) beside the merged PRs. Every finding on a merged PR was read against the PR's diff and marked in that folder's `checks.csv`. For the two new dependencies, the repo's other manifests and lock file were read too.

## The PRs

233 merged PRs from 18 projects, picked by [corpus.py](../../corpus.py), which holds the rule. The projects are well known, merge PRs every month, and are of different kinds. Each language has one database library (typeorm, gorm, sqlx), where the SQL rule has the most to misread.

| Language | Projects |
| --- | --- |
| JavaScript and TypeScript | expressjs/express, fastify/fastify, vitejs/vite, sveltejs/svelte, eslint/eslint, typeorm/typeorm |
| Go | gin-gonic/gin, go-gorm/gorm, caddyserver/caddy, prometheus/prometheus, cli/cli, etcd-io/etcd |
| Rust | tokio-rs/tokio, tokio-rs/axum, clap-rs/clap, rust-lang/cargo, launchbadge/sqlx, nushell/nushell |

- **The three language groups**, 60 PRs each: for each project, its 10 newest PRs that were merged into the default branch before 2026-11-13, add at most 400 lines, and add a line to a source file of the language that the checks don't take for a test. Almost nine in ten were merged since September 2026; express and gorm merge more slowly and go back to January and March.
- **Workflows and manifests**, 53 PRs: for each project, from the PRs not picked above and under the same size limit, the newest by a person that adds a line to a workflow (18), the newest by a person that adds a line to a dependency manifest (18), and the newest by a bot that does either (17: Dependabot 11, Renovate 4, a release action 2; sqlx had none in the 300 PRs read). 30 of the 53 change a workflow and 27 a manifest (`package.json`, `go.mod` or `Cargo.toml`; none changes a Python one).

PRs to release branches are left out: a backport repeats a PR already counted. Each PR is pinned by its head commit in [clean-js.json](../../planted/clean-js.json), [clean-go.json](../../planted/clean-go.json), [clean-rust.json](../../planted/clean-rust.json) and [clean-infra.json](../../planted/clean-infra.json). All 233 were fetched, and every diff parsed to the number of added lines GitHub reports.

## What the numbers show

No finding was plainly wrong. Of 87 findings on 41 of the 233 PRs, 85 are right and 2 are unsure. On the 180 language PRs a maintainer would have seen 14 findings on 11 PRs, 12 of them "a workflow file changed".

**The 2 unsure ones are the only new-dependency findings in the run**, and both name a package the repo already used. The check reads only the manifests a PR changes. In a workspace, another member's manifest often names the package already, and that file isn't in the diff.

- Smallest example, tokio-rs/tokio#8753: `tokio-util/Cargo.toml` gains `tracing-mock = "= 0.1.0-beta.1"`, and `tokio/Cargo.toml`, which the PR doesn't touch, has had the same line all along. vitejs/vite#24079 is the same shape in a pnpm workspace: `tinyexec` added to `playground/package.json`, already in `packages/vite` and `packages/create-vite`.
- They're marked unsure, not wrong, because the manifest did gain the name. If "new" means new to the repo, as the finding's text says ("someone else's code running in this project"), both are false alarms.
- To narrow it: before reporting, look for the name in the repo's other manifests of that ecosystem, or in the lock file's diff. vite's `pnpm-lock.yaml` gains no package in that PR, only an entry under the playground. Cargo's own way of sharing a version (`divan.workspace = true`, rust-lang/cargo#17700) is already read as local and stays quiet.

**The rest is accurate, and much of it repeats.** None of this is a false alarm, but it's what a maintainer of these projects would read most:

- `paths/always-human`, 63 findings on 40 PRs: one for every workflow file a PR changes. A Dependabot PR that bumps actions in 8 workflow files gets 8 of the same finding (caddyserver/caddy#8340). To narrow it: one finding for the PR, naming the files.
- `workflow/unpinned-action`, 20 findings on 4 PRs: all four are a bot moving a tag (`uses: actions/setup-node@v4` to `@v7`, tokio-rs/tokio#8442), one finding per `uses:` line, 10 on gin-gonic/gin#4787. The action is used by tag, as the finding says, but it was before the PR too. To narrow it: skip a `uses:` line when the line it replaces named the same action by tag. The other 77 `uses:` lines the 233 PRs add were let pass: 72 pinned to a commit or local, 5 with no `@` to pin.
- `workflow/write-permission`, 2 findings: both are `id-token: write` on the one job that publishes, for trusted publishing to npm (expressjs/express#7464) and crates.io (rust-lang/cargo#17703). That's the setup the finding's own advice asks for, and it lets the job get a short-lived identity token (OIDC); it gives no write access to the repo, which "Workflow token gets write access" suggests. To narrow it: say what `id-token: write` is, or leave it out when it's the only write scope and it's set on one job.

## What they don't show

**The rules for code had almost nothing to read**, so their silence proves little. The three language groups add 4,693 lines of code outside tests (789 in the JavaScript and TypeScript group, 1,060 in the Go one, 2,844 in the Rust one; tests are another 6,976). Not one of those lines has a SQL statement of the kind the rule reads (`SELECT ... FROM`, `INSERT INTO`, `UPDATE ... SET`, `DELETE FROM`) in a string, an `eval`, `exec` or `spawn` call in JavaScript, a CORS origin, or a quoted value given to a name like `token` or `password`. The secret, eval, shell, SQL and CORS rules were quiet because nothing had their shape, not because they read something and let it pass. Even the three database libraries added no such SQL outside their tests.

- As a rate it stands: a maintainer of one of these projects would have had no finding from those rules on the 10 PRs read.
- It doesn't say how often those rules are right when the shape does turn up, such as SQL put together inside a query builder. That needs PRs picked for having such lines: read the diffs of more of the database libraries' PRs and keep the ones with SQL in a string outside tests, about 2 API requests for each PR read.
- On Go and Rust only the secret, SQL and CORS rules can fire at all. The eval and shell rules know Python and JavaScript.

**The dependency and workflow rules were read, but thinly on new names.** The PRs add 150 dependency lines the checks could see. 97 are crates of the project's own workspace and 51 are version bumps, moves, or names the same file already had, all let pass; 2 were reported, the two above. No PR added a package that was new to its repo, so the look-alike, made-up and brand-new-package rules had nothing to judge. The workflow rules saw 97 `uses:` lines and 2 write scopes, and no new `pull_request_target` trigger, self-hosted runner, or issue or PR text in a script. No PR touched a coding-agent file on the list or added a hidden character.

**Misses aren't measured here.** These are PRs taken to be fine, so the run counts false alarms only. Seen in passing: cli/cli#14976 changes `.github/workflows/issue-triage.md`, the prompt of an agentic workflow, and `.github/skills/ghmock/SKILL.md`, an agent skill. The first got the protected-path finding, but neither kind of file is on the coding-agent list.

**The sample is narrow in other ways too**: 18 projects, all libraries and tools and none an application, where secrets, CORS settings and hand-written SQL are more common; PRs of at most 400 added lines; one reader marked the findings.

## What it cost

About 850 GitHub REST requests for the 233 PRs (the PR twice, its diff, and each manifest, workflow and Python file it changes), from the cache after that. Picking the PRs used the GraphQL API only: about 130 points for one pass over the 18 projects.
