"""Create the first administrator in an empty database.

    python -m app.create_admin                       (asks for name, email, password)
    ADMIN_NAME=.. ADMIN_EMAIL=.. ADMIN_PASSWORD=.. python -m app.create_admin   (non-interactive)

After that, sign in at the web page and create the other users from the Users screen.
"""
import getpass
import os
import re
import sys

from .db import IntegrityError, audit, backend_name, connect, init_db, now_iso
from .security import hash_password


def main() -> int:
    name = os.environ.get("ADMIN_NAME") or input("Admin name: ").strip()
    email = (os.environ.get("ADMIN_EMAIL") or input("Admin email: ")).strip().lower()
    pw = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Password (min 8 characters): ")
    if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(pw) < 8:
        print("Please give a name, a valid email and a password of at least 8 characters.")
        return 1
    init_db()
    conn = connect()
    try:
        role = conn.execute("SELECT role_id FROM roles WHERE role_name = ?", ("admin",)).fetchone()
        try:
            with conn:
                uid = conn.execute("INSERT INTO users (name, email, password_hash, role_id, created_at) VALUES (?,?,?,?,?)",
                                   (name, email, hash_password(pw), role["role_id"], now_iso())).lastrowid
                audit(conn, uid, "CREATE_USER", "user", uid, {"role": "admin", "via": "create_admin"})
        except IntegrityError:
            print(f"A user with email {email} already exists.")
            return 1
    finally:
        conn.close()
    print(f"Admin '{email}' created in the {backend_name()} database. You can now sign in.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
