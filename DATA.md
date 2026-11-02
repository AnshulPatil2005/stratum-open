# Data

What this project stores, for how long, and what leaves the maintainers'
infrastructure.

**What exists today:** only the policy file loader and the track record. Both
read from the repository and store nothing. The GitHub App's storage below
isn't built yet. This page changes in the same PR as any code that stores
something, and a CI check will fail when a stored field isn't listed here.

## Install modes

| Mode | Where it runs | What it keeps |
| --- | --- | --- |
| GitHub Action | The project's own CI runners | Nothing between runs |
| GitHub App | A server the maintainers run | The rows in the table below |
| CLI | A maintainer's machine | Nothing |

## What is stored and for how long

This applies to the GitHub App. The Action and the CLI store none of it.

| Data | Default retention | How it's deleted |
| --- | --- | --- |
| Review findings and summaries | 180 days; the PR comments stay on GitHub | Automatically, or `stratum data delete --pr <number>` |
| 👍/👎 feedback and muted rules | Until a maintainer deletes them | `stratum data delete --feedback` |
| Track records | Not kept: a cache rebuilt from git, dropped after 7 days | Nothing to delete; `track_record.exclude` stops one being built |
| Job log: PR, time, outcome, errors | 14 days | Automatically |
| Code, diffs, model prompts and replies | Never stored | Nothing to delete |
| App private key, webhook secret, model API key | Environment variables on the server, never the database | Rotate them at GitHub or the model provider |

`data.findings_retention_days` changes the 180 days. `review.log_prompts: true`
keeps prompts and replies in the job log for its 14 days. Uninstalling the App
deletes its rows at once.

## What leaves the maintainers' infrastructure

- **GitHub API calls:** reading the PR, its diff and the repo history; posting
  comments, labels and the check run.
- **The model endpoint in the policy file.** With `model: local`, the default,
  that's a server the maintainers run, so code stays with them.
- **Package registries** (PyPI, npm, crates.io): only the names of
  dependencies a PR adds, to check how new they are.
  `dependencies.registry_lookup: false` turns this off.
- **Nothing goes to Stratum:** no telemetry, no account, no license check.

## Contributors

A track record is about a person, so whenever records are on:

- **Everyone can see their own record.** Commenting `@stratum record` on any PR
  in the project replies with their merged PRs in the window, the ones later
  reverted or fixed, and the commits behind each. `stratum record <login>` in
  the CLI shows the same.
- **A record stays in one project.** It's built from one repo's history and is
  never sent anywhere else.
- **A record changes nothing by default.** It moves triage labels only when the
  policy sets `track_record.affects_triage: true`.

A merged PR counts against its author when its merge commit is reverted, or
when a commit landing within 14 days references it (`#123`) with "fix", "fixes"
or "follow-up". Maintainers can keep a commit out of every record with a
`Stratum-Record: ignore` trailer, for example on a revert made for an unrelated
reason. `track_record.exclude` stops a record being built for a login or email.
