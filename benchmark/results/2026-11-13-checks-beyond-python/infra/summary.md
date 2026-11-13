# Checks that need no model

44 planted risks, each a PR adding files with one problem in them, and 53 real merged PRs as they are.

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

On the merged PRs: 73 finding(s) on 30 of 53 PRs. Marked: 73 right, 0 wrong (false alarms), 0 unsure.

| PR | Rule | Finding | Verdict | Why |
| --- | --- | --- | --- | --- |
| expressjs/express#7464 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| expressjs/express#7464 | `workflow/write-permission` | Workflow token gets write access | right | The new workflow does give its publish job `id-token: write`, for trusted publishing to npm: accurate. It's on that job only, as the finding suggests, and gives no write access to the repo. |
| fastify/fastify#6966 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| fastify/fastify#6917 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| vitejs/vite#24032 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| vitejs/vite#24032 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| vitejs/vite#24032 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| vitejs/vite#24032 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| vitejs/vite#24032 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| sveltejs/svelte#19046 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| sveltejs/svelte#19046 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| eslint/eslint#21533 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| eslint/eslint#21533 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Renovate only moved the tag (`v4.39.2` to `v4.39.3`); it wasn't pinned before either. |
| eslint/eslint#21533 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Renovate only moved the tag (`v4.39.2` to `v4.39.3`); it wasn't pinned before either. |
| eslint/eslint#21533 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Renovate only moved the tag (`v4.39.2` to `v4.39.3`); it wasn't pinned before either. |
| eslint/eslint#21470 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| typeorm/typeorm#13053 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| typeorm/typeorm#12947 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| gin-gonic/gin#4832 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| gin-gonic/gin#4787 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| gin-gonic/gin#4787 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| gin-gonic/gin#4787 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| gin-gonic/gin#4787 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| gin-gonic/gin#4787 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (one major version up); it wasn't pinned before either. |
| gin-gonic/gin#4787 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (one major version up); it wasn't pinned before either. |
| gin-gonic/gin#4787 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (one major version up); it wasn't pinned before either. |
| gin-gonic/gin#4787 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (one major version up); it wasn't pinned before either. |
| gin-gonic/gin#4787 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (one major version up); it wasn't pinned before either. |
| gin-gonic/gin#4787 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (one major version up); it wasn't pinned before either. |
| gin-gonic/gin#4787 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (one major version up); it wasn't pinned before either. |
| gin-gonic/gin#4787 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (one major version up); it wasn't pinned before either. |
| gin-gonic/gin#4787 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (one major version up); it wasn't pinned before either. |
| gin-gonic/gin#4787 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (one major version up); it wasn't pinned before either. |
| go-gorm/gorm#7889 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| go-gorm/gorm#7578 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| go-gorm/gorm#6802 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| go-gorm/gorm#6802 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (`v3` to `v4`); it wasn't pinned before either. |
| go-gorm/gorm#6802 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (`v3` to `v4`); it wasn't pinned before either. |
| go-gorm/gorm#6802 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (`v3` to `v4`); it wasn't pinned before either. |
| go-gorm/gorm#6802 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (`v3` to `v4`); it wasn't pinned before either. |
| go-gorm/gorm#6802 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (`v3` to `v4`); it wasn't pinned before either. |
| go-gorm/gorm#6802 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (`v3` to `v4`); it wasn't pinned before either. |
| caddyserver/caddy#8340 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| caddyserver/caddy#8340 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| caddyserver/caddy#8340 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| caddyserver/caddy#8340 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| caddyserver/caddy#8340 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| caddyserver/caddy#8340 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| caddyserver/caddy#8340 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| caddyserver/caddy#8340 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| caddyserver/caddy#8312 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| caddyserver/caddy#8312 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| prometheus/prometheus#20370 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| prometheus/prometheus#20201 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| cli/cli#15041 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| cli/cli#15041 | `paths/always-human` | Protected path changed | right | The PR does change a file under `.github/workflows/` (the Markdown source of an agentic workflow), which the default policy sends to a person: accurate. |
| etcd-io/etcd#22226 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| tokio-rs/tokio#8725 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| tokio-rs/tokio#8442 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| tokio-rs/tokio#8442 | `workflow/unpinned-action` | Action not pinned to a commit | right | The line does use the action by tag, not by commit: accurate. Dependabot only moved the tag (`v4` to `v7`); it wasn't pinned before either. |
| tokio-rs/axum#3997 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| tokio-rs/axum#3945 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| clap-rs/clap#6556 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| clap-rs/clap#6556 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| clap-rs/clap#6556 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| clap-rs/clap#6556 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| clap-rs/clap#6556 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| clap-rs/clap#6305 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| rust-lang/cargo#17703 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| rust-lang/cargo#17703 | `workflow/write-permission` | Workflow token gets write access | right | The new workflow does give its publish job `id-token: write`, for trusted publishing to crates.io: accurate. It's on that job only, as the finding suggests, and gives no write access to the repo. |
| rust-lang/cargo#17670 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| launchbadge/sqlx#4475 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
| nushell/nushell#19453 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |
