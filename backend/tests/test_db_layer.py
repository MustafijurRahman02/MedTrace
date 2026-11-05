"""Tests for the SQLite/Azure SQL database layer (Azure side uses a fake pyodbc connection)."""
import pytest

from app import db


# ---- T-SQL paging translation used by the SQLite adapter ------------------------------------------------
def test_paging_with_placeholders_is_swapped():
    sql, p = db._to_sqlite("SELECT * FROM t WHERE a=? ORDER BY id OFFSET ? ROWS FETCH NEXT ? ROWS ONLY", ["x", 40, 20])
    assert sql.endswith("ORDER BY id LIMIT ? OFFSET ?") and p == ["x", 20, 40]


def test_paging_with_literals():
    sql, p = db._to_sqlite("SELECT 1 ORDER BY 1 OFFSET 0 ROWS FETCH NEXT 14 ROWS ONLY", ())
    assert sql.endswith("LIMIT 14 OFFSET 0") and p == ()


def test_other_sql_untouched():
    assert db._to_sqlite("SELECT 1", (1,)) == ("SELECT 1", (1,))


# ---- Azure adapter with a fake pyodbc connection ---------------------------------------------------------
class FakeCursor:
    def __init__(self, log, rows=None, desc=None):
        self.log, self._rows, self.description, self.rowcount = log, rows or [], desc, 1

    def execute(self, sql, params=()):
        self.log.append(("execute", sql, params))
        if "SCOPE_IDENTITY" in sql:
            self._rows, self.description = [(42,)], [("id",)]
        elif sql.startswith("SELECT name"):
            self._rows, self.description = [("Asha", "a@x.io")], [("name",), ("email",)]
        return self

    def executemany(self, sql, seq):
        self.log.append(("executemany", sql, list(seq)))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeRaw:
    def __init__(self):
        self.log, self.commits, self.rollbacks = [], 0, 0

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def test_insert_returns_lastrowid_via_scope_identity():
    raw = FakeRaw(); conn = db._AzureConn(raw)
    cur = conn.execute("INSERT INTO medicines (a) VALUES (?)", ["x"])
    assert cur.lastrowid == 42
    sent = raw.log[0][1]
    assert sent.startswith("SET NOCOUNT ON; INSERT") and "SCOPE_IDENTITY()" in sent and raw.log[0][2] == ("x",)


def test_select_row_supports_name_index_and_dict():
    conn = db._AzureConn(FakeRaw())
    r = conn.execute("SELECT name, email FROM users WHERE id=?", (1,)).fetchone()
    assert r["name"] == "Asha" and r[1] == "a@x.io" and dict(r) == {"name": "Asha", "email": "a@x.io"}


def test_with_block_commits_or_rolls_back():
    raw = FakeRaw()
    with db._AzureConn(raw):
        pass
    assert (raw.commits, raw.rollbacks) == (1, 0)
    with pytest.raises(ValueError):
        with db._AzureConn(raw):
            raise ValueError("boom")
    assert (raw.commits, raw.rollbacks) == (1, 1)


def test_executemany_passes_rows_through():
    raw = FakeRaw(); db._AzureConn(raw).executemany("INSERT INTO t VALUES (?,?)", [(1, 2), (3, 4)])
    assert raw.log[0] == ("executemany", "INSERT INTO t VALUES (?,?)", [(1, 2), (3, 4)])


# ---- configuration -----------------------------------------------------------------------------------------
def test_backend_switches_on_env(monkeypatch):
    monkeypatch.delenv("AZURE_SQL_SERVER", raising=False); monkeypatch.delenv("AZURE_SQL_CONNECTION_STRING", raising=False)
    assert db.backend_name() == "sqlite"
    monkeypatch.setenv("AZURE_SQL_SERVER", "x.database.windows.net")
    assert db.backend_name() == "azure-sql"


def test_connection_string_built_and_password_escaped(monkeypatch):
    monkeypatch.delenv("AZURE_SQL_CONNECTION_STRING", raising=False)
    for k, v in dict(AZURE_SQL_SERVER="s.database.windows.net", AZURE_SQL_DATABASE="medtrace",
                     AZURE_SQL_USER="medadmin", AZURE_SQL_PASSWORD="p;a}ss").items():
        monkeypatch.setenv(k, v)
    cs = db.azure_connection_string()
    assert "Server=tcp:s.database.windows.net,1433" in cs and "Database=medtrace" in cs
    assert "Pwd={p;a}}ss}" in cs and "Encrypt=yes" in cs and "TrustServerCertificate=no" in cs


def test_missing_settings_give_clear_error(monkeypatch):
    monkeypatch.delenv("AZURE_SQL_CONNECTION_STRING", raising=False)
    monkeypatch.setenv("AZURE_SQL_SERVER", "s")
    for k in ("AZURE_SQL_DATABASE", "AZURE_SQL_USER", "AZURE_SQL_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(db.DatabaseUnavailable, match="AZURE_SQL_DATABASE"):
        db.azure_connection_string()


@pytest.mark.parametrize("raw,expect", [
    ("Cannot open server ... Client with IP address '1.2.3.4' is not allowed to access the server (40615)", "firewall"),
    ("Login failed for user 'x'. (18456)", "user name or password"),
    ("[unixODBC][Driver Manager]Data source name not found (IM002)", "ODBC driver"),
    ("Cannot open database \"x\" requested by the login (4060)", "database name"),
    ("Database 'x' on server 's' is not currently available (40613)", "paused"),
])
def test_friendly_errors(raw, expect):
    assert expect.lower() in db._friendly(raw).lower()


def test_azure_schema_file_is_empty_tables_only():
    text = db.AZURE_SCHEMA.read_text()
    assert text.count("CREATE TABLE") == 9 and "v_drug_safety_status" in text and "trg_flag_temperature_excursion" in text
    assert "INSERT INTO dbo.users" not in text and "INSERT INTO dbo.batches" not in text


# ---- .env loader -----------------------------------------------------------------------------------------------
def test_dotenv_parsing(tmp_path, monkeypatch):
    from app import config
    f = tmp_path / ".env"
    f.write_text('# comment\nA_PLAIN=hello   # trailing comment\nA_QUOTED="pa#ss;w}rd !"\nA_SINGLE=\'x y\'\nA_KEEP=real\n', encoding="utf-8")
    for k in ("A_PLAIN", "A_QUOTED", "A_SINGLE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("A_KEEP", "from-environment"); monkeypatch.delenv("MEDTRACE_NO_DOTENV")
    config.load_env(f)
    import os
    assert (os.environ["A_PLAIN"], os.environ["A_QUOTED"], os.environ["A_SINGLE"]) == ("hello", "pa#ss;w}rd !", "x y")
    assert os.environ["A_KEEP"] == "from-environment"          # real environment wins
    for k in ("A_PLAIN", "A_QUOTED", "A_SINGLE"):
        monkeypatch.delenv(k, raising=False)


def test_demo_data_is_off_by_default():
    import re, pathlib
    main = (pathlib.Path(db.__file__).parent / "main.py").read_text()
    assert re.search(r'MEDTRACE_SEED", "0"\)', main)
