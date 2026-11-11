# Connect MedTrace to Azure SQL Database and Azure Blob Storage - step by step

MedTrace uses two Azure services:

* **Part A - Azure SQL Database** (Steps 1-10): all records. It starts **completely empty**: the app creates the tables, the safety view
  and the temperature trigger by itself, and you create your own first admin user. No demo data is loaded.
* **Part B - Azure Blob Storage**: the uploaded temperature CSV files (evidence). Optional - without it, files go to the local `storage/` folder.

Total time: about 30-40 minutes for Part A, 10-15 minutes more for Part B.

```
 Your PC                                   Azure
 ┌───────────────────────────┐  1433, TLS  ┌────────────────────────────┐
 │ Browser  ──>  MedTrace    │ ──────────> │ SQL server > "medtrace-db" │  records
 │ localhost:8000  (FastAPI) │             └────────────────────────────┘
 │  .env holds the settings  │  443, TLS   ┌────────────────────────────┐
 │                           │ ──────────> │ Storage account > container │  CSV evidence files (private)
 └───────────────────────────┘             └────────────────────────────┘
```

---

## Step 1 - Azure account

1. Go to https://portal.azure.com and sign in. If you have no account, create one at https://azure.microsoft.com/free
   (a student email may give you **Azure for Students** credits).
2. The free SQL database offer works with most subscriptions. **Exception:** the old *"Azure for Students Starter"*
   subscription is not compatible with the free offer. If you have that one, use a normal Azure Free account, or create
   the database on the paid Basic tier (a few dollars per month; delete it when the project is over).

## Step 2 - Create the database (empty)

