# Checks that need no model

99 planted risks, each a PR adding files with one problem in them, and 184 real merged PRs as they are.

| Planted risk | Caught by its rule |
| --- | --- |
| Secrets and risky code | 20 of 20 |
| Bandit (optional extra) | 3 of 3 |
| Ruff (optional extra, off by default) | 55 of 55 |
| GitHub workflows | 7 of 7 |
| Dependencies | 7 of 8 |
| Coding-agent files | 3 of 3 |
| Hidden Unicode | 3 of 3 |
| **All** | **98 of 99** |

Missed: `dep-jellyfish` (found: deps/missing, deps/new).

On the merged PRs: 6 finding(s) on 3 of 184 PRs. Marked: 6 right, 0 wrong (false alarms), 0 unsure.

| PR | Rule | Finding | Verdict | Why |
| --- | --- | --- | --- | --- |
| encode/httpx#3670 | `code/await-sync-file` | `await self._fin.close()` on a plain file | right | A real bug that was merged: the file comes from the builtin `open()`, so awaiting its `read()` and `close()` raises TypeError (see this PR's `invalid` note in bugs.json). |
| encode/httpx#3670 | `code/await-sync-file` | `await self._fin.read()` on a plain file | right | A real bug that was merged: the file comes from the builtin `open()`, so awaiting its `read()` and `close()` raises TypeError (see this PR's `invalid` note in bugs.json). |
| encode/httpx#3670 | `code/await-sync-file` | `await self._fin.close()` on a plain file | right | A real bug that was merged: the file comes from the builtin `open()`, so awaiting its `read()` and `close()` raises TypeError (see this PR's `invalid` note in bugs.json). |
| psf/requests#7496 | `deps/new` | New dependency: `cryptography` | right | The PR does add it, to the typing dependency group: accurate, for information. |
| psf/requests#7496 | `deps/new` | New dependency: `PyOpenSSL` | right | The PR does add it, to the typing dependency group: accurate, for information. |
| Textualize/rich#4075 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |

Run on 2026-11-13 on a laptop: the checks give the same answer on any machine. Ruff 0.17.5 and Bandit 1.9.4 were installed. The benchmark turns `checks.ruff` on, which is off by default, so the Ruff cases count only with the `ruff` extra and that key. Package registries were asked about each new dependency's name, as the Action does.

## What this run adds

The Ruff check (`stratum_open/checks/ruff.py`): 55 of Ruff's rules for errors in Python code, reported on the lines a PR adds, each with one planted case in [risks.yml](../../planted/risks.yml). The other checks are the ones on `main`. None of the six findings on the merged PRs above is Ruff's: three are the ones in [2026-11-07-checks](../2026-11-07-checks/summary.md), and the three on httpx#3670 come from the `await`-on-a-plain-file check, added after that run.

## Ruff on the merged PRs: no findings, and that shows little

For 181 of the 184 PRs the check read Python outside the tests: 352 files, 5,331 added lines. No rule fired on any of them. That is no false alarm, but it isn't much of a test. Going by their config files, 16 of the 17 projects have Ruff or flake8 set up for their own code (all but rich), most of them with the pyflakes rules (`F`) that are 30 of the check's 55. So much of what they merge has already passed these rules. These PRs can't show how often a rule is right about a mistake. They can only show where a rule fires on code the project chose to keep, because the check ignores `# noqa`.

## Every line instead of the added ones

So [whole_files.py](../../whole_files.py) ran the check's rules, and the candidates left out of it, over the whole text of the same files as if every line were new: 229 files, 152,104 lines. Each place a rule fired is a row in [whole.csv](whole.csv), read and marked.

| Rule | Places | Code that works | Real errors | What was done |
| --- | --- | --- | --- | --- |
| `F821` undefined name | 4 | 3 | 1 | Kept and narrowed: it now reports the real one only |
| `PLE0302` special method with the wrong parameters | 1 | 0 | 1 | Kept |
| `PLE0704` bare `raise` outside an `except` | 3 | 3 | 0 | Left out |
| `B018` expression that does nothing | 30 | 30 | 0 | Left out |
| `B020` loop variable named like what it loops over | 2 | 2 | 0 | Left out |
| `B023` function that reads a loop variable | 12 | 11, and 1 unsure | 0 | Left out |
| `B006`, `B008` defaults built once | 9 | 9 | 0 | Not added |
| The check's other 53 rules | 0 | | | Not measured (see below) |

