# The floating-promise check on merged JavaScript and TypeScript

## Dropped awaits

Every `await` that 60 merged PRs from six projects ([clean-js.json](../../planted/clean-js.json)) add inside a function, dropped one at a time: **42 of 126 caught** (95% interval 26% to 42%).

| What the await was | Caught | Of | 95% interval |
| --- | --- | --- | --- |
| A whole statement awaiting a call by name: `await save(x);` | 42 | 54 | 65% to 87% |
| Another whole statement: `await pending;`, `await a().b();` | 0 | 10 | 0% to 28% |
| A value the code goes on to use: `const x = await f()`, `return await f()`, `g(await f())` | 0 | 62 | 0% to 6% |

115 of the 126 are in test files (38 caught), 11 in other code (4 caught).

## The PRs as merged

**0 finding(s) on the 60 PRs** (95% interval for a PR getting one: 0% to 6%).

## Merged code read as if it were new

Right: a promise nothing waits for, by mistake. Wrong: a false alarm, mostly a call the project means to leave running. Unsure: can't tell from the code.

| Read | Files | Lines | Whole-statement calls in async functions | Findings | Right | Wrong | Unsure |
| --- | --- | --- | --- | --- | --- | --- | --- |
| The files the PRs touch | 193 | 98,917 | 1,389 | 1 | 0 | 1 | 0 |
| 12 whole repositories, `dev` | 19,108 | 2,300,697 | 40,856 | 27 | 6 | 16 | 5 |
| 6 whole repositories, `test` | 11,959 | 1,562,067 | 27,913 | 28 | 6 | 16 | 6 |

250 more file(s) didn't parse cleanly or were over the size limit, and were skipped as the check skips them.

By what the finding rests on:

| Evidence | Findings | Right | Wrong | Unsure |
| --- | --- | --- | --- | --- |
| Awaited elsewhere in the file | 45 | 11 | 27 | 7 |
| Declared `async` in the file | 11 | 1 | 6 | 4 |
| Awaited on a removed line | 0 | 0 | 0 | 0 |

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

## Every finding

### Every line of the files they touch

| Where | Code | Evidence | Verdict | Why |
| --- | --- | --- | --- | --- |
| fastify/fastify#7155 `test/route.8.test.js`:40 | `fastify.register((scope, opts, next) => {` | `fastify.register()` is awaited on line 143 | wrong | Fastify's `register()` can be awaited and needn't be: the `await fastify.ready()` after it waits for every plugin. |

### expressjs/express at `43fca52` (dev)

142 files, 22,046 lines, 4 whole-statement calls in async functions: 0 finding(s).

### fastify/fastify at `0b076ca` (dev)

282 files, 76,862 lines, 5,617 whole-statement calls in async functions: 6 finding(s).

| Where | Code | Evidence | Verdict | Why |
| --- | --- | --- | --- | --- |
| fastify/fastify `test/async-await.test.js`:482 | `reply.send(payload)` | `reply.send()` is awaited on line 113 | wrong | `reply.send()` returns the reply, which can be awaited and needn't be: the file awaits 6 of its 23 calls. |
| fastify/fastify `test/close.test.js`:747 | `fastify.close()` | `fastify.close()` is awaited on line 107 | wrong | The test closes the server while a request is still being served, to see what happens to it: not waiting is the point. |
| fastify/fastify `test/close.test.js`:766 | `fastify.close()` | `fastify.close()` is awaited on line 107 | wrong | The test closes the server while a request is still being served, to see what happens to it: not waiting is the point. |
| fastify/fastify `test/http2/closing.test.js`:214 | `fastify.close()` | `fastify.close()` is awaited on line 130 | wrong | The test closes the server while a request is still being served, to see what happens to it: not waiting is the point. |
| fastify/fastify `test/route.1.test.js`:160 | `t.test('Add invalid Multiple methods per route', t => {` | `t.test()` is awaited on line 14 | unsure | The one subtest of ten that isn't awaited. Its body has nothing async and the test's plan waits for it, so nothing breaks. |
| fastify/fastify `test/route.8.test.js`:40 | `fastify.register((scope, opts, next) => {` | `fastify.register()` is awaited on line 143 | wrong | Fastify's `register()` can be awaited and needn't be: the `await fastify.ready()` after it waits for every plugin. |

### vitejs/vite at `61622b8` (dev)

1,624 files, 110,904 lines, 1,406 whole-statement calls in async functions: 2 finding(s).

| Where | Code | Evidence | Verdict | Why |
| --- | --- | --- | --- | --- |
| vitejs/vite `packages/vite/src/node/optimizer/index.ts`:627 | `fsp.rm(temporaryPath, { recursive: true, force: true })` | `fsp.rm()` is awaited on line 447 | wrong | Meant: the comment above it says "Delete temporary path in the background". Nothing catches a failure, though. |
| vitejs/vite `packages/vite/src/node/server/pluginContainer.ts`:372 | `this.buildStart()` | `buildStart` is declared `async` on line 321 | wrong | Meant: the next line awaits `this._buildStartPromise`, which `buildStart()` sets. |

### sveltejs/svelte at `0156c5f` (dev)

3,545 files, 152,452 lines, 4,951 whole-statement calls in async functions: 1 finding(s).

| Where | Code | Evidence | Verdict | Why |
| --- | --- | --- | --- | --- |
| sveltejs/svelte `packages/svelte/tests/runtime-legacy/samples/await-mutate-array/_config.js`:15 | `Promise.resolve();` | `Promise.resolve()` is awaited on line 6 | right | A real one, and harmless: `Promise.resolve();` on its own does nothing. Line 6 has the `await` this one lost; the `flushSync()` after it does the work. |

### eslint/eslint at `16eb1d9` (dev)

1,476 files, 539,423 lines, 2,391 whole-statement calls in async functions: 0 finding(s).

### typeorm/typeorm at `7a2707c` (dev)

3,624 files, 306,385 lines, 4,842 whole-statement calls in async functions: 0 finding(s).

### nestjs/nest at `afa6e3d` (dev)

2,352 files, 255,875 lines, 3,134 whole-statement calls in async functions: 4 finding(s).

| Where | Code | Evidence | Verdict | Why |
| --- | --- | --- | --- | --- |
| nestjs/nest `integration/scopes/e2e/transient-global-enhancers.spec.ts`:570 | `client.close();` | `client.close()` is awaited on line 441 | wrong | A different `client`: this one is a WebSocket, whose `close()` returns nothing. The awaited `client.close()` is a microservice client's, in another `describe`. |
| nestjs/nest `packages/microservices/test/client/client-redis.spec.ts`:494 | `subscription(` | `subscription()` is awaited on line 477 | right | A real one, and harmless today: the callback is async, and the `beforeEach` 17 lines up awaits the same call. The test passes because the callback gets to its assertion before its first await. |
| nestjs/nest `packages/platform-fastify/test/adapters/fastify-adapter.spec.ts`:1431 | `adapter.register(plugin, { prefix: '/queues' });` | `adapter.register()` is awaited on line 1397 | wrong | Meant: the test is named "a plugin registered without await". |
| nestjs/nest `packages/platform-fastify/test/adapters/fastify-adapter.spec.ts`:1504 | `adapter.register(plugin, { prefix: '/queues' });` | `adapter.register()` is awaited on line 1397 | wrong | Meant: the test is about a plugin registered without await. |

### honojs/hono at `f6fbaf0` (dev)

444 files, 90,042 lines, 2,193 whole-statement calls in async functions: 0 finding(s).

### nodejs/undici at `af82906` (dev)

694 files, 160,343 lines, 9,694 whole-statement calls in async functions: 2 finding(s).

| Where | Code | Evidence | Verdict | Why |
| --- | --- | --- | --- | --- |
| nodejs/undici `test/fetch/abort.js`:36 | `t.test('Error defaults to an AbortError DOMException', async` | `t.test()` is awaited on line 15 | unsure | The last subtest isn't awaited where the one before it is. Whether the test can end before it depends on the runner. |
| nodejs/undici `test/interceptors/dump-interceptor.js`:570 | `t.rejects(client.request(requestOptions), {` | `t.rejects()` is awaited on line 357 | wrong | Meant: the test plans its assertions and ends with `await t.completed`, which waits for this one. |

### socketio/socket.io at `545e984` (dev)

367 files, 56,241 lines, 556 whole-statement calls in async functions: 4 finding(s).

