## What was run

Run on 2026-11-13 on a laptop: the check reads only the diff, so it gives the same answer on any machine. No model was run, and nothing from the PRs was executed.

```sh
python benchmark/corpus.py pick                                   # writes clean-tests.json and clean-flaky.json too
python benchmark/weakened_tests.py run --out benchmark/results/2026-11-13-weakened-tests
# mark checks.csv and markers.csv by hand
python benchmark/weakened_tests.py score benchmark/results/2026-11-13-weakened-tests
```

The check ([tests_weakened.py](../../../stratum_open/checks/tests_weakened.py)) is off unless `checks.weakened_tests` is on. Every finding on a merged PR was read against the PR's diff, title and description. Five rules were tried. Four are in the check. The fifth, a test deleted outright, was measured and taken out: its code and its planted case are in the commit "Measure the weakened-tests check on planted cases and merged PRs" of the PR that added the check, and its marked findings are in [dropped.csv](dropped.csv).

## The PRs

782 merged PRs from 35 projects, 615 of which change a test file.

- **The five lists the other checks are measured on**, 417 PRs: 17 Python projects ([clean.json](../../planted/clean.json)) and 18 JavaScript, TypeScript, Go and Rust ones. They were picked for changing source code, so only 250 change a test file and 100 remove a line from one. In the Rust list that is 9 PRs.
- **[clean-tests.json](../../planted/clean-tests.json)**, 343 PRs, added for this run: for each of the 18 projects, its 20 newest merged PRs, not in the lists above and adding at most 400 lines, that remove a line from a test file of the project's language (axum had 4, eslint 19). The rules about assertions and tolerances read what a PR takes out of its tests, and the first five lists take out little.
- **[clean-flaky.json](../../planted/clean-flaky.json)**, 22 PRs, added for this run: from the same 300 newest PRs of each project, the ones not listed above that change a test file and whose title is about a flaky test, or about skipping, disabling or ignoring one. These are the PRs most likely to switch a test off, and to say so.

[corpus.py](../../corpus.py) holds both rules and picks the same PRs again. Its listing now asks GitHub for each PR's title and each file's deleted lines; the four lists it wrote before come out unchanged. The Python list has no listing behind it, so it was not extended: Python is measured on 131 PRs that change a test file, 57 of which remove a line from one.

## What the numbers show

**`tests/assertions-removed`: 15 findings on 9 PRs, none wrong, 4 worth pointing out.** Every one names a line the PR did remove from a test that still runs, with nothing in its place.

