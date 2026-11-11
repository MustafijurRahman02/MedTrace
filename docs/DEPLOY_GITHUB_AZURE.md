# MedTrace on GitHub -> running on Azure (App Service + Azure SQL + Blob Storage)

You already have the project on GitHub. This guide does three things:

1. updates your repo with the Azure SQL + Blob Storage version,
2. creates a small Azure web app that runs it,
3. makes **every `git push` to `main` test and deploy the site automatically** (GitHub Actions).

```
 GitHub repo ──push──> GitHub Actions (tests, then deploy) ──> Azure App Service (the website)
                                                                  │ settings: SQL + Storage secrets
                                                                  ├──> Azure SQL Database   (records)
                                                                  └──> Azure Blob Storage   (CSV evidence files)
```

> **Golden rule: passwords and keys never go into GitHub.** They live only in (a) your private `.env` file on your PC and
> (b) the Azure App Service *Environment variables*. The repo contains `.env.example` (placeholders only) and a `.gitignore`
> that blocks `.env` and database files.

Do **Part A and B of `AZURE_SETUP.md` first** (create the SQL database and the storage account, test the connection from your PC,
create the admin user). Then come back here.

---

## Part 1 - Put this version into your GitHub repo

**The repo root must be the folder that contains `README.md`, `backend/`, `frontend/`, `database/`, `startup.sh` and `.github/`.**
If your repo has everything inside one extra sub-folder, move the contents up one level.

### Easiest way: git on your PC
```bash
git clone https://github.com/<your-user>/<your-repo>.git
cd <your-repo>
# 1. delete the old project files here (keep the hidden .git folder!)
# 2. copy everything from the new medtrace folder into this folder
#    (include hidden items: .github, .gitignore, .env.example)
git add -A
git status          # READ THIS LIST: there must be no ".env" and no "*.db" file
git commit -m "Add Azure SQL, Blob Storage and Azure deployment"
git push
```
Browser upload on github.com also works, but it often skips hidden folders (`.github`, `.gitignore`), so use git for this step.

### Check that no secret was committed
```bash
git ls-files | grep -E "(^|/)\.env$|\.db$"      # must print nothing
```
If it prints something, or if you ever pasted a password/key into a file you pushed:
1. **Rotate the secret** (this is what actually protects you): SQL server -> *Reset password*; storage account -> *Access keys -> Rotate key*;
   generate a new `SECRET_KEY`.
2. `git rm --cached .env` (or the file name), commit and push. (Old copies stay in git history, which is why rotating matters.)

---

## Part 2 - Prepare the database from your PC (once)

The website and your PC use the **same** Azure SQL database, so prepare it from your PC where it is easy:

```bash
cd backend
python -m app.dbcheck --init        # creates the empty tables, view and trigger
python -m app.create_admin          # creates your first admin user
python -m app.storagecheck          # confirms Blob Storage works (optional but recommended)
```
(Needs your `.env` filled in - `AZURE_SETUP.md` Steps 4-7 and Part B.)

---

## Part 3 - Create the Azure web app

1. Portal -> **App Services** -> **Create** -> **Web App**.
2. **Basics**
   * Resource group: `medtrace-rg`. **Name**: unique, e.g. `medtrace-yourname` -> your site will be `https://medtrace-yourname.azurewebsites.net`.
   * **Publish**: Code. **Runtime stack**: **Python 3.12**. **Operating System**: **Linux**. **Region**: same as your database.
   * **Pricing plan**: **Basic B1** is the reliable choice (a few dollars/month - delete it after the project). The Free F1 plan has tight
     CPU limits and may time out while the first start installs the database driver.
3. **Deployment** tab: leave *Continuous deployment* **disabled** (our workflow does it) and set **Basic authentication: Enable**.
4. **Review + create** -> **Create** -> **Go to resource**.

### Set the environment variables
App Service -> **Settings -> Environment variables -> App settings -> + Add** (one by one, then **Apply** at the bottom):

