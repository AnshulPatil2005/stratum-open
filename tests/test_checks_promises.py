import pytest
from fakes import FakeGitHub

from stratum_open.checks import promises, run_checks
from stratum_open.diff import parse_diff
from stratum_open.policy import Policy, parse_policy
from stratum_open.runner import Context, review_pull

# CI installs the `js` extra; without it these tests have no parser to run.
needs_parser = pytest.mark.skipif(not promises.available(), reason="needs the js extra (tree-sitter)")
ON = parse_policy("checks:\n  floating_promises: true\n")


def added_file(path, text):
    """A diff adding every line of `text`, and the file's full text."""
    lines = text.strip("\n").split("\n")
    head = [f"diff --git a/{path} b/{path}", "new file mode 100644", "--- /dev/null", f"+++ b/{path}", f"@@ -0,0 +1,{len(lines)} @@"]
    return parse_diff("\n".join(head + [f"+{line}" for line in lines]) + "\n"), {path: "\n".join(lines) + "\n"}


def edited_file(path, text):
    """A diff adding only the lines of `text` that start with `+`, the rest
    unchanged, and the file's full text."""
    lines = text.strip("\n").split("\n")
    head = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}", f"@@ -1,{sum(not line.startswith('+') for line in lines)} +1,{len(lines)} @@"]
    body = [line if line.startswith("+") else f" {line[1:]}" for line in lines]
    return parse_diff("\n".join(head + body) + "\n"), {path: "\n".join(line[1:] for line in lines) + "\n"}


def flagged(text, path="app.js", files_of=added_file):
    files, contents = files_of(path, text)
    return [(f.line_start, f.title) for f in promises.check(files, contents)]


@needs_parser
def test_a_call_awaited_elsewhere_in_the_file_but_not_here_is_flagged():
    files, contents = added_file("app.js", """
async function fetchOne(client) {
  return await client.get("/a");
}

async function warm(client) {
  await client.get("/b");
  client.get("/c");
}
""")
    [finding] = promises.check(files, contents)
    assert (finding.line_start, finding.rule, finding.title) == (7, "code/floating-promise", "`client.get()` isn't awaited")
    assert finding.detail.startswith("`client.get()` is awaited on line 2, so it returns a promise")
    assert (finding.source, finding.category, finding.path) == ("check", "bug", "app.js")


@needs_parser
def test_a_call_to_something_the_file_declares_async_is_flagged():
    files, contents = added_file("app.ts", """
async function load(): Promise<void> {}
const store = async (item: string) => {};
const api = { async get() {}, post: async function () {} };

class Cache {
  async flush() {}
  private evict = async () => {};

  async run() {
    load();
    store("a");
    api.get();
    api.post();
    this.flush();
    this.evict();
  }
}
""")
    findings = promises.check(files, contents)
    assert [(f.line_start, f.title) for f in findings] == [
        (10, "`load()` isn't awaited"), (11, "`store()` isn't awaited"), (12, "`api.get()` isn't awaited"),
        (13, "`api.post()` isn't awaited"), (14, "`this.flush()` isn't awaited"), (15, "`this.evict()` isn't awaited"),
    ]
    assert findings[0].detail.startswith("`load` is declared `async` on line 1, so")
    assert findings[4].detail.startswith("`flush` is declared `async` on line 6, so")


@needs_parser
def test_awaited_returned_assigned_handed_on_chained_voided_or_collected_calls_are_fine():
    assert flagged("""
async function job() {}

async function run(queue) {
  await job();
  const later = job();
  queue.push(job());
  job().then(() => {});
  job().catch(console.error);
  job().finally(() => {});
  void job();
  const all = [job(), job()];
  await Promise.all([job(), later, ...all]);
  return job();
}

const short = async () => job();
""") == []


@needs_parser
def test_sync_functions_and_sync_callbacks_inside_async_ones_are_left_alone():
    assert flagged("""
async function save(item) {}

function saveLater(item) {
  save(item);
}

async function saveAll(items, timer) {
  items.forEach((item) => { save(item); });
  timer.onTick(function () { save(1); });
  await save(0);
}

class Store {
  static { save(2); }
}

save(3);
""") == []
    # An async callback is an async function like any other.
    assert flagged("""
async function save(item) {}

function saveAll(items) {
  items.forEach(async (item) => {
    save(item);
  });
}
""") == [(5, "`save()` isn't awaited")]