| Where | Code | Evidence | Verdict | Why |
| --- | --- | --- | --- | --- |
| socketio/socket.io `docs/engine.io-protocol/v3-test-suite/test-suite.js`:123 | `waitFor(socket, "close");` | `waitFor()` is awaited on line 99 | right | A real one. The test ends before the socket closes, so it passes whether or not the server refuses the connection. The v4 suite beside it awaits the same four calls. |
| socketio/socket.io `docs/engine.io-protocol/v3-test-suite/test-suite.js`:133 | `waitFor(socket2, "close");` | `waitFor()` is awaited on line 99 | right | A real one. The test ends before the socket closes, so it passes whether or not the server refuses the connection. The v4 suite beside it awaits the same four calls. |
| socketio/socket.io `docs/engine.io-protocol/v3-test-suite/test-suite.js`:143 | `waitFor(socket, "close");` | `waitFor()` is awaited on line 99 | right | A real one. The test ends before the socket closes, so it passes whether or not the server refuses the connection. The v4 suite beside it awaits the same four calls. |
| socketio/socket.io `docs/engine.io-protocol/v3-test-suite/test-suite.js`:153 | `waitFor(socket2, "close");` | `waitFor()` is awaited on line 99 | right | A real one. The test ends before the socket closes, so it passes whether or not the server refuses the connection. The v4 suite beside it awaits the same four calls. |

### discordjs/discord.js at `5bb7492` (dev)

1,205 files, 128,911 lines, 926 whole-statement calls in async functions: 1 finding(s).

| Where | Code | Evidence | Verdict | Why |
| --- | --- | --- | --- | --- |
| discordjs/discord.js `packages/discord.js/typings/index.test-d.ts`:3007 | `interaction.channel.send();` | `interaction.channel.send()` is awaited on line 3011 | wrong | A test of the types, never run: the line is there to fail type-checking (`@ts-expect-error`). |

### directus/directus at `3ba2201` (dev)

3,353 files, 401,213 lines, 5,142 whole-statement calls in async functions: 7 finding(s).

| Where | Code | Evidence | Verdict | Why |
| --- | --- | --- | --- | --- |
| directus/directus `api/src/websocket/collab/room.ts`:470 | `this.sendAll({` | `this.sendAll()` is awaited on line 296 | unsure | `sendAll()` is async and awaited in the method above. Here it's the last thing `leave()` does, and nothing catches a failure: an oversight or a choice. |
| directus/directus `app/src/composables/use-collab.ts`:275 | `receiveInit(message);` | `receiveInit` is declared `async` on line 321 | unsure | Started from a message handler that returns at once. Nothing catches a failure; nothing would be different if it were awaited. |
| directus/directus `app/src/composables/use-preset.ts`:73 | `savePreset(preset);` | `savePreset()` is awaited on line 232 | wrong | The whole body of a debounced callback, whose result nobody reads: awaiting it changes nothing. |
| directus/directus `app/src/stores/insights.ts`:473 | `loadPanelData(panelsToLoad);` | `loadPanelData()` is awaited on line 171 | wrong | Meant: the panels load in the background while the edits are cleared; the store starts the same load without waiting in three other places. |
| directus/directus `packages/composables/src/use-items.ts`:173 | `if (!unref(version)) getTotalCount();` | `getTotalCount` is declared `async` on line 310 | wrong | Meant: a watcher starting a count in the background; nothing reads the watcher's result. |
| directus/directus `sdk/src/realtime/composable.ts`:302 | `handleMessages(self);` | `handleMessages` is declared `async` on line 201 | wrong | Meant: it starts the loop that reads messages for as long as the socket is open. |
| directus/directus `tests/e2e/tests/websockets/websockets.test.ts`:77 | `api.request(` | `api.request()` is awaited on line 17 | unsure | Sent after unsubscribing, to see that no event arrives. Nothing waits for the request or catches its failure. |

### puppeteer/puppeteer at `1eb68c1` (test)

499 files, 115,548 lines, 1,829 whole-statement calls in async functions: 0 finding(s).

### koajs/koa at `b7ead6f` (test)

82 files, 8,358 lines, 226 whole-statement calls in async functions: 1 finding(s).

| Where | Code | Evidence | Verdict | Why |
| --- | --- | --- | --- | --- |
| koajs/koa `__tests__/application/compose.test.js`:45 | `dispatch()` | `dispatch` is declared `async` on line 40 | unsure | A toy `compose` in a test: the middleware chain is started and not waited for. The test's assertions don't depend on it. |

### strapi/strapi at `468197a` (test)

