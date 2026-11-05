"""MedTrace API (FastAPI). Run: uvicorn app.main:app --reload  (from the backend/ folder)."""
import io
import os
from contextlib import asynccontextmanager
from pathlib import Path

import segno
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import services as svc
from .db import ROOT, IntegrityError, audit, backend_name, get_db, init_db, now_iso
from .schemas import (AlertPatch, BatchIn, BatchStatusIn, ConcernIn, EventIn, LoginIn, MedicineIn,
                      TempLogsIn, UserIn, UserPatch)
from .security import (DUMMY_HASH, client_ip, create_token, hash_password, login_limiter, optional_user,
                       require, verify_limiter, verify_password)

FRONTEND = ROOT / "frontend"
PAGE = " OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"  # T-SQL paging; args are always [..., offset, limit]


@asynccontextmanager
async def lifespan(app):
    init_db()
    if os.environ.get("MEDTRACE_SEED", "0") == "1":  # demo data is OFF by default: the database stays empty
        from .seed import seed_if_empty
        seed_if_empty()
    yield


app = FastAPI(title="MedTrace API", version="1.0", lifespan=lifespan,
              description="Academic prototype - synthetic data only.")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    if not request.url.path.startswith(("/docs", "/redoc", "/openapi")):
        resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if os.environ.get("MEDTRACE_HTTPS") == "1":
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


@app.exception_handler(svc.ApiError)
async def api_error(_, e: svc.ApiError):
    return JSONResponse({"detail": e.detail}, status_code=e.status)


@app.exception_handler(RequestValidationError)
async def bad_input(_, e: RequestValidationError):
    msgs = []
    for err in e.errors()[:5]:
        loc = ".".join(str(p) for p in err["loc"] if p not in ("body", "query", "path"))
        msgs.append(f"{loc}: {err['msg'].removeprefix('Value error, ')}" if loc else err["msg"])
    return JSONResponse({"detail": "; ".join(msgs) or "Invalid input"}, status_code=400)


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


# ------------------------------------------------------------------ auth
@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, conn=Depends(get_db)):
    email = body.email.lower()
    login_limiter.check(f"{client_ip(request)}|{email}")
    u = conn.execute("SELECT u.*, r.role_name FROM users u JOIN roles r ON r.role_id=u.role_id"
                     " WHERE u.email=?", (email,)).fetchone()
    ok = verify_password(body.password, u["password_hash"] if u else DUMMY_HASH)
    if not (u and ok and u["is_active"]):
        with conn:
            audit(conn, u["user_id"] if u else None, "LOGIN_FAILED", "user", u["user_id"] if u else None,
                  {"email": email[:120]})
        raise HTTPException(401, "Incorrect email or password")
    with conn:
        audit(conn, u["user_id"], "LOGIN", "user", u["user_id"])
    return {"token": create_token(u["user_id"]),
            "user": {"id": u["user_id"], "name": u["name"], "email": u["email"], "role": u["role_name"]}}


@app.get("/api/me")
def me(user=Depends(require())):
    return user


# ------------------------------------------------------------------ public verification
@app.get("/api/verify/{code}")
def verify(code: str, request: Request, location: str | None = Query(None, max_length=80),
           batch_number: str | None = Query(None, max_length=40),
           conn=Depends(get_db), user=Depends(optional_user)):
    verify_limiter.check(client_ip(request))
    code = code.strip().upper()
    if not svc.CODE_RE.match(code):
        raise HTTPException(400, "Code format is invalid: use 6-40 letters, digits or dashes")
    loc = (location or "").strip() or None
    claimed = (batch_number or "").strip() or None
    out = svc.verify_code(conn, code, user["user_id"] if user else None, loc, claimed)
    if not user:
        out.pop("batch_id", None)          # limited, non-sensitive public response
    return out


