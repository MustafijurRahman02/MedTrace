"""Synthetic demo data (clearly fake). Seeds only when the users table is empty."""
import random
from datetime import date, datetime, timedelta, timezone

from . import services as svc
from .db import audit, connect, init_db, now_iso
from .schemas import BatchIn, EventIn, ReadingIn, TempLogsIn
from .security import hash_password

DEMO_USERS = [
    ("Asha Admin", "admin@medtrace.demo", "Admin@123", "admin"),
    ("Ravi Registrar", "registrar@medtrace.demo", "Registrar@123", "registrar"),
    ("Dev Distributor", "distributor@medtrace.demo", "Distributor@123", "distributor"),
    ("Priya Pharmacy", "pharmacy@medtrace.demo", "Pharmacy@123", "pharmacy"),
    ("Rhea Reviewer", "reviewer@medtrace.demo", "Reviewer@123", "reviewer"),
]


def seed_if_empty() -> bool:
    init_db()
    conn = connect()
    try:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return False
        seed(conn)
        return True
    finally:
        conn.close()


def seed(conn) -> None:
    rng = random.Random(7)
    with conn:
        for name, email, pw, role in DEMO_USERS:
            rid = conn.execute("SELECT role_id FROM roles WHERE role_name=?", (role,)).fetchone()[0]
            conn.execute("INSERT INTO users (name, email, password_hash, role_id, created_at) VALUES (?,?,?,?,?)",
                         (name, email, hash_password(pw), rid, now_iso()))
        # Sample products. 2-8 C is an illustrative demo range, not medical guidance.
        meds = [("Demo Insulin", "DemoInsulin-R", "Sample Biologics Ltd", 1, 2.0, 8.0),
                ("Demo Vaccine", "DemoVax-A", "Sample Biologics Ltd", 1, 2.0, 8.0),
                ("Demo Paracetamol", "DemoPara-500", "Sample Pharma Pvt Ltd", 0, 15.0, 25.0)]
        for m in meds:
            conn.execute("INSERT INTO medicines (generic_name, brand_name, manufacturer_name, requires_cold_chain,"
                         " default_min_temp, default_max_temp) VALUES (?,?,?,?,?,?)", m)
    uid = {r["email"].split("@")[0]: r["user_id"] for r in conn.execute("SELECT user_id, email FROM users")}
    today = date.today()
    now = datetime.now(timezone.utc)

    def batch(med_id, number, mfg, exp, demo_code):
        out = svc.create_batch(conn, uid["registrar"], BatchIn(
            medicine_id=med_id, batch_number=number, manufacture_date=mfg, expiry_date=exp))
        with conn:
            conn.execute("UPDATE batches SET verification_code=? WHERE batch_id=?", (demo_code, out["batch_id"]))
            # registration happens before any handover, so backdate it for a sensible timeline
            conn.execute("UPDATE supply_events SET event_time=? WHERE batch_id=? AND event_type='REGISTERED'",
                         ((now - timedelta(days=6)).strftime(svc.ISO), out["batch_id"]))
        return out["batch_id"]

    def journey(bid, days_ago_start):
        steps = [("DISPATCHED", "Sample Biologics Plant, Pune", "distributor"),
                 ("RECEIVED_AT_DISTRIBUTION", "Central Depot, Delhi", "distributor"),
                 ("RECEIVED_AT_PHARMACY", "City Pharmacy, Delhi", "pharmacy")]
        for i, (etype, loc, who) in enumerate(steps):
            svc.add_event(conn, uid[who], bid, EventIn(
                event_type=etype, location_label=loc, event_time=now - timedelta(days=days_ago_start - i)))

    def readings(bid, days, spike=None):
        pts = []
        for h in range(0, days * 24, 6):
            pts.append(ReadingIn(recorded_at=now - timedelta(hours=days * 24 - h),
                                 temperature_value=round(rng.uniform(3.5, 6.5), 1)))
        if spike is not None:
            pts[len(pts) // 2] = ReadingIn(recorded_at=pts[len(pts) // 2].recorded_at, temperature_value=spike)
        svc.add_temperature_logs(conn, uid["distributor"], bid,
                                 TempLogsIn(readings=pts, source_type="SIMULATED"))

    b1 = batch(1, "DEMO-BATCH-001", today - timedelta(days=60), today + timedelta(days=300), "MT-DEMO-0001")
    journey(b1, 3); readings(b1, 3, spike=9.5)                       # temperature excursion -> flagged
    b2 = batch(2, "DEMO-BATCH-002", today - timedelta(days=45), today + timedelta(days=200), "MT-DEMO-0002")
    journey(b2, 3); readings(b2, 3)                                  # clean cold chain -> consistent
    batch(2, "DEMO-BATCH-003", today - timedelta(days=10), today + timedelta(days=250), "MT-DEMO-0003")
    #   ^ registered only, no evidence -> needs review
    b4 = batch(3, "DEMO-BATCH-004", today - timedelta(days=90), today + timedelta(days=500), "MT-DEMO-0004")
    journey(b4, 3)                                                   # room-temperature product -> consistent
    b5 = batch(3, "DEMO-BATCH-005", date(2024, 1, 1), date(2025, 12, 31), "MT-DEMO-0005")
    journey(b5, 3)                                                   # expired -> flagged

    # A few scans, including an unknown code, so the dashboard and review queue have content.
    svc.verify_code(conn, "MT-DEMO-0002", None, "City Pharmacy, Delhi", None)
    svc.verify_code(conn, "MT-DEMO-0004", None, None, None)
    svc.verify_code(conn, "MT-FAKE-0000", None, None, None)
    with conn:  # backdated scan history for the trend chart
        for d, n in ((5, 2), (4, 3), (3, 1), (2, 4), (1, 3)):
            for _ in range(n):
                conn.execute("INSERT INTO verification_requests (batch_id, verification_code_submitted, location_label,"
                             " requested_at, result) VALUES (2,'MT-DEMO-0002',NULL,?, 'REGISTERED')",
                             ((now - timedelta(days=d, minutes=rng.randint(0, 600))).strftime(svc.ISO),))
        audit(conn, None, "SEED", "system", None, {"note": "synthetic demo data loaded"})


if __name__ == "__main__":
    print("seeded" if seed_if_empty() else "database already has data - nothing changed")
