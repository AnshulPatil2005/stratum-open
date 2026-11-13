# Weakened tests: the check on merged PRs

7 planted cases, 6 controls the check must leave alone, and 782 real merged PRs as they are, 615 of which change a test file and 453 of which remove a line from one. Each finding was read against the PR's diff, title and description and marked: right when the PR does what the finding says, wrong when the rule misread the diff. A right one is plainly fine when the PR says it, or it follows from what the PR says it does; it's worth pointing out when the finding is the only notice a maintainer gets.

Planted: 7 of 7 caught, 6 of 6 controls left alone.

## By rule

| Rule | Findings | PRs | Right, worth pointing out | Right, plainly fine | Wrong | Unsure |
| --- | --- | --- | --- | --- | --- | --- |
| `tests/assertions-removed` | 15 | 9 | 4 | 11 | 0 | 0 |
| `tests/only` | 0 | 0 | 0 | 0 | 0 | 0 |
| `tests/skipped` | 0 | 0 | 0 | 0 | 0 | 0 |
| `tests/tolerance-loosened` | 0 | 0 | 0 | 0 | 0 | 0 |
| **All** | **15** | **9** | **4** | **11** | **0** | **0** |

Measured, then taken out of the check ([dropped.csv](dropped.csv)):

| Rule | Findings | PRs | Right, worth pointing out | Right, plainly fine | Wrong | Unsure |
| --- | --- | --- | --- | --- | --- | --- |
| `tests/deleted` | 19 | 17 | 2 | 14 | 3 | 0 |

## By list

| List | PRs | Change a test file | Remove a line from one | Assertion lines removed | Changed lines with a tolerance | Skip markers added | Findings | PRs with a finding |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [Python](../../planted/clean.json) | 184 | 131 | 57 | 105 | 0 | 9 | 4 | 1 |
| [JavaScript and TypeScript](../../planted/clean-js.json) | 60 | 39 | 15 | 346 | 0 | 2 | 0 | 0 |
| [Go](../../planted/clean-go.json) | 60 | 45 | 15 | 21 | 0 | 4 | 3 | 2 |
| [Rust](../../planted/clean-rust.json) | 60 | 29 | 9 | 1 | 0 | 1 | 0 | 0 |
| [Workflows and manifests](../../planted/clean-infra.json) | 53 | 6 | 4 | 8 | 0 | 0 | 0 | 0 |
| [PRs that remove test lines](../../planted/clean-tests.json) | 343 | 343 | 338 | 527 | 0 | 6 | 8 | 6 |
| [PRs about a flaky or skipped test](../../planted/clean-flaky.json) | 22 | 22 | 15 | 56 | 0 | 15 | 0 | 0 |
| **All** | **782** | **615** | **453** | **1064** | **0** | **37** | **15** | **9** |

The added lines that name a skip marker ([markers.csv](markers.csv)), by what they are: 35 conditional, 2 not a marker.

## Every finding

