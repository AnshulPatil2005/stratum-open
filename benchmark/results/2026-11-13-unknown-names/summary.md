# Checks that need no model

63 planted risks, each a PR adding files with one problem in them, and 184 real merged PRs as they are.

| Planted risk | Caught by its rule |
| --- | --- |
| Secrets and risky code | 20 of 20 |
| Bandit (optional extra) | 3 of 3 |
| GitHub workflows | 7 of 7 |
| Dependencies | 7 of 8 |
| Coding-agent files | 3 of 3 |
| Hidden Unicode | 3 of 3 |
| Names their module doesn't define (off by default) | 17 of 19 |
| **All** | **60 of 63** |

Missed: `dep-jellyfish` (found: deps/missing, deps/new); `name-third-party` (found: nothing); `name-stdlib-unlisted-module` (found: nothing).

Controls, files a rule must leave alone: 19 of 19 left alone.

On the merged PRs: 6 finding(s) on 3 of 184 PRs. Marked: 6 right, 0 wrong (false alarms), 0 unsure.

| PR | Rule | Finding | Verdict | Why |
| --- | --- | --- | --- | --- |
| encode/httpx#3670 | `code/await-sync-file` | `await self._fin.close()` on a plain file | right | In src/ahttpx/_streams.py the PR sets `self._fin = open(value, 'rb')`, the builtin, and then awaits its `read()` and `close()`, which raises TypeError: a real bug in the merged PR. Another rule's finding, the same as on its own earlier run. |
| encode/httpx#3670 | `code/await-sync-file` | `await self._fin.read()` on a plain file | right | In src/ahttpx/_streams.py the PR sets `self._fin = open(value, 'rb')`, the builtin, and then awaits its `read()` and `close()`, which raises TypeError: a real bug in the merged PR. Another rule's finding, the same as on its own earlier run. |
| encode/httpx#3670 | `code/await-sync-file` | `await self._fin.close()` on a plain file | right | In src/ahttpx/_streams.py the PR sets `self._fin = open(value, 'rb')`, the builtin, and then awaits its `read()` and `close()`, which raises TypeError: a real bug in the merged PR. Another rule's finding, the same as on its own earlier run. |
| psf/requests#7496 | `deps/new` | New dependency: `cryptography` | right | The PR does add it, to the typing dependency group: accurate, for information. |
| psf/requests#7496 | `deps/new` | New dependency: `PyOpenSSL` | right | The PR does add it, to the typing dependency group: accurate, for information. |
| Textualize/rich#4075 | `paths/always-human` | Protected path changed | right | The PR does change a workflow file, which the default policy sends to a person: accurate. |

Run on 2026-11-13 on a laptop (Windows, Python 3.11.14), with `--policy benchmark/planted/variants/unknown-names.yml`, which turns on `checks.unknown_names` (off by default), and `--repos`, a folder with a clone of each of the 17 projects. Bandit 1.9.4 was installed. Package registries were asked about each new dependency's name, as the Action does. Nothing from the clones was run or imported.

**Planted.** 19 new cases import or call a name its module doesn't define, and 17 were caught:

- 11 in the repository's own modules: a near-miss import (`normalize_path` for `normalise_path`), a module attribute, a relative import, a package's `__init__`, a dotted chain, a module the PR itself adds, a `src/` package read from the tests, a script beside its helper, an import inside a function, a module that binds other names in every branch, and a package behind star imports.
- 6 in the standard library: `os.path.makedirs`, `json.load_string`, `from shutil import copy_tree`, Python 2's `string.letters`, `urllib.parse.urlescape` and `logging.get_logger`.
- The 2 misses are out of the rule's reach and kept as misses: `requests.get_json` (a package from outside the repository) and `os.getcwdu` (`os` takes its names from C, so they can't be listed from its source).

19 controls are code the rule can't be sure about, one for each reason it gives, and all 19 were left alone: a module with `__getattr__`; a star import in the module, and in the importing file; names set through `globals()`, through `sys.modules`, and from another file; a name bound in one branch of an `if` or a `try`; a package re-exporting from its submodules; `TYPE_CHECKING` blocks; a package that is in two places; a namespace package; a compiled module; a stub-only module; a module the build writes; the module's name bound again as a variable; an attribute of something that isn't a module; a guarded line; names from other Python versions and platforms; and the project's own `json.py`.

