## The question

Hosted reviewers index the whole repository; stratum-open's model sees the diff and, with `review.context_lines`, a few lines around each hunk. `review.context_callers: N` (off by default, this branch) adds, for each Python function or method a PR changes, up to N places elsewhere in the repository that call it: a few lines around each call, with its file and line, under its own heading and inside a token budget taken from the diff's share. A call is listed only when the import resolution of `checks.unknown_names` pins it to that function (`stratum_open/repo/callers.py`), so a method called on an object is never listed. Does seeing the callers help the model find the bug?

## How often there is a caller to show

Counted on every planted case without a model (the project checked out where the PR merged, the PR's files on top). "Any" is a case where at least one function the PR changes has a call outside the diff; "the bug's function" is a case where the function the planted line is in has one.

| Case file | Set | Cases | Any changed function has callers | The bug's function has callers |
| --- | --- | --- | --- | --- |
| bugs.json | dev | 19 | 14 | 9 |
| bugs.json | test | 20 | 14 | 9 |
| bugs-2.json | dev | 16 | 10 | 5 |
| bugs-2.json | test | 16 | 11 | 3 |

Most planted bugs sit inside one function and don't change what it returns or takes, so a caller can show the model how the function is used, not that the bug breaks anyone. The bugs a caller would expose most directly, a changed return value or argument contract, aren't among the planted kinds: of `bugs.json`'s four inverted `return`s, two are in functions the PR adds, whose only call is in the diff itself, and two are in `HTTPParser.reset`, which is only called on an object (`self._parser.reset()`) and so is never listed; and `bugs-2.json`'s kinds (swapped arguments, wrong variable, wrong constant, dropped statement) are wrong on the calling side, where the callee's definition would help rather than its callers. So `bugs-2.json` wasn't run.

## The runs

All on GitHub's free runners (4 CPUs, 16 GB RAM, no GPU), 13 and 14 Nov 2026, with `gpt-oss:20b`, the Action's settings, `review.reasoning: low` and `review.verify: strict`, on the 19 dev cases, each reviewed with the bug and as merged (the clean control). The two controls ran on the same branch with `context_callers` off, where the prompts are the same as on main.

| Run | Variant | |
| --- | --- | --- |
| [43522584100](https://github.com/stratum-org/stratum-open/actions/runs/43522584100) | `context_callers: 3` (`callers-verify-strict.yml`) | |
| [43524221001](https://github.com/stratum-org/stratum-open/actions/runs/43524221001) | none (the baseline, `reasoning-low-verify-strict.yml`) | control |
| [43522589529](https://github.com/stratum-org/stratum-open/actions/runs/43522589529) | `logic_pass`, `context_lines: 10` and `context_callers: 3` (`logic-context10-callers-verify-strict.yml`) | |
| [43524226189](https://github.com/stratum-org/stratum-open/actions/runs/43524226189) | `logic_pass` and `context_lines: 10` (`logic-context10-verify-strict.yml`) | control |

One shard of the second control (5 cases with the bug) lost its runner after two hours and left no results; it was rerun and is included.

The time per PR in the callers runs includes cloning the case's project and checking it out (planted.py does that inside the timed part), which the Action, with its checkout already there, doesn't pay. Reading the callers took at most 6 s a case when it was counted locally.

## How the findings were marked

As in the logic-pass study: only the findings near a planted bug were marked, right when the finding describes the planted bug. A finding that names the right line for the wrong reason was marked unsure (click#3884 in the callers run: it says a sequence envvar is never added, which the bug does cause, but blames the `isinstance` branch). The findings away from the planted line weren't read, except where noted below.

## What it shows

| Variant | Found, dev | Found where the bug's function has callers (9 cases) | Found where it has none (10) | Findings on the 19 clean PRs | Median time per PR |
| --- | --- | --- | --- | --- | --- |
| none (control) | 8 of 19 | 4 | 4 | 4 | 202 s |
| `context_callers: 3` | 10 of 19 | 5 | 5 | 8 | 223 s |
| `logic_pass` and `context_lines: 10` (control) | 13 of 19 | 7 | 6 | 9 | 636 s |
| the same and `context_callers: 3` | 13 of 19 | 8 | 5 | 11 | 623 s |

For comparison, the earlier runs of the same settings on the same cases: the baseline found 6 to 9 of 19 with 4 or 5 findings on the clean PRs in about 220 s; the logic pass with ten lines found 14 of 19 with 8 on the clean PRs; and showing each hunk's whole function (`context_scope: function`, dropped) found 12 of 19 with the logic pass, at more time.

- **No gain beyond the noise.** Alone, callers found 10 against 8 in the control run the same night, inside the 6 to 9 the baseline has given before. With the logic pass and ten lines, 13 against 13. Reruns of one variant differ by one to three cases, so neither is a result.
- **Where it could help, it barely moved.** On the 9 cases whose buggy function has a caller to show, callers found 5 against 4, and 8 against 7 with the logic pass: one case each time. On the 10 cases with nothing to show, where the prompt only gains the callers of other functions, the counts moved as much (5 against 4, and 5 against 6), which is the noise.
- **The one case it may have won.** click#3818 (`Abort` re-raised in standalone mode instead of swallowed) was found only with callers and the logic pass. None of the six dev runs of the logic-pass study found it, nor the other three runs here, so it may be the callers, or one lucky draw: one case.
- **More comments on clean code.** 8 against 4 alone, 11 against 9 with the logic pass. Most of the rise is one PR: encode/httpx#3673 has an async package (`ahttpx`) and a sync copy of it (`httpx`), the callers of both were shown side by side, and the model mixed the two up. In the callers run it said three times that the sync code calls an async `reset` without awaiting it; with the logic pass it made four comments there, one saying the async package awaits a `reset` that isn't async. Each package's `reset` is what its callers expect (sync in `httpx`, async in `ahttpx`), so those comments are wrong. Neither control commented on that PR.
- **It doesn't beat the bar.** Whole-function context found 12 of 19 with the logic pass and was dropped for costing more time. Callers with the logic pass found 13, against 13 for the logic pass and ten lines without callers in the same night: no better than what's already offered, with more comments on clean code.

Per case (x = found):

| Case | Bug's function has callers | Control | Callers | Logic + 10 lines (control) | Logic + 10 lines + callers |
| --- | --- | --- | --- | --- | --- |
| Textualize/rich#3905 | yes | | x | x | x |
| Textualize/rich#4006 | yes | | | x | x |
| pallets/click#3818 | yes | | | | x |
| pallets/click#3884 | yes | x | | x | x |
| pallets/flask#5899 | yes | | x | x | x |
| pallets/werkzeug#3337 | yes | x | x | x | x |
| psf/black#5645 | yes | x | x | x | x |
| pydantic/pydantic#14244 | yes | x | x | x | x |
| pypa/pip#14349 | yes | | | | |
| Textualize/textual#6464 | no | | | x | |
| encode/httpx#3673 | no | | | | |
| pallets/jinja#2026 | no | | x | x | x |
| pallets/werkzeug#3293 | no | x | x | x | x |
| psf/black#5626 | no | | | | |
| psf/black#5634 | no | | | | x |
| psf/requests#7496 | no | x | x | x | x |
| pydantic/pydantic#14199 | no | | | | |
| scrapy/scrapy#8431 | no | x | x | x | x |
| scrapy/scrapy#8461 | no | x | x | x | |

## Recommendation

Don't open a PR for it. On the planted benchmark callers find no more bugs than the noise allows, alone or with the logic pass, and they add comments on clean code. The code stays on the `context-callers` branch: the lookup itself works (it never lists a call it can't pin to the function, and it found callers for 14 of 19 dev cases), and it would be worth measuring again on a set of PRs that change a function's contract and leave a caller broken, which this benchmark doesn't have.

## What it doesn't show

- One model, one run of each variant, 19 cases. A difference of one to three cases is within what reruns of the same variant give.
- Planted one-line bugs, almost none of which change a function's contract. A real PR that changes what a function returns, with its callers left alone, is the case callers are for, and the benchmark has no such case. A set of those would be the next thing to build before trying this again.
- The test cases weren't run: the variant didn't clearly help on dev.