@app.get("/api/qr/{code}")
def qr(code: str, request: Request, conn=Depends(get_db)):
    code = code.upper()
    if not svc.CODE_RE.match(code) or not conn.execute(
            "SELECT 1 FROM batches WHERE verification_code=?", (code,)).fetchone():
        raise HTTPException(404, "Code not found")
    url = f"{str(request.base_url).rstrip('/')}/#/verify/{code}"
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=8, border=2, dark="#0e2a2f", xmldecl=False)
    return Response(buf.getvalue(), media_type="image/svg+xml")


# ------------------------------------------------------------------ medicines & batches
@app.get("/api/medicines")
def list_medicines(conn=Depends(get_db), user=Depends(require())):
    return rows(conn.execute("SELECT * FROM medicines ORDER BY brand_name"))


@app.post("/api/medicines", status_code=201)
def create_medicine(d: MedicineIn, conn=Depends(get_db), user=Depends(require("admin", "registrar"))):
    try:
        with conn:
            mid = conn.execute(
                "INSERT INTO medicines (generic_name, brand_name, manufacturer_name, requires_cold_chain,"
                " default_min_temp, default_max_temp) VALUES (?,?,?,?,?,?)",
                (d.generic_name, d.brand_name, d.manufacturer_name, int(d.requires_cold_chain),
                 d.default_min_temp, d.default_max_temp)).lastrowid
            audit(conn, user["user_id"], "CREATE_MEDICINE", "medicine", mid, {"brand": d.brand_name})
    except IntegrityError:
        raise HTTPException(409, "This brand and manufacturer combination already exists")
    return {"medicine_id": mid}


@app.get("/api/batches")
def list_batches(q: str | None = Query(None, max_length=60), status: str | None = Query(None, max_length=20),
                 limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                 conn=Depends(get_db), user=Depends(require())):
    sql, args = "SELECT * FROM v_drug_safety_status WHERE 1=1", []
    if q:
        sql += " AND (batch_number LIKE ? OR brand_name LIKE ? OR generic_name LIKE ? OR verification_code LIKE ?)"
        args += [f"%{q}%"] * 4
    if status:
        sql += " AND safety_status = ?"
        args.append(status.upper())
    total = conn.execute(f"SELECT COUNT(*) FROM ({sql}) AS q", args).fetchone()[0]
    items = rows(conn.execute(sql + " ORDER BY batch_id DESC" + PAGE, [*args, offset, limit]))
    return {"total": total, "items": items}


@app.post("/api/batches", status_code=201)
def create_batch(d: BatchIn, request: Request, conn=Depends(get_db),
                 user=Depends(require("admin", "registrar"))):
    out = svc.create_batch(conn, user["user_id"], d)
    out["qr_url"] = f"/api/qr/{out['verification_code']}"
    return out