- 11 of the 15 are plainly fine: the PR removes or changes what the assertion checked and says so (10), or says outright that it removes the assertion (etcd-io/etcd#22258, a deflake whose description says "Removes an unnecessary assertion").
- 4 are worth pointing out, on 2 PRs. fastify/fastify#6728 is titled "ci: add node 26 to test matrices" and drops the assertion that a link checker made no request, twice. sveltejs/svelte#19014 adds tests and takes an assertion on an intermediate state out of two it already had. Neither says so.
- So on merged PRs about a quarter of what this rule says is news to a maintainer, and the rest repeats the PR. That is the rate to expect on PRs that maintainers reviewed. It doesn't say what the rate is on PRs nobody has reviewed yet, which is where the check is meant to run.
- The rule read 1,064 removed assertion lines and reported 15 of them. Of the others, 737 had code written in their place, 294 were in a function the PR removes or renames, 12 were added again elsewhere in the file, and 6 were in a function that gains an assertion elsewhere.

**How the rule was narrowed.** As first written it reported any function that loses assertion lines and gains none. That gave 21 findings, the 6 more all wrong and all in fastify/fastify#6887, which replaces a `t.assert.fail` callback in each test with a helper, `assertNoWarning(t)`. So a removed assertion now counts only when no code is written where it stood. That costs nothing on these PRs and removes all 6. A test the PR renames is also no longer taken for the same test: without that, rewritten assertions in two renamed tests (fastify/fastify#6929, typeorm/typeorm#12501) were reported when the first condition was off.

Reading which function a line is in took three tries, on the first 417 PRs. The first two misread a deleted test as assertions lost by the test above it (pallets/werkzeug#3292, scrapy/scrapy#8385), and split a test at a class defined inside it (scrapy/scrapy#8460). Functions are now read by indentation, on each side of the diff.

**`tests/skipped`: no finding, and 37 lines it had to tell apart.** The PRs add 37 lines that name a way to switch a test off ([markers.csv](markers.csv)). 35 are conditional and 2 are not markers at all (Go's `-test.skip` flag). None switches off, with nothing to decide when, a test that ran before.

- The 35: `skipif(...)` with a platform, a version or a missing program (8), a skip call inside an `if` or an `except` (11), `#[cfg_attr(miri, ignore)]` (8), and vitest's `runIf` and `skipIf` or node's `{ skip: condition }` (8).
- Even the 22 PRs whose title is about skipping or a flaky test do it with a condition: 15 of the 37 lines are theirs.
- So the rule stays out of the way of how these projects skip tests. Whether its findings are worth reading is not measured: it had none. That it finds an unconditional skip is shown only by the two planted cases and the unit tests.

**`tests/only` and `tests/tolerance-loosened`: no finding, and nothing to read.** No PR adds an `.only`, and no changed line of a test file carries a tolerance (`abs=`, `places=`, `toBeCloseTo(x, digits)` and the like): these are web frameworks, servers, command-line tools and database libraries, with almost no arithmetic in their tests. Both rules match one exact shape, ran over the 30,778 lines these PRs add to 1,294 test files without a finding, and are proven on planted cases only. A numeric project is where the tolerance rule would be tested.

**`tests/deleted`, taken out: 19 findings on 17 PRs, 3 wrong, 14 plainly fine, 2 worth pointing out.** The rule reported a test deleted from a file that gains none, in a PR that also changes code.

- 14 of the 16 right ones are plainly fine. 13 are the test of something the PR removes, reverts or turns into the default: five in etcd's removal of its v2 snapshot code and flags, a revert each in caddy, typeorm, vite and express, an error code in fastify, a feature flag in prometheus, a workaround in scrapy, a kind of call marshmallow never makes. One is replaced by new tests in another file (pallets/werkzeug#3292). The diff shows a whole test in red, and the title says why.
- 2 are worth pointing out: pallets/werkzeug#3304 deprecates `responder` and deletes its only test, and Textualize/textual#6362 deletes `test_dismiss_non_top_screen` without a word.
- 3 are wrong: tests moved to another file under new titles (vitejs/vite#23877), and tests rewritten as a table of `#[rstest]` cases (nushell/nushell#19402, #19352). The second kind could be fixed; the first can't, from names alone.
- At best that is 2 findings a maintainer wants out of 19, for a fact the diff already shows more plainly than any other. The file-level heuristic (`heuristic/tests-weakened`) still says where a test file shrinks.

## What they don't show

**"None wrong" is on the PRs the rules were narrowed on.** The misreadings above were found by reading these findings, and then fixed. The Go and Rust projects' 224 PRs of clean-tests and the 22 of clean-flaky were first run after the last change that altered a finding: 3 of the 15 findings are there (all etcd), and none is wrong. That is a small sample.

**Misses aren't measured.** These are PRs taken to be fine. What the rules can't see, by how they're written:

- an assertion replaced by a weaker one (`assert total == 42` by `assert total`): 737 removed assertion lines had code in their place, and nothing here says how many of those lost something;
- a test the PR adds already switched off, which is left alone on purpose, and a skip whose condition is always true;
- a skip in a form not on the list: Mocha's `this.skip()`, node's `{ skip: true }`, Ginkgo's `Skip`;
- a tolerance that appears where there was none (`approx(x)` to `approx(x, abs=0.1)`), or on a line of its own in a call split over several lines;
- anything in Rust tests that live beside the code, other than `#[ignore]`: assertions there are not all tests'.

**The sample is narrow.** 35 projects, all libraries and tools with careful maintainers; PRs of at most 400 added lines; one reader marked the findings, and "plainly fine" is that reader's call from the PR's title and description.

## What it cost

About 730 GitHub REST requests for the 365 new PRs (the PR and its diff), from the cache after that; the other 417 were in the cache. Listing the 18 projects with titles and deleted lines is about 130 GraphQL points.