1. In the portal search box type **SQL databases** and open it. Click **Create**.
2. **Basics** tab
   * **Subscription**: yours. **Resource group**: *Create new* -> `medtrace-rg`.
   * **Database name**: `medtrace-db`.
   * **Server**: *Create new*. A panel opens:
     * **Server name**: must be unique worldwide, e.g. `medtrace-yourname-2026`.
       Your full server address will be `medtrace-yourname-2026.database.windows.net`. **Write it down.**
     * **Location**: choose a region close to you. If you get *"region not available for your subscription"*, pick another region.
     * **Authentication method**: choose **Use SQL authentication** (this is important - the app logs in with a user name and password).
     * **Server admin login**: e.g. `medadmin`. **Password**: a strong one. **Write both down.** Click **OK**.
   * **Want to use SQL elastic pool?** No.
   * **Workload environment**: Development.
   * At the top you may see a banner **"Want to try Azure SQL Database for free?"** -> click **Apply offer**.
     Set **Behavior when free limit reached** to **Auto-pause the database until next month** so you can never be charged.
     (If you don't see the banner, choose Compute + storage -> *General Purpose, Serverless*, or *Basic* for the cheapest paid option.)
3. **Networking** tab
   * **Connectivity method**: *Public endpoint*.
   * **Allow Azure services and resources to access this server**: **Yes**.
   * **Add current client IP address**: **Yes**. (This lets your PC connect. See Step 5 if your IP changes.)
4. **Security** tab: leave defaults (Microsoft Defender: *Not now*).
5. **Additional settings** tab: **Use existing data = None**. (Do **not** choose *Sample* - that would fill the database with AdventureWorks tables.)
6. **Review + create** -> **Create**. Wait 1-3 minutes, then **Go to resource**.

> The free serverless database **pauses itself when idle**. The first connection after a pause can take 30-60 seconds.
> MedTrace retries automatically. This is normal.

## Step 3 - Install Python and the Microsoft ODBC driver on your PC

MedTrace connects through `pyodbc`, which needs **Microsoft ODBC Driver 18 for SQL Server**.

1. **Python 3.10 or newer** from https://www.python.org/downloads/ (Windows: tick *"Add python.exe to PATH"*).
   Check: `python --version`
2. **ODBC Driver 18**
   * **Windows**: download *"Microsoft ODBC Driver 18 for SQL Server"* (x64) from Microsoft
     (search "Download ODBC Driver for SQL Server", page on learn.microsoft.com) and run the installer.
   * **macOS** (Homebrew):
     ```bash
     brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release
     brew update
     HOMEBREW_ACCEPT_EULA=Y brew install msodbcsql18
     ```
   * **Ubuntu / Debian**: follow Microsoft's page *"Install the Microsoft ODBC driver for SQL Server (Linux)"* - it adds
     Microsoft's package repository, then:
     ```bash
     sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev
     ```
   If Microsoft's page differs from the commands above, follow Microsoft's page - it is the official source.
3. Check it is installed: Windows: *Start -> "ODBC Data Sources (64-bit)" -> Drivers* tab lists *ODBC Driver 18 for SQL Server*.
   Mac/Linux: `odbcinst -q -d` prints `[ODBC Driver 18 for SQL Server]`.

## Step 4 - Configure the project

Open a terminal in the `medtrace` folder (the one with `README.md`).

1. Create the environment and install packages
   * **Windows (PowerShell)**
     ```powershell
     cd backend
     python -m venv .venv
     .\.venv\Scripts\Activate.ps1
     pip install -r requirements.txt
     cd ..
     ```
     (If PowerShell blocks the activate script: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then retry.)
   * **macOS / Linux**
     ```bash
     cd backend && python3 -m venv .venv && source .venv/bin/activate
     pip install -r requirements.txt && cd ..
     ```
2. Create your settings file: copy `.env.example` to `.env`
   (Windows: `copy .env.example .env`, Mac/Linux: `cp .env.example .env`) and edit it:
   ```
   AZURE_SQL_SERVER=medtrace-yourname-2026.database.windows.net
   AZURE_SQL_DATABASE=medtrace-db
   AZURE_SQL_USER=medadmin
   AZURE_SQL_PASSWORD="the-password-you-chose"
   SECRET_KEY=<paste a long random value>
   MEDTRACE_SEED=0
   ```
   (Part B adds two more lines to this file later.)
   * Generate `SECRET_KEY`: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
   * Put the password in **double quotes** if it contains `#`, `;`, spaces or quotes.
   * You can see the server name any time: Azure portal -> your database -> **Overview** -> *Server name*.
   * `.env` is private - never upload it to GitHub or send it to anyone.

## Step 5 - Allow your computer through the Azure firewall

Azure blocks all outside computers until they are allowed. Step 2 already added your IP, but your IP changes when you
switch network (home -> college -> hotspot). To add it again:

1. Azure portal -> open your **SQL server** (not the database) -> **Security -> Networking**
   (or on the database page click **Set server firewall**).
2. Under **Firewall rules** click **Add your client IPv4 address** -> **Save**.

Some college and office networks block outgoing port **1433**. If connection times out even with the correct IP,
switch to your phone's hotspot.

## Step 6 - Test the connection and create the tables

From the `medtrace` folder, with the virtual environment active:

```bash
cd backend
python -m app.dbcheck --init
```

Expected output:
```
Backend: azure-sql
Schema created / verified (tables, view v_drug_safety_status, trigger, role rows).
Server: Microsoft SQL Azure (RTM) - ...
Database: medtrace-db
Connection OK.
Table row counts:
  roles                   5      <- the 5 fixed role names (admin, registrar, distributor, pharmacy, reviewer)
  users                   0
  medicines               0
  ... (everything else 0)
```
If you see `FAILED: ...`, the message tells you what to fix - see **Troubleshooting** below.

## Step 7 - Create your first admin user

The database has no users yet, so create one:

```bash
python -m app.create_admin
```
Enter a name, email and a password (8+ characters). Example: `admin@mycollege.edu`.

## Step 8 - Run the app

```bash
python -m uvicorn app.main:app --port 8000
```
(or from the `medtrace` folder: `./run.sh` on Mac/Linux, `.\run.ps1` on Windows)

1. Open http://localhost:8000/api/health - you should see `{"status":"ok","database":"azure-sql"}`.
   That proves the app is using Azure SQL.
2. Open http://localhost:8000 -> **Sign in** with the admin you just created.
3. **Users** screen: create accounts for the other roles (registrar, distributor, pharmacy, reviewer).
4. Follow the demo script in `README.md` (register a medicine and batch, add journey events, upload temperatures,
   verify the code, review the alert).

## Step 9 - See your data inside Azure (for screenshots and the viva)

1. Azure portal -> your database -> **Query editor (preview)** -> sign in with **SQL server authentication**
   (`medadmin` + password). If it says your IP is not allowed, click the link shown to allowlist it.
2. Run:
   ```sql
   SELECT name FROM sys.tables ORDER BY name;      -- 9 tables
   SELECT * FROM dbo.roles;                        -- 5 rows
   SELECT * FROM dbo.v_drug_safety_status;         -- the view
   SELECT * FROM dbo.audit_logs ORDER BY audit_id DESC;
   ```
3. `database/queries.sql` contains more queries (joins, view, trigger demo, transaction demo) written for this editor.
4. Alternatives to the portal editor: **Azure Data Studio**, **SSMS** (Windows), or the **MSSQL extension in VS Code**.

## Step 10 - Optional extras

* **Demo data for a practice run** (only on an empty database): `python -m app.seed`
  (adds 5 demo users such as `admin@medtrace.demo` and 5 demo batches). Do not use this on the database you submit unless you want demo data in it.
* **Start again from zero**: run `database/azure_reset.sql` in the Query editor (deletes everything), restart the app,
  then run `python -m app.create_admin` again.
* **Run the tests**: `cd backend && python -m pytest -q`. Tests use a throw-away local file and **never** touch Azure.
* **Put the app itself on Azure from GitHub**: follow `docs/DEPLOY_GITHUB_AZURE.md` (ready-made workflow and start-up script). Manual summary: create a Web App (Linux, Python 3.12), set the same values as
  *Application settings* (`AZURE_SQL_SERVER`, `AZURE_SQL_DATABASE`, `AZURE_SQL_USER`, `AZURE_SQL_PASSWORD`,
  `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_CONTAINER`, `SECRET_KEY`, `MEDTRACE_HTTPS=1`), set the startup command to `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`, and deploy the
  `backend/` + `frontend/` + `database/` folders together. You must confirm the ODBC Driver 18 is available on that
  App Service image (if `pyodbc` cannot find it, install it from a startup script or deploy as a container). "Allow Azure
  services" (Step 2) is what lets App Service reach the database.

# Part B - Azure Blob Storage for evidence files

When someone uploads a temperature CSV, MedTrace saves the original file as a **blob** in a **private container** and keeps only
the reference in the database. Nobody can open the file by URL: downloads go through the MedTrace API, which checks the user's
role (admin, distributor, registrar, reviewer) and writes a `DOWNLOAD_EVIDENCE` line to the audit trail.

## Step B1 - Create the storage account

1. Portal search box -> **Storage accounts** -> **Create**.
2. **Basics**
   * **Subscription** and **Resource group**: the same `medtrace-rg` as before.
   * **Storage account name**: 3-24 characters, lowercase letters and numbers only, unique worldwide, e.g. `medtraceyourname26`.
   * **Region**: the same as your database. **Primary service** (if shown): *Azure Blob Storage*.
   * **Performance**: Standard. **Redundancy**: **Locally-redundant storage (LRS)** (cheapest).
3. **Advanced** tab: keep *Require secure transfer* **on**, *Minimum TLS version* 1.2 and **Allow enabling anonymous access on individual containers = off**.
4. **Networking** tab: **Public network access: Enabled from all networks** is the simplest for a student project.
   (More locked-down: *Enabled from selected networks* and add your IP address - then re-add it whenever your IP changes, like the SQL firewall in Step 5.)
5. **Review + create** -> **Create** -> **Go to resource** (about a minute).

## Step B2 - Copy the connection string

1. In the storage account open **Security + networking -> Access keys**.
2. Click **Show** next to **key1** and copy the **Connection string** (it starts with `DefaultEndpointsProtocol=https;AccountName=...`).
3. Treat it like a password: never post it or commit it. If it leaks, click **Rotate key** on the same page.

## Step B3 - Put it in `.env`

Add these two lines (keep the double quotes around the connection string):

```
AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;AccountName=medtraceyourname26;AccountKey=xxxxxxxx==;EndpointSuffix=core.windows.net"
AZURE_STORAGE_CONTAINER=medtrace-evidence
```
The container is created automatically as **Private** the first time a file is stored. (Container names: 3-63 characters, lowercase letters, numbers, hyphens.)

## Step B4 - Test it

```bash
cd backend
pip install -r requirements.txt        # installs azure-storage-blob if you have not yet
python -m app.storagecheck
```
Expected:
```
Storage backend: azure-blob
Container: medtrace-evidence
Write / read / delete test file: OK
```

## Step B5 - Use it and see the file in Azure

1. Start the app (Step 8). `http://localhost:8000/api/health` now shows `"storage":"azure-blob"`.
2. Sign in -> open a batch -> **Add temperature evidence -> CSV import** -> choose a file with the columns `recorded_at,temperature` -> **Upload CSV**.
   (Example row: `2026-10-27T08:30:00Z,5.2`.)
3. The batch page now has an **Evidence files** panel: "stored in Azure Blob Storage" and a **Download** button.
4. In the portal: storage account -> **Data storage -> Containers -> medtrace-evidence -> csv** - your file `batch1_xxxxxxxxxxxx.csv` is there.
   Click the container's **Change access level** to confirm it says **Private (no anonymous access)**.

## How it behaves (good to know for the viva)

* **Upload order**: the file is stored first; then the readings, the alert trigger and the audit entry are saved in one database transaction.
  If the database part fails, MedTrace **deletes the blob again**, so no orphan files remain.
* **If Blob Storage is down or misconfigured**: the upload is refused with an error ("Could not store the evidence file...") and **no readings are saved**,
  so the database never claims evidence that was not stored.
* **Access**: private container + downloads only through `GET /api/batches/{id}/evidence/{file_id}`. A file id only works for the batch it belongs to.
* **Switching**: files uploaded earlier to the local folder keep working (their reference starts with `csv/`); new files go to Blob Storage.
  The reference of a blob file looks like `blob://medtrace-evidence/csv/batch1_ab34cd56ef78.csv`.
* **Not covered**: access uses the account key from the connection string. If your organisation disables key access on storage accounts,
  you would need Microsoft Entra authentication, which this prototype does not implement.

---

## Costs and clean-up

* Free SQL offer + *Auto-pause* = no charge. Check **Overview -> Free amount remaining** now and then.
* Blob Storage (LRS) costs a few cents per month for a handful of small CSV files (new Azure accounts also get free storage for 12 months).
* When your project is finished and marked, delete everything (database and storage account together): portal -> **Resource groups** -> `medtrace-rg` -> **Delete resource group**.
* Never leave the firewall open to all IPs (`0.0.0.0 - 255.255.255.255`).

---

## Troubleshooting

| What you see | Cause | Fix |
|---|---|---|
| `Missing settings: AZURE_SQL_...` | `.env` not found or incomplete | The file must be named exactly `.env` and sit next to `README.md`. Fill all four `AZURE_SQL_*` lines |
| `pyodbc could not be loaded (libodbc...)` / `ImportError` | ODBC driver / unixODBC missing | Step 3. On Linux also `sudo apt-get install unixodbc` |
| `The ODBC driver was not found` (IM002) | Different driver name installed | Install Driver 18, or set `AZURE_SQL_DRIVER=ODBC Driver 17 for SQL Server` in `.env` |
| `Azure SQL refused this computer's IP address` (40615) | Firewall | Step 5 |
| `rejected the user name or password` (18456) | Wrong login | Re-check `AZURE_SQL_USER` / `AZURE_SQL_PASSWORD` (quotes if it has `#` or `;`). Reset the password: SQL server -> **Reset password** |
| `database name is wrong` (4060) | Wrong `AZURE_SQL_DATABASE` | Use the exact database name (not the server name) |
| `database is paused / resuming` (40613) | Serverless auto-pause | Wait a minute and retry; the app already retries |
| Timeout / `Login timeout expired` | Port 1433 blocked, or wrong server name | Try a phone hotspot; check the server name ends with `.database.windows.net` |
| `Client with IP address ... not allowed` in the Query editor | Same firewall rule | Step 5 |
| `503` in the browser, or "Database unavailable" | The database is down / paused / unreachable | Read the message text; run `python -m app.dbcheck` |
| Page loads but login says wrong password for the demo accounts | Demo users only exist if you ran `python -m app.seed` | Use the admin you created in Step 7 |
| SSL / certificate error | Wrong server name | Use the full name from the portal. Never set `TrustServerCertificate=yes` for Azure |
| `Azure Storage refused the credentials or this network` | Wrong/rotated key, storage firewall, or key access disabled | Re-copy the connection string (Step B2); Storage account -> Networking -> allow your IP or all networks |
| `Could not reach Azure Storage` | Account name typo or no internet | Check `AccountName=` in the connection string |
| `AZURE_STORAGE_CONNECTION_STRING is not valid` | String cut off or quotes missing | Copy the whole string again and keep the double quotes in `.env` |
| `503 Could not store the evidence file` on upload | Any of the three rows above | Run `python -m app.storagecheck` - it prints the exact reason. No readings were saved |
| `The evidence file is missing from Azure Blob Storage` | Blob deleted by hand, or `.env` points at another account/container | Re-upload the CSV, or restore the original connection string |

---

## What changed from the SQLite version (good to know for the viva)

* The same API and screens run on **Azure SQL Database (T-SQL)**. `database/azure_sql_schema.sql` holds the 3NF schema,
  CHECK/UNIQUE/FOREIGN KEY constraints, indexes, the view `v_drug_safety_status` and the trigger
  `trg_flag_temperature_excursion` (this is the Azure SQL form of the proposal's `sp_FlagSuspiciousBatch` rule).
* The database layer is `backend/app/db.py`. All SQL in the app is written in T-SQL style (`OFFSET ... FETCH` paging,
  parameterised `?` placeholders - never string concatenation, so SQL injection is not possible).
* Timestamps are stored as ISO-8601 UTC text (`2026-10-26T09:30:00Z`) and dates as `YYYY-MM-DD`. This keeps comparisons and
  JSON output identical on every database. A production system would use `DATETIME2`.
* SQLite is still used by the automated tests only.
* Uploaded CSV evidence files go to **Azure Blob Storage** (`backend/app/storage.py`) when `AZURE_STORAGE_CONNECTION_STRING` is set, otherwise to the local `storage/` folder.
  The Blob code is tested against Azurite, Microsoft's official storage emulator.
