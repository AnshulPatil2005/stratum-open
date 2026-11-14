# Gitea and Forgejo: what support would take

Not built yet. This is the plan, and what has to be checked first.

## Why it looks cheap

Gitea and Forgejo Actions run GitHub-style workflows, and their REST API
(`/api/v1`) follows GitHub's closely: `/repos/{owner}/{repo}/pulls/{index}`,
issue comments and their edits, the contents API with base64 text, commit
statuses, and pull request reviews with line comments. Much of
`stratum_open.github.api.GitHub` would work pointed at `https://host/api/v1`,
and the review talks to the host only through the calls listed in
`stratum_open.platform`, so a `Gitea` client could be a subclass that
overrides what differs, with `action.py` reused for the event payload.

## What differs, call by call

| Call | GitHub | Gitea / Forgejo | Work |
| --- | --- | --- | --- |
| `pull_diff` | `Accept: application/vnd.github.diff` | `GET /repos/{o}/{r}/pulls/{index}.diff` | small |
| `review_threads` | GraphQL `reviewThreads` | No GraphQL. List reviews, then each review's comments (`GET .../pulls/{index}/reviews/{id}/comments`); "resolved" from the comment's `resolver` field | medium |
| `resolve_thread` | GraphQL mutation | No documented endpoint found. A third-party page names `POST .../pulls/comments/{id}/resolve`; unconfirmed. `publish.update_threads` already copes with resolving being refused: it edits the comment to say it's closed | check |
| `edit_review_comment` | `PATCH .../pulls/comments/{id}` | Review comments are comments; whether `PATCH .../issues/comments/{id}` edits a code comment needs checking | check |
| `create_review` | `line`, `side`, `start_line` | `new_position` (new-file line) / `old_position`; no ranges, so a multi-line suggestion needs checking | small |
| `check_run` | Checks API | None. `POST .../statuses/{sha}` (`state`, `context`, `description`, `target_url`), as GitLab's commit status | small |
| `ci_state` | check runs + combined status | `GET .../commits/{ref}/status` only | small |
| labels | by name | `POST .../issues/{index}/labels` takes label ids on older versions; look the ids up by name | small |
| `linked_issues` | GraphQL `closingIssuesReferences` | none: return `[]` | none |
| `compare_files` | `/compare/{base}...{head}` | the same route on recent versions; on older ones return None (review everything) | check |
| `commit_emails` | `/commits?author=` | the same filter, if the version has it; otherwise an empty set | check |

Comment commands come with it: an Actions workflow on `issue_comment` and the
review-comment event runs `commands.handle` as on GitHub, with `permission()`
from `GET .../collaborators/{user}/permission`.

## What has to be checked before building it

- Whether `pull_request_target` runs the base branch's workflow with
  secrets on fork PRs, as on GitHub, in the Gitea and Forgejo versions to
  support. The safety model rests on it; if it doesn't hold, the GitLab
  section's caveats apply instead.
- The resolve and edit endpoints above, on a live instance of each.
- Gitea and Forgejo have diverged since 2024, so each claim needs checking on
  both, at the versions people run.

Without a live instance to test against, a client built now would be checked
only against a fake written from the same guesses, so it stops at this plan.
