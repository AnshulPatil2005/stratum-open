# ShellCheck and actionlint on merged PRs

194 merged PRs as they are, and the scripts and workflows they change read whole, as if every line were new. Each issue was read and marked: right when what it says is true of the code, wrong when the code works as meant (a false alarm), unsure otherwise.

| Check | PRs it read | Added lines | Findings | Right | Wrong | Unsure | Files read whole | Lines | Findings | Right | Wrong | Unsure |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `checks.shellcheck` | 65 | 2,146 | 0 | 0 | 0 | 0 | 109 | 23,997 | 1 | 0 | 1 | 0 |
| `checks.actionlint` | 129 | 1,467 | 0 | 0 | 0 | 0 | 144 | 24,353 | 21 | 9 | 11 | 1 |

On 21 PRs, 17 files weren't read (past the limit of files, too long, or without their text): `acme.sh`, `cluster/gce/util.sh`, `libexec/pyenv---version`, `libexec/pyenv-completions`, `libexec/pyenv-global`, `libexec/pyenv-root`, `libexec/pyenv-sh-rehash`, `libexec/pyenv-shims`, `libexec/pyenv-version-file`, `libexec/pyenv-version-file-read`, `libexec/pyenv-whence`, `nvm.sh`, `plugins/pyenv-binary/bin/pyenv-binary`, `plugins/pyenv-binary/libexec/pyenv-binary-package`, `plugins/pyenv-binary/libexec/pyenv-binary-package-name`, `plugins/pyenv-binary/libexec/pyenv-binary-relocate`, `plugins/python-build/bin/python-build`.


## On added lines: every issue, kept or not

| Program | Code or kind | Kept | Issues | Right | Wrong | Unsure |
| --- | --- | --- | --- | --- | --- | --- |
| actionlint | `runner-label` | no | 8 | 0 | 8 | 0 |
| actionlint | `action` | no | 5 | 0 | 5 | 0 |
| actionlint | `workflow-call` | no | 2 | 0 | 2 | 0 |
| actionlint+shellcheck | `SC2193` | no | 2 | 0 | 2 | 0 |
| actionlint+shellcheck | `SC2034` | no | 1 | 1 | 0 | 0 |
| actionlint+shellcheck | `SC2046` | no | 1 | 0 | 1 | 0 |
| shellcheck | `SC2034` | no | 34 | 0 | 34 | 0 |
| shellcheck | `SC1007` | no | 6 | 0 | 6 | 0 |
| shellcheck | `SC2207` | no | 6 | 0 | 6 | 0 |
| shellcheck | `SC2124` | no | 2 | 0 | 2 | 0 |
| shellcheck | `SC2145` | no | 2 | 0 | 2 | 0 |
| shellcheck | `SC2184` | no | 2 | 0 | 0 | 2 |
| shellcheck | `SC2294` | no | 2 | 0 | 2 | 0 |
| shellcheck | `SC2064` | no | 1 | 0 | 1 | 0 |
| shellcheck | `SC2154` | no | 1 | 0 | 1 | 0 |
| shellcheck | `SC2206` | no | 1 | 0 | 1 | 0 |

## On whole files: every issue, kept or not

| Program | Code or kind | Kept | Issues | Right | Wrong | Unsure |
| --- | --- | --- | --- | --- | --- | --- |
| actionlint | `expression` | yes | 7 | 6 | 1 | 0 |
| actionlint | `permissions` | yes | 6 | 0 | 6 | 0 |
| actionlint | `syntax-check` | yes | 5 | 0 | 4 | 1 |
| actionlint | `events` | yes | 2 | 2 | 0 | 0 |
| actionlint | `matrix` | yes | 1 | 1 | 0 | 0 |
| shellcheck | `SC1073` | yes | 1 | 0 | 1 | 0 |
| actionlint | `runner-label` | no | 39 | 0 | 39 | 0 |
| actionlint | `syntax-check` | no | 14 | 0 | 14 | 0 |
| actionlint | `action` | no | 11 | 0 | 11 | 0 |
| actionlint | `workflow-call` | no | 2 | 0 | 2 | 0 |
| actionlint+shellcheck | `SC1070` | no | 10 | 0 | 10 | 0 |
| actionlint+shellcheck | `SC1133` | no | 7 | 0 | 7 | 0 |
| actionlint+shellcheck | `SC1065` | no | 3 | 0 | 3 | 0 |
| actionlint+shellcheck | `SC1072` | no | 3 | 0 | 3 | 0 |
| actionlint+shellcheck | `SC1073` | no | 3 | 0 | 3 | 0 |
| actionlint+shellcheck | `SC1081` | no | 3 | 0 | 3 | 0 |
| actionlint+shellcheck | `SC1141` | no | 3 | 0 | 3 | 0 |
| actionlint+shellcheck | `SC2276` | no | 2 | 0 | 2 | 0 |
| actionlint+shellcheck | `SC2283` | no | 2 | 0 | 2 | 0 |
| actionlint+shellcheck | `SC1050` | no | 1 | 0 | 1 | 0 |
| actionlint+shellcheck | `SC1102` | no | 1 | 0 | 1 | 0 |
| actionlint+shellcheck | `SC1137` | no | 1 | 0 | 1 | 0 |
| actionlint+shellcheck | `SC2242` | no | 1 | 0 | 1 | 0 |
| shellcheck | `SC2145` | no | 2 | 0 | 2 | 0 |
| shellcheck | `SC1072` | no | 1 | 0 | 1 | 0 |
| shellcheck | `SC1087` | no | 1 | 0 | 1 | 0 |
| shellcheck | `SC2068` | no | 1 | 0 | 1 | 0 |

