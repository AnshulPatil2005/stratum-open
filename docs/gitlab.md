# Stratum Open on GitLab

Stratum reviews merge requests from a GitLab CI job, on gitlab.com or a
self-hosted GitLab. It's the same review as on GitHub (the same checks, model
passes, summary and triage), posted the GitLab way.

## Set it up

1. Create a **project access token** (Settings > Access tokens) with the
   **Developer** role and the **api** scope, and an expiry date. Stratum posts as
   the token's bot user. A group access token works too.
2. Add it as a CI/CD variable named `STRATUM_GITLAB_TOKEN` (Settings > CI/CD >
   Variables), with **Masked and hidden** set. Read [the safety section](#what-a-token-in-ci-means-on-gitlab)
   before you decide whether to protect it.
3. Add the job in [examples/gitlab-ci.yml](../examples/gitlab-ci.yml) to your
   `.gitlab-ci.yml`. It runs Ollama in the job with `qwen2.5-coder:7b`, as the
   GitHub Action does. To use your own model server, drop the Ollama lines and
   set `review.endpoint` in `.stratum/policy.yml`.
4. Optional: turn on **Auto-cancel redundant pipelines** (Settings > CI/CD >
   General pipelines), so a new push cancels the review of the old one.

The job needs `CI_JOB_TOKEN` only to clone your default branch for history. It
can't post the review: GitLab doesn't let a job token write MR notes.

Variables Stratum reads:

| Variable | Set by | What it's for |
| --- | --- | --- |
| `CI_MERGE_REQUEST_IID`, `CI_MERGE_REQUEST_PROJECT_ID`, `CI_API_V4_URL` | GitLab | Which MR, in which project, on which GitLab. A self-hosted instance's API URL comes from here. |
| `CI_PIPELINE_ID`, `CI_JOB_URL`, `CI_JOB_NAME` | GitLab | The commit status goes in this pipeline and links to this job; this job doesn't count as CI for triage. |
| `STRATUM_GITLAB_TOKEN` | you | The token Stratum posts with. |
| `STRATUM_CHECKOUT` | the example | A clone of the default branch with history. |
| `STRATUM_GITLAB_STATUS` | you, optional | `off` posts no commit status. |
| `STRATUM_GITLAB_API` | you, optional | Another API URL than the job's. |
| `STRATUM_BOT_LOGIN` | you, optional | The token user's username, which saves one call. |
| `STRATUM_MODEL_ENDPOINT`, `STRATUM_MODEL_NAME`, `STRATUM_MODEL_API_KEY`, `STRATUM_CONTEXT_TOKENS` | the example, or you | The model, as for the Action. |

`stratum-open gitlab --dry-run` prints the review instead of posting it.

## What it posts

- **A summary note** on the MR, edited in place on later pipelines. As on
  GitHub, it carries the hidden state of the last review, read back only from
  a note the token's user wrote, so the next push reviews only what changed.
- **Diff threads** on the lines, with a suggestion you can apply from GitLab
  (` ```suggestion:-N+0 `, N being the lines above the commented one that it
  replaces). A finding on several lines sits on its last line, and its
  suggestion covers them all.
- **Threads closed when the code changes.** The comment is struck through with
  the reason, and the thread is resolved.
- **A commit status** named like the check run (`Stratum`, or your
  `review.check_name`), in the MR's pipeline: `failed` where GitHub's check
  fails (a critical finding, by default), `success` otherwise, since GitLab has
  no neutral.
- **One triage label**, when your policy turns labels on, as on GitHub.

## What a token in CI means on GitLab

**What's the same.** Stratum never runs the MR's code. The job checks out
nothing of the MR (`GIT_STRATEGY: none`) and reads it only through the API.
The rules come from `.stratum/policy.yml` on the default branch, never from the
MR, and the history from a clone of the default branch.

**What's different.** On GitHub, `pull_request_target` runs the workflow as it
is on the default branch, so a PR can't change the job that holds the token.
On GitLab, an MR's pipeline runs the `.gitlab-ci.yml` of the MR's source
branch. So **anyone who can push a branch to the project can change this job
in their branch and read `STRATUM_GITLAB_TOKEN`**. Masking hides it from the
job log, nothing more; GitLab itself says masking "is not a guaranteed way to
prevent malicious users from accessing variable values".

What follows from that:

- **Keep the token's role no higher than that of the people who can push
  branches.** They are Developers already, so a Developer token lets them post
  as Stratum's bot, not do anything they couldn't. Never give it Maintainer or
  Owner, and set an expiry. Developer is what everything needs: resolving
  threads takes it, by GitLab's discussions API docs. With Reporter, expect
  resolving and the commit status to be refused; Stratum carries on without
  them.
- **Protecting the variable** limits it to pipelines on protected branches.
  An MR pipeline gets protected variables only on GitLab 18.1 or later, with
  "Allow merge request pipelines to access protected variables and runners"
  turned on, and only when both the source and target branches are protected,
  in the same project, and the person who started the pipeline can push or
  merge to the target. On most projects MR branches aren't protected, so the
  job wouldn't get the token. If yours are, protect it.
- **If that isn't enough**, the job's definition has to come from somewhere an
  MR can't edit: GitLab's pipeline execution policies (Ultimate) can enforce
  it, or a webhook service could run the review outside CI, as the GitHub App
  does. Stratum doesn't ship that service for GitLab.

**MRs from forks.** A fork's MR pipeline runs in the fork, and by default it
can't see the parent project's variables, so it has no token: the job says so
and posts nothing. To review one, a maintainer can run the MR's pipeline in
the parent project (the MR's Pipelines tab, Run pipeline), which then has the
project's variables. GitLab warns before it runs, and that pipeline runs the
fork's `.gitlab-ci.yml`, so read the MR's changes first, the CI file above all.
GitLab's docs say it plainly: "Fork merge requests can contain malicious code
that tries to steal secrets in the parent project when the pipeline runs."

## What works differently from GitHub

- **No `/stratum` commands.** A note doesn't start a pipeline, so the job
  never sees one. The hints that name commands are left out of what's posted
  on GitLab. To hide a finding on an MR, resolve its thread: Stratum doesn't
  post it again there. `/stratum review` becomes "run the pipeline again".
  See [Commands on GitLab](#commands-on-gitlab).
- **The commit status can block merging by itself.** On GitHub, a check blocks
  only when branch protection requires it. On GitLab, the status is part of the
  MR's pipeline, so with "Pipelines must succeed" on, a `failed` status blocks
  the merge. Set `STRATUM_GITLAB_STATUS: "off"` to post none, or
  `review.conclusion` in your policy to choose when it fails.
- **Drafts.** Marking a draft ready doesn't start a pipeline, so with
  `review.skip_drafts` the review waits for the next push or a pipeline you
  run. `review.events` doesn't apply on GitLab.
- **Slash lines are escaped.** GitLab runs quick actions (`/approve`,
  `/merge`, ...) found at the start of a line in a note, with the rights of the
  note's author. Any such line in what Stratum posts, which can quote the
  model or the MR, is written `\/approve` (shown as `/approve`, never run),
  outside code blocks. A suggestion holding such a line is left out, since its
  code can't be changed.
- **Track records** match a username to commits through the user's name and
  public email. GitLab's noreply addresses aren't matched yet.
- **Not on GitLab:** the triage queue issue, `replay`, the App, 👍/👎 feedback,
  `review.learn_from_history` and `review.early_checks` (each is turned off,
  and the summary says so), and annotations (GitLab has none; the findings are in the threads and the
  summary). The summary still says "PR" in places.

## Commands on GitLab

GitLab CI starts pipelines on pushes, schedules, triggers and the API, not on
notes, so commands need something that hears notes:

- A **project webhook** for "Comments" events, pointed at a small service that
  checks the `X-Gitlab-Token` header, reads `/stratum ...` from the note
  (`object_attributes.note`; `merge_request.iid` says which MR), and looks up
  the commenter's access (`GET /projects/:id/members/all/:user_id`, Developer
  is access level 30) before `review`, `ignore` or `explain`.
- Then either the service runs the command itself (the GitHub App's model,
  with the model reachable from that server), or it starts a pipeline with the
  [pipeline trigger API](https://docs.gitlab.com/ci/triggers/) and a variable
  such as `STRATUM_COMMAND=ignore:<note id>`, so the model keeps running in CI.
- `ignore`, `triage` and `record` need no model and would map onto the code
  that exists: `ignore` records the finding in the summary's state and redraws
  it, as on GitHub.

That service isn't built: it's a server to run and secure, which the CI job
avoids. Resolving a thread covers the commonest command, `ignore`.

## Verified how

Everything here is tested against a fake GitLab API (`tests/e2e/fake_gitlab.py`)
written from GitLab's REST docs: merge requests, notes, discussions and their
positions, commits and statuses, repositories (compare, merge base), repository
files, labels, and pagination. It hasn't run against a live GitLab yet, so
treat the first runs as a trial, and please report what differs.
