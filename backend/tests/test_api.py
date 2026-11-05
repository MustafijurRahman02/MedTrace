from datetime import datetime, timedelta, timezone

import pytest

from app import services as svc
from app.db import connect


def ts(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------- authentication ----------------
def test_login_success_and_failure(client):
    assert client.post("/api/auth/login", json={"email": "admin@medtrace.demo", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "nobody@x.com", "password": "x"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "ADMIN@medtrace.demo", "password": "Admin@123"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin"


def test_login_sql_injection_is_just_data(client):
    r = client.post("/api/auth/login", json={"email": "' OR '1'='1' --", "password": "' OR '1'='1"})
    assert r.status_code == 401


def test_passwords_are_hashed():
    conn = connect()
    h = conn.execute("SELECT password_hash FROM users WHERE email='admin@medtrace.demo'").fetchone()[0]
    assert h.startswith("pbkdf2_sha256$") and "Admin@123" not in h


def test_login_rate_limit(client):
    codes = [client.post("/api/auth/login", json={"email": "rl@x.com", "password": "bad"}).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and 429 in codes[10:]


# ---------------- RBAC ----------------
def test_requires_token(client):
    assert client.get("/api/batches").status_code == 401
    assert client.get("/api/alerts").status_code == 401
    assert client.get("/api/batches", headers={"Authorization": "Bearer garbage"}).status_code == 401


@pytest.mark.parametrize("role,method,url,body", [
    ("pharmacy", "post", "/api/batches", {}),
    ("pharmacy", "post", "/api/medicines", {}),
    ("distributor", "get", "/api/alerts", None),
    ("pharmacy", "get", "/api/analytics/summary", None),
    ("registrar", "get", "/api/audit", None),
    ("reviewer", "get", "/api/users", None),
    ("reviewer", "post", "/api/batches/1/temperature-logs", {}),
    ("registrar", "post", "/api/batches/1/events", {}),
])
def test_role_forbidden(client, auth, role, method, url, body):
    r = getattr(client, method)(url, headers=auth[role], **({"json": body} if body is not None else {}))
    assert r.status_code == 403


# ---------------- verification ----------------
def test_verify_valid_code_public_is_limited(client):
    r = client.get("/api/verify/mt-demo-0002")      # lower-case input is normalised
    j = r.json()
    assert r.status_code == 200 and j["result"] == "REGISTERED" and j["status"] == "RECORDS_CONSISTENT"
    assert j["batch"]["batch_number"] == "DEMO-BATCH-002"
    assert "batch_id" not in j and "actor_name" not in str(j)
    assert "not proof" in j["disclaimer"]


def test_verify_flagged_batch_explains_why(client):
    j = client.get("/api/verify/MT-DEMO-0001").json()
    assert j["status"] == "FLAGGED"
    assert any("outside 2 to 8 C" in x for x in j["reasons"])


def test_missing_evidence_is_never_green(client):
    j = client.get("/api/verify/MT-DEMO-0003").json()
    assert j["status"] == "NEEDS_REVIEW"
    assert any("No temperature evidence" in x for x in j["reasons"])


def test_expired_batch_flagged(client):
    j = client.get("/api/verify/MT-DEMO-0005").json()
    assert j["status"] == "FLAGGED" and any("expiry" in x for x in j["reasons"])


def test_unknown_code_creates_single_alert(client, auth):
    for _ in range(2):
        j = client.get("/api/verify/MT-NOPE-1234").json()
        assert j["status"] == "UNKNOWN_CODE" and j["batch"] is None
    alerts = client.get("/api/alerts", headers=auth["reviewer"]).json()
    assert len([a for a in alerts if a["verification_code"] == "MT-NOPE-1234"]) == 1


@pytest.mark.parametrize("bad", ["abc", "x" * 60, "MT DEMO", "MT-DEMO-0001';DROP TABLE batches;--", "<script>alert(1)</script>"])
def test_malformed_code_is_400(client, bad):
    assert client.get(f"/api/verify/{bad}").status_code in (400, 404)
    assert client.get("/api/verify/MT-DEMO-0002").status_code == 200   # tables intact


def test_conflicting_batch_number(client, auth):
    j = client.get("/api/verify/MT-DEMO-0004", params={"batch_number": "WRONG-999"}).json()
    assert j["result"] == "CONFLICT" and j["status"] == "FLAGGED"
    r = client.get("/api/verify/MT-DEMO-0004", params={"batch_number": "'; DROP TABLE batches;--"})
    assert r.status_code == 200 and r.json()["result"] == "CONFLICT"
    types = [a["alert_type"] for a in client.get("/api/alerts", headers=auth["reviewer"]).json()]
    assert "CONFLICTING_RECORD" in types


def test_repeated_scan_and_location_anomaly(client, auth):
    for _ in range(7):
        client.get("/api/verify/MT-DEMO-0002", params={"location": "Mumbai"})
    client.get("/api/verify/MT-DEMO-0002", params={"location": "Chennai"})
    types = {a["alert_type"] for a in client.get("/api/alerts", headers=auth["reviewer"]).json()}
    assert {"REPEATED_SCAN", "LOCATION_ANOMALY"} <= types


def test_verification_transaction_rolls_back(monkeypatch):
    conn = connect()
    before = conn.execute("SELECT COUNT(*) FROM verification_requests").fetchone()[0]

    def boom(*a, **k):
        raise RuntimeError("simulated failure")
    monkeypatch.setattr(svc, "audit", boom)
    with pytest.raises(RuntimeError):
        svc.verify_code(conn, "MT-DEMO-0004", None, None, None)
    after = conn.execute("SELECT COUNT(*) FROM verification_requests").fetchone()[0]
    assert before == after


# ---------------- database ----------------
def test_db_constraints():
    import sqlite3
    conn = connect()
    with pytest.raises(sqlite3.IntegrityError):   # FK enforced
        conn.execute("INSERT INTO supply_events (batch_id, actor_user_id, event_type, location_label, event_time)"
                     " VALUES (9999, 1, 'DISPATCHED', 'x', '2026-01-01T00:00:00Z')")
    with pytest.raises(sqlite3.IntegrityError):   # unique verification code
        conn.execute("INSERT INTO batches (medicine_id, batch_number, manufacture_date, expiry_date, verification_code)"
                     " VALUES (3, 'X-1', '2025-01-01', '2026-01-01', 'MT-DEMO-0001')")
    with pytest.raises(sqlite3.IntegrityError):   # date check
        conn.execute("INSERT INTO batches (medicine_id, batch_number, manufacture_date, expiry_date, verification_code)"
                     " VALUES (3, 'X-2', '2026-01-01', '2025-01-01', 'MT-ZZZZ-0001')")
    conn.rollback()


# ---------------- full workflow ----------------
def test_end_to_end_workflow(client, auth):
    reg, dist, rev = auth["registrar"], auth["distributor"], auth["reviewer"]
    med = client.post("/api/medicines", headers=reg, json={
        "generic_name": "E2E Test Drug", "brand_name": "E2E-Brand", "manufacturer_name": "Test Mfg",
        "requires_cold_chain": True, "default_min_temp": 2, "default_max_temp": 8}).json()["medicine_id"]
    b = client.post("/api/batches", headers=reg, json={
        "medicine_id": med, "batch_number": "E2E-001", "manufacture_date": "2026-01-01",
        "expiry_date": "2028-01-01"})
    assert b.status_code == 201
    bid, code = b.json()["batch_id"], b.json()["verification_code"]
    assert client.post("/api/batches", headers=reg, json={
        "medicine_id": med, "batch_number": "E2E-001", "manufacture_date": "2026-01-01",
        "expiry_date": "2028-01-01"}).status_code == 409            # duplicate
    assert client.get(f"/api/qr/{code}").headers["content-type"] == "image/svg+xml"

    assert client.get(f"/api/verify/{code}").json()["status"] == "NEEDS_REVIEW"   # only registered

    for et, loc in (("DISPATCHED", "Plant"), ("RECEIVED_AT_DISTRIBUTION", "Depot"), ("RECEIVED_AT_PHARMACY", "Shop")):
        assert client.post(f"/api/batches/{bid}/events", headers=dist,
                           json={"event_type": et, "location_label": loc}).status_code == 201
    csv_text = f"recorded_at,temperature\n{ts(6)},4.5\n{ts(4)},5.0\n{ts(2)},12.5\n"
    r = client.post(f"/api/batches/{bid}/temperature-logs", headers=dist, json={"csv_text": csv_text, "filename": "x.csv"})
    assert r.status_code == 201 and r.json()["inserted"] == 3 and r.json()["excursion_count"] == 1
    assert r.json()["file_reference"].startswith("csv/")

    j = client.get(f"/api/verify/{code}").json()
    assert j["status"] == "FLAGGED"
    detail = client.get(f"/api/batches/{bid}", headers=auth["pharmacy"]).json()
    assert [e["event_type"] for e in detail["events"]] == ["REGISTERED", "DISPATCHED", "RECEIVED_AT_DISTRIBUTION", "RECEIVED_AT_PHARMACY"]
    assert detail["alerts"][0]["alert_type"] == "TEMP_EXCURSION" and detail["alerts"][0]["severity"] == "HIGH"

    aid = detail["alerts"][0]["alert_id"]
    assert client.patch(f"/api/alerts/{aid}", headers=rev, json={"status": "RESOLVED"}).status_code == 400  # reason required
    assert client.patch(f"/api/alerts/{aid}", headers=rev, json={"status": "UNDER_REVIEW"}).status_code == 200
    assert client.patch(f"/api/alerts/{aid}", headers=rev, json={"status": "RESOLVED", "note": "Sensor fault confirmed"}).status_code == 200
    assert client.patch(f"/api/alerts/{aid}", headers=rev, json={"status": "ESCALATED", "note": "again"}).status_code == 409
    assert client.get(f"/api/verify/{code}").json()["status"] == "RECORDS_CONSISTENT"

    actions = [a["action"] for a in client.get("/api/audit", headers=rev).json()]
    assert "REVIEW_ALERT" in actions and "ADD_TEMP_LOGS" in actions and "CREATE_BATCH" in actions

    c = client.post(f"/api/batches/{bid}/report", headers=auth["pharmacy"], json={"note": "Packaging looks tampered"})
    assert c.status_code == 201
    assert client.get(f"/api/batches/{bid}/safety-status", headers=auth["pharmacy"]).json()["status"] == "NEEDS_REVIEW"


# ---------------- input validation ----------------
def test_validation_errors_are_400(client, auth):
    dist = auth["distributor"]
    assert client.post("/api/batches/1/temperature-logs", headers=dist, json={}).status_code == 400
    assert client.post("/api/batches/1/temperature-logs", headers=dist,
                       json={"readings": [{"recorded_at": ts(1), "temperature_value": 500}]}).status_code == 400
    assert client.post("/api/batches/1/events", headers=dist,
                       json={"event_type": "TELEPORTED", "location_label": "x"}).status_code == 400
    future = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert client.post("/api/batches/1/events", headers=dist,
                       json={"event_type": "DISPATCHED", "location_label": "x", "event_time": future}).status_code == 400
    assert client.post("/api/batches/99999/events", headers=dist,
                       json={"event_type": "DISPATCHED", "location_label": "x"}).status_code == 404
    assert client.post("/api/batches", headers=auth["registrar"], json={
        "medicine_id": 1, "batch_number": "<b>x</b>", "manufacture_date": "2026-01-01", "expiry_date": "2027-01-01"}).status_code == 400


def test_bad_csv_inserts_nothing(client, auth):
    conn = connect()
    before = conn.execute("SELECT COUNT(*) FROM temperature_logs").fetchone()[0]
    bad = f"recorded_at,temperature\n{ts(3)},5\nnot-a-date,6\n{ts(1)},abc\n"
    r = client.post("/api/batches/2/temperature-logs", headers=auth["distributor"], json={"csv_text": bad})
    assert r.status_code == 400 and "row 3" in r.json()["detail"]
    assert conn.execute("SELECT COUNT(*) FROM temperature_logs").fetchone()[0] == before


def test_user_management(client, auth):
    r = client.post("/api/users", headers=auth["admin"], json={
        "name": "New Pharmacist", "email": "new@medtrace.demo", "password": "Passw0rd99", "role": "pharmacy"})
    assert r.status_code == 201
    assert client.post("/api/users", headers=auth["admin"], json={
        "name": "Dup", "email": "new@medtrace.demo", "password": "Passw0rd99", "role": "pharmacy"}).status_code == 409
    assert client.post("/api/users", headers=auth["admin"], json={
        "name": "Weak", "email": "weak@medtrace.demo", "password": "alllettersnodigit", "role": "pharmacy"}).status_code == 400
    uid = r.json()["user_id"]
    tok = client.post("/api/auth/login", json={"email": "new@medtrace.demo", "password": "Passw0rd99"}).json()["token"]
    assert client.get("/api/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert client.patch(f"/api/users/{uid}", headers=auth["admin"], json={"is_active": False}).status_code == 200
    assert client.get("/api/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401   # deactivated


# ---------------- analytics ----------------
def test_analytics_matches_database(client, auth):
    j = client.get("/api/analytics/summary", headers=auth["reviewer"]).json()
    conn = connect()
    assert j["total_batches"] == conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
    assert sum(x["value"] for x in j["batches_by_status"]) == j["total_batches"]
    assert sum(x["value"] for x in j["verifications_by_result"]) == conn.execute(
        "SELECT COUNT(*) FROM verification_requests").fetchone()[0]
    assert sum(x["value"] for x in j["open_alerts_by_severity"]) == conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE status IN ('OPEN','UNDER_REVIEW','ESCALATED')").fetchone()[0]
    assert j["evidence"]["complete"] + j["evidence"]["missing"] == j["total_batches"]
    csv = client.get("/api/analytics/export", headers=auth["admin"])
    assert csv.status_code == 200 and "total_batches" in csv.text


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["x-content-type-options"] == "nosniff" and "script-src 'self'" in r.headers["content-security-policy"]
