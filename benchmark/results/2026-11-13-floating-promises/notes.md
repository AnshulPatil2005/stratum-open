## What was run

Run on 2026-11-13 on a laptop, on `corpus-beyond-python` at 18f9e18 with the check added, with `checks.floating_promises` on and tree-sitter 0.26.1, tree-sitter-javascript 0.25.0 and tree-sitter-typescript 0.23.2 from PyPI. The check gives the same answer on any machine. No model was run, and nothing from the PRs or the repositories was executed: their text was parsed, the repositories' straight out of GitHub's archives.

```sh
pip install -e ".[js]"
python benchmark/floating_promises.py --out benchmark/results/2026-11-13-floating-promises
python benchmark/planted.py checks --risks benchmark/planted/risks.yml --prs benchmark/planted/clean-js.json \
    --policy benchmark/planted/variants/floating-promises.yml --out <a scratch folder>
```

The last command runs the planted cases with the key on: both of the rule's cases are caught and its control is left alone (45 of 46 planted risks in all; the miss is `dep-jellyfish`, as on 2026-11-07).

One reader read every finding in its file and marked it in [findings.csv](findings.csv). Right is a promise dropped by mistake, whether or not anything breaks today. Wrong is a false alarm: the callee returns no promise, or the project means the call to run on its own. Unsure is where the code doesn't say which.

## The rule, and how it got here

The first version was the Python check's rule in JavaScript: a call that is a whole statement in an async function, on an added line, whose callee the file awaits elsewhere, a removed line awaited, or the file declares `async`. It caught 43 of the 126 dropped awaits and said nothing on the PRs as merged. On the files they touch it had 15 findings, every one a `fastify.register(...)` in two test files, and every one a false alarm: Fastify's `register()` can be awaited and needn't be.

It was then narrowed, each time from findings that had been read, on the PRs' files and the `dev` repositories only:

- **The file's own habit counts against a finding.** Nothing is said when an unchanged line already calls the callee on its own, or when the file does so more than half as often as it awaits it. This removed `fastify.register()` (14 of the first 15), socket.io's `writer.write()` (10 calls beside 13 awaits) and directus's `useItems()` (5).
- **The callback form of a call is another call.** `app.listen(port, done)` beside `await app.listen(port)`: 6 findings in fastify's tests.
- **A name means what it's bound to.** A local or a parameter counts only in its function, and a name that hides another isn't the other: `commonModuleExport`, imported from two modules in two functions of vite's playground, one of them async.
- **`.then`, `.catch` and `.finally` handle a promise.** `attempt.catch(() => {})` in nest isn't a dropped call.

Together they cost one catch, 43 to 42. The `test` repositories were read once, after the last narrowing, and the rule hasn't changed since. Their false alarms are listed below as they are.

## What the numbers show

**A third of dropped awaits are caught: four in five of the kind the rule reads, and none of the rest.**