5,782 files, 822,339 lines, 9,109 whole-statement calls in async functions: 14 finding(s).

| Where | Code | Evidence | Verdict | Why |
| --- | --- | --- | --- | --- |
| strapi/strapi `packages/cli/create-strapi-app/src/create-strapi.ts`:181 | `fse.writeFile(join(rootPath, 'license.txt'), data.license);` | `fse.writeFile()` is awaited on line 158 | right | A real one, outside the tests. The write sits in a `try` whose `catch` is there to report a failure, and an unawaited write's failure never reaches it; the success message is logged before the file exists. |
| strapi/strapi `packages/core/admin/admin/src/hooks/tests/useSessionKeepalive.test.ts`:100 | `act(() => {` | `act()` is awaited on line 117 | wrong | React's `act()` with a callback that isn't async runs it at once and has nothing to wait for; the awaited ones are given an async callback. |
| strapi/strapi `packages/core/admin/admin/src/pages/Auth/components/Register.tsx`:470 | `handleRegisterUser(` | `handleRegisterUser` is declared `async` on line 387 | unsure | The last call in a form's submit handler, inside a `try`. A failure in it wouldn't reach the `catch`; whether that matters depends on what it handles itself. |
| strapi/strapi `packages/core/admin/admin/src/pages/Settings/components/Tokens/FormHead.tsx`:67 | `regenerateData();` | `regenerateData` is declared `async` on line 37 | wrong | Meant: the dialog closes while the token is regenerated, and `regenerateData()` reports its own errors. |
| strapi/strapi `packages/core/admin/server/src/services/project-settings.ts`:188 | `deleteOldFiles({ previousSettings, newSettings });` | `deleteOldFiles` is declared `async` on line 115 | wrong | Meant: the comment above it says "No await to proceed asynchronously". |
| strapi/strapi `packages/core/content-manager/admin/src/pages/ListView/components/tests/CustomOrder.test.tsx`:650 | `act(() => {` | `act()` is awaited on line 335 | wrong | React's `act()` with a callback that isn't async runs it at once and has nothing to wait for; the awaited ones are given an async callback. |
| strapi/strapi `packages/core/content-releases/server/src/services/release.ts`:470 | `schedulingService.cancel(id);` | `schedulingService.cancel()` is awaited on line 538 | wrong | `cancel()` isn't async: it's a plain function, and the `await` of it 68 lines down is needless. |
| strapi/strapi `packages/core/core/src/Strapi.ts`:393 | `this.openAdmin({ isInitialized });` | `openAdmin` is declared `async` on line 371 | wrong | Meant: opening the browser is the last thing start-up does, and `openAdmin()` catches its own errors. |
| strapi/strapi `packages/core/upload/admin/src/components/AssetPickerDialog/tests/AssetPickerDialog.test.tsx`:234 | `act(() => screen.getByRole('button', { name: 'Filter' }).foc` | `act()` is awaited on line 655 | wrong | React's `act()` with a callback that isn't async runs it at once and has nothing to wait for; the awaited ones are given an async callback. |
| strapi/strapi `packages/core/upload/admin/src/services/tests/uploadPerFile.test.ts`:181 | `act(() => {` | `act()` is awaited on line 128 | wrong | React's `act()` with a callback that isn't async runs it at once and has nothing to wait for; the awaited ones are given an async callback. |
| strapi/strapi `tests/e2e/tests/admin/login.spec.ts`:78 | `clickLoginTimes(6);` | `clickLoginTimes` is declared `async` on line 65 | right | A real one. The helper that clicks Login six times is async and isn't awaited, so the clicks race the assertion after it, and a failed click is never reported. |
| strapi/strapi `tests/e2e/tests/content-manager/history.spec.ts`:181 | `previousVersion.click();` | `previousVersion.click()` is awaited on line 320 | right | A real one. A Playwright action returns a promise, and the next line reads the page before the action is known to have happened. |
| strapi/strapi `tests/e2e/tests/content-manager/history.spec.ts`:200 | `previousVersion.click();` | `previousVersion.click()` is awaited on line 320 | right | A real one. A Playwright action returns a promise, and the next line reads the page before the action is known to have happened. |
| strapi/strapi `tests/e2e/tests/content-manager/preview.spec.ts`:221 | `titleBox.fill('West Ham pre match jokes');` | `titleBox.fill()` is awaited on line 204 | right | A real one. A Playwright action returns a promise, and the next line reads the page before the action is known to have happened. |

