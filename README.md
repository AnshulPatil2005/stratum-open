# stratum-core (working name)

PR review and triage for open-source maintainers, running on a model the
maintainers host themselves. This is the open core of
[Stratum](https://github.com/AnshulPatil2005/Stratum), being split out into its
own project.

**Status:** early, and not published. The name and the license haven't been
chosen, so there is no LICENSE file yet. Until there is one, no rights to this
code are granted.

## What's here

- **`stratum_core.policy`** reads the maintainers' rules from
  `.stratum/policy.yml` on the default branch. A PR is always judged by the
  rules already merged, never by a policy it changes itself. No file means the
  defaults. A file that can't be used falls back to the defaults and says why.
  [`policy.example.yml`](policy.example.yml) lists every key with its default,
  and a test keeps it in step with the code.
- **`stratum_core.track_record`** builds each contributor's record from the
  default branch's git history: their merged PRs, and which of those were later
  reverted or fixed within `followup_days`. Nothing is stored. A
  `Stratum-Record: ignore` trailer on a commit keeps it out of every record.
  PRs landed by rebase carry no PR number in git, so they aren't counted.

What's planned next: the review pipeline from Stratum, the GitHub Action, the
GitHub App, the CLI and the small-model benchmark.

## Development

Python 3.11 or newer.

```sh
pip install -e ".[test]"
pytest
```

## Data

[DATA.md](DATA.md) says what is stored, for how long, and what leaves the
maintainers' infrastructure.