def get_batch_row(conn, batch_id: int):
    r = conn.execute("SELECT * FROM v_drug_safety_status WHERE batch_id=?", (batch_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Batch not found")
    return r


@app.get("/api/batches/{batch_id}")
def batch_detail(batch_id: int, conn=Depends(get_db), user=Depends(require()),
                 event_limit: int = Query(100, ge=1, le=500), event_offset: int = Query(0, ge=0)):
    r = get_batch_row(conn, batch_id)
    events = rows(conn.execute(
        "SELECT e.event_id, e.event_type, e.location_label, e.event_time, e.notes, u.name AS actor_name,"
        " ro.role_name AS actor_role FROM supply_events e JOIN users u ON u.user_id=e.actor_user_id"
        " JOIN roles ro ON ro.role_id=u.role_id WHERE e.batch_id=? ORDER BY e.event_time, e.event_id"
        + PAGE, (batch_id, event_offset, event_limit)))
    logs = rows(conn.execute(
        "SELECT log_id, temperature_value, recorded_at, source_type FROM temperature_logs WHERE batch_id=?"
        " ORDER BY recorded_at, log_id OFFSET 0 ROWS FETCH NEXT 500 ROWS ONLY", (batch_id,)))
    stats = conn.execute("SELECT MIN(temperature_value) lo, MAX(temperature_value) hi, AVG(temperature_value) av"
                         " FROM temperature_logs WHERE batch_id=?", (batch_id,)).fetchone()
    alerts = rows(conn.execute("SELECT alert_id, alert_type, severity, reason, status, created_at FROM alerts"
                               " WHERE batch_id=? ORDER BY alert_id DESC OFFSET 0 ROWS FETCH NEXT 50 ROWS ONLY", (batch_id,)))
    scans = conn.execute("SELECT COUNT(*) FROM verification_requests WHERE batch_id=?", (batch_id,)).fetchone()[0]
    return {"batch": dict(r), "reasons": svc.status_reasons(r), "events": events, "temperature_logs": logs,
            "temperature_stats": {"min": stats["lo"], "max": stats["hi"],
                                  "avg": None if stats["av"] is None else round(stats["av"], 2)},
            "alerts": alerts, "verification_count": scans, "qr_url": f"/api/qr/{r['verification_code']}"}


@app.patch("/api/batches/{batch_id}")
def set_batch_status(batch_id: int, d: BatchStatusIn, conn=Depends(get_db),
                     user=Depends(require("admin", "registrar"))):
    get_batch_row(conn, batch_id)
    with conn:
        conn.execute("UPDATE batches SET status=? WHERE batch_id=?", (d.status, batch_id))
        audit(conn, user["user_id"], "SET_BATCH_STATUS", "batch", batch_id, {"status": d.status})
    return {"batch_id": batch_id, "status": d.status}


@app.post("/api/batches/{batch_id}/events", status_code=201)
def add_event(batch_id: int, d: EventIn, conn=Depends(get_db),
              user=Depends(require("admin", "distributor", "pharmacy"))):
    return {"event_id": svc.add_event(conn, user["user_id"], batch_id, d)}


@app.post("/api/batches/{batch_id}/temperature-logs", status_code=201)
def add_temperature_logs(batch_id: int, d: TempLogsIn, conn=Depends(get_db),
                         user=Depends(require("admin", "distributor"))):
    return svc.add_temperature_logs(conn, user["user_id"], batch_id, d)


@app.get("/api/batches/{batch_id}/temperature-logs")
def get_temperature_logs(batch_id: int, limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0),
                         conn=Depends(get_db), user=Depends(require())):
    get_batch_row(conn, batch_id)
    return rows(conn.execute("SELECT * FROM temperature_logs WHERE batch_id=? ORDER BY recorded_at, log_id"
                             + PAGE, (batch_id, offset, limit)))


@app.get("/api/batches/{batch_id}/safety-status")
def safety_status(batch_id: int, conn=Depends(get_db), user=Depends(require())):
    r = get_batch_row(conn, batch_id)
    return {"batch_id": batch_id, "status": r["safety_status"], "headline": svc.HEADLINES[r["safety_status"]],
            "reasons": svc.status_reasons(r), "summary": svc.summary(r),
            "limits": {"min": r["min_temp"], "max": r["max_temp"]}}


@app.post("/api/batches/{batch_id}/report", status_code=201)
def report_concern(batch_id: int, d: ConcernIn, conn=Depends(get_db),
                   user=Depends(require("pharmacy", "admin"))):
    get_batch_row(conn, batch_id)
    with conn:
        cur = conn.execute("INSERT INTO alerts (batch_id, alert_type, severity, reason, created_at)"
                           " VALUES (?,?,?,?,?)", (batch_id, "PHARMACY_CONCERN", "MEDIUM",
                                                   f"Reported by {user['name']}: {d.note}", now_iso()))
        audit(conn, user["user_id"], "REPORT_CONCERN", "alert", cur.lastrowid, {"batch_id": batch_id})
    return {"alert_id": cur.lastrowid}


# ------------------------------------------------------------------ alerts / review
@app.get("/api/alerts")
def list_alerts(status: str | None = Query(None, max_length=20), severity: str | None = Query(None, max_length=20),
                limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                conn=Depends(get_db), user=Depends(require("reviewer", "admin"))):
    sql, args = ("SELECT a.*, b.batch_number, u.name AS reviewer_name FROM alerts a"
                 " LEFT JOIN batches b ON b.batch_id=a.batch_id LEFT JOIN users u ON u.user_id=a.reviewed_by"
                 " WHERE 1=1"), []
    if status:
        sql += " AND a.status=?"; args.append(status.upper())
    if severity:
        sql += " AND a.severity=?"; args.append(severity.upper())
    sql += (" ORDER BY CASE a.status WHEN 'RESOLVED' THEN 1 ELSE 0 END,"
            " CASE a.severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END,"
            " a.created_at DESC" + PAGE)
    return rows(conn.execute(sql, [*args, offset, limit]))


@app.patch("/api/alerts/{alert_id}")
def review_alert(alert_id: int, d: AlertPatch, conn=Depends(get_db), user=Depends(require("reviewer", "admin"))):
    a = conn.execute("SELECT status FROM alerts WHERE alert_id=?", (alert_id,)).fetchone()
    if not a:
        raise HTTPException(404, "Alert not found")
    if a["status"] == "RESOLVED":
        raise HTTPException(409, "This alert is already resolved")
    if d.status in ("RESOLVED", "ESCALATED") and (not d.note or len(d.note) < 3):
        raise HTTPException(400, "A reason is required to resolve or escalate an alert")
    with conn:
        conn.execute("UPDATE alerts SET status=?, review_note=COALESCE(?, review_note), reviewed_by=?,"
                     " reviewed_at=? WHERE alert_id=?", (d.status, d.note, user["user_id"], now_iso(), alert_id))
        audit(conn, user["user_id"], "REVIEW_ALERT", "alert", alert_id, {"from": a["status"], "to": d.status})
    return {"alert_id": alert_id, "status": d.status}


# ------------------------------------------------------------------ analytics / audit
@app.get("/api/analytics/summary")
def analytics(conn=Depends(get_db), user=Depends(require("admin", "reviewer"))):
    q = lambda sql, *a: rows(conn.execute(sql, a))
    act = "('OPEN','UNDER_REVIEW','ESCALATED')"
    lim = "COALESCE(b.min_temp, m.default_min_temp)", "COALESCE(b.max_temp, m.default_max_temp)"
    return {
        "total_batches": conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0],
        "batches_by_status": q("SELECT safety_status AS label, COUNT(*) AS value FROM v_drug_safety_status"
                               " GROUP BY safety_status"),
        "excursions_by_medicine": q("SELECT brand_name AS label, SUM(excursion_count) AS value FROM"
                                    " v_drug_safety_status GROUP BY medicine_id, brand_name HAVING SUM(excursion_count) > 0"),
        "excursions_by_day": q(f"SELECT SUBSTRING(t.recorded_at,1,10) AS label, COUNT(*) AS value FROM temperature_logs t"
                               f" JOIN batches b ON b.batch_id=t.batch_id JOIN medicines m ON m.medicine_id=b.medicine_id"
                               f" WHERE t.temperature_value < {lim[0]} OR t.temperature_value > {lim[1]}"
                               f" GROUP BY SUBSTRING(t.recorded_at,1,10) ORDER BY label DESC OFFSET 0 ROWS FETCH NEXT 14 ROWS ONLY"),
        "verifications_by_result": q("SELECT result AS label, COUNT(*) AS value FROM verification_requests"
                                     " GROUP BY result"),
        "verifications_by_day": q("SELECT SUBSTRING(requested_at,1,10) AS label, COUNT(*) AS value FROM"
                                  " verification_requests GROUP BY SUBSTRING(requested_at,1,10) ORDER BY label DESC"
                                  " OFFSET 0 ROWS FETCH NEXT 14 ROWS ONLY"),
        "open_alerts_by_severity": q(f"SELECT severity AS label, COUNT(*) AS value FROM alerts WHERE status IN {act}"
                                     f" GROUP BY severity"),
        "open_alerts_by_age": q(f"SELECT label, COUNT(*) AS value FROM (SELECT CASE WHEN created_at >= ? THEN 'Under 1 day'"
                                f" WHEN created_at >= ? THEN '1 to 7 days' ELSE 'Over 7 days' END AS label FROM alerts"
                                f" WHERE status IN {act}) AS x GROUP BY label", svc.ago(days=1), svc.ago(days=7)),
        "alerts_by_status": q("SELECT status AS label, COUNT(*) AS value FROM alerts GROUP BY status"),
        "evidence": dict(conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN event_count >= 3 AND (requires_cold_chain = 0 OR reading_count > 0)"
            " THEN 1 ELSE 0 END), 0) AS complete,"
            " COALESCE(SUM(CASE WHEN event_count >= 3 AND (requires_cold_chain = 0 OR reading_count > 0)"
            " THEN 0 ELSE 1 END), 0) AS missing FROM v_drug_safety_status").fetchone()),
    }