@needs_parser
def test_this_counts_only_within_its_own_class_or_object():
    # One class's `this.conn.close()` returns a promise, the other's doesn't:
    # the second isn't missing an await because the first awaits.
    assert flagged("""
class AsyncPool {
  async stop() { await this.conn.close(); }
  async flush() {}
}

class SyncPool {
  async stop() {
    this.conn.close();
    this.flush();
  }
  flush() {}
}

async function plain() {
  this.flush();
}
""") == []
    assert flagged("""
class Pool {
  async stop() { await this.conn.close(); }
  async restart() {
    this.conn.close();
    const again = async () => { this.flush(); };
  }
  async flush() {}
}

export default {
  methods: {
    async load() {},
    async refresh() { this.load(); },
  },
};
""") == [(4, "`this.conn.close()` isn't awaited"), (5, "`this.flush()` isn't awaited"), (13, "`this.load()` isn't awaited")]


@needs_parser
def test_a_parameter_counts_only_within_its_own_function():
    # `next` is whatever each caller passed: one middleware awaiting its own says nothing about another's.
    assert flagged("""
async function timed(ctx, next) {
  await next();
}

async function legacy(req, res, next) {
  next();
}

async function twice(ctx, next) {
  await next();
  next();
}
""") == [(11, "`next()` isn't awaited")]
    # A parameter hides a function of the same name that the file declares async.
    assert flagged("""
async function done() {}

async function run(done) {
  done();
}
""") == []


@needs_parser
def test_a_name_the_file_also_binds_to_something_else_isnt_taken_as_async():
    assert flagged("""
import { load } from "./loader";

async function a() {
  const load = async () => {};
}

async function b() {
  load();
}
""") == []
    assert flagged("""
let handler = async () => {};
handler = () => {};

async function run() {
  handler();
}
""") == []
    # A generator and a getter don't return a promise when called.
    assert flagged("""
async function* rows() {}

class Feed {
  get ready() { return this.pending; }
  async *pages() {}
  async run() {
    rows();
    this.ready();
    this.pages();
  }
}
""") == []


@needs_parser
def test_a_callee_the_file_already_calls_on_its_own_is_left_alone():
    # `app.register()` can be awaited and needn't be: an unchanged line already calls it bare.
    assert flagged("""
 async function build(app) {
   app.register(routes);
   await app.register(auth);
+  app.register(metrics);
+  app.listen();
   await app.ready();
 }
""", files_of=edited_file) == []
    # In a new file, calls on their own more than half as often as awaited say the same.
    assert flagged("""
async function build(app) {
  await app.register(routes);
  await app.register(metrics);
  await app.register(auth);
  app.register(docs);
  app.register(health);
}
""") == []
    assert flagged("""
async function build(app) {
  await app.register(routes);
  await app.register(metrics);
  await app.register(auth);
  await app.register(admin);
  app.register(docs);
  app.register(health);
}
""") == [(6, "`app.register()` isn't awaited"), (7, "`app.register()` isn't awaited")]


@needs_parser
def test_the_callback_form_of_a_call_is_left_alone():
    # `listen(port, callback)` is the form where the callback waits: awaiting the other form says nothing about it.
    for callback in ("(err) => { if (err) throw err; }", "function () {}", "done"):
        assert flagged("""
async function start(app, port, done) {
  await app.listen(port);
  app.listen(port + 1, %s);
}
""" % callback) == [], callback
    assert flagged("""
async function start(app, port, options) {
  await app.listen(port);
  app.listen(port + 1, options);
}
""") == [(3, "`app.listen()` isn't awaited")]
    # Where the awaited calls hand over a function too, it's no callback form.
    assert flagged("""
test("suite", async (t) => {
  await t.test("first", async () => {});
  t.test("second", async () => {});
});
""") == [(3, "`t.test()` isn't awaited")]


