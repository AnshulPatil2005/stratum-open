# Checks that need no model

44 planted risks, each a PR adding files with one problem in them, and 184 real merged PRs as they are.

| Planted risk | Caught by its rule |
| --- | --- |
| Secrets and risky code | 20 of 20 |
| Bandit (optional extra) | 3 of 3 |
| GitHub workflows | 7 of 7 |
| Dependencies | 7 of 8 |
| Coding-agent files | 3 of 3 |
| Hidden Unicode | 3 of 3 |
| **All** | **43 of 44** |

Missed: `dep-jellyfish` (found: deps/missing, deps/new).

On the merged PRs: 3 finding(s) on 2 of 184 PRs. Marked by hand: 3 right, 0 wrong (false alarms), 0 unsure.

| PR | Rule | Finding | Verdict | Why |
| --- | --- | --- | --- | --- |
| psf/requests#7496 | `deps/new` | New dependency: `cryptography` | right | The PR does add it, to the typing dependency group: accurate, for information. |
| psf/requests#7496 | `deps/new` | New dependency: `PyOpenSSL` | right | The PR does add it, to the typing dependency group: accurate, for information. |
| Textualize/rich#4075 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |

Run on 2026-11-07 on a laptop: the checks give the same answer on any machine. Bandit 1.9.4 was installed; the Action doesn't install it by default, so its three cases count only with the `bandit` extra. Package registries were asked about each new dependency's name, as the Action does.

The planted risks follow real incidents where there was one (see `seen` in [risks.yml](../../planted/risks.yml)), and the typosquat names are those incidents' own; nothing was changed to make a case pass, and the one miss is kept. Written by the people who wrote the checks, they show the checks work on these shapes, not how much of everything they'd catch. The merged PRs are the false-alarm test: PRs from 17 popular Python projects that changed Python code outside the tests, up to 400 added lines each.

`jeIlyfish` (a capital I for an l) was taken down from PyPI, so it was still flagged, as a package that doesn't exist there. But it wasn't flagged as a look-alike, because `jellyfish` isn't on the popular-package list. Three other typosquats were flagged both ways, since PyPI has removed them too.