What the checks keep:

| Where | Code or kind | Line | Verdict | Why |
| --- | --- | --- | --- | --- |
| cli/cli `.github/workflows/issue-triage.lock.yml`:173 | `expression` | `GH_AW_INFO_WORKFLOW_ID: ${{ job.workflow_ref }}` | wrong | `job.workflow_ref`, which GitHub added this after actionlint 1.7.12 was released. |
| cli/cli `.github/workflows/issue-triage.lock.yml`:481 | `permissions` | `copilot-requests: write` | wrong | A permission scope GitHub added this after actionlint 1.7.12 was released. |
| cli/cli `.github/workflows/issue-triage.lock.yml`:1539 | `syntax-check` | `queue: max` | wrong | `concurrency.queue`, which GitHub added this after actionlint 1.7.12 was released. |
| cli/cli `.github/workflows/issue-triage.lock.yml`:1875 | `permissions` | `copilot-requests: write` | wrong | A permission scope GitHub added this after actionlint 1.7.12 was released. |
| cli/cli `.github/workflows/dependabot-triage.lock.yml`:84 | `syntax-check` | `queue: max` | wrong | `concurrency.queue`, which GitHub added this after actionlint 1.7.12 was released. |
| cli/cli `.github/workflows/dependabot-triage.lock.yml`:398 | `permissions` | `copilot-requests: write` | wrong | A permission scope GitHub added this after actionlint 1.7.12 was released. |
| cli/cli `.github/workflows/dependabot-triage.lock.yml`:1196 | `syntax-check` | `queue: max` | wrong | `concurrency.queue`, which GitHub added this after actionlint 1.7.12 was released. |
| cli/cli `.github/workflows/dependabot-triage.lock.yml`:1480 | `permissions` | `copilot-requests: write` | wrong | A permission scope GitHub added this after actionlint 1.7.12 was released. |
| etcd-io/etcd `.github/workflows/antithesis-test.yml`:18 | `syntax-check` | `type: int` | unsure | `type: int` isn't one GitHub documents, yet the workflow runs every day on its schedule; a manual run's form is what it may break. |
| tokio-rs/tokio `.github/workflows/ci.yml`:679 | `expression` | `RUSTFLAGS: --cfg tokio_unstable -Dwarnings --cfg tokio_no_parking_lot --cfg toki` | right | The job's matrix has no such key, so the expression is empty: a blank in a step's name, or an empty input. Harmless here, but not what the line says. |
| tokio-rs/tokio `.github/workflows/ci.yml`:764 | `expression` | `target: ${{ matrix.target }}` | right | The job's matrix has no such key, so the expression is empty: a blank in a step's name, or an empty input. Harmless here, but not what the line says. |
| tokio-rs/tokio `.github/workflows/ci.yml`:1279 | `expression` | `- name: Install Rust ${{ matrix.rust }}` | right | The job's matrix has no such key, so the expression is empty: a blank in a step's name, or an empty input. Harmless here, but not what the line says. |
| clap-rs/clap `.github/workflows/ci.yml`:270 | `permissions` | `code-quality: write` | wrong | A permission scope GitHub added this after actionlint 1.7.12 was released. |
| termux/termux-packages `.github/workflows/packages.yml`:430 | `syntax-check` | `queue: max` | wrong | `concurrency.queue`, which GitHub added this after actionlint 1.7.12 was released. |
| pyenv/pyenv `plugins/python-build/bin/python-build`:125 | `SC1073` | `[ $# -eq 0 ] \|\| [ "$(osx_version)" "$@" ]` | wrong | `[ "$(osx_version)" "$@" ]` passes the operator in `$@`: bash runs it, ShellCheck can't parse it. |
| Homebrew/brew `.github/workflows/tests.yml`:227 | `permissions` | `code-quality: write` | wrong | A permission scope GitHub added this after actionlint 1.7.12 was released. |
| nvm-sh/nvm `.github/workflows/lint.yml`:21 | `expression` | `name: 'nvm install ${{ matrix.node-version }} && npm install'` | right | The job's matrix has no such key, so the expression is empty: a blank in a step's name, or an empty input. Harmless here, but not what the line says. |
| nvm-sh/nvm `.github/workflows/lint.yml`:40 | `expression` | `name: 'nvm install ${{ matrix.node-version }} && npm install'` | right | The job's matrix has no such key, so the expression is empty: a blank in a step's name, or an empty input. Harmless here, but not what the line says. |
| nvm-sh/nvm `.github/workflows/lint.yml`:57 | `expression` | `name: 'nvm install ${{ matrix.node-version }} && npm install'` | right | The job's matrix has no such key, so the expression is empty: a blank in a step's name, or an empty input. Harmless here, but not what the line says. |
| nvm-sh/nvm `.github/workflows/tests.yml`:29 | `matrix` | `- shell: ksh` | right | The `exclude` names a combination the matrix doesn't have, so it does nothing. Harmless. |
| moby/moby `.github/workflows/.test.yml`:27 | `events` | `default: "graphdriver"` | right | A required input with a default, which GitHub never uses. Harmless. |
| moby/moby `.github/workflows/.windows.yml`:18 | `events` | `default: "graphdriver"` | right | A required input with a default, which GitHub never uses. Harmless. |

