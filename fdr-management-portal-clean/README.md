# FDR Management Portal

A college-level prototype for managing Fixed Deposit Receipts (FDRs). It includes registration, review and verification, certificate upload/download, maturity alerts, renewal/replacement requests, and release/refund decisions.

**Stack:** React + Vite, Python + FastAPI, and SQLite.

## 1. Install Prerequisites

Install these before continuing:

- [Git](https://git-scm.com/downloads).
- [Python](https://www.python.org/downloads/): Python 3.12 is recommended; Python 3.10 or later is required. On Windows, enable **Add Python to PATH** during installation.
- [Node.js](https://nodejs.org/): Node.js 22 LTS is recommended. npm is included with Node.js.

Open a new terminal after installation and verify:

```text
git --version
node --version
npm --version
```

Check Python with `python --version` on Windows or `python3 --version` on macOS/Linux. If Windows only recognizes `py`, use `py -3.12` instead of `python` when creating the virtual environment below.

No separate database server, API key, account, or `.env` file is required. You do not need this developer's `.tools` folder or VS Code tasks.

## 2. Clone the Repository

```text
git clone https://github.com/karthikeyar85-netizen/fdr-management-portal.git
cd fdr-management-portal
cd fdr-management-portal-clean
```

You should now be in the **application directory**, which contains this README and both folders:

```text
fdr-management-portal/
	fdr-management-portal-clean/
		README.md
		backend/
			main.py
			requirements.txt
			test_main.py
		frontend/
			package.json
			package-lock.json
			src/
```

Keep **two terminals** open: one for the backend and one for the frontend. The commands in the next two sections each start from the application directory, not the repository's outer directory.

## 3. Set Up and Start the Backend

### Windows PowerShell

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

These commands use the virtual environment directly. Activation and PowerShell execution-policy changes are not necessary. Backend packages are installed inside `backend/.venv`, not into your global Python installation.

### macOS / Linux

```bash
cd backend
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

On Ubuntu/Debian, if creating the environment reports that `venv` or `ensurepip` is unavailable, install the matching Python venv package first, for example `sudo apt install python3-venv`, then retry environment creation.

Leave this terminal running. Wait for **Application startup complete**.

- API health check: http://127.0.0.1:8000/
- Interactive API documentation: http://127.0.0.1:8000/docs

The health check should return:

```json
{"message":"FDR Portal API is running"}
```

## 4. Set Up and Start the Frontend

Open the second terminal in the application directory, then run:

```text
cd frontend
npm ci
npm run dev -- --host localhost --port 5173 --strictPort
```

`npm ci` installs the versions recorded in the lockfile into the local `frontend/node_modules` folder. It does not install global npm packages.

If dependency installation fails with a **404 for a locked package version**, use this compatible fallback inside `frontend`, then rerun the dev-server command:

```text
npm install --no-save --package-lock=false react@18.3.1 react-dom@18.3.1 axios@1.7.9 vite@6.0.5 @vitejs/plugin-react@4.3.4
```

This fallback installs versions allowed by the manifest without rewriting the existing manifest or lockfile. The installed versions can differ from the lockfile. Use it only when the normal locked installation fails because an artifact is unavailable.

Leave the frontend terminal running and open:

**http://localhost:5173/**

Use this exact address. The backend currently allows browser requests from `http://localhost:5173`; opening the frontend at `http://127.0.0.1:5173` or a different port requires updating the backend's CORS configuration.

## 5. Try the Application

A fresh clone starts with an empty database. Register your own demo records:

1. In **College Dashboard**, register an FDR and click **Submit FDR** in its row.
2. Switch to **Officer Dashboard**, click **Start Review**, then **Review FDR**. Choose **Verify** and save the review. Corrections and rejection are also available; corrections can be edited and resubmitted from the college view.
3. Return to **College Dashboard** and open the FDR's **Details**. Upload a PDF, JPEG, or PNG certificate, then use **Download** to retrieve it. The upload limit is 10 MB per file.
4. For a verified FDR, choose a renewal, replacement, release, or refund request in Details and submit it. Renewal requires a maturity date later than the current one. Only one request may be pending for an FDR at a time.
5. In **Officer Dashboard**, approve or reject the request in its history table. Rejection requires a reason. Approved renewals/replacements update the FDR; approved release/refund requests change its status to **Released** or **Refunded**.

Maturity alerts show verified FDRs that have matured or will mature within 30 days. Use a nearby maturity date when testing alerts. Search and status filters are available above the FDR records table.

## Stop and Restart

Press **Ctrl+C** in each server terminal to stop the app. Your records and uploaded files remain on disk.

For subsequent runs, skip cloning, environment creation, and dependency installation. Open one terminal in `backend` and one in `frontend`.

Backend on Windows:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Backend on macOS/Linux:

```bash
./.venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Frontend on either platform:

```text
npm run dev -- --host localhost --port 5173 --strictPort
```

## Data and Configuration

- SQLite creates `backend/fdr_portal.db` automatically when the backend starts. Tables are initialized automatically; no manual migration command is required.
- Certificates are stored in `backend/uploads`, created on the first upload. Back up **both the database and uploads folder**, preferably while the backend is stopped, to preserve records and their documents.
- Database files, uploads, virtual environments, and installed packages are not committed. Do not delete the database or uploads folder to resolve a startup error.
- Optional backend environment variables: `FDR_DB_PATH` changes the database file and `FDR_UPLOAD_DIR` changes the certificate storage directory. Use absolute paths; the database's parent directory must already exist.
- Optional frontend environment variable: `VITE_API_URL` changes the API base URL. It defaults to `http://127.0.0.1:8000`. Set it before starting Vite, and restart Vite after changing it. Keep the frontend origin consistent with the backend CORS setting.
- The automatic daily maturity check runs while the backend is running; the dashboard alert endpoint also calculates alerts on request.

## Tests and Build

Backend tests use temporary databases and upload storage, not your working records. From `backend` on Windows:

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_main
```

From `backend` on macOS/Linux:

```bash
./.venv/bin/python -m unittest -v test_main
```

To check the production frontend build, run from `frontend`:

```text
npm run build
```

The generated files are placed in `frontend/dist`. This build command does not start either server; use the development commands above to run the prototype locally.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| `python`, `node`, `npm`, or `git` is not recognized | Install the prerequisite, reopen your terminal, and check its version. On Windows, try `py -3.12` if `python` is unavailable. |
| `.\.venv\Scripts\python.exe` is not recognized | Run the command from `backend`. Create `.venv` there first with `python -m venv .venv`; wait for creation to finish. |
| `No module named uvicorn`, `fastapi`, or `multipart` | Reinstall requirements using the virtual environment's Python, not global `pip`. All four backend requirements are needed. |
| PowerShell blocks `npm.ps1` | Use `npm.cmd` instead of `npm` in the frontend commands. Do not change execution policy just to run this app. |
| Port 8000 or 5173 is already in use | Stop an earlier instance with Ctrl+C in its terminal. Do not stop unknown processes. Keep port 5173 unless you also update backend CORS. |
| The page cannot load FDR data | Check the backend health URL, both terminal outputs, and that the browser uses exactly `http://localhost:5173/`. |
| npm reports 404 for a locked artifact | Use the compatible frontend installation fallback in Step 4. |
| An install fails with a proxy, network, or certificate error | Check internet access and your organization's approved package proxy/certificate setup. Do not disable TLS verification. |
| A release or renewal request is unavailable | The FDR must be Verified and have no other pending request. Released/refunded FDRs cannot create new requests. |

## Prototype Scope

This application does not implement authentication, real bank transactions, or actual money transfers. College/officer dashboards demonstrate workflow roles only. Use synthetic data for demonstrations, and do not upload real financial documents or personal information.
