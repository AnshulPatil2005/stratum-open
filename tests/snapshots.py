"""Compare text with a file under tests/snapshots/, so a change to a prompt or
to the public API shows up as a diff in review. Run the tests with
STRATUM_UPDATE_SNAPSHOTS=1 to rewrite the files after a change you meant."""

from __future__ import annotations

import os
from pathlib import Path

FOLDER = Path(__file__).parent / "snapshots"


def check(name: str, text: str) -> None:
    path = FOLDER / name
    if os.environ.get("STRATUM_UPDATE_SNAPSHOTS"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return
    assert path.exists(), f"No snapshot {name}. Run with STRATUM_UPDATE_SNAPSHOTS=1 to create it."
    expected = path.read_text(encoding="utf-8")
    assert text == expected, (
        f"{name} changed. If the change is intended, run the tests with STRATUM_UPDATE_SNAPSHOTS=1 "
        "and commit the new snapshot, so the diff shows in review."
    )
