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
