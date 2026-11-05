"""Database access for MedTrace.

Two interchangeable back ends behind one tiny API (execute / executemany / commit / rollback / `with conn:`):
  * Azure SQL Database (SQL Server)  - used whenever AZURE_SQL_* settings are present (production / your project)
  * SQLite file                      - fallback for quick local tests only
All application SQL is written in T-SQL style (OFFSET ... FETCH paging, [timestamp] quoting); the SQLite
adapter translates the paging clause so the same statements run on both.
"""
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config  # noqa: F401  (loads the .env file before anything reads settings)

_PYODBC_ERROR = ""
try:  # pyodbc is only needed for Azure SQL
    import pyodbc
except ImportError as _e:  # not installed, or installed but the system ODBC library is missing
    pyodbc, _PYODBC_ERROR = None, str(_e)

ROOT = Path(__file__).resolve().parents[2]
SQLITE_SCHEMA = ROOT / "database" / "schema.sql"
AZURE_SCHEMA = ROOT / "database" / "azure_sql_schema.sql"

ROLES = {
    "admin": "Manage users, products, batches and thresholds",
    "registrar": "Manufacturer / batch registrar (demo role)",
    "distributor": "Records handovers and temperature logs",
    "pharmacy": "Verifies batches and reports concerns",
    "reviewer": "Reviews flagged events (regulator prototype role)",
}

IntegrityError = (sqlite3.IntegrityError,) + ((pyodbc.IntegrityError,) if pyodbc else ())


class DatabaseUnavailable(RuntimeError):
    """Raised with a human-readable message when the database cannot be reached."""


def use_azure() -> bool:
    return bool(os.environ.get("AZURE_SQL_CONNECTION_STRING") or os.environ.get("AZURE_SQL_SERVER"))


def backend_name() -> str:
    return "azure-sql" if use_azure() else "sqlite"


def storage_dir() -> Path:
    p = Path(os.environ.get("MEDTRACE_STORAGE", str(ROOT / "storage")))
    p.mkdir(parents=True, exist_ok=True)
    return p


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- SQLite adapter (tests / quick local use)
_PAGE = re.compile(r"OFFSET\s+(\?|\d+)\s+ROWS\s+FETCH\s+NEXT\s+(\?|\d+)\s+ROWS\s+ONLY\s*$", re.I)


def _to_sqlite(sql: str, params):
    """Translate the T-SQL paging clause to LIMIT/OFFSET (SQLite has no OFFSET..FETCH)."""
    m = _PAGE.search(sql)
    if not m:
        return sql, params
    off, lim = m.groups()
    sql = sql[:m.start()] + f"LIMIT {lim} OFFSET {off}"
    if off == "?" and lim == "?":
        params = list(params)
        params[-2], params[-1] = params[-1], params[-2]
    return sql, params


class _SqliteConn(sqlite3.Connection):
    def execute(self, sql, params=()):
        sql, params = _to_sqlite(sql, params)
        return super().execute(sql, params)


def _sqlite_path() -> str:
    return os.environ.get("MEDTRACE_DB", str(ROOT / "backend" / "medtrace.db"))


def _connect_sqlite():
    conn = sqlite3.connect(_sqlite_path(), check_same_thread=False, timeout=15, factory=_SqliteConn)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------- Azure SQL adapter (pyodbc)
class Row:
    """Result row usable like sqlite3.Row: row['col'], row[0], dict(row), row.keys()."""
    __slots__ = ("_names", "_vals")

    def __init__(self, names, vals):
        self._names, self._vals = names, tuple(vals)

    def __getitem__(self, k):
        return self._vals[self._names.index(k)] if isinstance(k, str) else self._vals[k]

    def keys(self):
        return list(self._names)

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)


class _Cursor:
    def __init__(self, cur, lastrowid=None):
        self._cur, self.lastrowid = cur, lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    def _names(self):
        return [d[0] for d in (self._cur.description or [])]

    def fetchone(self):
        r = self._cur.fetchone()
        return None if r is None else Row(self._names(), r)

    def fetchall(self):
        names = self._names()
        return [Row(names, r) for r in self._cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class _AzureConn:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=()):
        cur = self.raw.cursor()
        params = tuple(params or ())
        if sql.lstrip()[:6].upper() == "INSERT":
            # SCOPE_IDENTITY() only works inside the same batch, so it is sent together with the INSERT.
            cur.execute("SET NOCOUNT ON; " + sql + "; SELECT CAST(SCOPE_IDENTITY() AS BIGINT); SET NOCOUNT OFF;", params)
            r = cur.fetchone()
            return _Cursor(cur, int(r[0]) if r and r[0] is not None else None)
        cur.execute(sql, params)
        return _Cursor(cur)

    def executemany(self, sql, seq):
        cur = self.raw.cursor()
        cur.executemany(sql, [tuple(p) for p in seq])
        return _Cursor(cur)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        self.rollback() if et else self.commit()
        return False


