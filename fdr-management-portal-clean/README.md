# FDR Management Portal

A web application prototype for managing Fixed Deposit Receipt (FDR) records, review status, and maturity information.

## Project structure

- `frontend/` — React + Vite user interface.
- `backend/` — FastAPI API with SQLite database initialization.

## Run locally

### Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

The API runs at `http://127.0.0.1:8000` by default. Interactive API docs: `http://127.0.0.1:8000/docs`.

### Frontend

In a separate terminal:

```bash
cd frontend
npm install
npm run dev
```

Vite usually serves the frontend at `http://localhost:5173`.

## Notes

- The local SQLite database is created by the backend; database files are intentionally excluded from Git.
- Do not commit credentials, real financial documents, or personal data.
- This is a project prototype; add authentication, authorization, and production deployment hardening before handling real financial records.