| PR | Rule | Where | Finding | Verdict | Why |
| --- | --- | --- | --- | --- | --- |
| cli/cli#14805 | `tests/assertions-removed` | `pkg/cmd/issue/comment/comment_test.go`:476 | Assertions removed from `TestNewCmdComment` | right, plainly fine | The PR removes the `Config` field from the command's options (the credential check it fed moves to the server), so `assert.NotNil(t, gotOpts.Config)` can't stay. It follows from what the PR says it does. |
| cli/cli#14805 | `tests/assertions-removed` | `pkg/cmd/pr/comment/comment_test.go`:497 | Assertions removed from `TestNewCmdComment` | right, plainly fine | The PR removes the `Config` field from the command's options (the credential check it fed moves to the server), so `assert.NotNil(t, gotOpts.Config)` can't stay. It follows from what the PR says it does. |
| etcd-io/etcd#22258 | `tests/assertions-removed` | `tests/robustness/validate/operations_test.go`:360 | Assertions removed from `TestValidateLinearizableOperationsTimeoutIsRespected` | right, plainly fine | A deflake that removes the check of elapsed time against the timeout. The description says so: "Removes an unnecessary assertion", with the failure it caused and why the other assertion is enough. |
| etcd-io/etcd#22263 | `tests/assertions-removed` | `etcdutl/etcdutl/common_test.go`:84 | Assertions removed from `TestGetLatestWalSnap` | right, plainly fine | The loop that wrote v2 snapshot files goes, and with it the `require.NoError` on each write. The PR is about no longer writing them. |
| etcd-io/etcd#22306 | `tests/assertions-removed` | `server/etcdmain/config_test.go`:482 | Assertions removed from `validateMemberFlags` | right, plainly fine | The helper stops comparing `MaxSnapFiles` because the PR removes the `--max-snapshots` flag. |
| fastify/fastify#6728 | `tests/assertions-removed` | `test/scripts/validate-ecosystem-links.test.js`:181 | Assertions removed from `returns invalid status for malformed owner or repository na…` | right, worth pointing out | A PR titled "ci: add node 26 to test matrices" makes the script take `fetch` from undici, deletes the test's stub of `global.fetch`, and with it the assertion that no request was made. Nothing in the PR says so. |
| fastify/fastify#6728 | `tests/assertions-removed` | `test/scripts/validate-ecosystem-links.test.js`:317 | Assertion removed: `assert.strictEqual(requests, 0)` | right, worth pointing out | A PR titled "ci: add node 26 to test matrices" makes the script take `fetch` from undici, deletes the test's stub of `global.fetch`, and with it the assertion that no request was made. Nothing in the PR says so. |
| fastify/fastify#6929 | `tests/assertions-removed` | `test/types/errors.tst.ts`:81 | Assertion removed: `expect(errorCodes.FST_ERR_ROUTE_MISSING_CONTENT).type.toBeA…` | right, plainly fine | The type test of an error code the PR removes. |
| pallets/jinja#2026 | `tests/assertions-removed` | `tests/test_api.py`:325 | Assertions removed from `test_default_undefined` | right, plainly fine | The removed lines asserted that `__slots__` raises AttributeError. The PR is "Preserve `__slots__` metadata": that is the behaviour it changes. |
| pallets/jinja#2026 | `tests/assertions-removed` | `tests/test_api.py`:335 | Assertions removed from `test_chainable_undefined` | right, plainly fine | The removed lines asserted that `__slots__` raises AttributeError. The PR is "Preserve `__slots__` metadata": that is the behaviour it changes. |
| pallets/jinja#2026 | `tests/assertions-removed` | `tests/test_api.py`:366 | Assertions removed from `test_debug_undefined` | right, plainly fine | The removed lines asserted that `__slots__` raises AttributeError. The PR is "Preserve `__slots__` metadata": that is the behaviour it changes. |
| pallets/jinja#2026 | `tests/assertions-removed` | `tests/test_api.py`:382 | Assertions removed from `test_strict_undefined` | right, plainly fine | The removed lines asserted that `__slots__` raises AttributeError. The PR is "Preserve `__slots__` metadata": that is the behaviour it changes. |
| prometheus/prometheus#20335 | `tests/assertions-removed` | `promql/parser/unquoted_utf8_test.go`:84 | Assertions removed from `TestUnquotedUTF8Names` | right, plainly fine | The removed cases asserted that `sum by (a:b)` is an error. The PR is "accept unquoted colons in grouping labels again". |
| sveltejs/svelte#19014 | `tests/assertions-removed` | `packages/svelte/tests/runtime-runes/samples/async-overlap-multiple-3/_config.js`:80 | Assertion removed: `assert.htmlEqual(` | right, worth pointing out | An assertion on an intermediate state is removed from an existing test, in a PR described as adding tests that already pass. Nothing says why. |
| sveltejs/svelte#19014 | `tests/assertions-removed` | `packages/svelte/tests/runtime-runes/samples/async-overlap-multiple-4/_config.js`:79 | Assertion removed: `assert.htmlEqual(` | right, worth pointing out | An assertion on an intermediate state is removed from an existing test, in a PR described as adding tests that already pass. Nothing says why. |

## Every finding of a rule taken out