@needs_parser
def test_a_name_that_hides_another_isnt_the_same_one():
    # The inner `client` is a socket whose close() returns nothing; the outer one's is awaited.
    assert flagged("""
describe("gateway", () => {
  let client;

  afterEach(async () => {
    await client.close();
  });

  const send = async (event) => {
    const client = new WebSocket(url);
    client.send(event);
    client.close();
  };
});
""") == []
    # Side by side, two functions' `client` are taken to be the same kind of thing.
    assert flagged("""
async function first(client) {
  await client.close();
}

async function second(client) {
  client.close();
}
""") == [(6, "`client.close()` isn't awaited")]


@needs_parser
def test_a_promise_being_handled_isnt_a_dropped_call():
    assert flagged("""
async function connect(client) {
  const attempt = client.connect();
  attempt.catch(() => {});
  attempt.then(() => {});
  attempt.finally(() => {});
  await attempt.catch(() => {});
}
""") == []


@needs_parser
def test_only_calls_that_start_on_an_added_line_are_reported():
    assert flagged("""
 async function sync(db) {
   await db.save(1);
+  db.save(
+    2,
+  );
 }
 async function old(db) {
   db.save(3);
 }
""", files_of=edited_file) == []  # the unchanged `db.save(3)` shows the file already does this
    assert flagged("""
 async function sync(db) {
   await db.save(1);
+  db.save(
+    2,
+  );
 }
""", files_of=edited_file) == [(3, "`db.save()` isn't awaited")]
    assert flagged("""
 async function sync(db) {
+  await db.save(1);
   db.load(
+    2,
   );
   await db.load(3);
 }
""", files_of=edited_file) == []


@needs_parser
def test_a_call_the_prs_removed_line_awaited_is_flagged():
    diff = "\n".join([
        "diff --git a/app.js b/app.js", "--- a/app.js", "+++ b/app.js",
        "@@ -1,4 +1,4 @@", " import { flush } from './queue';", " export async function stop() {", "-  await flush();", "+  flush();", " }",
    ]) + "\n"
    text = "import { flush } from './queue';\nexport async function stop() {\n  flush();\n}\n"
    [finding] = promises.check(parse_diff(diff), {"app.js": text})
    assert (finding.line_start, finding.title) == (3, "`flush()` isn't awaited")
    assert finding.detail.startswith("A line this PR removes awaited `flush()`, so")
    # The same PR making `flush` a plain function is why the await went.
    plain = "function flush() {}\nexport async function stop() {\n  flush();\n}\n"
    assert promises.check(parse_diff(diff.replace("import { flush } from './queue';", "function flush() {}")), {"app.js": plain}) == []


@needs_parser
def test_a_removed_this_await_counts_only_in_the_class_its_hunk_is_in():
    new = "\n".join([
        "class AsyncStream {", "  async close() {", "    return 1;", "  }", "}",
        "class SyncStream {", "  async close() {", "    this.file.close();", "  }", "}",
    ]) + "\n"
    diff = "\n".join([
        "diff --git a/app.js b/app.js", "--- a/app.js", "+++ b/app.js",
        "@@ -1,4 +1,4 @@", " class AsyncStream {", "   async close() {", "-    await this.file.close();", "+    return 1;", "   }",
        "@@ -6,4 +6,4 @@", " class SyncStream {", "   async close() {", "-    return 1;", "+    this.file.close();", "   }",
    ]) + "\n"
    assert promises.check(parse_diff(diff), {"app.js": new}) == []
    same = "\n".join([
        "diff --git a/app.js b/app.js", "--- a/app.js", "+++ b/app.js",
        "@@ -1,4 +1,4 @@", " class Stream {", "   async close() {", "-    await this.file.close();", "+    this.file.close();", "   }",
    ]) + "\n"
    text = "class Stream {\n  async close() {\n    this.file.close();\n  }\n}\n"
    assert [f.line_start for f in promises.check(parse_diff(same), {"app.js": text})] == [3]


@needs_parser
def test_typescript_generics_decorators_and_optional_chains_are_read():
    assert flagged("""
@Injectable()
export class Users<T extends Row = Row> {
  constructor(private readonly repo: Repository<T>) {}

  @Transactional()
  async rename<K extends keyof T>(id: number, key: K, value: T[K]): Promise<void> {
    const row = <T>(await this.repo.findOne<T>(id))!;
    await this.repo.save<T>({ ...row, [key]: value } as T);
    this.repo.save<T>(row);
    this.repo?.audit!(id);
    await this.repo.audit(id);
  }
}
""", path="users.ts") == [(9, "`this.repo.save()` isn't awaited"), (10, "`this.repo.audit()` isn't awaited")]


