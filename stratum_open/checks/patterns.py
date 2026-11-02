"""Risky code on the lines a PR adds: secrets, strings run as code or as shell
commands, SQL put together from strings, and CORS opened to every origin.

Each rule matches only shapes it's sure of, so a finding is worth reading but
a quiet run proves little. Lines are judged one at a time: a call or a SQL
string split over several lines is mostly missed. Test files are skipped for
everything but token-shaped secrets, which leak just the same from a test.
"""

from __future__ import annotations

import re

from stratum_open.checks.common import basename, extension, is_comment, is_test_path, view
from stratum_open.diff import FileDiff
from stratum_open.findings import Finding

PYTHON = {".py"}
JAVASCRIPT = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}
SQL_HOSTS = PYTHON | JAVASCRIPT | {".go", ".java", ".kt", ".scala", ".rb", ".php", ".cs", ".rs"}
DOCS = {".md", ".markdown", ".rst", ".txt", ".adoc"}


def check(files: list[FileDiff], contents: dict[str, str] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for file in files:
        added = file.added()
        if not added:
            continue
        ext = extension(file.path)
        test = is_test_path(file.path)
        lines = dict(view(file, contents))
        child_process = ext in JAVASCRIPT and any("child_process" in line for line in lines.values())
        for number, text in added:
            findings.extend(_secrets(file.path, number, text, lines, test))
            if is_comment(text):
                continue
            if not test:
                findings.extend(_eval(file.path, number, text, ext))
                findings.extend(_shell(file.path, number, text, ext, lines, child_process))
                if ext in SQL_HOSTS:
                    findings.extend(_sql(file.path, number, text, ext))
            if ext not in DOCS and not test:
                findings.extend(_cors(file.path, number, text))
    return findings


def _finding(path: str, number: int, rule: str, title: str, detail: str, suggestion: str,
             severity: str = "high", confidence: float = 0.8) -> Finding:
    return Finding(
        title=title, detail=detail, severity=severity, category="security", path=path,
        line_start=number, line_end=number, suggestion=suggestion, source="check", rule=rule,
        confidence=confidence,
    )


# ── secrets ──────────────────────────────────────────────────────────────

_TOKENS = (
    ("a GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    ("a GitHub token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}")),
    ("an AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("an API key", re.compile(r"(?<![\w-])sk-[A-Za-z0-9_-]{20,}")),
    ("a Stripe key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{20,}")),
    ("a Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("an npm token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("a PyPI token", re.compile(r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}")),
)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")
_KEY_BODY = re.compile(r"[A-Za-z0-9+/=]{40,}")

# A name ending in api key / token / password / secret, given a quoted value.
_ASSIGNED = re.compile(
    r"""(?ix)
    (?<![\w.-])["']?
    (?P<name>[a-z0-9_.-]*?(?:api[_-]?key|secret[_-]?key|access[_-]?key|private[_-]?key|token|passw(?:or)?d|secret))
    ["']?\]?\s*
    (?::\s*[a-z_][\w\[\]., |]*?\s*(?==))?
    (?:=>|:=|=|:)\s*
    [rbu]?(?P<q>["'])(?P<value>[^"'\s]{8,})(?P=q)
    """
)
_PLACEHOLDER = re.compile(
    r"(?i)change[_-]?me|example|dummy|test|sample|placeholder|your|fake|redacted|xxx|\*{3}|\.{3}|[<>]|\$\{|\{\{|%\(|%s|^\$"
)
_WORDY = re.compile(r"[A-Za-z]+(?:[_.-][A-Za-z]+)*")  # a field name like "access_token", not a secret
_ENV_LOOKUP = re.compile(r"environ|getenv|process\.env|\bENV\[|\bsecrets\.|\benv\(")
_TEMPLATE_FILE = re.compile(r"\.(?:example|sample|template|tmpl|dist)(?:\.|$)")


def _secrets(path: str, number: int, text: str, lines: dict[int, str], test: bool) -> list[Finding]:
    if _TEMPLATE_FILE.search(basename(path).lower()):
        return []
    kind = _token_kind(text) or _private_key(number, text, lines)
    if kind is None and not test and not _ENV_LOOKUP.search(text):
        for match in _ASSIGNED.finditer(text):
            if _real_value(match.group("value")):
                kind = f"a hard-coded {_secret_word(match.group('name'))}"
                break
    if kind is None:
        return []
    return [_finding(
        path, number, "code/secret",
        title="Possible secret in the code",
        detail=(
            f"Line {number} has what looks like {kind}. Anything committed stays in the git history, "
            "readable by anyone who can see the repo, even after a later commit removes it."
        ),
        suggestion="If it's real, revoke it now, then load it from an environment variable or a secret store.",
        severity="critical", confidence=0.9 if kind.startswith(("a GitHub", "an AWS", "a private")) else 0.75,
    )]


def _token_kind(text: str) -> str | None:
    for kind, pattern in _TOKENS:
        for match in pattern.finditer(text):
            token = match.group(0)
            if _PLACEHOLDER.search(token) or len(set(token)) < 8:
                continue
            if token.startswith("sk-") and not (any(c.isdigit() for c in token) and any(c.isupper() for c in token)):
                continue
            return kind
    return None


def _private_key(number: int, text: str, lines: dict[int, str]) -> str | None:
    """Only a header followed by key material counts, not docs naming the format."""
    match = _PRIVATE_KEY.search(text)
    if not match:
        return None
    following = text[match.end():].replace("\\n", " ") + " " + lines.get(number + 1, "")
    return "a private key" if _KEY_BODY.search(following) else None


def _real_value(value: str) -> bool:
    if _PLACEHOLDER.search(value) or len(set(value)) < 5:
        return False
    if _WORDY.fullmatch(value) and len(value) < 20:
        return False
    return not (value.startswith(("/", "./", "../", "~/")) or "://" in value)


def _secret_word(name: str) -> str:
    name = name.lower()
    for word, label in (("passw", "password"), ("token", "token"), ("secret", "secret"), ("api", "API key")):
        if word in name:
            return label
    return "key"


# ── strings run as code ──────────────────────────────────────────────────

_PY_EVAL = re.compile(r"(?<![\w.$])(?P<fn>eval|exec)\s*\(")
_JS_EVAL = re.compile(r"(?<![\w.$])(?P<fn>eval)\s*\(|\bnew\s+(?P<ctor>Function)\s*\(")
_DEFINES = re.compile(r"\b(?:def|function)\s+(?:eval|exec)\b")
# A call whose first argument is a fixed string: nothing from outside gets in.
_LITERAL_ARG = re.compile(r"""\(\s*[rRbBuU]?(?P<q>["'`])[^"'`{}%\\]*(?P=q)\s*[,)]""")
# new Function() takes its code last, so every argument has to be fixed.
_ALL_LITERAL_ARGS = re.compile(r"""\((?:\s*(?P<q>["'`])[^"'`{}%\\]*(?P=q)\s*,)*\s*(?P<last>["'`])[^"'`{}%\\]*(?P=last)\s*\)""")


def _eval(path: str, number: int, text: str, ext: str) -> list[Finding]:
    pattern = _PY_EVAL if ext in PYTHON else _JS_EVAL if ext in JAVASCRIPT else None
    if pattern is None or _DEFINES.search(text):
        return []
    for match in pattern.finditer(text):
        if _in_string(text, match.start()):
            continue
        fixed = _ALL_LITERAL_ARGS if match.groupdict().get("ctor") else _LITERAL_ARG
        if fixed.match(text, match.end() - 1) or text[match.end():].lstrip().startswith(")"):
            continue
        name = f"new {match.group('ctor')}()" if match.groupdict().get("ctor") else f"{match.group('fn')}()"
        return [_finding(
            path, number, "code/eval",
            title=f"`{name}` runs a string as code",
            detail=(
                f"`{name}` runs whatever string it's given as code. If any part of that string can come "
                "from a user, a request or a file, so can the code it runs."
            ),
            suggestion=(
                "Parse the value instead (`json.loads` or `ast.literal_eval` in Python, `JSON.parse` in "
                "JavaScript), or map the expected inputs to functions."
            ),
        )]
    return []


# ── shell commands ───────────────────────────────────────────────────────

_SHELL_TRUE = re.compile(r"\bshell\s*=\s*True\b")
_SUBPROCESS_CALL = re.compile(r"\b(?:subprocess\.)?(?:run|call|check_call|check_output|Popen)\s*\(")
_PY_SHELL_CALL = re.compile(
    r"\b(?:os\.(?:system|popen)|subprocess\.(?:getoutput|getstatusoutput)|(?:asyncio\.)?create_subprocess_shell)\s*\("
)
_JS_CP_EXEC = re.compile(r"\b(?:child_process|childProcess|cp)\.exec(?:Sync)?\s*\(")
_JS_BARE_EXEC = re.compile(r"(?<![\w.$])exec(?:Sync)?\s*\(")
_JS_SHELL_OPTION = re.compile(r"\b(?:spawn|spawnSync|execFile|execFileSync)\s*\(.*\bshell\s*:\s*true\b")


def _shell(path: str, number: int, text: str, ext: str, lines: dict[int, str], child_process: bool) -> list[Finding]:
    risky = False
    if ext in PYTHON:
        call = _code_search(_PY_SHELL_CALL, text)
        if call:
            risky = not _LITERAL_ARG.match(text, call.end() - 1)
        elif _code_search(_SHELL_TRUE, text):
            risky = not _constant_command(lines, number)
    elif ext in JAVASCRIPT:
        call = _code_search(_JS_CP_EXEC, text)
        if call is None and child_process:  # a bare exec() is child_process's only if it's imported
            call = _code_search(_JS_BARE_EXEC, text)
        if call:
            risky = not _LITERAL_ARG.match(text, call.end() - 1)
        else:
            risky = bool(_code_search(_JS_SHELL_OPTION, text))
    if not risky:
        return []
    return [_finding(
        path, number, "code/shell",
        title="Command run through a shell",
        detail=(
            "This runs a command through a shell. If any part of it comes from outside (a filename, a "
            "branch name, user input), characters like `;` or `$(...)` in it run extra commands."
        ),
        suggestion=(
            "Pass the command as a list of arguments without `shell=True` in Python, or use `execFile` / "
            "`spawn` without `shell` in Node."
        ),
        confidence=0.7,
    )]


def _code_search(pattern: re.Pattern, text: str) -> re.Match | None:
    """The first match that's code, not text inside a string: a regex, a
    message or a docstring that mentions `shell=True` runs nothing."""
    return next((m for m in pattern.finditer(text) if not _in_string(text, m.start())), None)


def _in_string(text: str, pos: int) -> bool:
    """Whether `pos` falls inside a string literal opened earlier on the line."""
    quote, i = None, 0
    while i < pos:
        if quote:
            if text[i] == "\\":
                i += 2
            elif text.startswith(quote, i):
                i, quote = i + len(quote), None
            else:
                i += 1
            continue
        opener = next((q for q in ('"""', "'''", '"', "'", "`") if text.startswith(q, i)), None)
        if opener:
            quote, i = opener, i + len(opener)
        else:
            i += 1
    return quote is not None


def _constant_command(lines: dict[int, str], number: int) -> bool:
    """Whether the call this `shell=True` belongs to, found up to five lines
    above, runs a fixed string."""
    for start in range(number, number - 6, -1):
        if start not in lines:
            return False
        if _SUBPROCESS_CALL.search(lines[start]):
            joined = " ".join(lines[n].strip() for n in range(start, number + 1))
            call = _SUBPROCESS_CALL.search(joined)
            return bool(_LITERAL_ARG.match(joined, call.end() - 1))
    return False


# ── SQL from strings ─────────────────────────────────────────────────────

_SQL = re.compile(r"(?i)\b(?:select\s.+?\sfrom|insert\s+into|update\s+\S+\s+set|delete\s+from)\b")
_SQL_UPPER = re.compile(r"\b(?:SELECT\s.+?\sFROM|INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM)\b")
_SQL_SIGNS = re.compile(r"(?i)\bwhere\b|\bvalues\b|\*|=")  # tells lowercase SQL from prose
_STRING = re.compile(r"""(?P<q>\"\"\"|'''|["'`])(?P<body>(?:\\.|(?!(?P=q)).)*?)(?P=q)""")
_F_PREFIX = re.compile(r"(?<![\w])(?:[rRbB]?[fF]|[fF][rRbB]|\$@?|@\$)$")
_AFTER = re.compile(r"""^\s*(?:%\s*[\w(\[]|\.\s*format\s*\(|\+\s*[^\s"'`])""")
_BEFORE = re.compile(r"""[\w)\]]\s*\+\s*$""")
_FORMAT_CALL = re.compile(r"(?:Sprintf|String\.format|string\.Format|format)\s*\(\s*$")


def _sql(path: str, number: int, text: str, ext: str) -> list[Finding]:
    for match in _STRING.finditer(text):
        body, quote = match.group("body"), match.group("q")
        if not _SQL.search(body) or not (_SQL_UPPER.search(body) or _SQL_SIGNS.search(body)):
            continue
        before, after = text[: match.start()], text[match.end():]
        interpolated = (
            (_F_PREFIX.search(before) and "{" in body)
            or (quote in ('"', "`", '"""') and "${" in body)
            or (ext == ".rb" and quote == '"' and "#{" in body)
            or (ext == ".php" and quote == '"' and re.search(r"\$[A-Za-z_]", body))
        )
        built = _AFTER.match(after) or _BEFORE.search(before) or (_FORMAT_CALL.search(before) and re.search(r"%[sdv]|\{\d*\}", body))
        if interpolated or built:
            return [_finding(
                path, number, "code/sql",
                title="SQL built from strings",
                detail=(
                    "This SQL is put together with string formatting, so a value containing a quote can "
                    "change what the query does (SQL injection)."
                ),
                suggestion=(
                    "Use the database driver's placeholders and pass the values separately, for example "
                    '`cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))`.'
                ),
                confidence=0.7,
            )]
    return []


# ── CORS ─────────────────────────────────────────────────────────────────

_CORS = (
    re.compile(r"""(?i)access-control-allow-origin["']?\]?\s*(?:[:,=]|\s)\s*["']?\*(?![\w*.])"""),
    re.compile(
        r"""(?i)\b(?:allow_?origins|allowed_?origins|origins|origin)"""
        r"""["']?\s*[:=]\s*(?:\[\]string\s*)?[\[{(]?\s*["']\*["']"""
    ),
    re.compile(r"""\borigins\s+["']\*["']"""),
    re.compile(r"\bcors\s*\(\s*\{[^}]*\borigin\s*:\s*true\b"),
    re.compile(r"\bCORS_(?:ORIGIN_ALLOW_ALL|ALLOW_ALL_ORIGINS)\s*=\s*True\b"),
)


def _cors(path: str, number: int, text: str) -> list[Finding]:
    if not any(pattern.search(text) for pattern in _CORS):
        return []
    return [_finding(
        path, number, "code/cors-any",
        title="CORS allows any origin",
        detail=(
            "This lets any website call this server from a visitor's browser and read the replies. That's "
            "fine for a public, read-only API, but not for anything that uses cookies or returns private data."
        ),
        suggestion="List the origins that need access instead of allowing all of them.",
        severity="medium",
    )]
