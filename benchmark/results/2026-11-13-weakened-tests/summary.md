# Weakened tests: the check on merged PRs

8 planted cases, 6 controls the check must leave alone, and 782 real merged PRs as they are, 615 of which change a test file and 453 of which remove a line from one. Each finding was read against the PR's diff, title and description and marked: right when the PR does what the finding says, wrong when the rule misread the diff. A right one is plainly fine when the PR says it, or it follows from what the PR says it does; it's worth pointing out when the finding is the only notice a maintainer gets.

Planted: 8 of 8 caught, 6 of 6 controls left alone.

## By rule

| Rule | Findings | PRs | Right, worth pointing out | Right, plainly fine | Wrong | Unsure |
| --- | --- | --- | --- | --- | --- | --- |
| `tests/assertions-removed` | 15 | 9 | 4 | 11 | 0 | 0 |
| `tests/deleted` | 19 | 17 | 2 | 14 | 3 | 0 |
| `tests/only` | 0 | 0 | 0 | 0 | 0 | 0 |
| `tests/skipped` | 0 | 0 | 0 | 0 | 0 | 0 |
| `tests/tolerance-loosened` | 0 | 0 | 0 | 0 | 0 | 0 |
| **All** | **34** | **23** | **6** | **25** | **3** | **0** |

## By list

| List | PRs | Change a test file | Remove a line from one | Assertion lines removed | Changed lines with a tolerance | Skip markers added | Findings | PRs with a finding |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [Python](../../planted/clean.json) | 184 | 131 | 57 | 105 | 0 | 9 | 9 | 6 |
| [JavaScript and TypeScript](../../planted/clean-js.json) | 60 | 39 | 15 | 346 | 0 | 2 | 1 | 1 |
| [Go](../../planted/clean-go.json) | 60 | 45 | 15 | 21 | 0 | 4 | 3 | 2 |
| [Rust](../../planted/clean-rust.json) | 60 | 29 | 9 | 1 | 0 | 1 | 0 | 0 |
| [Workflows and manifests](../../planted/clean-infra.json) | 53 | 6 | 4 | 8 | 0 | 0 | 0 | 0 |
| [PRs that remove test lines](../../planted/clean-tests.json) | 343 | 343 | 338 | 527 | 0 | 6 | 21 | 14 |
| [PRs about a flaky or skipped test](../../planted/clean-flaky.json) | 22 | 22 | 15 | 56 | 0 | 15 | 0 | 0 |
| **All** | **782** | **615** | **453** | **1064** | **0** | **37** | **34** | **23** |

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