def azure_connection_string() -> str:
    cs = os.environ.get("AZURE_SQL_CONNECTION_STRING")
    if cs:
        return cs
    need = [k for k in ("AZURE_SQL_SERVER", "AZURE_SQL_DATABASE", "AZURE_SQL_USER", "AZURE_SQL_PASSWORD") if not os.environ.get(k)]
    if need:
        raise DatabaseUnavailable("Missing settings: " + ", ".join(need) + ". See docs/AZURE_SETUP.md (.env file).")
    pwd = "{" + os.environ["AZURE_SQL_PASSWORD"].replace("}", "}}") + "}"
    driver = os.environ.get("AZURE_SQL_DRIVER", "ODBC Driver 18 for SQL Server")
    return (f"Driver={{{driver}}};Server=tcp:{os.environ['AZURE_SQL_SERVER']},1433;"
            f"Database={os.environ['AZURE_SQL_DATABASE']};Uid={os.environ['AZURE_SQL_USER']};Pwd={pwd};"
            "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;")


def _friendly(msg: str) -> str:
    if "40615" in msg or "not allowed to access the server" in msg:
        return ("Azure SQL refused this computer's IP address. Add your IP in the Azure portal: "
                "SQL server > Networking > Firewall rules (see docs/AZURE_SETUP.md, step 5).")
    if "18456" in msg or "Login failed" in msg:
        return "Azure SQL rejected the user name or password. Check AZURE_SQL_USER / AZURE_SQL_PASSWORD."
    if "IM002" in msg or "Data source name not found" in msg:
        return ("The ODBC driver was not found. Install 'ODBC Driver 18 for SQL Server' "
                "(docs/AZURE_SETUP.md, step 3) or set AZURE_SQL_DRIVER to the driver you have.")
    if "4060" in msg or "Cannot open database" in msg:
        return "The database name is wrong or the database does not exist. Check AZURE_SQL_DATABASE."
    if "40613" in msg or "not currently available" in msg:
        return "The Azure SQL database is paused / resuming (serverless). Wait about a minute and try again."
    return "Could not connect to Azure SQL: " + msg[:300]


def _connect_azure(autocommit=False, attempts=2, wait=6):
    if pyodbc is None:
        raise DatabaseUnavailable(
            "The Python package 'pyodbc' could not be loaded (" + (_PYODBC_ERROR or "not installed") + "). "
            "Run 'pip install -r backend/requirements.txt' and install the Microsoft ODBC Driver 18 for SQL Server "
            "(on Linux this also installs unixODBC). See docs/AZURE_SETUP.md, step 3.")
    cs, last = azure_connection_string(), ""
    for i in range(attempts):
        try:
            return _AzureConn(pyodbc.connect(cs, autocommit=autocommit))
        except pyodbc.Error as e:
            last = str(e)
            resuming = "40613" in last or "not currently available" in last or "HYT00" in last
            if not resuming or i == attempts - 1:
                break
            print(f"[MedTrace] Database is waking up, retrying in {wait}s ...", file=sys.stderr)
            time.sleep(wait)
    raise DatabaseUnavailable(_friendly(last))


def connect(autocommit=False, attempts=2):
    return _connect_azure(autocommit, attempts) if use_azure() else _connect_sqlite()


def get_db():
    """FastAPI dependency: one connection per request."""
    from fastapi import HTTPException
    try:
        conn = connect()
    except DatabaseUnavailable as e:
        raise HTTPException(503, str(e))
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------- schema creation (tables only - no data)
def _run_azure_script(path: Path) -> None:
    conn = _connect_azure(autocommit=True, attempts=6, wait=15)
    try:
        for batch in re.split(r"^\s*GO\s*$", path.read_text(encoding="utf-8"), flags=re.I | re.M):
            if batch.strip():
                conn.raw.cursor().execute(batch)
    finally:
        conn.close()


def init_db() -> None:
    """Create tables, the safety view, the excursion trigger and the 5 fixed role rows. Never inserts demo data."""
    if use_azure():
        _run_azure_script(AZURE_SCHEMA)
        conn = connect()
        try:
            with conn:
                for i, (name, desc) in enumerate(ROLES.items(), start=1):
                    conn.execute("IF NOT EXISTS (SELECT 1 FROM roles WHERE role_name = ?) "
                                 "INSERT INTO roles (role_id, role_name, description) VALUES (?, ?, ?)",
                                 (name, i, name, desc))
        finally:
            conn.close()
        return
    conn = _connect_sqlite()
    try:
        conn.executescript(SQLITE_SCHEMA.read_text(encoding="utf-8"))
        for i, (name, desc) in enumerate(ROLES.items(), start=1):
            conn.execute("INSERT OR IGNORE INTO roles (role_id, role_name, description) VALUES (?,?,?)", (i, name, desc))
        conn.commit()
    finally:
        conn.close()


def audit(conn, actor_id, action, entity_type, entity_id=None, meta=None) -> None:
    """Write an audit row. Call inside the caller's transaction so both commit together."""
    conn.execute(
        "INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, [timestamp], safe_metadata)"
        " VALUES (?,?,?,?,?,?)",
        (actor_id, action, entity_type, None if entity_id is None else str(entity_id),
         now_iso(), json.dumps(meta or {}, default=str)),
    )