**The 184 merged PRs: no finding from this rule.** Each PR was read against its project as it was just before the PR merged: the first parent of its merge commit, checked out in a clone. (GitHub's GraphQL API was asked once per project for the merge commits; the clones came over git.) The base commit of each PR is in [names.json](names.json), with what the check looked up.

- These PRs add lines to 523 Python files. 501 were read. 21 were skipped because the PR's diff doesn't apply to that commit as it is: in 13 the default branch had changed the file too since the PR branched, and 8 are one PR whose base already held its lines. 1 doesn't parse.
- On the added lines the check looked up 291 imported names and 626 module attributes. It decided 310 of them (153 imports, 157 attributes), all defined. 93 were in modules whose names can't be listed, and 514 were outside what it reads: other packages, standard-library modules not on its list, and imports it couldn't pin to one file.
- So the rule stays out of the way of PRs like these. With nothing wrong on those lines, this says nothing about how often a finding is right.

The six findings in the table above are other rules', each read again: the three `code/await-sync-file` ones are a real bug (`self._fin = open(value, 'rb')`, then `await self._fin.read()`), the other three are the same as on 2026-11-07.

**Every line of the 17 projects, as if a PR had just added it** ([source.csv](source.csv)). Merged PRs add few imports, so the check was also run over all 8,912 Python files of each project's default branch (commits in names.json), tests and examples included. 27 files of black's test data don't parse and were skipped.

- It looked up 51,302 imported names and 56,199 module attributes, and decided 38,759 of them: 26,699 imports (52%) and 12,060 attributes (21%). 6,839 of those were in the standard library.
- 8,452 were in modules whose names can't be listed, and 60,290 outside what it reads (most attributes are of other packages, or of `os`, `sys` and `typing`).
- **Four findings, all four right, no false alarm.** Each is a name that really isn't there, in code nobody runs:
  - Textualize/textual `src/textual/css/tokenize.py:343`: `from textual.markup import _parse`, in the file's `if __name__ == "__main__":` block. `markup.py` has no `_parse`, so running the module as a script raises ImportError.
  - pallets/flask `tests/test_apps/subdomaintestmodule/__init__.py:1`: `from flask import Module`. Flask dropped `Module` for blueprints long ago. No test imports the file.
  - pydantic/pydantic `tests/benchmarks/corpus/google-genai/google/genai/_gaos/utils/unions.py:23` and `:44`: two imports from `.serializers` inside `parse_open_union`. The frozen corpus (merged in #14137) keeps a `serializers.py` cut down to three functions, so the function raises ImportError when it gets there.
- No standard-library name was reported. The review ran on Python 3.11 and these projects name Pythons up to 3.15, so a name newer than the runner had to come from the list: `copy.replace`, new in 3.13, is one they use.

**One false alarm on the way, and its fix.** An earlier run of the same sweep had a fifth finding: scrapy/scrapy `tests/test_core_scraper.py:182`, `scraper_module.Foo` inside `with pytest.raises(AttributeError):`. The name isn't there, and that is what the test checks. Code under `pytest.raises`, `assertRaises`, `suppress` or an `xfail` mark is now left alone (the exception it names is still read), and the sweep was run again.

Two other changes came out of the sweeps without a wrong finding to show for them. The first version treated everything after an `if TYPE_CHECKING:` block as guarded, which hid most of a typed file; now only an early `return` or `raise` under such a test guards what follows. And a module whose `__getattr__` only answers for a few names spelled out in it (how a deprecated name is kept alive) is now read, which is what brought the scrapy line into view.

**Would a made-up name be caught here?** Each of the 38,585 names found defined was asked for again under a name that doesn't exist, in the same place. 36,329 (94%) came back as missing. Of the rest, 2,168 are names in a package of one of the three projects with native sources (pydantic's Rust core, and one C file each in pip's and poetry's test data): a build could add a submodule there, so the rule stays quiet. The other 88 are a standard-library submodule named through its parent (`unittest.mock`, `os.path`), where the made-up name lands on a module that isn't on the list.

**The standard library.** [stdlib_names.py](../../../stratum_open/repo/stdlib_names.py) lists 47 modules, read from CPython's sources for 3.8 to 3.15 by `scripts/stdlib_names.py` (48 when this run was made: `collections.abc` has since been taken off, because from 3.13 it is another name for `_collections_abc`, whose names can't be listed in 3.15. It had no finding, and the counts here include it.) A module is on it only if its names can be listed from its text in every one of those releases, and if everything it exports is among them. That leaves out `os`, `sys`, `re`, `socket`, `typing`, `collections`, `datetime`, `pathlib`, `inspect`, `enum` and every module written in C; `pprint` and `traceback` are out because their newest sources don't parse on Python 3.11, which ran the script. On Python 3.11 on Windows the list has every name the interpreter's own 48 modules have (a test checks this); on other versions and on Linux that test hasn't run yet.

**Not measured.**

- Applications. The 17 projects are well-kept libraries, where a name that doesn't exist is rare (four in 8,912 files, none in code that runs). Settings modules, generated code and plugin systems are where a module gets names in ways this rule may not see.
- Projects that build their Python modules from native sources. The rule only knows that such sources are there, and then never reports a name missing from a package. Whether that is enough for a project like NumPy wasn't tried.
- How often the mistake is made in real PRs. The planted cases stand in for it.
- Another package adding a name to a standard-library module (`logging.TRACE` is left alone as a constant; a lower-case name would be reported).

**What the rule costs.** It walks the checkout once per PR that adds Python lines, then parses the changed files and the modules they import from: 0.03 s on pydantic (3,156 Python files). Only when it has something to report does it read every Python file, to see that nothing sets the name from outside: 0.6 s there.
