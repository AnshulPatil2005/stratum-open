## What this is

No model ran for this. `benchmark/quiet_filter.py` reads the findings the earlier runs left in this folder's neighbours (every `findings.csv` of a stratum-open model review with a `verdict` column: 1,102 findings, 215 marked wrong and 220 marked right; PR-Agent's findings in the same folders are left out) and tries patterns on them. [summary.md](summary.md) is its output. It decides two things in `review.quiet`: which comments are left out as not being defects, and from how many files up a repeated title counts as one claim.

## The rule

A pattern ships only if it removes no finding marked right. One that passes is still left out when its matches are a kind of defect rather than a kind of comment; the table says why for each.

## What it shows

- **The obvious patterns lose real findings.** Dropping whatever mentions a docstring loses 5 right findings, and a comment 4: a right finding quotes the docstring as its evidence ("the docstring says it returns True when..."). "Name" in a title loses 17 ("Incorrect package name"), "redundant" 1, "test" 1. So the shipped patterns read the title only, and ask for more than one word.
- **One pattern does most of the work.** A title that starts with "Potential" or "Possible" removes 35 of the 215 wrong findings, from 19 PRs and 8 of the 11 models, and none of the 220 right ones. When a model found a real bug it named it ("Inverted condition", "Incorrect return value"); the hedged titles were mostly about an error that can't happen there.
- **A guess about security is kept.** "Potential SQL injection" is how a model names an injection it can't trace to its source, and a missed one costs more than a wrong one, so the guess pattern doesn't apply to findings in the `security` category. Of the 20 security findings here, one is a guess marked wrong ("Potential shell injection"), which quiet mode keeps; without the exception the guesses would remove 36.
- **The others are small.** Praise ("Improving...", "Optimized...") removes 4 and style 2. Asking for docs removes nothing here: no finding in the benchmark has such a title. These ship because the prompt already asks for none of them, and because Stratum's review of its own PR #55 had "Missing docstring for new parameter" and "Large constant" among its comments.
- **Three files is the right place to fold.** One title on three or more files of a review happened 15 times; the 16 copies that were marked are all wrong. On two files or more, one of the 45 marked right or wrong is right.

## What it doesn't show

- **Whether a guess is ever right outside this benchmark.** What's right here is mostly a planted logic bug: 208 of the 220 right findings are within three lines of one. Nobody planted a missing None check or an injection, so "Potential IndexError" had less chance to be right than it has on a real PR. Quiet mode would drop it there too.
- **The 119 findings nobody marked** that the patterns also match (118 of them a guess). In the later runs only the findings near a planted bug were marked.
- **Other models' wording.** 179 of the 435 marked findings are from the Qwen 2.5 Coder family and 177 from `gpt-oss:20b`. A model that starts every title with "Possible" would lose all its findings.
- **A review.** The counts are per finding. No benchmark run has been made with `review.quiet` on.
