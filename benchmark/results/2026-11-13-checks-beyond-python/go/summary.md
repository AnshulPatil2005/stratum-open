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

On the merged PRs: 2 finding(s) on 1 of 60 PRs. Marked: 2 right, 0 wrong (false alarms), 0 unsure.

| PR | Rule | Finding | Verdict | Why |
| --- | --- | --- | --- | --- |
| cli/cli#14976 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| cli/cli#14976 | `paths/always-human` | Protected path changed | right | The PR does change a file under `.github/workflows/` (the Markdown source of an agentic workflow), which the default policy sends to a person: accurate. |