### trpc/trpc at `3d04c7e` (test)

1,256 files, 176,393 lines, 1,794 whole-statement calls in async functions: 5 finding(s).

| Where | Code | Evidence | Verdict | Why |
| --- | --- | --- | --- | --- |
| trpc/trpc `packages/server/src/adapters/node-http/incomingMessageToRequest.test.ts`:162 | `server.close();` | `server.close()` is awaited on line 71 | right | A real one, and harmless: the helper's `close()` is async, and the file's four other tests await it. |
| trpc/trpc `packages/tests/server/adapters/next.test.ts`:35 | `server.close();` | `server.close()` is awaited on line 73 | wrong | A different `server`: this one is Node's HTTP server, whose `close()` returns no promise. The awaited one is the test's own wrapper. |
| trpc/trpc `packages/tests/server/websockets.test.ts`:860 | `ctx.wsClient.close();` | `ctx.wsClient.close()` is awaited on line 273 | unsure | 47 calls in the file await this close. These three go on to wait for what the close causes, so nothing breaks. |
| trpc/trpc `packages/tests/server/websockets.test.ts`:910 | `ctx.wsClient.close();` | `ctx.wsClient.close()` is awaited on line 273 | unsure | As on line 860. |
| trpc/trpc `packages/tests/server/websockets.test.ts`:926 | `ctx.wsClient.close();` | `ctx.wsClient.close()` is awaited on line 273 | unsure | As on line 860. |

### withastro/astro at `f9a1062` (test)

3,174 files, 263,544 lines, 10,615 whole-statement calls in async functions: 8 finding(s).

| Where | Code | Evidence | Verdict | Why |
| --- | --- | --- | --- | --- |
| withastro/astro `packages/astro/src/runtime/client/dev-toolbar/apps/audit/index.ts`:84 | `run();` | `run()` is awaited on line 43 | wrong | The whole body of an event listener, whose result nobody reads: awaiting it changes nothing. |
| withastro/astro `packages/astro/src/vite-plugin-astro-server/plugin.ts`:263 | `localStorage.run(request, () => {` | `localStorage.run()` is awaited on line 234 | wrong | `AsyncLocalStorage.run()` returns what its callback returns: a promise on the awaited line, nothing here. |
| withastro/astro `packages/astro/test/fixtures/sessions/src/actions/index.ts`:40 | `context.session.set('favoriteUrl', url);` | `context.session.set()` is awaited on line 11 | wrong | Astro's `session.set()` isn't async, and the awaits of it elsewhere in the file are needless. One of four copies of this fixture. |
| withastro/astro `packages/integrations/cloudflare/test/fixtures/sessions/src/actions/index.ts`:32 | `context.session.set('favoriteUrl', url);` | `context.session.set()` is awaited on line 11 | wrong | Astro's `session.set()` isn't async, and the awaits of it elsewhere in the file are needless. One of four copies of this fixture. |
| withastro/astro `packages/integrations/netlify/test/functions/fixtures/sessions/src/actions/index.ts`:32 | `context.session.set('favoriteUrl', url);` | `context.session.set()` is awaited on line 11 | wrong | Astro's `session.set()` isn't async, and the awaits of it elsewhere in the file are needless. One of four copies of this fixture. |
| withastro/astro `packages/integrations/node/test/fixtures/sessions/src/actions/index.ts`:32 | `context.session.set('favoriteUrl', url);` | `context.session.set()` is awaited on line 11 | wrong | Astro's `session.set()` isn't async, and the awaits of it elsewhere in the file are needless. One of four copies of this fixture. |
| withastro/astro `packages/integrations/node/test/node-middleware.test.ts`:79 | `server.close();` | `server.close()` is awaited on line 177 | wrong | Node's HTTP server: `close()` returns no promise, and the `await` of it further down is needless. |
| withastro/astro `packages/runtime/src/internal/server/astro-island.ts`:100 | `this.start();` | `start` is declared `async` on line 139 | unsure | The last call of a custom element's callback, which the browser doesn't wait for either. Nothing catches a failure in `start()`. |

### immich-app/immich at `e5d6eb7` (test)

1,166 files, 175,885 lines, 4,340 whole-statement calls in async functions: 0 finding(s).