| PR | Rule | Where | Finding | Verdict | Why |
| --- | --- | --- | --- | --- | --- |
| Textualize/textual#6362 | `tests/deleted` | `tests/test_screens.py`:275 | `test_dismiss_non_top_screen` is deleted | right, worth pointing out | `test_dismiss_non_top_screen` is deleted in a PR about a `mode` parameter for `push_screen`, with nothing in its place and no word on it. |
| caddyserver/caddy#8242 | `tests/deleted` | `modules/caddyhttp/reverseproxy/active_health_test.go`:220 | `TestActiveHealthCheckRedirectDialAddress` is deleted | right, plainly fine | A revert: the test goes with the change it tested. |
| etcd-io/etcd#22263 | `tests/deleted` | `tests/e2e/v2store_deprecation_test.go`:37 | `TestV2DeprecationSnapshotMatches` is deleted | right, plainly fine | The test that v2 snapshots are still written, in the PR that stops writing them. |
| etcd-io/etcd#22306 | `tests/deleted` | `server/etcdmain/config_test.go`:529 | `TestConfigFileDeprecatedOptions` is deleted | right, plainly fine | The tests of the two flags the PR removes. |
| etcd-io/etcd#22306 | `tests/deleted` | `tests/e2e/etcd_config_test.go`:663 | 2 tests are deleted | right, plainly fine | The tests of the two flags the PR removes. |
| etcd-io/etcd#22306 | `tests/deleted` | `tests/e2e/v2store_deprecation_test.go`:27 | `TestV2DeprecationNotYet` is deleted | right, plainly fine | The tests of the two flags the PR removes. |
| etcd-io/etcd#22341 | `tests/deleted` | `server/etcdserver/api/snap/snapshotter_test.go`:27 | 8 tests are deleted | right, plainly fine | The tests of the v2 snapshot code the PR removes. |
| expressjs/express#6933 | `tests/deleted` | `test/req.query.js`:82 | 3 tests are deleted | right, plainly fine | The release reverts the query-parser change of 5.2.0, and its tests with it; the description says so. |
| fastify/fastify#6929 | `tests/deleted` | `test/internals/errors.test.js`:791 | `FST_ERR_ROUTE_MISSING_CONTENT` is deleted | right, plainly fine | The test of an error code the PR removes. |
| marshmallow-code/marshmallow#3024 | `tests/deleted` | `tests/test_utils.py`:69 | `test_get_value` is deleted | right, plainly fine | The description says the test was incorrect: `get_value` is only ever called with a string key. |
| nushell/nushell#19352 | `tests/deleted` | `tests/repl/test_spread.rs`:87 | `duplicate_cols` is deleted | wrong | The test is rewritten in the same file as a table of `#[rstest]` cases. The rule didn't take the new function for a test: its attributes span several lines. |
| nushell/nushell#19402 | `tests/deleted` | `crates/nu-command/tests/commands/into_datetime.rs`:11 | `into_datetime_from_record_cell_path` is deleted | wrong | The test is rewritten in the same file as a table of `#[rstest]` cases. The rule didn't take the new function for a test: its attributes span several lines. |
| pallets/werkzeug#3292 | `tests/deleted` | `tests/test_http.py`:305 | 2 tests are deleted | right, plainly fine | The two tests of the deprecated `ETagSet` methods go, and tests of the new comparison methods are added in tests/test_datastructures.py. |
| pallets/werkzeug#3304 | `tests/deleted` | `tests/test_wsgi.py`:73 | `test_responder` is deleted | right, worth pointing out | `responder` is deprecated, not removed, and its only test is deleted with none in its place. The description is "closes #3302". |
| prometheus/prometheus#20169 | `tests/deleted` | `cmd/prometheus/features_test.go`:133 | `TestSetFeatureListOptions_UnquotedUTF8Names` is deleted | right, plainly fine | The test of a feature flag the PR turns into the default. |
| scrapy/scrapy#8385 | `tests/deleted` | `tests/test_pipeline_media.py`:99 | `test_should_remove_req_res_references_before_caching_the_re…` is deleted | right, plainly fine | A regression test for a workaround around Twisted's old `_DefGen_Return`, which the PR removes after the Twisted bump. |
| typeorm/typeorm#12478 | `tests/deleted` | `test/functional/query-builder/join/query-builder-joins.test.ts`:1682 | `should return correct number of results when limit is used …` is deleted | right, plainly fine | A revert: the test goes with the change it tested. |
| vitejs/vite#23877 | `tests/deleted` | `playground/optimize-deps/__tests__/optimize-deps.spec.ts`:65 | 8 tests are deleted | wrong | The eight tests are moved to playground/dynamic-import, as the title says, under new titles. The rule only knows a move by the test's name. |
| vitejs/vite#24065 | `tests/deleted` | `packages/vite/src/node/__tests__/build.spec.ts`:95 | `transform hook meta keeps rolldown magicString and ast` is deleted | right, plainly fine | A revert: the test goes with the change it tested. |

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
