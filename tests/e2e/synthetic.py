"""Two made-up pull requests with the shapes that broke Stratum on real ones,
built in code so no other project's files live in this repo.

PR 1 ("fixes", from a fork): a browser extension's build output and sources.
Two minified bundles, one deleted and one added, each a single line over
130 KB long with thousands of backslash escapes and an unterminated quote at
the end: the shape that made the pattern check's string regex backtrack for
eleven minutes. A lockfile with a hundred churned lines, and four TypeScript
sources with ordinary edits.

PR 2 ("Improve content script fetch and tests", same repo): 24 files, 900
added lines, no description. Five new test files, a new devDependency in
`package.json`, a README change, two minified bundles, and library code with
a few concrete defects for a model to find (a `JSON.parse` with no guard, a
`fetch` with no timeout, an index into a possibly empty list).

`add_pr(fake, number)` puts one on a `FakeGitHubServer`; `diff(number)` and
`pull(number)` give the raw diff and the PR's title and body for tests that
replay recorded model replies against them.
"""

from __future__ import annotations

from functools import lru_cache

import diffs
from diffs import minified_bundle

OWNER = "acme"
REPO = "widget"


# ── building blocks ──────────────────────────────────────────────────────


def lockfile_entries(names: list[str], version: str) -> list[str]:
    lines = []
    for name in names:
        lines += [
            f'    "node_modules/{name}": {{',
            f'      "version": "{version}",',
            f'      "resolved": "https://registry.npmjs.org/{name}/-/{name}-{version}.tgz",',
            f'      "integrity": "sha512-{name[:6]:<6}AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==",',
            '      "dev": true',
            "    },",
        ]
    return lines


PACKAGES = ["esbuild", "rollup", "typescript", "vite", "postcss", "picocolors", "nanoid", "source-map-js", "tslib",
            "magic-string", "acorn", "estree-walker", "fsevents", "@types/node", "undici-types", "@esbuild/win32-x64",
            "@rollup/rollup-win32-x64-msvc", "semver", "lru-cache", "yallist", "glob", "minimatch", "brace-expansion",
            "balanced-match", "path-scurry"]


def ts_module(name: str, functions: int, *, defect: str | None = None) -> list[str]:
    """A TypeScript module with `functions` small exported functions and,
    optionally, one concrete defect a reviewer should notice."""
    lines = [f"// {name}: helpers for the {name.split('/')[-1].split('.')[0]} step of the pipeline.", ""]
    for n in range(functions):
        lines += [
            f"export function step{n}(items: string[], limit = {n + 1}): string[] {{",
            "  const out: string[] = [];",
            "  for (const item of items) {",
            "    if (item.length > limit) out.push(item.trim());",
            "  }",
            "  return out;",
            "}",
            "",
        ]
    if defect == "json":
        lines += [
            "export function parseReply(text: string): { verdict: string; confidence: number } {",
            "  const data = JSON.parse(text);",
            "  return { verdict: data.verdict, confidence: data.confidence };",
            "}",
            "",
        ]
    if defect == "fetch":
        lines += [
            "export async function fetchSource(url: string): Promise<string> {",
            "  const response = await fetch(url);",
            "  return response.text();",
            "}",
            "",
        ]
    if defect == "index":
        lines += [
            "export function best(candidates: Array<{ score: number }>): number {",
            "  const sorted = [...candidates].sort((a, b) => b.score - a.score);",
            "  return sorted[0].score;",
            "}",
            "",
        ]
    return lines


def test_file(name: str, cases: int) -> list[str]:
    lines = ['import { describe, expect, it } from "vitest";', f'import * as mod from "../{name}";', "", f'describe("{name}", () => {{']
    for n in range(cases):
        lines += [
            f'  it("keeps items longer than the limit, case {n}", () => {{',
            f'    expect(mod.step0(["a", "bb", "ccc"], {n})).toEqual(["a", "bb", "ccc"].filter((s) => s.length > {n}));',
            "  });",
        ]
    lines += ["});", ""]
    return lines


