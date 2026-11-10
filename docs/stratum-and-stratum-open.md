# How Stratum and Stratum Open fit together

There are two products and one review engine.

- **Stratum Open** (this repo, AGPL-3.0) is the review engine plus three ways to run it yourself: a GitHub Action,
  a GitHub App on your own server, and a command-line tool. It reviews on a model you run or choose, and nothing
  is sent to Stratum.
- **Stratum** is the hosted service. It runs the same engine (it installs this package), adds managed models and
  accounts, and adds the features that need a database: incidents, Drift, Insights, team context and projects.

## The rule

**Stratum Open owns everything that works from a repository alone:**
- the review engine;
- the model-free checks;
- the policy file;
- repository analysis;
- posting to GitHub;
- the `/stratum` commands.

**Stratum adds what needs a hosted service:**
- accounts, teams and billing;
- managed models;
- the database;
- the features built on them: incidents, Drift, Insights, team context and projects.

Stratum passes in what it knows (team docs, past feedback, incident history) and stores what comes back. It never
changes how the engine reviews: if Stratum needs something to work differently, it becomes an option here, so
both products get it.

## What each one has

| | Stratum Open | Stratum (hosted) |
| --- | --- | --- |
| Review engine and model-free checks | Yes | The same engine, installed from this repo |
| Model | One you run or choose: Ollama on the Actions runner by default, or any OpenAI-compatible endpoint | Stratum's managed models, or the team's own AI provider (then used for every AI task) |
| Where it runs | Your GitHub Action, your App server or your machine | Stratum's servers, through its GitHub App |
| Config | `.stratum/policy.yml` | The same file; its `services:` section feeds Stratum's service map and is ignored here |
| `/stratum` commands | `review`, `ignore`, `ignore <rule>`, `mute`/`unmute` (App only), `explain`, `triage`, `record` | The same names, except `triage` and `record` |
| Triage labels and author track records | Yes | No |
| Incidents, Drift, Insights, team context, projects | No | Yes |
| Command-line tool | `stratum-open` | `stratum` (a client for the hosted service) |
| Cost | Free (your model, your compute) | Free plan with one included AI task, Pro with a monthly allowance, or unlimited with the team's own AI provider |

### The commands side by side

| Command | Stratum Open | Stratum |
| --- | --- | --- |
| `/stratum review` (also `re-review`) | Review the whole PR again (write access) | Same |
| `/stratum ignore` (as a reply) | Hide that finding on this PR (write access) | Same |
| `/stratum ignore <rule>` | Hide a rule on this PR (write access) | Same |
| `/stratum mute <rule>` / `unmute` | Hide a rule on every PR; GitHub App only, since it stores it | Same; works on every install |
| `/stratum explain` | The PR's author or write access, a few a day | Write access; it uses the team's AI provider or allowance |
| `/stratum triage`, `/stratum record` | Anyone | Not available |

Neither reads the older `@stratum` form any more, since GitHub turned it into a mention of an unrelated organization.

## Which repository owns what

| Repository | Visibility | Owns |
| --- | --- | --- |
| `stratum-open` (this one) | Public | The engine, the checks, the policy format, repository analysis, the Action, the App and the `stratum-open` CLI |
| `Stratum` | Private | The hosted service: its web app, API, database, billing, managed models, and its `stratum` CLI |

Stratum depends on a specific version of this package and moves to new versions after they pass both products'
quality gates.

## What is still done twice

Some parts work the same way in both products but are still implemented separately. They are being merged into
this repo one at a time.

- **Posting reviews to GitHub, and the commands.** Hosted Stratum still uses its own code, built on its database
  and its GitHub App.
- **Boundary rules, the code graph and code-health hotspots.** Hosted Stratum keeps its own versions until the
  ones here cover the same languages and keep its stored history consistent. Code ownership has already moved
  here.

## Which one to use

- **Stratum Open:** you want reviews on a model you control, for free, with nothing leaving your
  infrastructure, and you're happy to run a workflow, an App or a CLI yourself.
- **Stratum:** you want a hosted service with strong managed models (or your own provider's key), and
  incidents, Drift, Insights, team context and projects across your repositories.