- 62 of the 126 awaits give a value the code goes on to use, 39 of them `const x = await f()`. The rule leaves those alone by design: `const x = f()` is how JavaScript starts work to await later, and `return f()` hands the promise on. The Python check does read `x = f()`, which is most of why it caught 42 of 56 and this catches 42 of 126.
- 10 are whole statements that aren't a call by name: 6 `await expect(...)` chains, 3 `await new Promise(...)` and 1 call on an array's element.
- Of the 54 it reads, it misses 12. In 11 the file has no other `await` of that callee (`await init()`, the file's one `await Promise.allSettled(...)`), so nothing in the file says it returns a promise. The twelfth is `await fastify.register(...)` in a file that also calls it on its own.
- 115 of the 126 are in test files. That's where these PRs add awaits.

**Nothing on the 60 PRs as merged.** That's the test the Python check passed on 184 PRs. 60 is fewer, so the interval for a PR getting a finding runs up to 6%, and these PRs add only 3,705 lines to JavaScript and TypeScript files.

**On merged code read as new it says little, and about one finding in five is a real mistake.** In the `test` repositories: 28 findings in 1.56 million lines, one for every 56,000 lines. 6 are right (21%, 95% interval 10% to 40%), 16 wrong and 6 unsure, so a false alarm about once in 100,000 lines. The `dev` repositories come out the same (27 findings in 2.3 million lines: 6 right, 16 wrong, 5 unsure), though the rule was narrowed on them.

Merged code is the hardest place for this rule to be right. A dropped await that breaks something has been found and fixed by the time the code is merged, and what's left unawaited is mostly meant. A PR under review is where awaits are still being dropped, and there the rule catches a third of them. How often a finding is right on PRs under review isn't measured here.

**The 12 real ones.** One is outside the tests:

- strapi/strapi `packages/cli/create-strapi-app/src/create-strapi.ts`:181. `fse.writeFile(...)` isn't awaited inside a `try` whose `catch` is there to report a failure. The failure never reaches it, and the success message is logged before the file exists.

The other 11 are tests that check less than they look to, or pass by luck:

- socketio/socket.io `docs/engine.io-protocol/v3-test-suite/test-suite.js`:123, 133, 143 and 153. `waitFor(socket, "close")` isn't awaited, so two tests end before the close they're there to see, and pass whatever the server does. The v4 suite beside it awaits the same calls.
- strapi/strapi `tests/e2e/tests/content-manager/history.spec.ts`:181 and 200, `preview.spec.ts`:221 and `tests/e2e/tests/admin/login.spec.ts`:78. Playwright actions (`click()`, `fill()`) and an async helper aren't awaited.
- nestjs/nest `packages/microservices/test/client/client-redis.spec.ts`:494, trpc/trpc `packages/server/src/adapters/node-http/incomingMessageToRequest.test.ts`:162 and sveltejs/svelte `packages/svelte/tests/runtime-legacy/samples/await-mutate-array/_config.js`:15. Each is the one call of its kind in the file without the `await` the others have. Harmless today.

**Where the 16 false alarms in the `test` repositories come from:**

- 6: the callee isn't async, and the file awaits it needlessly somewhere else. Astro's `session.set()` (one fixture, copied four times), Node's `server.close()`, strapi's `cancel()`. An `await` in the file is taken as proof, and here it proves nothing. The Python check has the same weakness.
- 5: the call returns what its callback returns. React's `act(() => {...})` beside `await act(async () => {...})`, 4 times, and `AsyncLocalStorage.run()`.
- 4: the call is meant to run on its own. Two say so (a comment, `regenerateData()` that reports its own errors), and two are the last thing a function does.
- 1: the same name on a different object (`server`, a wrapper in one function and Node's HTTP server in another).

Of the 11 findings in all that rest on `async` in a declaration, 1 is right. An async function the file declares and calls once without `await` is, in merged code, nearly always meant.

**Not ready to be on by default.** It's quiet enough: nothing on 60 merged PRs, and a finding every 56,000 lines when merged code is read as new. What isn't shown is that a finding is right often enough: one in five on merged code, and unmeasured on PRs under review. Two things would settle it:

- A corpus of real dropped awaits: commits whose fix adds an `await`. The commit before each one is a PR with a known mistake and the rest of its code, so it would give the rule's precision where it's meant to work.
- Narrowings to try on such a corpus, none of them tested here: take an `await` as evidence only when its call has an async callback if this one's has; ask for more than a declaration when the call is the last statement of its function; ask for two awaits in the file, not one.

## What they don't show

- **Two of the rule's parts are exercised only by its unit tests.** No finding here rests on a removed line, and when every line of a file counts as added there's no unchanged line to show the file's habit.
- **250 files, 0.8% of those read, were skipped whole**, as the check skips them: 249 didn't parse cleanly and one was over the size limit. About 40 are fixtures with a syntax error on purpose. Most of the rest use syntax the TypeScript grammar on PyPI (0.23.2, from November 2024) doesn't know: a tagged template with type arguments (74 files, 65 of them strapi's: `styled(Box)<Props>` and kysely's `sql<Row>` before a template), `export type * from` (66), `typeof import(...)` as a type argument (about 30, all of them vitest's `await importOriginal<typeof import('./x')>()`, so test files), and `&` in the text of a JSX attribute (11). The check says nothing about such a file, and says nothing wrong. Reading the functions a syntax error doesn't touch would get most of these files back, at the price of trusting a tree the parser had to guess at.
- **One construct is misread without an error.** The grammar takes `await f<T>(x)` as `(await f)<T>(x)`. The check knows this one and reads it as written. Another like it would cost evidence, not make a finding: an `await` not recognized means a call not flagged.
- **The sample.** 18 projects, mostly libraries and tools with three applications among them (directus, strapi, immich), and one reader for the verdicts.
- **The Action can't run it yet.** `action.yml` installs no extras, as with Bandit, so the key does nothing there until the `js` extra is installed.

## The parser

The check parses with tree-sitter, from the wheels on PyPI, as the optional extra `js`. Without it the check is silent.

| Package | Licence | Wheel for Linux x86-64 | Unpacked |
| --- | --- | --- | --- |
| `tree-sitter` 0.26.1 | MIT | 0.7 MB, one for each of Python 3.11, 3.12 and 3.13 | 2.1 MB |
| `tree-sitter-javascript` 0.25.0 | MIT | 0.1 MB, one for Python 3.10 and later | 0.6 MB |
| `tree-sitter-typescript` 0.23.2 | MIT | 0.3 MB, one for Python 3.9 and later | 3.1 MB |

All three wheels were downloaded for manylinux x86-64 under Python 3.11, 3.12 and 3.13. MIT code can be used from an AGPL-3.0 project. The unit tests pass with tree-sitter 0.23.2, 0.24.0, 0.25.2 and 0.26.1, so the extra asks for 0.23 or later.

The other choice was a tokenizer written here. To say where an async function ends, or whether a call is a whole statement, it would have to tell a regex from a division, a generic's `<` from a comparison, JSX text from code, and `${}` inside a template from a string. That is a parser, and one that would be wrong without saying so where it gave up. tree-sitter says where it failed, and the check skips the file.

Parsing happens in the review's own process and runs nothing. A crafted file can't make it slow: 200,000 unclosed brackets, generics or JSX tags parse in under a second, and a file over a million characters isn't read. The whole run here, 3.96 million lines, took under two minutes.

## What it cost

About 220 GitHub REST requests, one for each JavaScript or TypeScript file of each PR at its pinned head, from the cache after that. The 18 repositories come as archives from codeload.github.com, which doesn't count against the API quota: about 220 MB.
