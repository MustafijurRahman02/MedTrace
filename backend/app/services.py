"""Business logic: verification, risk rules, batch/event/temperature writes, CSV import."""
import csv
import io
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from .db import IntegrityError, audit, now_iso, storage_dir

ACTIVE = ("OPEN", "UNDER_REVIEW", "ESCALATED")
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{5,39}$")
ISO = "%Y-%m-%dT%H:%M:%SZ"
DISCLAIMER = ("Academic prototype. A matching record is not proof that a medicine "
              "is genuine and this is not a certified authentication service.")


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail


# ---------- time helpers ----------
def norm_ts(dt: datetime, allow_future: bool = False) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    if dt.year < 2000:
        raise ApiError(400, "Timestamp is too far in the past")
    if not allow_future and dt > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ApiError(400, "Timestamp cannot be in the future")
    return dt.strftime(ISO)


def ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).strftime(ISO)


# ---------- alerts ----------
def create_alert_once(conn, *, batch_id, code, alert_type, severity, reason):
    """Create an alert unless an active one of the same type exists for that batch/code."""
    marks = ",".join("?" * len(ACTIVE))
    if batch_id is not None:
        dup = conn.execute(f"SELECT 1 FROM alerts WHERE batch_id=? AND alert_type=? AND status IN ({marks})",
                           (batch_id, alert_type, *ACTIVE)).fetchone()
    else:
        dup = conn.execute(f"SELECT 1 FROM alerts WHERE batch_id IS NULL AND verification_code=? "
                           f"AND alert_type=? AND status IN ({marks})", (code, alert_type, *ACTIVE)).fetchone()
    if dup:
        return None
    cur = conn.execute("INSERT INTO alerts (batch_id, verification_code, alert_type, severity, reason, created_at)"
                       " VALUES (?,?,?,?,?,?)", (batch_id, code, alert_type, severity, reason, now_iso()))
    audit(conn, None, "AUTO_ALERT", "alert", cur.lastrowid, {"rule": alert_type})
    return cur.lastrowid


# ---------- status + reasons ----------
def status_reasons(r) -> list:
    out = []
    if r["batch_status"] == "RECALLED":
        out.append("This batch has been marked as recalled.")
    elif r["batch_status"] == "QUARANTINED":
        out.append("This batch has been placed in quarantine.")
    if r["expiry_date"] < datetime.now(timezone.utc).strftime("%Y-%m-%d"):
        out.append(f"The recorded expiry date ({r['expiry_date']}) has passed.")
    if r["excursion_count"]:
        rng = f"{r['min_temp']:g} to {r['max_temp']:g} C"
        note = "" if r["active_alerts"] else " (reviewed)"
        out.append(f"{r['excursion_count']} of {r['reading_count']} temperature readings are outside {rng}{note}.")
    if r["requires_cold_chain"] and r["reading_count"] == 0:
        out.append("No temperature evidence is recorded for this cold-chain product.")
    if r["event_count"] < 2:
        out.append("The supply journey is incomplete: only registration is recorded.")
    if r["active_alerts"]:
        out.append(f"{r['active_alerts']} open alert(s) are waiting for review.")
    if not out:
        out.append("Registration, journey and temperature records are consistent with the configured limits.")
    return out


def batch_public(r) -> dict:
    return {
        "brand_name": r["brand_name"], "generic_name": r["generic_name"],
        "manufacturer_name": r["manufacturer_name"], "batch_number": r["batch_number"],
        "manufacture_date": r["manufacture_date"], "expiry_date": r["expiry_date"],
        "requires_cold_chain": bool(r["requires_cold_chain"]),
        "min_temp": r["min_temp"], "max_temp": r["max_temp"],
    }


def summary(r) -> dict:
    return {k: r[k] for k in ("event_count", "reading_count", "excursion_count", "active_alerts")}


HEADLINES = {
    "UNKNOWN_CODE": "This code is not registered",
    "FLAGGED": "Flagged for review",
    "NEEDS_REVIEW": "Needs review",
    "RECORDS_CONSISTENT": "Records are consistent",
}


# ---------- verification ----------
def new_code(conn) -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    for _ in range(20):
        code = "MT-" + "".join(secrets.choice(alphabet) for _ in range(4)) + "-" + \
               "".join(secrets.choice(alphabet) for _ in range(4))
        if not conn.execute("SELECT 1 FROM batches WHERE verification_code=?", (code,)).fetchone():
            return code
    raise ApiError(500, "Could not generate a unique code")


