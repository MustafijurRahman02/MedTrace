"""Runs last. Every SQL statement the API issued during the test session must be valid T-SQL (Azure SQL)."""
import re

import pytest

sqlglot = pytest.importorskip("sqlglot")
from tests.conftest import SEEN_SQL  # noqa: E402


def test_collected_statements_parse_as_tsql():
    assert len(SEEN_SQL) > 30, "expected the API tests to have run first"
    failures = []
    for s in sorted(SEEN_SQL):
        if s.lstrip().upper().startswith(("PRAGMA", "INSERT OR IGNORE")):  # SQLite-only init branch
            continue
        try:
            sqlglot.parse_one(s, read="tsql")
        except sqlglot.errors.ParseError as e:
            failures.append(f"{s[:90]!r}: {str(e)[:80]}")
    assert not failures, "\n".join(failures)


def test_no_sqlite_only_syntax_in_app_sql():
    bad = re.compile(r"\bLIMIT\b|\bsubstr\(|date\('now'\)|strftime|printf|last_insert_rowid", re.I)
    assert [s[:80] for s in SEEN_SQL if bad.search(s)] == []


def test_azure_schema_parses_as_tsql():
    from app.db import AZURE_SCHEMA
    for batch in re.split(r"^\s*GO\s*$", AZURE_SCHEMA.read_text(), flags=re.I | re.M):
        if batch.strip() and "CREATE OR ALTER TRIGGER" not in batch:  # trigger body checked statement-wise below
            sqlglot.parse(batch, read="tsql")
    body = AZURE_SCHEMA.read_text().split("CREATE OR ALTER TRIGGER")[1]
    body = body[body.index("DECLARE"):body.rindex("END;")]
    for stmt in re.split(r";\s*\n(?=\s*(?:WITH|INSERT|DECLARE)\b)", body):
        sqlglot.parse_one(stmt.strip().rstrip(";"), read="tsql")
