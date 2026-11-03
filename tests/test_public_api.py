"""The public API, pinned. Stratum imports these names; a change here is a
new minor version and has to be deliberate (see stratum_open/__init__.py)."""

import dataclasses
import inspect

import snapshots

import stratum_open


def describe(name: str) -> str:
    obj = getattr(stratum_open, name)
    if dataclasses.is_dataclass(obj):
        return f"{name}: dataclass(" + ", ".join(f"{f.name}" for f in dataclasses.fields(obj)) + ")"
    if inspect.isclass(obj):
        return f"{name}: class{inspect.signature(obj.__init__)}"
    return f"{name}{inspect.signature(obj)}"


def test_the_public_api_is_pinned():
    names = sorted(n for n in stratum_open.__all__ if n != "__version__")
    snapshots.check("public_api.txt", "\n".join(describe(n) for n in names) + "\n")


def test_everything_exported_exists():
    for name in stratum_open.__all__:
        assert hasattr(stratum_open, name), name
