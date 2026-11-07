from stratum_open.checks import awaits, run_checks
from stratum_open.diff import parse_diff
from stratum_open.policy import Policy


def added_file(path, text):
    """A diff adding every line of `text`, and the file's full text."""
    lines = text.strip("\n").split("\n")
    head = [f"diff --git a/{path} b/{path}", "new file mode 100644", "--- /dev/null", f"+++ b/{path}", f"@@ -0,0 +1,{len(lines)} @@"]
    return parse_diff("\n".join(head + [f"+{line}" for line in lines]) + "\n"), {path: "\n".join(lines) + "\n"}


def flagged(text, path="app.py"):
    files, contents = added_file(path, text)
    return [(f.line_start, f.title) for f in awaits.check(files, contents)]


def test_a_call_awaited_elsewhere_in_the_file_but_not_here_is_flagged():
    assert flagged("""
async def fetch(client):
    return await client.get("/a")

async def fetch_both(client):
    first = client.get("/b")
    return first
""") == [(5, "`client.get()` isn't awaited")]


def test_awaited_handed_on_or_collected_calls_are_fine():
    assert flagged("""
import asyncio

async def job():
    await asyncio.sleep(1)

async def run(tasks):
    await job()
    task = asyncio.create_task(job())
    done = await asyncio.gather(job(), job())
    pending = [job() for _ in range(3)]
    later = job()
    await later
    return task, done, pending
""") == []


def test_sync_functions_and_nested_sync_defs_are_left_alone():
    assert flagged("""
async def save(store, item):
    await store.put(item)

def save_sync(store, item):
    store.put(item)

async def outer(store):
    def callback(item):
        store.put(item)
    await store.put(1)
    return callback
""") == []


def test_self_attributes_count_only_within_their_own_class():
    # One class's `self._file` is an async file, the other's a plain one: a
    # call in the second isn't missing an await just because the first awaits.
    assert flagged("""
class AsyncStream:
    async def close(self):
        await self._file.close()

class SyncStream:
    async def close(self):
        self._file.close()
""") == []
    assert flagged("""
class Stream:
    async def read(self):
        return await self._file.read()

    async def close(self):
        self._file.read()
""") == [(6, "`self._file.read()` isn't awaited")]


def test_a_call_the_prs_removed_line_awaited_counts_without_the_full_text():
    diff = "\n".join([
        "diff --git a/app.py b/app.py", "--- a/app.py", "+++ b/app.py",
        "@@ -10,3 +10,3 @@ async def export(self, item):",
        "     if self.ready:",
        "-        await self.lock.acquire()",
        "+        self.lock.acquire()",
        "     return item",
    ]) + "\n"
    findings = awaits.check(parse_diff(diff))
    assert [(f.line_start, f.rule) for f in findings] == [(11, "code/missing-await")]


def test_unparseable_files_and_other_languages_are_skipped():
    assert flagged("async def broken(:\n    await x()\n    x()\n") == []
    assert flagged("async function f() { await g(); g(); }", path="app.js") == []


def test_it_runs_with_the_other_checks():
    files, contents = added_file("app.py", "async def a(s):\n    await s.send()\n    s.send()\n")
    rules = [f.rule for f in run_checks(files, Policy(), contents=contents)]
    assert "code/missing-await" in rules


def test_touches_async():
    files, _ = added_file("app.py", "async def a():\n    pass\n")
    assert awaits.touches_async(files[0])
    files, _ = added_file("app.py", "def a():\n    pass\n")
    assert not awaits.touches_async(files[0])