@app.get("/api/analytics/export")
def analytics_export(conn=Depends(get_db), user=Depends(require("admin", "reviewer"))):
    data = analytics(conn, user)
    lines = ["section,label,value", f"overview,total_batches,{data['total_batches']}"]
    for k, v in data.items():
        if isinstance(v, list):
            lines += [f"{k},{r['label']},{r['value']}" for r in v]
    lines += [f"evidence,{k},{v or 0}" for k, v in data["evidence"].items()]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=medtrace_summary.csv"})


@app.get("/api/audit")
def audit_trail(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), action: str | None = Query(None, max_length=40),
                conn=Depends(get_db), user=Depends(require("admin", "reviewer"))):
    sql, args = ("SELECT l.audit_id, l.action, l.entity_type, l.entity_id, l.[timestamp] AS [timestamp], l.safe_metadata,"
                 " u.name AS actor_name FROM audit_logs l LEFT JOIN users u ON u.user_id=l.actor_user_id WHERE 1=1"), []
    if action:
        sql += " AND l.action=?"; args.append(action.upper())
    return rows(conn.execute(sql + " ORDER BY l.audit_id DESC" + PAGE, [*args, offset, limit]))


# ------------------------------------------------------------------ users (admin)
@app.get("/api/users")
def list_users(conn=Depends(get_db), user=Depends(require("admin"))):
    return rows(conn.execute("SELECT u.user_id, u.name, u.email, r.role_name AS role, u.is_active, u.created_at"
                             " FROM users u JOIN roles r ON r.role_id=u.role_id ORDER BY u.user_id"))


