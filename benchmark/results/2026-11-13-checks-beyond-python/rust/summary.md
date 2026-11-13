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

On the merged PRs: 7 finding(s) on 6 of 60 PRs. Marked: 6 right, 0 wrong (false alarms), 1 unsure.

| PR | Rule | Finding | Verdict | Why |
| --- | --- | --- | --- | --- |
| tokio-rs/tokio#8753 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| tokio-rs/tokio#8753 | `deps/new` | New dependency: `tracing-mock` | unsure | The PR does add `tracing-mock` to tokio-util's dev-dependencies, but `tokio/Cargo.toml` in the same workspace already has it at the same version: new to that manifest, not to the repo. |
| tokio-rs/tokio#8745 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| tokio-rs/axum#3935 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| clap-rs/clap#6545 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| clap-rs/clap#6535 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| launchbadge/sqlx#4514 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