# ── the two PRs ──────────────────────────────────────────────────────────


@lru_cache(maxsize=None)
def diff(number: int) -> str:
    if number == 1:
        return "".join([
            diffs.deleted("dist/assets/popup-1a2b3c4d.js", [minified_bundle(1)]),
            diffs.added("dist/assets/popup-5e6f7a8b.js", [minified_bundle(2)]),
            diffs.modified("dist/background.js", [minified_bundle(3, 10_000)], [minified_bundle(4, 10_000)]),
            diffs.modified("dist/contentScript.js", [minified_bundle(5, 5_000)], [minified_bundle(6, 5_000)]),
            diffs.modified("dist/index.html", ["    <script src=\"/assets/popup-1a2b3c4d.js\"></script>"],
                           ["    <script src=\"/assets/popup-5e6f7a8b.js\"></script>"], start=12),
            diffs.modified("package-lock.json", lockfile_entries(PACKAGES, "1.0.0"), lockfile_entries(PACKAGES[:18], "1.1.0"), start=40),
            diffs.modified("src/App.tsx",
                           ['  const [settings, setSettings] = useState(defaultSettings);'],
                           ['  const [settings, setSettings] = useState(defaultSettings);',
                            '  const [settingsLoaded, setSettingsLoaded] = useState(false);',
                            '  useEffect(() => {',
                            '    if (!hasChromeStorage()) {',
                            '      setSettingsLoaded(true);',
                            '      return;',
                            '    }',
                            '    chrome.storage.local.get([SETTINGS_KEY]).then((stored) => {',
                            '      const saved = stored[SETTINGS_KEY] as Settings | undefined;',
                            '      if (saved) setSettings({ ...defaultSettings, ...saved });',
                            '    }).finally(() => setSettingsLoaded(true));',
                            '  }, []);',
                            '  useEffect(() => {',
                            '    if (!settingsLoaded || !hasChromeStorage()) return;',
                            '    chrome.storage.local.set({ [SETTINGS_KEY]: settings });',
                            '  }, [settings, settingsLoaded]);'], start=80),
            diffs.modified("src/background/serviceWorker.ts",
                           ['      if (!tab.url.includes(`/contest/${failed.contestId}/submission/`)) continue;'],
                           ['      const tabContestId = tab.url.match(/\\/contest\\/(\\d+)\\/submission\\/\\d+/i)?.[1];',
                            '      if (tabContestId !== failed.contestId) continue;'], start=296),
            diffs.modified("src/content/contentScript.ts",
                           ['    if (location.hostname.includes("example.com") && problem.contestId) {'],
                           ['    const isProblemPage = /\\/(?:contest|problemset)\\/\\d+\\/problem\\/[A-Z0-9]+/i.test(location.pathname);',
                            '    if (location.hostname.includes("example.com") && isProblemPage && problem.contestId) {'], start=102),
            diffs.modified("src/lib/types.ts", ["  explanationSkipped?: boolean;"], ["  explanationSkipped?: boolean;", "  explanationError?: string;"], start=84),
        ])
    if number == 2:
        parts = [
            diffs.modified("README.md", ["## Development"], ["## Development", "", "Run the unit tests with `npm test`; they use vitest and need no browser."], start=30),
            diffs.added("dist/assets/fingerprint-9f8e7d6c.js", [minified_bundle(7, 20_000)]),
            diffs.deleted("dist/assets/fingerprint-0a1b2c3d.js", [minified_bundle(8, 20_000)]),
            diffs.added("dist/assets/popup-c0ffee00.js", [minified_bundle(9)]),
            diffs.deleted("dist/assets/popup-5e6f7a8b.js", [minified_bundle(2)]),
            diffs.modified("dist/background.js", [minified_bundle(4, 10_000)], [minified_bundle(10, 10_000)]),
            diffs.modified("dist/contentScript.js", [minified_bundle(6, 5_000)], [minified_bundle(11, 5_000)]),
            diffs.modified("dist/index.html", ['    <script src="/assets/popup-5e6f7a8b.js"></script>'], ['    <script src="/assets/popup-c0ffee00.js"></script>'], start=12),
            diffs.modified("package-lock.json", lockfile_entries(PACKAGES[:18], "1.1.0"), lockfile_entries(PACKAGES, "1.2.0"), start=40),
            diffs.modified("package.json", ['    "typescript": "^5.4.0"'], ['    "typescript": "^5.4.0",', '    "vitest": "^2.1.0"'], start=22),
            diffs.modified("src/App.tsx", ['  const failedLogic = useMemo(() => null, []);'],
                           ['  const failedLogic = useMemo(', '    () => (result?.failedSubmission.code ? extractLogicFingerprint(result.failedSubmission.code) : null),',
                            '    [result?.failedSubmission.code],', '  );'], start=120),
            diffs.modified("src/background/serviceWorker.ts", ["  let explanationSkipped = !shouldExplain;"],
                           ["  let explanationSkipped = !shouldExplain;", "  let explanationError: string | undefined;"], start=436),
            diffs.modified("src/content/contentScript.ts", ["  return { ok: false, reason: \"Not a supported page.\" };"],
                           ["  const text = document.body?.innerText ?? \"\";", "  if (!text) return { ok: false, reason: \"The page has no text yet.\" };",
                            "  return { ok: false, reason: \"Not a supported page.\" };"], start=140),
            diffs.added("src/lib/__tests__/ai.test.ts", test_file("ai", 4)),
            diffs.added("src/lib/__tests__/fingerprint.test.ts", test_file("fingerprint", 6)),
            diffs.added("src/lib/__tests__/language.test.ts", test_file("language", 3)),
            diffs.added("src/lib/__tests__/matcher.test.ts", test_file("matcher", 8)),
            diffs.added("src/lib/__tests__/promptBuilder.test.ts", test_file("promptBuilder", 5)),
            diffs.added("src/lib/ai.ts", ts_module("src/lib/ai.ts", 6, defect="json")),
            diffs.added("src/lib/codeforces.ts", ts_module("src/lib/codeforces.ts", 5, defect="fetch")),
            diffs.added("src/lib/fingerprint.ts", ts_module("src/lib/fingerprint.ts", 9)),
            diffs.added("src/lib/language.ts", ts_module("src/lib/language.ts", 4)),
            diffs.added("src/lib/matcher.ts", ts_module("src/lib/matcher.ts", 7, defect="index")),
            diffs.added("vite.config.ts", ['import { defineConfig } from "vitest/config";', "", "export default defineConfig({", "  test: {",
                                           '    environment: "node",', '    include: ["src/**/*.test.ts"],', "  },", "});"]),
        ]
        return "".join(parts)
    raise ValueError(f"no synthetic PR {number}")


def pull(number: int) -> dict:
    """Title, body and author as GitHub would send them."""
    if number == 1:
        return {"title": "fixes", "body": "Persisted AI settings/API keys across popup closes.\r\nShow a clearer error when the explanation fails.",
                "author": "forker", "fork": True, "commits": 1}
    if number == 2:
        return {"title": "Improve content script fetch and tests", "body": None, "author": "carol", "fork": False, "commits": 3}
    raise ValueError(f"no synthetic PR {number}")


def add_pr(fake, number: int) -> dict:
    """Put synthetic PR `number` on a FakeGitHubServer; returns the PR as GitHub would send it."""
    meta = pull(number)
    return fake.add_pull(number, diff=diff(number), title=meta["title"], body=meta["body"], author=meta["author"],
                         fork=meta["fork"], commits=meta["commits"], linked_issues=[])