@needs_parser
def test_jsx_template_literals_regexes_and_comments_dont_count_as_code():
    # The only awaits of `track` are in a comment, a string, a template and JSX text.
    text = r"""
import { track, load } from "./api";

export function Button({ id }) {
  // await track(id);
  const hint = "await track(id)";
  const label = `${id}: await track(${id / 2})`;
  const slash = /await track\(/g.test(hint) ? 1 / 2 : 2 / 1;
  const onClick = async () => {
    track(id);
    load(`${id}`);
    await load(id);
  };
  return <button onClick={onClick}>await track(id) {label}</button>;
}
"""
    assert flagged(text, path="button.jsx") == [(10, "`load()` isn't awaited")]
    assert flagged(text.replace("({ id })", "({ id }: { id: number })"), path="button.tsx") == [(10, "`load()` isn't awaited")]


@needs_parser
def test_unparseable_files_and_other_languages_are_skipped():
    assert flagged("async function f() { await g(); g(); }") == [(1, "`g()` isn't awaited")]
    assert flagged("async function f() { await g(); g(); ") == []
    assert flagged("async function f() { await g(); g(); }", path="app.vue") == []
    assert flagged("async def f():\n    await g()\n    g()\n", path="app.py") == []
    # TypeScript in a .js file doesn't parse as JavaScript.
    assert flagged("async function f(x: number) { await g(); g(); }") == []


@needs_parser
def test_a_deeply_nested_file_is_read_without_an_error():
    deep = "x = " + "[" * 5000 + "]" * 5000 + ";\nasync function f() { await g(); g(); }\n"
    assert flagged(deep) == [(2, "`g()` isn't awaited")]


def test_without_the_parser_or_the_text_it_says_nothing(monkeypatch):
    files, contents = added_file("app.js", "async function f() { await g(); g(); }")
    assert promises.check(files) == []
    monkeypatch.setattr(promises, "available", lambda: False)
    assert promises.check(files, contents) == []
    assert [f.rule for f in run_checks(files, ON, contents=contents)] == []


def test_a_broken_install_counts_as_no_parser(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_tree_sitter(name, *args, **kwargs):
        if name.startswith("tree_sitter"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    promises._languages.cache_clear()
    monkeypatch.setattr(builtins, "__import__", no_tree_sitter)
    try:
        assert not promises.available()
    finally:
        promises._languages.cache_clear()


@needs_parser
def test_it_runs_only_when_the_policy_turns_it_on():
    files, contents = added_file("app.js", "async function f() { await g(); g(); }")
    assert Policy().checks.floating_promises is False
    assert [f.rule for f in run_checks(files, Policy(), contents=contents)] == []
    assert [f.rule for f in run_checks(files, ON, contents=contents)] == ["code/floating-promise"]


def test_worth_reading():
    def worth(path, line):
        return promises.worth_reading(added_file(path, line)[0][0])

    for line in ("save(item);", "  this.repo.save(item)", "db?.save(item)", "app.register(async () => {"):
        assert worth("src/app.ts", line), line
    for line in ("const saved = save(item);", "if (ready) {", "return save(item);", "await save(item);", "// save(item)", "}"):
        assert not worth("src/app.ts", line), line
    assert not worth("src/app.py", "save(item)")


@needs_parser
def test_the_review_fetches_the_file_when_the_policy_turns_it_on():
    text = "async function f() {\n  await g();\n  g();\n}\n"
    diff = "\n".join([
        "diff --git a/src/app.js b/src/app.js", "--- a/src/app.js", "+++ b/src/app.js",
        "@@ -1,3 +1,4 @@", " async function f() {", "   await g();", "+  g();", " }",
    ]) + "\n"

    def review(policy):
        gh = FakeGitHub(diff=diff, files={("src/app.js", "a" * 40): text})
        context = Context(gh=gh, policy=policy, model=None, bot="stratum[bot]", log=lambda line: None)
        return [(f.rule, f.path, f.line_start) for f in review_pull(context, 7).findings]

    assert review(ON) == [("code/floating-promise", "src/app.js", 3)]
    assert review(Policy()) == []