| Name | Value |
|---|---|
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` |
| `WEBSITES_PORT` | `8000` |
| `WEBSITES_CONTAINER_START_TIME_LIMIT` | `600` |
| `AZURE_SQL_SERVER` | `medtrace-yourname-2026.database.windows.net` |
| `AZURE_SQL_DATABASE` | `medtrace-db` |
| `AZURE_SQL_USER` | `medadmin` |
| `AZURE_SQL_PASSWORD` | your SQL password (type it exactly, no quotes here) |
| `AZURE_STORAGE_CONNECTION_STRING` | the storage connection string (no surrounding quotes here) |
| `AZURE_STORAGE_CONTAINER` | `medtrace-evidence` |
| `SECRET_KEY` | a long random value (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) |
| `MEDTRACE_HTTPS` | `1` |
| `MEDTRACE_SEED` | `0` |

(In App Service you do **not** wrap values in quotes - that is only for the `.env` file.)

### Set the start-up command
**Settings -> Configuration -> General settings -> Startup Command**: `bash startup.sh` -> **Save**.
Also turn **Always on = On** (Basic plan and above).

`startup.sh` installs *Microsoft ODBC Driver 18* the first time the app starts (the first start takes a minute or two), then starts MedTrace.

---

## Part 4 - Connect GitHub to the web app

1. **Get the publish profile**: App Service -> **Overview** -> **Get publish profile** (downloads a small `.PublishSettings` file).
   If the button is greyed out: **Settings -> Configuration -> General settings -> SCM Basic Auth Publishing Credentials = On**, Save, then try again.
2. In your GitHub repo: **Settings -> Secrets and variables -> Actions**
   * **Secrets** tab -> **New repository secret**: name `AZURE_WEBAPP_PUBLISH_PROFILE`, value = open the downloaded file in a text editor and paste **all** of it.
   * **Variables** tab -> **New repository variable**: name `AZURE_WEBAPP_NAME`, value = your App Service name (e.g. `medtrace-yourname`).
3. Trigger the first deployment: push to `main`, or **Actions** tab -> **Test and deploy MedTrace** -> **Run workflow**.
   * Job **test** (about 1 minute): runs the automated tests on GitHub's server (local SQLite only - it never touches your Azure data).
   * Job **deploy** (3-6 minutes the first time): zips the project and sends it to Azure, which installs the Python packages.
4. From now on: **edit -> commit -> push -> tests -> automatic deploy**. If tests fail, nothing is deployed.

*Alternative:* App Service -> **Deployment Center** -> Source **GitHub** lets Azure create its own workflow file. Use that **or** our workflow, not both.

---

## Part 5 - Let the website reach SQL and Storage

* **SQL**: you already set *Allow Azure services and resources to access this server = Yes* (AZURE_SETUP Step 2). That covers App Service.
  (Check: SQL server -> **Security -> Networking**.)
* **Storage**: storage account -> **Security + networking -> Networking** -> **Enabled from all networks**.
  Reason: a storage firewall rule by IP address does not apply to traffic coming from inside the same Azure region, so "selected networks"
  would block your website unless you set up a private network, which is beyond this project.

---

## Part 6 - Test the live site

1. Open `https://<your-app>.azurewebsites.net/api/health`. Expected:
   `{"status":"ok","database":"azure-sql","storage":"azure-blob"}`
   (The first request after a deployment can take 1-2 minutes.)
2. Open `https://<your-app>.azurewebsites.net/`, sign in with the admin from Part 2, create users, register a batch, upload a CSV.
3. Watching what happens: App Service -> **Monitoring -> Log stream**.
4. Show it in the viva: portal -> SQL database -> **Query editor** (rows appear), storage account -> **Containers -> medtrace-evidence -> csv** (the file appears).

## If something goes wrong

| What you see | Likely cause | Fix |
|---|---|---|
| GitHub **test** job red | A test failed | Open the job log; fix and push again. Nothing is deployed until it is green |
| **deploy** job: "No credentials found" / "publish profile" error | Secret missing or wrongly named | Part 4 step 2: secret name must be exactly `AZURE_WEBAPP_PUBLISH_PROFILE`; paste the whole file |
| **deploy** job: 401 / basic auth error | Basic authentication disabled on the App Service | Enable *SCM Basic Auth Publishing Credentials*, download a new publish profile, update the secret |
| `AZURE_WEBAPP_NAME` is empty | Variable created as a secret, or wrong tab | Create it under **Variables**, not Secrets |
| Site shows "Your web app is running and waiting for your content" | Nothing deployed yet | Run the workflow (Part 4 step 3) |
| "Application Error" or 503 right after deploy | App still starting (driver install) or crashed | Wait 2 minutes; then read **Log stream** |
| Log: `ModuleNotFoundError` | Packages not installed | Add `SCM_DO_BUILD_DURING_DEPLOYMENT=true`, redeploy |
| Log: `The ODBC driver was not found` / `libodbc` | Start-up command not applied or driver install failed | Check Startup Command is `bash startup.sh`; read the `[startup]` lines in the Log stream |
| Log: `Missing settings: AZURE_SQL_...` | App settings not saved | Environment variables -> **Apply**, then restart the app |
| `/api/health` -> 503 "refused this computer's IP" (40615) | "Allow Azure services" is off | SQL server -> Networking -> turn it on |
| `/api/health` -> 503 "database is paused" | Serverless auto-pause | Wait a minute and refresh |
| Upload says "Could not store the evidence file" | Storage network/key problem | Part 5 Storage; re-copy the connection string into App settings |
| Page loads but login fails | You did not create the admin in this database | Part 2: `python -m app.create_admin` |

If you are stuck, send me the **last 30 lines of the Log stream** and the **red step of the GitHub Actions run**.

---

## Honest notes (for your report / viva)
* The tests, the app start-up from the repository root and the GitHub workflow file were checked here, but I could **not** run a real
  App Service. The part most likely to need a small adjustment on first deployment is the start-up command / driver install in `startup.sh`.
* Costs: App Service Basic B1 is billed while it exists. After marking, delete the resource group (`medtrace-rg`) - it removes the web app, SQL database and storage together.
* Security already in place: secrets only in App Service settings, HTTPS-only header (`MEDTRACE_HTTPS=1`), private Blob container, parameterised SQL,
  role-based access and an audit trail. Not included: Microsoft Entra / managed identity login for SQL and Storage (would remove the stored passwords).
