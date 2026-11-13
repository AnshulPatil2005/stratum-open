# Checks that need no model

44 planted risks, each a PR adding files with one problem in them, and 60 real merged PRs as they are.

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

On the merged PRs: 5 finding(s) on 4 of 60 PRs. Marked: 4 right, 0 wrong (false alarms), 1 unsure.

| PR | Rule | Finding | Verdict | Why |
| --- | --- | --- | --- | --- |
| vitejs/vite#24079 | `deps/new` | New dependency: `tinyexec` | unsure | The PR does add `tinyexec` to `playground/package.json`, but `packages/vite` and `packages/create-vite` in the same workspace already depend on it, and the lock file gains no package: new to that manifest, not to the repo. |
| eslint/eslint#21505 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| typeorm/typeorm#12893 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| typeorm/typeorm#12880 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| typeorm/typeorm#12880 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
