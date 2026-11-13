# Checks that need no model

50 planted risks, each a PR adding files with one problem in them, and 184 real merged PRs as they are.

| Planted risk | Caught by its rule |
| --- | --- |
| Secrets and risky code | 20 of 20 |
| Bandit (optional extra) | 3 of 3 |
| GitHub workflows | 7 of 7 |
| Dependencies | 13 of 14 |
| Coding-agent files | 3 of 3 |
| Hidden Unicode | 3 of 3 |
| **All** | **49 of 50** |

Missed: `dep-jellyfish` (found: deps/missing, deps/new).

Controls, files a rule must leave alone: 3 of 3 left alone.

On the merged PRs: 6 finding(s) on 3 of 184 PRs. Marked: 6 right, 0 wrong (false alarms), 0 unsure.

| PR | Rule | Finding | Verdict | Why |
| --- | --- | --- | --- | --- |
| encode/httpx#3670 | `code/await-sync-file` | `await self._fin.close()` on a plain file | right | `self._fin` is the file from the builtin `open(value, 'rb')` a few lines up, so its `read()` and `close()` return plain values and awaiting them raises TypeError: a real bug in the merged PR. From the rule added after the 2026-11-07 run. |
| encode/httpx#3670 | `code/await-sync-file` | `await self._fin.read()` on a plain file | right | `self._fin` is the file from the builtin `open(value, 'rb')` a few lines up, so its `read()` and `close()` return plain values and awaiting them raises TypeError: a real bug in the merged PR. From the rule added after the 2026-11-07 run. |
| encode/httpx#3670 | `code/await-sync-file` | `await self._fin.close()` on a plain file | right | `self._fin` is the file from the builtin `open(value, 'rb')` a few lines up, so its `read()` and `close()` return plain values and awaiting them raises TypeError: a real bug in the merged PR. From the rule added after the 2026-11-07 run. |
| psf/requests#7496 | `deps/new` | New dependency: `cryptography` | right | The PR does add it, to the typing dependency group: accurate, for information. |
| psf/requests#7496 | `deps/new` | New dependency: `PyOpenSSL` | right | The PR does add it, to the typing dependency group: accurate, for information. |
| Textualize/rich#4075 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |

Run on 2026-11-13 on a laptop, with `--policy benchmark/planted/variants/known-vulnerabilities.yml`, which turns on `dependencies.known_vulnerabilities` (off by default). Bandit 1.9.4 was installed. Package registries were asked about each new dependency's name, and OSV (api.osv.dev) about each pinned version.

**These numbers are as of the run date.** OSV's answer for a version changes when an advisory is published or withdrawn, so the same command can give other findings on another day. Right means OSV does list that advisory for the pinned version, not that the project can be attacked through it.

**Planted.** Six new cases pin a version with a well-known advisory: `requests==2.19.1` in a requirements file, `PyYAML==5.3` in pyproject.toml, `lodash` 4.17.15 and `event-stream` 3.3.6 in package.json, `smallvec = "=1.6.0"` in Cargo.toml and `jwt-go v3.2.0+incompatible` in go.mod. All six were caught. Three controls must stay quiet, and did: a range that takes in vulnerable versions next to a current pin with no advisory (`requests>=2.19.1` with `urllib3==2.8.0`; `"lodash": "^4.17.15"` with `"minimist": "1.2.8"`), and a bare Cargo version, which is a range. For each of the 13 pinned versions in risks.yml, the finding (or its absence) was compared with OSV's records read a second way, with the `packaging` library's version order and the schema's own matching rules: the number of advisories, the severity and the fixed version agreed for all 13. One of them is a finding outside the new cases: `flask==3.1.0` in `dep-typo-requests` has two low advisories, fixed by 3.1.3.

**The 184 merged PRs: no `deps/vulnerable` finding, and no test of it either.** None of these PRs pins an exact version on a line it adds. They change 13 manifest files and add 5 dependency lines, all ranges or bare names, so OSV was asked nothing. This shows the rule stays out of the way of PRs like these. It says nothing about how often the rule is right.

The six findings on these PRs come from other rules. Three are the same as on 2026-11-07. The other three are `code/await-sync-file` on encode/httpx#3670, from the rule added after that run: the PR awaits `read()` and `close()` on a file from the builtin `open()`, which raises TypeError, so they are right.

**47 more merged PRs, picked because they change a manifest.** [pinned-prs.json](pinned-prs.json) lists them, and [pinned.csv](pinned.csv) has one row per pinned version. They come from the same 17 projects: for each manifest on the default branch outside test folders (12 at most per project), the PRs behind its 5 newest commits, where the commit title names the PR, and the 6 newest of those PRs per project. PRs already in clean.json were left out. They were run with the same command and `--prs pinned-prs.json`; only the `deps/vulnerable` findings were read.

- These PRs add 18 exact pins, all for PyPI, in requirements.txt and pyproject.toml.
- 3 were flagged, all in encode/httpx#3658: `mkdocs-material` 9.6.18 (1 advisory, medium), `cryptography` 45.0.7 (7 advisories, high) and `pytest` 8.4.1 (1 advisory, medium). All 3 are right: each version is inside the range of every advisory named, and the fixed version given (9.7.7, 50.0.0, 9.0.3) is outside all of them.
- 15 were not flagged, and OSV lists no advisory for any of the 15.
- That PR was merged on 2025-09-04 and every one of those advisories is newer, so the check would have said nothing on the day. Run today, it reports what has been found in those versions since.

**Not measured.** npm, crates.io and Go on real PRs (only the planted cases), and lockfiles, which the check doesn't read. Eighteen pins, in 8 PRs from three projects, is a small sample. An advisory is about the package, not about what a project calls in it, so some right findings won't matter to the project; how many isn't counted here.

**What the rule costs.** One request to OSV for all of a PR's pinned versions, and one more for each version that has advisories. A PR with no exact pin sends nothing.
