# Other reviewers on the planted-bug benchmark

The [planted-bug benchmark](../README.md#planted-bugs) says how many planted
bugs stratum-open's review finds. This folder runs another open-source
reviewer through the same cases, with the same local model on the same
machine, so the two numbers differ only by the review method.

Only open-source tools are compared. Hosted reviewers' terms restrict
publishing benchmarks of them.

So far there is one: [PR-Agent](https://github.com/The-PR-Agent/pr-agent).
The aim is a method its maintainers would accept. If something here is unfair
to PR-Agent, that is a bug: please open an issue.

## PR-Agent

**Version:** 0.50.0 from PyPI (tag `v0.50.0`, commit `e673a02`), with the
LiteLLM it pins, 1.105.1. [pr_agent.py](pr_agent.py) refuses to run with
another version, because the settings and output shapes below belong to this
one.

### How a case is run

1. **The same PR.** The case's PR data comes from the benchmark's cache: the
   title, the description and the diff stratum-open reviews.
2. **A local repository.** PR-Agent's
   [`local` provider](https://github.com/The-PR-Agent/pr-agent/blob/v0.50.0/docs/docs/usage-guide/local_git_provider.md)
   reviews a branch of a local repository against another branch, with no
   hosted pull request. The runner builds that repository:
   - It fetches the upstream project at the PR's head (`refs/pull/N/head`,
     one commit, over git: no API calls).
   - Branch `base` is that tree with the PR's diff taken back out. The diff's
     own `index` lines name every file's content before and after by hash,
     and the runner checks both, so a PR whose head moved fails loudly.
   - Branch `pr-N` is one commit on `base`: the PR's head, with the planted
     line swapped in, or as merged for the clean control.
   - Nothing is checked out. The provider reads commits, never the working
     tree.
3. **The PR's title and description.** The `local` provider would use the
   branch name as the title and the commit messages as the description. The
   runner writes the real title and description as the head commit's message,
   and [pr_agent_local.py](pr_agent_local.py) registers a subclass of the
   provider that returns them as such. This is the only code added to
   PR-Agent, through `register_git_provider`, its documented way to add a
   provider.
4. **Two commands.** PR-Agent's own command line runs `review`, then
   `improve`: what its GitHub Action runs on a new PR, less `describe`, which
   reports no defects. Each writes its result with `--json-output`.
5. **Nothing is published.** `config.publish_output` is off, and PR-Agent's
   process gets no token and none of the CI job's environment.
6. **The model calls are recorded.** PR-Agent talks to Ollama through a
   pass-through in the runner that forwards each request unchanged and keeps
   it. From it come `shown` (the planted line is in a prompt, as an added
   line of its file), the number of calls, and their time.

### What counts as a finding

One row of `findings.csv` per issue PR-Agent raises that names a file:

- **`review`:** each entry of `key_issues_to_review`, on the file and lines
  it gives. An entry without a usable line keeps its file and can't be near
  the bug. The `security_concerns` text names no file or line and isn't
  counted.
- **`improve`:** each suggestion its summary comment would list. PR-Agent
  itself drops a suggestion that scores 0, one whose file isn't in the PR,
  and one whose lines aren't in the file; the runner applies the last two,
  which PR-Agent applies only when it publishes. The suggestion's score is
  kept: `severity` is PR-Agent's own impact label (high from 9, medium
  from 7).

`category` says which command raised it. `planted.py score` then applies its
one rule to both tools: near the bug means on the planted file within 3
lines, and found means a person read the finding and it describes the bug.

### Settings

Set as PR-Agent's own documentation says to for a local model
([Changing a model, Ollama](https://github.com/The-PR-Agent/pr-agent/blob/v0.50.0/docs/docs/usage-guide/changing_a_model.md#ollama)).
Where it says nothing, the choice is in PR-Agent's favour. Every setting that
isn't its default:

| Setting | Value | Default | Why |
| --- | --- | --- | --- |
| `config.model` | `ollama/<tag>` | a hosted model | The documentation's form for an Ollama model. The tag is the one stratum-open runs. |
| `config.fallback_models` | `[]` | a hosted model | The documentation's Ollama example. |
| `ollama.api_base` | the Ollama server | none | The documentation's Ollama example. `OLLAMA_API_BASE`, LiteLLM's own name for it, is set to the same address. |
| `config.custom_model_max_tokens` | `8192` | unset | The documentation: the same number as the server's context window. |
| `config.duplicate_prompt_examples` | `true` | `false` | The documentation's Ollama example: it repeats the output example, which helps a small model keep the format. |
| `config.reasoning_effort`, `config.additional_reasoning_effort_models` | `low`, the model | `medium`, none | Only when the stratum-open run it is compared with sets `review.reasoning` (pass its policy file with `--policy`), so both tools ask the model to think as much. Without the second setting PR-Agent sends no effort to an Ollama model. `gpt-oss` gets the level itself (`think: low`, as from stratum-open); for other models LiteLLM can only switch thinking on or off. Skipped for a model Ollama doesn't list as a thinking one. |
| `config.git_provider` | `local-pr` | `github` | The `local` provider with the PR's title and description (above). |
| `config.publish_output` | `false` | `true` | Nothing is posted or written but the JSON results. |
| `config.ai_timeout` | `1800` | `120` | Seconds for one model call. The default is for hosted models; a 20B model on 4 CPUs takes minutes. stratum-open allows 900. |
| `pr_reviewer.enable_large_pr_chunking` | `true` | `false` | A diff that doesn't fit one call is reviewed in up to 3, where the default drops the files that don't fit. stratum-open splits a diff too. |
| `pr_code_suggestions.parallel_calls` | `false` | `true` | One model on a CPU answers one call at a time. In parallel, the later calls would spend their timeout waiting. |
| `config.repo_context_files` | `[]` | `["AGENTS.md"]` | Two of the twelve projects have an `AGENTS.md` (pydantic: one line; scrapy: 99 lines of contributor rules, about 1,200 tokens). stratum-open's benchmark review reads no such file, so neither tool gets it, and PR-Agent keeps that room for the diff. |
| `LITELLM_LOCAL_MODEL_COST_MAP` | `True` | unset | As PR-Agent's own Docker images set it: LiteLLM uses the model data it ships with, and downloads nothing at start. |

Everything else is PR-Agent's default, including its temperature (0.2;
stratum-open uses 0.1), at most 3 key issues a review and 3 suggestions a
call, and its retries.

### The context window

Both tools get 8,192 tokens, the window stratum-open's benchmark uses.

- **stratum-open** sends `num_ctx: 8192` with every request.
- **PR-Agent's requests set no window.** They go to Ollama's `/api/generate`
  with `options: {"temperature": 0.2}` and nothing else, so the server's
  default decides, and Ollama's default is smaller. Its documentation says to
  start Ollama with `OLLAMA_CONTEXT_LENGTH`, so the workflow's job does:
  `OLLAMA_CONTEXT_LENGTH=8192`.
- **The runner checks it.** Before the first case it loads the model with an
  empty prompt and reads the window from Ollama's `/api/ps`. It stops unless
  that is 8,192, and it checks again after the last case.
- **What a prompt really took** is in `calls.csv`: `prompt_tokens` is
  Ollama's own count for each call, and `done` is `length` where the window
  ran out.

Of those 8,192 tokens, PR-Agent's instructions take about 1,600 in a review
and 1,200 in a suggestions call, and it keeps 1,000 to 1,500 for the answer.
That leaves about 5,000 for the diff.

What that means for the 39 cases was measured before any model ran, by
sending all of them through PR-Agent against a fake model server:

- **The planted line is in a prompt in all 39 cases.**
- **The prompts fit.** They are 1,500 to 6,500 tokens (median 4,100 for a
  review), counted with the tokenizer `gpt-oss` is built on, which leaves at
  least 1,600 for the thinking and the answer. Another model's tokenizer
  counts differently; a real run's `calls.csv` has the server's own count.
- **Three calls a case:** a review, suggestions, and the second pass that
  scores the suggestions and gives them their line numbers. The largest diff
  (pallets/werkzeug#3337) is reviewed in two calls.
- **For the two largest diffs the second pass doesn't fit**
  (pallets/werkzeug#3337 and #3339). PR-Agent then keeps the suggestions
  without line numbers, and drops them from its comment for having none, so
  on these two cases only `review` can find the bug.

### What favours which side

In PR-Agent's favour:

- **More calls.** Two commands, so at least 3 model calls a PR (review,
  suggestions, a second pass that scores the suggestions), against 1 or 2 for
  stratum-open. Every key issue and every suggestion counts, whatever its
  score. That also means more findings on the clean PRs; the summary shows
  both.
- **No limit on the answer.** stratum-open stops an answer at 2,048 tokens.
  PR-Agent sets no limit, and has twice the time for a call.
- **Time.** `seconds` is the time in model calls only. PR-Agent's start-up, a
  few seconds a command, is left out.
- **Paths.** A finding's file is matched to the PR's files with stratum-open's
  own, lenient rule.
- The settings marked above: chunked reviews, no `AGENTS.md`.

In stratum-open's favour:

- **Its benchmark.** The cases, the kinds of bug and the 3-line rule were
  chosen by stratum-open's authors, and its prompt and settings were tuned on
  the `dev` cases. PR-Agent is run untuned. Compare on `test`.
- **Its window.** 8,192 tokens is what stratum-open's benchmark is run with.
  PR-Agent's default budget is 32,000, and in 8,192 it loses the suggestions
  for the two largest diffs (above).
- **Lines.** stratum-open moves a finding to the code it quotes when the
  model miscounts a line. PR-Agent's line numbers are taken as it gives them,
  as its own comment would link them.

Neither way, as far as we know:

- **One prompt, not a chat.** For `ollama/` models LiteLLM joins PR-Agent's
  system and user text into one prompt for `/api/generate`. stratum-open
  sends chat messages to `/api/chat`. This is PR-Agent's documented setup.
- **Its own diff.** PR-Agent diffs the two branches itself. On the PRs as
  merged, git's diff has the same added and removed lines as GitHub's in all
  39 cases. With the bug planted, git lines up two lines differently in one
  case (pallets/werkzeug#3337); the planted line is an added line in every
  case.
- **The same inputs.** Both tools get the title, the description, the diff
  and the changed files, and nothing else. On a hosted PR, PR-Agent also
  reads commit messages, linked issues and its earlier findings.

### What the comparison does not show

- **PR-Agent with the models it is built for.** Its documentation says local
  open-source models are for experimenting, and recommends large hosted
  models for real use. This measures it where stratum-open is meant to run: a
  model of 20B or less, a small window, a CPU. It says nothing about PR-Agent
  with a large hosted model.
- **Other kinds of review.** This is one benchmark: 39 one-line logic bugs
  planted in Python PRs. It doesn't measure other languages, security
  problems, design problems, bugs that span lines or files, or the rest of
  what either tool does (PR-Agent's descriptions, effort estimates,
  questions and answers; stratum-open's checks and triage).
- **Run-to-run spread.** Each case is reviewed once, above temperature 0. Two
  stratum-open runs of the same 19 cases found 7 and 8 bugs, 5 of them the
  same ones. A difference of a few cases is noise.
- **The hosted product.** A local branch, not a pull request: no inline
  comments, labels or earlier findings.
- **Later versions.** PR-Agent's main branch is merging `improve` into
  `review`.

## Running it

PR-Agent goes in an environment of its own, so its dependencies don't mix
with stratum-open's:

```sh
python -m venv ~/pr-agent && ~/pr-agent/bin/pip install pr-agent==0.50.0
OLLAMA_CONTEXT_LENGTH=8192 ollama serve &
python benchmark/others/pr_agent.py --bugs benchmark/planted/bugs.json --set dev \
    --model gpt-oss:20b@http://localhost:11434 --pr-agent ~/pr-agent/bin/python \
    --policy benchmark/planted/variants/reasoning-low-verify-strict.yml \
    --out benchmark/results/<name>/raw/pr-agent-planted
python benchmark/others/pr_agent.py ... --clean --out benchmark/results/<name>/raw/pr-agent-clean
python benchmark/planted.py score benchmark/results/<name>   # after marking the verdict columns
```

On GitHub's free runners, start the **Planted-bug benchmark** workflow with
`other-tool` set to `pr-agent`. It then runs PR-Agent next to stratum-open:
the same models, cases and shards, each tool on its own runner, and one
summary. PR-Agent's rows have a variant that starts with `pr-agent-`.

Besides `reviews.csv` and `findings.csv`, a run folder holds `calls.csv` (one
row a model call) and `pr_agent.json` (the versions, the settings, the window
the server reported). `--trace <folder>` also keeps every prompt, every reply
and PR-Agent's log.

Mark the findings as for stratum-open, and without looking at the `variant`
column where you can: the rule must be the same for both tools.

## Checking the plumbing

No model is needed to check that the cases reach PR-Agent and its answers
come back as rows:

```sh
PR_AGENT_PYTHON=~/pr-agent/bin/python python -m pytest tests/e2e/test_others_pr_agent.py
```

It reviews three real cases with PR-Agent against a fake model server that
flags the planted line whenever a prompt shows it, and checks the rows, the
prompts and that `planted.py score` takes the folder. It is skipped without
`PR_AGENT_PYTHON`, and unlike the rest of the suite it uses the network: git
fetches the three PRs. `tests/test_others_pr_agent.py` covers the parts that
need neither.