The two real errors, both merged and both on lines their PR didn't touch:

- **textual, `src/textual/css/styles.py` line 163** (seen in Textualize/textual#6402): an annotation names `ScrollbarVisibility`, which the file never imports. Nothing fails until something reads the annotation.
- **black, `src/blib2to3/pytree.py` line 95** (seen in psf/black#5617): `def __deepcopy__(self)` has no `memo` parameter, so `copy.deepcopy()` of a node raises TypeError.

`F821`'s three false alarms, and what stops each now:

- rich calls `get_ipython()` after probing for it in a `try/except NameError` (`rich/console.py` line 511, `rich/traceback.py` line 211). Ruff is now told the names a host defines: `get_ipython`, `__IPYTHON__` and the `_` of `gettext.install()`. Those two rows are gone from whole.csv for that reason. `_` and `__IPYTHON__` matched nothing in these files: they are there from knowing the pattern, not from this run.
- pip's vendored truststore has `del _api` in a package's `__init__.py`, where `from ._api import ...` binds `_api` too. The check leaves out an undefined name in an `__init__.py` that imports from a submodule of that name.

## Not measured

53 of the 55 rules fired nowhere in these files. For them this run shows two things: each catches its planted case, and each stays quiet on 152,104 lines of code that was already linted. How often they would be right about a real mistake isn't measured here. The planted cases were written by the person who chose the rules, so they show each rule is wired up, not how much it would catch.

Since the merged PRs couldn't judge them, each rule was also tried by hand on working code written to look like its mistake. That found false alarms the PRs never showed. What the kept rules must stay quiet on is now `WORKING`, and the tests around it, in `tests/test_checks_ruff.py`.

- `F822`: a name in `__all__` that the module's own `__getattr__` supplies. Left out when the module defines `__getattr__`.
- `F821`, `F822`: a name the file writes into `globals()`, or sets on its own module. A file that does that gets no undefined names.
- `F821`: an annotation naming a class defined further down, which is fine from Python 3.14 and an error before it. Ruff is told the code is for 3.14, so this isn't reported, for any project.
- `PLE0101`: `return super().__init__()`, which returns None. Left out when the returned call is to an `__init__`.
- `PLE1519` (`@singledispatch` on a method): fires on `@staticmethod` over `@singledispatch`, which works. The rule is left out.
- `PLE0604`, `PLE0605` (what `__all__` may hold): fire on `__all__ = sorted(names)` and `[*names, "c"]`, which work. Both are left out.

One rule is narrowed on judgement, not on a false alarm: `F811` is reported only when a `def` or `class` replaces another, which loses code. A name imported twice, or a function replacing an import, is untidy and nothing more.

## The other candidates left out

- `F504`, `F522`, `F523`: arguments a format string doesn't use. Nothing fails.
- `PLE0116` (`continue` in `finally`): an error only before Python 3.8; Ruff didn't report the case planted for it.
- `PLE1300`: the same finding as `F509`.
- `PLE1310` (`strip` with repeated characters): about what was meant, not about what runs, and it missed the case planted for it, `url.lstrip("https://")`.
- `PLE2502`, `PLE2510` to `PLE2515` (control characters): the hidden-Unicode check covers the dangerous ones, and the rest are legal.
- `F621`: needs an assignment with over 255 targets, so no case could be planted.
- `F703`, `F705`, `PLE0102` to `PLE0114`: Ruff 0.17.5 has no such rules. `PLE0211`, `PLE1141`, `PLE4703` are preview rules.

## What the check doesn't see

- An error the PR causes on a line it doesn't add, such as deleting an import that is still used. Only added lines are reported.
- Names a host defines beyond the three above (Databricks' `spark`, Snakemake's `snakemake`, Salt's `__salt__`) would be reported as undefined. A project's own Ruff `builtins` setting isn't read, since no config is. None of these 17 projects is of that kind, so how often this happens isn't measured.
- A file Ruff can't parse gets no findings at all.

The tests also pass with Ruff 0.16.0, the oldest the `ruff` extra allows.