## What the numbers show

**On the lines merged PRs add, neither check reported anything.** The two programs reported 76 issues there (ShellCheck's errors and warnings, actionlint's own, and ShellCheck's on `run:` scripts through actionlint). The checks keep none of them. Of the 76, 73 are false alarms, 2 are unsure, and 1 is right but harmless: a variable a `run:` script sets and never reads (acmesh-official/acme.sh#7274). So what's left out was left out with reason. What's kept was barely tested here: merged PRs rarely add a broken script.

**Whole files, read as new, are a harder test.** That's 23,997 lines of 109 scripts and 24,353 lines of 144 workflows:

- **ShellCheck** kept 1 finding, a false alarm. pyenv's `python-build` line 125 has `[ "$(osx_version)" "$@" ]`, which passes the test's operator in `$@`. Bash runs it, but ShellCheck can't parse it (SC1073). No parse error, nor any other kept code, fired anywhere else.
- **actionlint** kept 21 findings: 9 right, 1 unsure, 11 wrong.
  - The 9 right ones are all harmless. Six expressions name a matrix key the job doesn't have, which leaves a step's name blank or an input empty (tokio-rs/tokio, nvm-sh/nvm). Two are a required input that also has a default (moby/moby). One is an `exclude` for a combination the matrix doesn't have (nvm-sh/nvm).
  - The unsure one is etcd's `type: int` for a `workflow_dispatch` input. GitHub doesn't document that type, yet the workflow runs every day on its schedule.
  - Each of the 11 wrong ones is something GitHub added after actionlint 1.7.12 came out: `concurrency.queue` (4), the `copilot-requests` and `code-quality` permissions (6), and `job.workflow_ref` (1). This will happen whenever GitHub adds something, until actionlint catches up. So the finding says so for anything actionlint calls unknown, and no such finding is high.

## How the kept lists were decided

**ShellCheck: its errors, less five, and four warnings.** Every issue on the added lines and every error in the whole files is in the tables above, marked.

- **The errors.** All of them are kept except these five:
  - SC2145 and SC1087 each fired on merged code that works. SC2145 hit an array inside a string handed to `eval`, in pyenv's rehash hooks. SC1087 hit `$prefix_re[-.]` in a regular expression.
  - SC1072 always comes with SC1073 for the same syntax error, and only SC1073 is on the line where the error starts.
  - SC2045 (`for f in $(ls)`) and SC2068 (an unquoted `$@`) were left out on judgement. Both are about scripts that break on unusual input, not scripts that don't work. SC2068 fired once on moby/moby, on code that works.
- **The warnings.** Of the warnings that fired on the added lines, none named a real bug. The noisiest were SC2034, a variable that seems unused (34 places: termux's build files are sourced by its build system, and acme.sh reads its `*_info` variables by name), and SC1007 (6). SC2154, a name never assigned, was suggested as a candidate. It fired once, on acme.sh's `Le_Keylength`, which acme.sh sets before it runs the hook. The PR said so in a `# shellcheck disable` comment, which the check switches off. So SC2154 isn't kept.
- **The four warnings kept.** These are SC2069 (`2>&1 >file` in the wrong order), SC2098 (an expansion that can't see the assignment in front of its command), SC2216 (a pipe into a command that doesn't read its input) and SC2252 (`!= a || != b`, which is always true). Each names a script that does something other than what it says, and none fired on these 48,000 lines. So their noise isn't measured; it's kept down by their being rare. SC2097, SC2098's twin on the assignment, always comes with it and is left out.

**actionlint: its own rules, less four kinds of issue.**

- Runner labels it doesn't know: 39 in the whole files, all wrong. They're hosted labels newer than actionlint (`ubuntu-26.04`, 36), a matrix value, and a larger runner's name. A self-hosted runner's labels would need the project's actionlint config, which isn't read.
- `uses: $/...`: 13 in the whole files, 7 of them on added lines. It's GitHub's form for an action of the same repository at the running commit (sveltejs/svelte, vitejs/vite, prometheus/prometheus), which actionlint 1.7.12 calls malformed. The workflow check doesn't misread it.
- YAML anchors defined and not used: 14, all in nushell's CI. That's tidiness, and the workflow runs.
- Issue or PR text in a script, which `workflows.py` already reports.

**actionlint's ShellCheck on `run:` scripts is off.** In the whole files it reported 40 errors, all wrong:

- 38 were PowerShell, which ShellCheck read as bash: 36 in moby's Windows workflow, whose job picks its runner from an input, so actionlint couldn't tell the shell, and 2 in vite's.
- 1 came from the `${{ }}` placeholder actionlint puts in a script, which ShellCheck then takes for a constant: `exit ${{ ... && 1 || 0 }}` (SC2242, typeorm).
- 1 was `$((echo ...`, which bash runs as a command substitution (SC1102, nvm).
- On the added lines it found 4 issues: 3 wrong, and 1 right but harmless. Two of the wrong ones were the placeholder again: `if [[ "${{ matrix.python-version }}" == "micropython-"* ]]` (SC2193, pyenv).

## What it doesn't show

**Misses aren't measured.** These are merged PRs, so the run counts false alarms only. A check that keeps nothing looks clean here whatever it would miss. The kept codes are shown to work only by the planted scripts and workflows in `tests/test_checks_shellcheck.py` and `tests/test_checks_actionlint.py`, which were written by the person who chose the codes.

**The longest scripts weren't read.** ShellCheck slows down a lot on long scripts. On this machine it took about 20 s for acme.sh (295 KB) and more than 60 s for nvm.sh (243 KB). So the check reads scripts of up to 100,000 characters, and names any longer one in the summary as not read. On 21 PRs, 17 files went unread: acme.sh (8 PRs), nvm.sh (10), kubernetes' `cluster/gce/util.sh` (2), and 14 of the scripts pyenv/pyenv#3581 changes, past the limit of 20 files. So of the shell-heavy projects, acme.sh and nvm are measured mostly on their smaller files.

**Some scripts can't be found from a listing.** The PRs were picked by `.sh` and `.bash` files. A PR that changes only scripts with no extension (dokku's plugins, pyenv's `libexec/`) is picked only for its workflows. The check reads such scripts when a picked PR changes them, as it did for pyenv.

**The sample is narrow in other ways too:** 26 projects, PRs of at most 400 added lines, and one reader marked every row.

## What was run

Run on 2026-11-14 on a laptop, under Ubuntu 24.04 (WSL), on the `checks-shell` branch. It used ShellCheck 0.11.0 and actionlint 1.7.12, the official Linux release binaries, checked against the checksums GitHub lists for them. The tests also pass with ShellCheck 0.9.0, the version Ubuntu 24.04 packages. No model was run, and nothing from the PRs was run.

```sh
python benchmark/shell_checks.py pick
python benchmark/shell_checks.py run --out benchmark/results/2026-11-14-shell-checks
python benchmark/shell_checks.py whole --out benchmark/results/2026-11-14-shell-checks
python benchmark/shell_checks.py score benchmark/results/2026-11-14-shell-checks
```

**The PRs.** There are 194, listed in [clean-shell.json](../../planted/clean-shell.json), each pinned by its head commit. They come from the 18 projects of [corpus.py](../../corpus.py) and 8 with much of their code in shell: termux/termux-packages, acmesh-official/acme.sh, pyenv/pyenv, dokku/dokku, kubernetes/kubernetes, Homebrew/brew, nvm-sh/nvm and moby/moby. For each project, from the same 300 newest merged PRs as corpus.py and under the same size limit, the pick takes the 10 newest that add a line to a `.sh` or `.bash` file outside the tests, and the 5 newest that add a line to a workflow. 65 PRs gave ShellCheck a script to read (2,146 added lines), and 129 gave actionlint a workflow (1,467 added lines).

**On Windows, actionlint hangs** when it hands ShellCheck a script longer than a few KB. That's why this ran under Linux, the system GitHub's runners use, and why the check never asks actionlint for ShellCheck.

## What it cost

GitHub REST requests for each PR's diff that wasn't cached already, and one for each script and workflow it changes, from the cache after that. Picking the PRs listed 8 more projects with the GraphQL API: 48 requests, 6 for each project's 300 PRs.