def verify_code(conn, code: str, user_id, location, claimed_batch) -> dict:
    row = conn.execute("SELECT * FROM v_drug_safety_status WHERE verification_code=?", (code,)).fetchone()
    # One transaction: the request row, any alerts and the audit entry commit or roll back together.
    with conn:
        if row is None:
            result, reason = "UNKNOWN", "No registered batch matches this code. It may be mistyped or not a genuine MedTrace code."
        elif claimed_batch and claimed_batch.lower() != row["batch_number"].lower():
            result, reason = "CONFLICT", (f"The batch number you entered ({claimed_batch}) does not match "
                                         "the registered record for this code.")
        else:
            result, reason = "REGISTERED", None
        rid = conn.execute(
            "INSERT INTO verification_requests (batch_id, verification_code_submitted, requester_user_id,"
            " location_label, requested_at, result, risk_reason) VALUES (?,?,?,?,?,?,?)",
            (row["batch_id"] if row else None, code, user_id, location, now_iso(), result, reason)).lastrowid
        bid = row["batch_id"] if row else None
        if result == "UNKNOWN":
            create_alert_once(conn, batch_id=None, code=code, alert_type="UNKNOWN_CODE",
                              severity="MEDIUM", reason=f"Unregistered code {code} was submitted for verification.")
        elif result == "CONFLICT":
            create_alert_once(conn, batch_id=bid, code=code, alert_type="CONFLICTING_RECORD",
                              severity="HIGH", reason=reason)
        if row is not None:
            n = conn.execute("SELECT COUNT(*) FROM verification_requests WHERE verification_code_submitted=?"
                             " AND requested_at>=?", (code, ago(minutes=10))).fetchone()[0]
            if n > 5:
                create_alert_once(conn, batch_id=bid, code=code, alert_type="REPEATED_SCAN", severity="MEDIUM",
                                  reason=f"This code was scanned {n} times within 10 minutes.")
            if location:
                other = conn.execute(
                    "SELECT MIN(location_label) FROM verification_requests WHERE verification_code_submitted=?"
                    " AND requested_at>=? AND request_id<>? AND location_label IS NOT NULL"
                    " AND lower(location_label)<>lower(?)", (code, ago(minutes=30), rid, location)).fetchone()
                if other and other[0] is not None:
                    create_alert_once(conn, batch_id=bid, code=code, alert_type="LOCATION_ANOMALY", severity="HIGH",
                                      reason=f"Scanned at '{location}' and at '{other[0]}' within 30 minutes.")
        audit(conn, user_id, "VERIFY", "verification_request", rid, {"result": result})
    if row is None:
        return {"result": "UNKNOWN", "status": "UNKNOWN_CODE", "headline": HEADLINES["UNKNOWN_CODE"],
                "reasons": [reason], "batch": None, "summary": None, "disclaimer": DISCLAIMER}
    row = conn.execute("SELECT * FROM v_drug_safety_status WHERE batch_id=?", (bid,)).fetchone()
    status = "FLAGGED" if result == "CONFLICT" else row["safety_status"]
    reasons = status_reasons(row)
    if result == "CONFLICT":
        reasons.insert(0, reason)
    return {"result": result, "status": status, "headline": HEADLINES[status], "reasons": reasons,
            "batch": batch_public(row), "summary": summary(row), "batch_id": bid, "disclaimer": DISCLAIMER}


# ---------- writes ----------
def create_batch(conn, user_id, d) -> dict:
    med = conn.execute("SELECT * FROM medicines WHERE medicine_id=?", (d.medicine_id,)).fetchone()
    if not med:
        raise ApiError(404, "Medicine not found")
    if med["requires_cold_chain"] and d.min_temp is None and med["default_min_temp"] is None:
        raise ApiError(400, "Cold-chain batches need temperature limits")
    try:
        with conn:
            code = new_code(conn)
            bid = conn.execute(
                "INSERT INTO batches (medicine_id, batch_number, manufacture_date, expiry_date, verification_code,"
                " min_temp, max_temp, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (d.medicine_id, d.batch_number, d.manufacture_date.isoformat(), d.expiry_date.isoformat(), code,
                 d.min_temp, d.max_temp, user_id, now_iso())).lastrowid
            conn.execute("INSERT INTO supply_events (batch_id, actor_user_id, event_type, location_label, event_time)"
                         " VALUES (?,?,?,?,?)", (bid, user_id, "REGISTERED", d.location_label, now_iso()))
            audit(conn, user_id, "CREATE_BATCH", "batch", bid, {"batch_number": d.batch_number})
    except IntegrityError as e:
        dup = "UNIQUE" in str(e).upper() or "duplicate key" in str(e).lower()
        raise ApiError(409 if dup else 400, "That batch number already exists for this medicine"
                       if dup else "Batch values violate a database constraint")
    return {"batch_id": bid, "verification_code": code}


