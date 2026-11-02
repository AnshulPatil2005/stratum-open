"""DATA.md's "Stored fields" section lists every column the GitHub App's
database has. These tests keep the page and the schema in step both ways: a new
column must be documented, and a documented column must still exist."""

import re
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from stratum_open.storage import SQLiteStorage

DATA_PAGE = Path(__file__).parent.parent / "DATA.md"
SECTION = "## Stored fields"
# A field is a list item or table row that starts with `table.column`, so the
# descriptions can still mention policy keys like `review.log_prompts`.
_FIELD = re.compile(r"^\s*[-*|]\s*`(\w+)\.(\w+)`", re.MULTILINE)


@pytest.fixture(scope="module")
def stored(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("data-page") / "stratum.db")
    SQLiteStorage(path).close()
    with closing(sqlite3.connect(path)) as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        return {f"{table}.{row[1]}" for table in tables for row in db.execute(f"PRAGMA table_info({table})")}


@pytest.fixture(scope="module")
def documented():
    text = DATA_PAGE.read_text(encoding="utf-8")
    assert SECTION in text, f'DATA.md has no "{SECTION}" section. Add one listing every database column as "- `table.column`: what it holds."'
    section = text.split(SECTION, 1)[1].split("\n## ", 1)[0]
    return {f"{table}.{column}" for table, column in _FIELD.findall(section)}


def test_every_stored_column_is_on_the_data_page(stored, documented):
    missing = sorted(stored - documented)
    assert not missing, (
        f'DATA.md\'s "Stored fields" section doesn\'t list {len(missing)} column(s) the database has. '
        "Add a line for each under its table, saying in plain words what it holds:\n"
        + "\n".join(f"- `{field}`: ..." for field in missing)
    )


def test_every_column_on_the_data_page_is_stored(stored, documented):
    gone = sorted(documented - stored)
    assert not gone, (
        f'DATA.md\'s "Stored fields" section lists {len(gone)} column(s) the database doesn\'t have. '
        "Remove them, or fix their names:\n" + "\n".join(f"- `{field}`" for field in gone)
    )
