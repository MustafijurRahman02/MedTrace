"""Check the database connection and show what is in it.   python -m app.dbcheck [--init]"""
import sys

from .db import DatabaseUnavailable, backend_name, connect, init_db

TABLES = ["roles", "users", "medicines", "batches", "supply_events", "temperature_logs",
          "verification_requests", "alerts", "audit_logs"]


def main() -> int:
    print(f"Backend: {backend_name()}")
    try:
        if "--init" in sys.argv:
            init_db()
            print("Schema created / verified (tables, view v_drug_safety_status, trigger, role rows).")
        conn = connect()
    except DatabaseUnavailable as e:
        print("FAILED:", e)
        return 1
    try:
        if backend_name() == "azure-sql":
            print("Server:", conn.execute("SELECT @@VERSION").fetchone()[0].splitlines()[0])
            print("Database:", conn.execute("SELECT DB_NAME()").fetchone()[0])
        print("Connection OK.\nTable row counts:")
        for t in TABLES:
            try:
                print(f"  {t:<24}{conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
            except Exception:
                print(f"  {t:<24}(missing - run:  python -m app.dbcheck --init)")
                return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