@app.post("/api/users", status_code=201)
def create_user(d: UserIn, conn=Depends(get_db), user=Depends(require("admin"))):
    role = conn.execute("SELECT role_id FROM roles WHERE role_name=?", (d.role,)).fetchone()
    try:
        with conn:
            uid = conn.execute("INSERT INTO users (name, email, password_hash, role_id, created_at) VALUES (?,?,?,?,?)",
                               (d.name, d.email, hash_password(d.password), role["role_id"], now_iso())).lastrowid
            audit(conn, user["user_id"], "CREATE_USER", "user", uid, {"role": d.role})
    except IntegrityError:
        raise HTTPException(409, "A user with this email already exists")
    return {"user_id": uid}


@app.patch("/api/users/{user_id}")
def update_user(user_id: int, d: UserPatch, conn=Depends(get_db), user=Depends(require("admin"))):
    if user_id == user["user_id"]:
        raise HTTPException(400, "You cannot change your own active status")
    with conn:
        n = conn.execute("UPDATE users SET is_active=? WHERE user_id=?", (int(d.is_active), user_id)).rowcount
        if not n:
            raise HTTPException(404, "User not found")
        audit(conn, user["user_id"], "SET_USER_ACTIVE", "user", user_id, {"is_active": d.is_active})
    return {"user_id": user_id, "is_active": d.is_active}


# ------------------------------------------------------------------ health
@app.get("/api/health")
def health(conn=Depends(get_db)):
    """Open /api/health in the browser to confirm the app can reach the database."""
    conn.execute("SELECT 1").fetchone()
    return {"status": "ok", "database": backend_name()}


# ------------------------------------------------------------------ frontend
@app.get("/api/{rest:path}")
def api_not_found(rest: str):
    raise HTTPException(404, "Unknown API endpoint")


app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(FRONTEND / "index.html")
