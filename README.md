# MedTrace - cold chain and counterfeit drug verification (academic prototype)

Working full-stack prototype of the MedTrace proposal: QR-linked batch verification, supply-chain journey,
temperature monitoring with excursion detection, rule-based suspicious-activity flags, a reviewer workflow,
role-based access control, an audit trail and an analytics dashboard.

> Synthetic/demo data only. This is not a certified pharmaceutical traceability or medical safety system.
> A matching record is **not** proof that a medicine is genuine.

## Stack
| Layer | Technology |
|---|---|
| Frontend | Plain HTML/CSS/JavaScript single-page app (no build step), served by the API |
| API | Python FastAPI, JWT auth, strict Pydantic validation |
| Database | **Azure SQL Database** (T-SQL: 3NF schema, FKs, CHECKs, indexes, a view, a trigger, transactions). Starts **empty**. SQLite is used by the automated tests only |
| File storage | **Azure Blob Storage** (private container) for uploaded CSV evidence; falls back to the local `storage/` folder when not configured |
| Tests | pytest, 61 tests (unit, API, DB layer, security, integration, analytics, T-SQL compatibility, Azure Blob via the Azurite emulator) |

## Run it with Azure SQL (empty database)
Full walkthrough with screenshots-style detail and troubleshooting: **[docs/AZURE_SETUP.md](docs/AZURE_SETUP.md)**.
The short version:

