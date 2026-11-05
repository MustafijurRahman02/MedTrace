import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="medtrace_test_")
os.environ["MEDTRACE_DB"] = str(Path(_tmp) / "test.db")
os.environ["MEDTRACE_STORAGE"] = str(Path(_tmp) / "storage")
os.environ["MEDTRACE_NO_DOTENV"] = "1"   # never read a real .env (could point at your Azure database)
os.environ["SECRET_KEY"] = "test-secret"
os.environ["MEDTRACE_SEED"] = "1"   # tests use demo data on a throw-away SQLite file
for _k in ("AZURE_SQL_SERVER", "AZURE_SQL_CONNECTION_STRING"):
    os.environ.pop(_k, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from app import db as _db  # noqa: E402

# Record every SQL statement the app runs so test_zz_tsql_compat.py can check it is valid T-SQL.
SEEN_SQL: set = set()
_orig_exec, _orig_many = _db._SqliteConn.execute, _db.sqlite3.Connection.executemany
_db._SqliteConn.execute = lambda self, sql, params=(): (SEEN_SQL.add(sql), _orig_exec(self, sql, params))[1]
_db._SqliteConn.executemany = lambda self, sql, seq: (SEEN_SQL.add(sql), _orig_many(self, sql, seq))[1]
from app.main import app  # noqa: E402
from app import security  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_limits():
    security.verify_limiter.hits.clear()
    security.login_limiter.hits.clear()


def _login(client, email, pw):
    r = client.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="session")
def auth(client):
    return {
        "admin": _login(client, "admin@medtrace.demo", "Admin@123"),
        "registrar": _login(client, "registrar@medtrace.demo", "Registrar@123"),
        "distributor": _login(client, "distributor@medtrace.demo", "Distributor@123"),
        "pharmacy": _login(client, "pharmacy@medtrace.demo", "Pharmacy@123"),
        "reviewer": _login(client, "reviewer@medtrace.demo", "Reviewer@123"),
    }