def add_event(conn, user_id, batch_id, d) -> int:
    b = conn.execute("SELECT status FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
    if not b:
        raise ApiError(404, "Batch not found")
    ts = norm_ts(d.event_time) if d.event_time else now_iso()
    with conn:
        eid = conn.execute("INSERT INTO supply_events (batch_id, actor_user_id, event_type, location_label,"
                           " event_time, notes) VALUES (?,?,?,?,?,?)",
                           (batch_id, user_id, d.event_type, d.location_label, ts, d.notes)).lastrowid
        audit(conn, user_id, "ADD_EVENT", "batch", batch_id, {"event_id": eid, "type": d.event_type})
    return eid


def parse_csv(text: str) -> list:
    reader = csv.DictReader(io.StringIO(text.strip()))
    if not reader.fieldnames:
        raise ApiError(400, "CSV is empty")
    cols = {c.strip().lower(): c for c in reader.fieldnames if c}
    tcol = cols.get("temperature") or cols.get("temperature_value")
    rcol = cols.get("recorded_at")
    if not tcol or not rcol:
        raise ApiError(400, "CSV needs a header row with: recorded_at,temperature")
    out, errors = [], []
    for i, row in enumerate(reader, start=2):
        try:
            when = datetime.fromisoformat((row[rcol] or "").strip().replace("Z", "+00:00"))
            val = float((row[tcol] or "").strip())
            if not -90 <= val <= 80:
                raise ValueError("temperature out of range")
            out.append((norm_ts(when), val))
        except (ValueError, TypeError, ApiError) as e:
            errors.append(f"row {i}: {getattr(e, 'detail', e)}")
        if len(out) + len(errors) > 5000:
            raise ApiError(400, "CSV is limited to 5000 rows")
    if errors:
        raise ApiError(400, "Invalid CSV - " + "; ".join(errors[:5]))
    if not out:
        raise ApiError(400, "CSV has no data rows")
    return out


def add_temperature_logs(conn, user_id, batch_id, d) -> dict:
    if not conn.execute("SELECT 1 FROM batches WHERE batch_id=?", (batch_id,)).fetchone():
        raise ApiError(404, "Batch not found")
    if d.source_event_id and not conn.execute(
            "SELECT 1 FROM supply_events WHERE event_id=? AND batch_id=?", (d.source_event_id, batch_id)).fetchone():
        raise ApiError(400, "source_event_id does not belong to this batch")
    file_ref, source = None, d.source_type
    if d.csv_text:
        rows, source = parse_csv(d.csv_text), "CSV_IMPORT"
        file_ref = f"csv/batch{batch_id}_{uuid.uuid4().hex[:12]}.csv"   # server-generated name only
        path = storage_dir() / file_ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(d.csv_text, encoding="utf-8")
    else:
        rows = [(norm_ts(r.recorded_at), r.temperature_value) for r in d.readings]
    with conn:
        conn.executemany(
            "INSERT INTO temperature_logs (batch_id, source_event_id, temperature_value, recorded_at, source_type,"
            " file_reference) VALUES (?,?,?,?,?,?)",
            [(batch_id, d.source_event_id, v, t, source, file_ref) for t, v in rows])
        audit(conn, user_id, "ADD_TEMP_LOGS", "batch", batch_id,
              {"count": len(rows), "source": source, "file": file_ref, "filename": d.filename})
    exc = conn.execute("SELECT excursion_count, safety_status FROM v_drug_safety_status WHERE batch_id=?",
                       (batch_id,)).fetchone()
    return {"inserted": len(rows), "excursion_count": exc["excursion_count"], "safety_status": exc["safety_status"],
            "file_reference": file_ref}
