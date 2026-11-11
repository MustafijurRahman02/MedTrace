# MedTrace design notes

## 1. Normalization (target 3NF)
* **Users** hold identity only; the role is a foreign key to **Roles** (no repeated role text).
* **Medicines** hold product facts once (names, manufacturer, default limits). **Batches** reference a medicine and add only
  batch facts (number, dates, code, optional limit override). Product details are never copied into batches or events.
* **Supply_Events**, **Temperature_Logs**, **Verification_Requests** and **Alerts** reference the batch by key and store only
  facts about that single event/reading/scan/flag. Actor names are reached through a join to Users, never duplicated.
* Derived values (status, excursion count) are not stored; the view `v_drug_safety_status` computes them, so they cannot go stale.
* Deliberate choice: `manufacturer_name` stays in Medicines because it has no attributes of its own in this prototype.
  A separate Manufacturers table would be the next step if manufacturers gained addresses or licences.

## 2. Constraints
PRIMARY KEY on every table; FOREIGN KEYs enforced (`PRAGMA foreign_keys=ON` per connection); UNIQUE `verification_code`,
`users.email`, `(medicine_id, batch_number)`; CHECKs on dates (expiry after manufacture), temperature ranges (min < max,
-90..80 C), and all status/type columns (controlled values). Cold-chain medicines must have a range.

## 3. Trade-off (from the proposal)
All temperature readings are kept in the relational table for simple joins and consistency; raw uploaded CSV files are kept in
file storage and referenced by `file_reference`. This costs database size at high volume but keeps the prototype queryable.

## 4. Threat model and mitigations (implemented)
| Threat | Mitigation in this code |
|---|---|
| Unauthorized role access | `require(*roles)` dependency on every protected route; deny by default; role read from the DB per request (so deactivation applies immediately). Tests assert 401/403. |
| SQL injection | Only parameterized SQL; strict Pydantic validation; code format regex. Tests send injection strings as data. |
| XSS | Frontend builds DOM with text nodes (no `innerHTML`); CSP `script-src 'self'`; `nosniff`, `X-Frame-Options: DENY`. |
| Credential/secret exposure | PBKDF2-SHA256 (200k iterations, per-user salt); `SECRET_KEY` from environment; `.env` git-ignored; generic login error; dummy-hash timing equalisation. |
| Brute force / abuse | Login limit 10/5 min per IP+email; public verify limit 60/min per IP; unknown-code alerts de-duplicated. |
| Fake or copied QR code | Unique random codes; scan history, repeated-scan and location rules; UI states that a QR match is not proof of authenticity. |
| Tampered or missing logs | Missing evidence yields NEEDS_REVIEW; every write is audited; CSV is all-or-nothing; server-generated file names (no path traversal). |
| Database exposure | Deployment note: restrict network access, TLS, least-privilege DB account (relevant when moving to Azure SQL). |

## 5. Security checklist
- [x] HTTPS-ready (HSTS header with `MEDTRACE_HTTPS=1`; TLS provided by the host)
- [x] Passwords hashed - [x] Server-side RBAC - [x] Secrets outside the repository
- [x] Input validation and parameterized SQL - [x] Audit logging (users, batches, events, logs, reviews, scans, auto-alerts)
- [x] Demo data only - [ ] Database network restriction (set up in the cloud) - [ ] Dependency review (run `pip list --outdated` / `pip-audit` before submission)

## 6. Data classification (DP-900)
| Category | Examples here | Where |
|---|---|---|
| Structured | users, roles, medicines, batches, events, readings, scans, alerts | relational tables |
| Semi-structured | `audit_logs.safe_metadata` (JSON text) | JSON column |
| Unstructured | uploaded CSV evidence | `storage/csv/` (Blob Storage in the target design) |
| Analytics output | `/api/analytics/summary`, CSV export | aggregated SQL |