1. Create an Azure SQL database (free serverless offer is fine) and allow your IP in its firewall.
2. Install Python 3.10+ and **Microsoft ODBC Driver 18 for SQL Server**.
3. `cd backend`, create a virtual environment, `pip install -r requirements.txt`.
4. Copy `.env.example` to `.env` and fill in `AZURE_SQL_SERVER`, `AZURE_SQL_DATABASE`, `AZURE_SQL_USER`, `AZURE_SQL_PASSWORD`, `SECRET_KEY`.
5. `python -m app.dbcheck --init` - tests the connection and creates the **empty** tables, view and trigger.
6. `python -m app.create_admin` - creates your first administrator (the database has no users until you do this).
7. `python -m uvicorn app.main:app --port 8000`, then open http://localhost:8000
   (check http://localhost:8000/api/health shows `"database":"azure-sql"`). API docs: http://localhost:8000/docs
8. *Optional - Azure Blob Storage for CSV evidence:* create a storage account, add `AZURE_STORAGE_CONNECTION_STRING` and
   `AZURE_STORAGE_CONTAINER` to `.env`, run `python -m app.storagecheck` (**Part B** of the Azure guide). Then `/api/health` shows `"storage":"azure-blob"`.

Shortcuts after step 4: `./run.sh` (Linux/macOS) or `.\run.ps1` (Windows).

No Azure yet? With **no** `AZURE_SQL_*` settings the app falls back to a local SQLite file, so you can still try it:
`python -m app.create_admin` then `python -m uvicorn app.main:app --port 8000`.

Run the tests (never touches your Azure resources): `cd backend && python -m pytest -q`
(The 7 Blob tests start Azurite automatically if it is installed: `npm install -g azurite`; otherwise they are skipped.)

## Accounts
The database starts with **no users and no data**. You create the first admin with `python -m app.create_admin`,
then create the registrar, distributor, pharmacy and reviewer accounts from the **Users** screen.
Public users need no account: open the home page and enter a verification code.

*Optional practice data* (empty database only): `python -m app.seed` loads 5 demo users (`admin@medtrace.demo` / `Admin@123`,
`registrar@`, `distributor@`, `pharmacy@`, `reviewer@` with passwords `Registrar@123`, `Distributor@123`, `Pharmacy@123`,
`Reviewer@123`) and demo batches `MT-DEMO-0001` ... `MT-DEMO-0005`. To remove it again run `database/azure_reset.sql`.

## Viva demo script (matches Appendix A of the proposal)
0. Sign in as **admin** > Users: create one account per role (first time only).
1. Sign in as **registrar** > Register: add a medicine (cold chain, 2-8 C) and a batch. A QR code and code are created.
2. Sign in as **distributor**: open the batch, add *Dispatched*, *Received at distribution point*, *Received at pharmacy*.
3. Add temperature evidence: upload a CSV (`recorded_at,temperature`) or use *Simulate* with the out-of-range option. The database trigger raises a temperature-excursion alert.
4. Sign out. On the home page enter the code: see identity, status, reasons and the "needs review/flagged" outcome.
5. Enter an unknown code (e.g. `MT-FAKE-0000`) or scan one code 6+ times, or from two locations: alerts are raised.
6. Sign in as **reviewer** > Alerts: mark under review, then resolve with a reason.
7. Open **Audit** (the decision is recorded) and **Dashboard** (counts update; export CSV).

## How the rules work
* **Status is derived, never stored as "green".** The view `v_drug_safety_status` returns:
  `FLAGGED` (recalled/quarantined, expired, or an open HIGH/CRITICAL alert), `NEEDS_REVIEW`
  (any open alert, fewer than 2 journey events, or a cold-chain product with no readings) or `RECORDS_CONSISTENT`.
* **Temperature trigger** `trg_flag_temperature_excursion` fires on every reading inserted into `temperature_logs`
  (manual, CSV or simulated). Limits come from the batch override, else the medicine default.
  Severity is CRITICAL when more than 5 C outside the range, else HIGH.
* **Verification** (`GET /api/verify/{code}`) runs in one transaction: request row + alerts + audit entry.
  Rules: unknown code; batch number supplied does not match; more than 5 scans of a code in 10 minutes;
  scans from two different location labels within 30 minutes. All alerts are de-duplicated while one is still active.
* Flags are review signals with explanations, not proof of counterfeiting.

## API overview
Public: `POST /api/auth/login`, `GET /api/verify/{code}`, `GET /api/qr/{code}`.
Authenticated: `GET /api/me`, `/api/medicines`, `/api/batches`, `/api/batches/{id}`, `.../safety-status`, `.../temperature-logs`.
Role-restricted writes: `POST /api/medicines|batches` (admin, registrar), `POST .../events` (admin, distributor, pharmacy),
`POST .../temperature-logs` (admin, distributor), `POST .../report` (pharmacy, admin), `PATCH /api/batches/{id}` (admin, registrar).
Evidence files (admin, distributor, registrar, reviewer): `GET /api/batches/{id}/evidence`, `GET /api/batches/{id}/evidence/{file_id}` (audited download).
Reviewer/admin: `GET /api/alerts`, `PATCH /api/alerts/{id}`, `GET /api/analytics/summary|export`, `GET /api/audit`.
Admin: `GET|POST /api/users`, `PATCH /api/users/{id}`. Full contract at `/docs`.

## Project layout
```
backend/app/   main.py (routes) services.py (rules, verification) schemas.py security.py
               db.py (Azure SQL + SQLite layer) storage.py (Azure Blob / local files) config.py (.env)
               create_admin.py dbcheck.py storagecheck.py seed.py (optional demo data)
requirements.txt  startup.sh  .github/workflows/azure-webapp.yml   (App Service deployment from GitHub)
backend/tests/ pytest suite
database/      azure_sql_schema.sql (T-SQL: tables, indexes, view, trigger)  queries.sql (viva queries for the Azure Query editor)
               azure_reset.sql (wipe everything)  schema.sql + queries_sqlite.sql (SQLite, tests only)
frontend/      index.html style.css app.js
docs/          AZURE_SETUP.md (Azure SQL + Blob setup)  DEPLOY_GITHUB_AZURE.md (GitHub -> App Service)  design_notes.md (3NF, threat model, security checklist, data classification)
storage/       local fallback folder for CSV evidence (used only when Blob Storage is not configured)
```

## Configuration
Settings live in a `.env` file (copy `.env.example`); real environment variables override it. Never commit `.env`.
`SECRET_KEY` should be a long random value; if unset a random key is generated per run (logins reset on restart).
`MEDTRACE_SEED=0` (default) keeps the database empty; the app never loads demo data unless you set it to `1` or run `python -m app.seed`.

## GitHub -> Azure deployment (for the cloud-review milestone)
Full guide: **[docs/DEPLOY_GITHUB_AZURE.md](docs/DEPLOY_GITHUB_AZURE.md)**. Summary: push to `main` -> GitHub Actions
(`.github/workflows/azure-webapp.yml`) runs the tests, then deploys to an Azure App Service (Linux, Python 3.12) whose
*Environment variables* hold the Azure SQL and Blob Storage settings. `startup.sh` installs the ODBC driver and starts the app.
Secrets are never stored in the repository (`.env` is git-ignored). Delete the Azure resources afterwards to avoid charges.

## Honest limitations (say these in the viva)
* The T-SQL schema, view, trigger and every query were **validated by an automated T-SQL parser and by running the identical logic
  on SQLite**, but the build environment had no SQL Server, so the first run against your own Azure database is the real
  integration test. If `python -m app.dbcheck --init` reports an error, send it to your supervisor/AI helper with the message.
* Timestamps are stored as ISO text (`...Z`) rather than `DATETIME2`, to keep behaviour identical across databases.
* Blob access uses the storage account key (connection string), not Microsoft Entra identities. The Blob code was exercised against the Azurite emulator, not a live storage account, so run `python -m app.storagecheck` once on your own account.
* The optional Isolation Forest model and the Power BI dashboard are **not** included; the dashboard is built into the app.
* "Implausible location" is approximated by two different free-text location labels within 30 minutes (no geo-coordinates).
* Rate limiting is in-memory (single process). Login tokens live in `sessionStorage` and last 8 hours.
* The GitHub Actions workflow and `startup.sh` were validated (YAML/shell syntax, app start from the repo root, CI test run) but not run on a live App Service; the driver install in `startup.sh` is the first thing to check if the first deployment misbehaves.
* Cloud deployment of the web app and its screenshots still have to be done by you.

## Credits
FastAPI, Pydantic, PyJWT, segno (QR codes), pytest, Hanken Grotesk font. Code assisted by Claude (Anthropic) - declare AI assistance as your institution requires.
