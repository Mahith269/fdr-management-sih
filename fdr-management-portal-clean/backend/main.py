

import logging
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from uuid import uuid4

from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict


# Logging configuration
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="FDR Management Portal API")

DB_PATH = Path(os.environ.get("FDR_DB_PATH", Path(__file__).parent / "fdr_portal.db"))
UPLOAD_DIR = Path(os.environ.get("FDR_UPLOAD_DIR", Path(__file__).parent / "uploads"))
SESSION_COOKIE = "fdr_session"
SESSION_SECONDS = 12 * 60 * 60
COOKIE_SECURE = os.environ.get("FDR_COOKIE_SECURE", "false").lower() == "true"


def password_digest(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


# Database connection
def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


# Initialize database
def initialize_database():
    with closing(get_connection()) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS fdrs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                institution_name TEXT NOT NULL,
                certificate_number TEXT NOT NULL UNIQUE,
                bank_name TEXT NOT NULL,
                amount REAL NOT NULL CHECK (amount > 0),
                maturity_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Draft'
            )
        """)

        columns = [
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(fdrs)"
            ).fetchall()
        ]

        if "review_note" not in columns:
            connection.execute(
                "ALTER TABLE fdrs ADD COLUMN review_note TEXT"
            )

        connection.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('college', 'officer')),
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_attempts (
                client TEXT PRIMARY KEY,
                failures INTEGER NOT NULL,
                window_started INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fdr_id INTEGER NOT NULL REFERENCES fdrs(id),
                filename TEXT NOT NULL,
                stored_name TEXT NOT NULL UNIQUE,
                content_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS renewal_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fdr_id INTEGER NOT NULL REFERENCES fdrs(id),
                kind TEXT NOT NULL,
                certificate_number TEXT NOT NULL,
                bank_name TEXT NOT NULL,
                amount REAL NOT NULL,
                maturity_date TEXT NOT NULL,
                previous_certificate_number TEXT NOT NULL,
                previous_bank_name TEXT NOT NULL,
                previous_amount REAL NOT NULL,
                previous_maturity_date TEXT NOT NULL,
                notes TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending',
                review_note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS release_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fdr_id INTEGER NOT NULL REFERENCES fdrs(id),
                kind TEXT NOT NULL,
                reason TEXT NOT NULL,
                certificate_number TEXT,
                amount REAL,
                status TEXT NOT NULL DEFAULT 'Pending',
                review_note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TEXT
            );
            DROP INDEX IF EXISTS pending_renewal_per_fdr;
            DROP INDEX IF EXISTS pending_release_per_fdr;
            CREATE UNIQUE INDEX pending_renewal_per_fdr
                ON renewal_requests(fdr_id) WHERE status IN ('Pending', 'Under Review');
            CREATE UNIQUE INDEX pending_release_per_fdr
                ON release_requests(fdr_id) WHERE status IN ('Pending', 'Under Review');
            CREATE TABLE IF NOT EXISTS request_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_type TEXT NOT NULL,
                request_id INTEGER NOT NULL,
                fdr_id INTEGER NOT NULL REFERENCES fdrs(id),
                actor TEXT NOT NULL,
                decision TEXT NOT NULL,
                remarks TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
        """)
        for role, display_name, default_password in (
            ("college", "College User", "College@12345"),
            ("officer", "Review Officer", "Officer@12345"),
        ):
            username = os.environ.get(f"FDR_{role.upper()}_USERNAME", role).strip()
            password = os.environ.get(f"FDR_{role.upper()}_PASSWORD", default_password)
            if not username or len(password) < 12:
                raise RuntimeError("Bootstrap usernames must be nonempty and passwords at least 12 characters")
            if not connection.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
                salt = secrets.token_hex(16)
                connection.execute("""INSERT INTO users
                    (username, display_name, role, password_salt, password_hash) VALUES (?, ?, ?, ?, ?)""",
                    (username, display_name, role, salt, password_digest(password, salt)))
        request_columns = {row["name"] for row in connection.execute("PRAGMA table_info(release_requests)")}
        for column_name, column_type in (("certificate_number", "TEXT"), ("amount", "REAL")):
            if column_name not in request_columns:
                connection.execute(f"ALTER TABLE release_requests ADD COLUMN {column_name} {column_type}")
        for request_type in ("renewal", "release"):
            for row in connection.execute(f"SELECT * FROM {request_type}_requests").fetchall():
                if connection.execute("SELECT id FROM request_events WHERE request_type=? AND request_id=?",
                                      (request_type, row["id"])).fetchone():
                    continue
                connection.execute("""INSERT INTO request_events
                    (request_type, request_id, fdr_id, actor, decision, remarks, created_at)
                    VALUES (?, ?, ?, 'Legacy actor unknown', 'Submitted', ?, ?)""",
                    (request_type, row["id"], row["fdr_id"], row["notes"] if request_type == "renewal" else row["reason"], row["created_at"]))
                if row["status"] in ("Approved", "Rejected") and row["reviewed_at"]:
                    connection.execute("""INSERT INTO request_events
                        (request_type, request_id, fdr_id, actor, decision, remarks, created_at)
                        VALUES (?, ?, ?, 'Legacy actor unknown', ?, ?, ?)""",
                        (request_type, row["id"], row["fdr_id"], row["status"], row["review_note"] or "", row["reviewed_at"]))
        connection.commit()


initialize_database()


def session_user(request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    with closing(get_connection()) as connection:
        row = connection.execute("""SELECT users.id, users.username, users.display_name, users.role
            FROM sessions JOIN users ON users.id=sessions.user_id
            WHERE sessions.token_hash=? AND sessions.expires_at>?""", (token_digest(token), int(time.time()))).fetchone()
        return dict(row) if row else None


@app.middleware("http")
async def protect_portal(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin and origin != "http://localhost:5173":
            return JSONResponse({"detail": "Untrusted request origin"}, status_code=403)
    if request.url.path in ("/", "/auth/login"):
        return await call_next(request)
    user = session_user(request)
    if not user:
        return JSONResponse({"detail": "Please sign in to continue"}, status_code=401)
    request.state.user = user
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path not in ("/auth/logout", "/assistant/query"):
        required_role = "officer" if request.url.path.endswith("/review") else "college"
        if user["role"] != required_role:
            return JSONResponse({"detail": f"This action requires a {required_role} account"}, status_code=403)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginData(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)
    role: Literal["college", "officer"]


@app.post("/auth/login")
def login(data: LoginData, request: Request, response: Response):
    now = int(time.time())
    client = request.client.host if request.client else "unknown"
    with closing(get_connection()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        attempt = connection.execute("SELECT * FROM login_attempts WHERE client=?", (client,)).fetchone()
        recent_failures = attempt["failures"] if attempt and now - attempt["window_started"] < 300 else 0
        if recent_failures >= 10:
            raise HTTPException(429, "Too many sign-in attempts. Try again in five minutes.")
        user = connection.execute("SELECT * FROM users WHERE username=?", (data.username.strip(),)).fetchone()
        salt = user["password_salt"] if user else "00" * 16
        expected = user["password_hash"] if user else "00" * 32
        valid = hmac.compare_digest(password_digest(data.password, salt), expected)
        if not user or not valid or user["role"] != data.role:
            connection.execute("INSERT OR REPLACE INTO login_attempts VALUES (?, ?, ?)",
                (client, recent_failures + 1, attempt["window_started"] if recent_failures else now))
            connection.commit()
            raise HTTPException(401, "Invalid username, password, or account type")
        connection.execute("DELETE FROM login_attempts WHERE client=?", (client,))
        old_token = request.cookies.get(SESSION_COOKIE)
        if old_token:
            connection.execute("DELETE FROM sessions WHERE token_hash=?", (token_digest(old_token),))
        connection.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
        token = secrets.token_urlsafe(32)
        connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (token_digest(token), user["id"], now + SESSION_SECONDS))
        connection.commit()
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                            secure=COOKIE_SECURE, samesite="lax", path="/")
        response.headers["Cache-Control"] = "no-store"
        return {key: user[key] for key in ("id", "username", "display_name", "role")}


@app.get("/auth/me")
def current_user(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return request.state.user


@app.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response):
    with closing(get_connection()) as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash=?", (token_digest(request.cookies[SESSION_COOKIE]),))
        connection.commit()
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=COOKIE_SECURE, samesite="lax")


def audit_actor(request):
    user = request.state.user
    return f"{user['display_name']} ({user['username']})"


# Pydantic models
class FDRCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False)

    institution_name: str = Field(min_length=2)
    certificate_number: str = Field(min_length=2)
    bank_name: str = Field(min_length=2)
    amount: float = Field(gt=0)
    maturity_date: date


class FDR(FDRCreate):
    id: int
    status: Literal[
        "Draft",
        "Submitted",
        "Under Review",
        "Correction Required",
        "Verified",
        "Rejected",
        "Released",
        "Refunded",
    ]
    review_note: str | None = None


class ReviewAction(BaseModel):
    action: Literal[
        "under_review",
        "verified",
        "correction_required",
        "rejected",
    ]
    review_note: str = ""


# Home endpoint
@app.get("/")
def home():
    return {"message": "FDR Portal API is running"}


# List FDRs
@app.get("/fdrs", response_model=list[FDR])
def list_fdrs():
    with closing(get_connection()) as connection:
        rows = connection.execute(
            "SELECT * FROM fdrs ORDER BY id DESC"
        ).fetchall()

        return [dict(row) for row in rows]


# Create FDR
@app.post("/fdrs", response_model=FDR, status_code=201)
def create_fdr(data: FDRCreate):
    try:
        with closing(get_connection()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT id FROM fdrs WHERE certificate_number = ? COLLATE NOCASE",
                (data.certificate_number,),
            ).fetchone():
                raise HTTPException(409, "Certificate number already exists")
            cursor = connection.execute(
                """
                INSERT INTO fdrs (
                    institution_name,
                    certificate_number,
                    bank_name,
                    amount,
                    maturity_date,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    data.institution_name,
                    data.certificate_number,
                    data.bank_name,
                    data.amount,
                    data.maturity_date.isoformat(),
                    "Draft",
                ),
            )

            connection.commit()

            row = connection.execute(
                "SELECT * FROM fdrs WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()

            return dict(row)

    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="Certificate number already exists",
        )


@app.put("/fdrs/{fdr_id}", response_model=FDR)
def edit_fdr(fdr_id: int, data: FDRCreate):
    with closing(get_connection()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = require_fdr(connection, fdr_id)
        if row["status"] not in ("Draft", "Correction Required"):
            raise HTTPException(400, "Only draft or correction-required FDRs can be edited")
        ensure_certificate_available(connection, data.certificate_number, fdr_id)
        connection.execute(
            """UPDATE fdrs SET institution_name=?, certificate_number=?,
               bank_name=?, amount=?, maturity_date=? WHERE id=?""",
            (data.institution_name, data.certificate_number, data.bank_name,
             data.amount, data.maturity_date.isoformat(), fdr_id),
        )
        connection.commit()
        return dict(require_fdr(connection, fdr_id))


# Submit FDR
@app.post("/fdrs/{fdr_id}/submit")
def submit_fdr(fdr_id: int):
    with closing(get_connection()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM fdrs WHERE id = ?",
            (fdr_id,),
        ).fetchone()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="FDR not found",
            )

        if row["status"] not in ("Draft", "Correction Required"):
            raise HTTPException(
                status_code=400,
                detail="Only draft or corrected FDRs can be submitted",
            )

        connection.execute(
            """
            UPDATE fdrs
            SET status = 'Submitted', review_note = NULL
            WHERE id = ?
            """,
            (fdr_id,),
        )
        connection.commit()

    return {
        "message": "FDR submitted successfully",
        "status": "Submitted",
    }


# Review and verify FDR
@app.post("/fdrs/{fdr_id}/review")
def review_fdr(fdr_id: int, review: ReviewAction):
    with closing(get_connection()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM fdrs WHERE id = ?",
            (fdr_id,),
        ).fetchone()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="FDR not found",
            )

        if review.action == "under_review":
            if row["status"] != "Submitted":
                raise HTTPException(
                    status_code=400,
                    detail="Only submitted FDRs can enter review",
                )
            note = None

        elif review.action == "verified":
            if row["status"] != "Under Review":
                raise HTTPException(
                    status_code=400,
                    detail="FDR must be under review before verification",
                )
            note = review.review_note.strip() or None

        else:
            if row["status"] != "Under Review":
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "FDR must be under review before "
                        "requesting corrections"
                    ),
                )

            if not review.review_note.strip():
                raise HTTPException(
                    status_code=400,
                    detail="Please provide a reason for corrections or rejection",
                )

            note = review.review_note.strip()

        status_map = {
            "under_review": "Under Review",
            "verified": "Verified",
            "correction_required": "Correction Required",
            "rejected": "Rejected",
        }

        new_status = status_map[review.action]

        connection.execute(
            """
            UPDATE fdrs
            SET status = ?, review_note = ?
            WHERE id = ?
            """,
            (new_status, note, fdr_id),
        )
        connection.commit()

    return {
        "message": "Review updated successfully",
        "status": new_status,
        "review_note": note,
    }


def require_fdr(connection, fdr_id):
    row = connection.execute("SELECT * FROM fdrs WHERE id = ?", (fdr_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "FDR not found")
    return row


def ensure_certificate_available(connection, certificate_number, fdr_id):
    duplicate = connection.execute(
        "SELECT id FROM fdrs WHERE certificate_number = ? COLLATE NOCASE AND id != ?",
        (certificate_number, fdr_id),
    ).fetchone()
    if duplicate:
        raise HTTPException(409, "Certificate number already exists")


def require_eligible(connection, fdr_id):
    row = require_fdr(connection, fdr_id)
    if row["status"] != "Verified":
        raise HTTPException(400, "Only verified FDRs are eligible")
    pending = connection.execute(
          """SELECT id FROM renewal_requests WHERE fdr_id=? AND status IN ('Pending', 'Under Review')
              UNION ALL SELECT id FROM release_requests WHERE fdr_id=? AND status IN ('Pending', 'Under Review')""",
        (fdr_id, fdr_id),
    ).fetchone()
    if pending:
        raise HTTPException(409, "This FDR already has a pending request")
    return row


class RenewalCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False)

    kind: Literal["Renewal", "Replacement"]
    certificate_number: str = Field(min_length=2)
    bank_name: str = Field(min_length=2)
    amount: float = Field(gt=0)
    maturity_date: date
    notes: str = Field(default="", max_length=2000)


class ReleaseCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    kind: Literal["Release", "Refund"]
    reason: str = Field(min_length=3, max_length=2000)


class RequestReview(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    action: Literal["start_review", "approve", "reject"]
    review_note: str = Field(default="", max_length=2000)


def record_request_event(connection, request_type, request_id, fdr_id, actor, decision, remarks):
    connection.execute("""INSERT INTO request_events
        (request_type, request_id, fdr_id, actor, decision, remarks) VALUES (?, ?, ?, ?, ?, ?)""",
        (request_type, request_id, fdr_id, actor, decision, remarks))


def request_with_timeline(connection, row, request_type):
    return {**dict(row), "timeline": [dict(event) for event in connection.execute(
        "SELECT * FROM request_events WHERE request_type=? AND request_id=? ORDER BY id",
        (request_type, row["id"]))]}


def review_request(request_type, request_id, review, actor):
    table = "renewal_requests" if request_type == "renewal" else "release_requests"
    with closing(get_connection()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Request not found")
        if row["status"] not in ("Pending", "Under Review"):
            raise HTTPException(409, "Request has already been reviewed")
        if review.action == "start_review" and row["status"] != "Pending":
            raise HTTPException(409, "Request is already under review")
        if review.action == "reject" and not review.review_note:
            raise HTTPException(400, "A rejection reason is required")
        if review.action == "approve":
            fdr = require_fdr(connection, row["fdr_id"])
            if fdr["status"] != "Verified":
                raise HTTPException(400, "FDR is no longer verified")
            if request_type == "renewal":
                ensure_certificate_available(connection, row["certificate_number"], row["fdr_id"])
                connection.execute("""UPDATE fdrs SET certificate_number=?, bank_name=?, amount=?,
                    maturity_date=? WHERE id=?""", (row["certificate_number"], row["bank_name"],
                    row["amount"], row["maturity_date"], row["fdr_id"]))
            else:
                connection.execute("UPDATE fdrs SET status=? WHERE id=?",
                    ("Released" if row["kind"] == "Release" else "Refunded", row["fdr_id"]))
        if row["status"] == "Pending":
            record_request_event(connection, request_type, request_id, row["fdr_id"], actor,
                                 "Under Review", review.review_note if review.action == "start_review" else "")
        status = {"start_review": "Under Review", "approve": "Approved", "reject": "Rejected"}[review.action]
        if review.action != "start_review":
            record_request_event(connection, request_type, request_id, row["fdr_id"], actor, status, review.review_note)
        connection.execute(f"""UPDATE {table} SET status=?, review_note=?,
            reviewed_at=CASE WHEN ?='Under Review' THEN NULL ELSE CURRENT_TIMESTAMP END WHERE id=?""",
            (status, review.review_note, status, request_id))
        connection.commit()
        return {"status": status}


@app.get("/fdrs/{fdr_id}/documents")
def list_documents(fdr_id: int):
    with closing(get_connection()) as connection:
        require_fdr(connection, fdr_id)
        rows = connection.execute(
            """SELECT id, fdr_id, filename, content_type, size, created_at
               FROM documents WHERE fdr_id=? ORDER BY id DESC""", (fdr_id,),
        ).fetchall()
        return [dict(row) for row in rows]


@app.post("/fdrs/{fdr_id}/documents", status_code=201)
async def upload_document(fdr_id: int, file: UploadFile = File(...)):
    with closing(get_connection()) as connection:
        require_fdr(connection, fdr_id)
    filename = Path((file.filename or "").replace("\\", "/")).name
    extension = Path(filename).suffix.lower()
    formats = {
        ".pdf": ("application/pdf", b"%PDF-"),
        ".png": ("image/png", b"\x89PNG\r\n\x1a\n"),
        ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
        ".jpeg": ("image/jpeg", b"\xff\xd8\xff"),
    }
    if extension not in formats or len(filename) > 255:
        raise HTTPException(400, "Choose a PDF, JPEG, or PNG certificate")
    content = await file.read(10 * 1024 * 1024 + 1)
    await file.close()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "Certificate must be 10 MB or smaller")
    content_type, signature = formats[extension]
    if not content.startswith(signature):
        raise HTTPException(400, "The file contents do not match its PDF or image format")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = uuid4().hex + extension
    stored_path = UPLOAD_DIR / stored_name
    try:
        stored_path.write_bytes(content)
        with closing(get_connection()) as connection:
            cursor = connection.execute(
                """INSERT INTO documents (fdr_id, filename, stored_name, content_type, size)
                   VALUES (?, ?, ?, ?, ?)""",
                (fdr_id, filename, stored_name, content_type, len(content)),
            )
            connection.commit()
            document_id = cursor.lastrowid
    except Exception:
        stored_path.unlink(missing_ok=True)
        raise
    return {"id": document_id, "filename": filename, "size": len(content)}


@app.get("/documents/{document_id}/download")
def download_document(document_id: int):
    with closing(get_connection()) as connection:
        row = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    if row is None or not (UPLOAD_DIR / row["stored_name"]).is_file():
        raise HTTPException(404, "Certificate file not found")
    return FileResponse(UPLOAD_DIR / row["stored_name"], media_type=row["content_type"], filename=row["filename"])


@app.get("/renewal-requests")
def list_renewals():
    with closing(get_connection()) as connection:
        return [request_with_timeline(connection, row, "renewal") for row in connection.execute(
            """SELECT requests.*, fdrs.institution_name
               FROM renewal_requests AS requests JOIN fdrs ON fdrs.id=requests.fdr_id
               ORDER BY requests.id DESC"""
        ).fetchall()]


@app.post("/fdrs/{fdr_id}/renewal-requests", status_code=201)
def create_renewal(fdr_id: int, data: RenewalCreate, request: Request):
    with closing(get_connection()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = require_eligible(connection, fdr_id)
        ensure_certificate_available(connection, data.certificate_number, fdr_id)
        if data.kind == "Renewal" and data.maturity_date <= date.fromisoformat(row["maturity_date"]):
            raise HTTPException(400, "Renewal maturity must be later than the current maturity date")
        cursor = connection.execute(
            """INSERT INTO renewal_requests
               (fdr_id, kind, certificate_number, bank_name, amount, maturity_date,
                previous_certificate_number, previous_bank_name, previous_amount,
                previous_maturity_date, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fdr_id, data.kind, data.certificate_number, data.bank_name, data.amount,
             data.maturity_date.isoformat(), row["certificate_number"], row["bank_name"],
             row["amount"], row["maturity_date"], data.notes),
        )
        record_request_event(connection, "renewal", cursor.lastrowid, fdr_id, audit_actor(request), "Submitted", data.notes)
        connection.commit()
        return {"id": cursor.lastrowid, "status": "Pending"}


@app.post("/renewal-requests/{request_id}/review")
def review_renewal(request_id: int, review: RequestReview, request: Request):
    return review_request("renewal", request_id, review, audit_actor(request))


@app.get("/release-requests")
def list_releases():
    with closing(get_connection()) as connection:
        return [request_with_timeline(connection, row, "release") for row in connection.execute(
            """SELECT requests.id, requests.fdr_id, requests.kind, requests.reason,
                      requests.status, requests.review_note, requests.created_at, requests.reviewed_at,
                      fdrs.institution_name,
                      COALESCE(requests.certificate_number, fdrs.certificate_number) AS certificate_number,
                      COALESCE(requests.amount, fdrs.amount) AS amount
               FROM release_requests AS requests JOIN fdrs ON fdrs.id=requests.fdr_id
               ORDER BY requests.id DESC"""
        ).fetchall()]


@app.post("/fdrs/{fdr_id}/release-requests", status_code=201)
def create_release(fdr_id: int, data: ReleaseCreate, request: Request):
    with closing(get_connection()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        fdr = require_eligible(connection, fdr_id)
        cursor = connection.execute(
            "INSERT INTO release_requests (fdr_id, kind, reason, certificate_number, amount) VALUES (?, ?, ?, ?, ?)",
            (fdr_id, data.kind, data.reason, fdr["certificate_number"], fdr["amount"]),
        )
        record_request_event(connection, "release", cursor.lastrowid, fdr_id, audit_actor(request), "Submitted", data.reason)
        connection.commit()
        return {"id": cursor.lastrowid, "status": "Pending"}


@app.post("/release-requests/{request_id}/review")
def review_release(request_id: int, review: RequestReview, request: Request):
    return review_request("release", request_id, review, audit_actor(request))


def portfolio_intelligence(connection, today=None):
    today = today or date.today()
    records = []
    weights = {"Critical": 100, "High": 65, "Upcoming": 25, "Safe": 0}
    for row in connection.execute("SELECT * FROM fdrs WHERE status='Verified' ORDER BY maturity_date, id"):
        record = dict(row)
        days = (date.fromisoformat(row["maturity_date"]) - today).days
        risk = "Critical" if days <= 7 else "High" if days <= 30 else "Upcoming" if days <= 90 else "Safe"
        record.update(days_remaining=days, risk=risk,
                      suggested_action="Initiate release" if days <= 0 else "Review renewal" if days <= 30 else "Monitor")
        records.append(record)
    total = sum(record["amount"] for record in records)
    return {
        "as_of": today.isoformat(), "records": records, "total_principal": total,
        "risk_score": round(sum(record["amount"] * weights[record["risk"]] for record in records) / total, 1) if total else 0,
        "categories": {risk: sum(record["risk"] == risk for record in records) for risk in weights},
        "windows": [{"days": window,
                     "amount": sum(record["amount"] for record in records if 0 <= record["days_remaining"] <= window),
                     "count": sum(0 <= record["days_remaining"] <= window for record in records)} for window in (7, 30, 90)],
        "overdue_principal": sum(record["amount"] for record in records if record["days_remaining"] < 0),
        "rules": "Verified FDRs only. Critical: overdue or 0-7 days (100 points); High: 8-30 days (65); Upcoming: 31-90 days (25); Safe: over 90 days (0). Score = sum(principal x points) / total principal. Windows include today and exclude overdue records. Suggestions require an eligibility review; no action is automatic.",
    }


@app.get("/intelligence")
def get_intelligence():
    with closing(get_connection()) as connection:
        return portfolio_intelligence(connection)


class AssistantQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    action: Literal["maturing_7", "maturing_30", "maturing_90", "attention", "pending_releases", "top_bank"] | None = None
    question: str = Field(default="", max_length=500)


@app.post("/assistant/query")
def query_assistant(query: AssistantQuery):
    action = query.action
    question = query.question.lower().rstrip("?.!").strip()
    if not action:
        maturity = re.fullmatch(r"how much (?:money|principal) matures in the next (7|30|90) days", question)
        if maturity:
            action = f"maturing_{maturity.group(1)}"
        else:
            action = {
                "which fdrs need immediate attention": "attention",
                "show all pending release requests": "pending_releases",
                "which bank holds the highest principal amount": "top_bank",
            }.get(question)
    with closing(get_connection()) as connection:
        portfolio = portfolio_intelligence(connection)
        records = portfolio["records"]
        amount = 0
        if action and action.startswith("maturing_"):
            days = int(action.split("_")[1])
            records = [record for record in records if 0 <= record["days_remaining"] <= days]
            answer = f"Verified principal maturing from today through the next {days} days; overdue FDRs excluded."
        elif action == "attention":
            records = [record for record in records if record["risk"] == "Critical"]
            answer = "Immediate attention: verified FDRs overdue or maturing within 7 days."
        elif action == "pending_releases":
            records = [dict(row) for row in connection.execute("""SELECT fdrs.id, fdrs.institution_name,
                COALESCE(requests.certificate_number, fdrs.certificate_number) AS certificate_number,
                COALESCE(requests.amount, fdrs.amount) AS amount, requests.id AS request_id,
                requests.kind, requests.status FROM release_requests AS requests
                JOIN fdrs ON fdrs.id=requests.fdr_id WHERE requests.status IN ('Pending', 'Under Review')
                ORDER BY requests.id DESC""")]
            answer = "Open release and refund requests, including those under review. Amounts are request snapshots."
        elif action == "top_bank":
            totals = {}
            for record in records:
                key = record["bank_name"].strip().casefold()
                totals[key] = totals.get(key, 0) + record["amount"]
            highest = max(totals.values(), default=0)
            leaders = {key for key, total in totals.items() if total == highest}
            records = [record for record in records if record["bank_name"].strip().casefold() in leaders]
            names = sorted({record["bank_name"] for record in records})
            answer = f"Highest verified principal: {', '.join(names)}." if names else "No verified bank holdings yet."
            if len(leaders) > 1:
                answer += " Banks are tied; the amount below is their combined principal."
        else:
            return {"supported": False, "as_of": portfolio["as_of"], "answer": "That question is not supported. Choose a portfolio query below.",
                    "amount": None, "count": 0, "records": []}
        amount = sum(record["amount"] for record in records)
        return {"supported": True, "as_of": portfolio["as_of"], "answer": answer,
                "amount": amount, "count": len(records), "records": records}


# Maturity alerts for the React dashboard
@app.get("/fdrs/alerts")
def get_maturity_alerts():
    today = date.today()
    alerts = []

    with closing(get_connection()) as connection:
        rows = connection.execute(
            """
            SELECT id, certificate_number, institution_name,
                   maturity_date, status
            FROM fdrs
            WHERE status = 'Verified'
            ORDER BY maturity_date ASC
            """
        ).fetchall()

    for row in rows:
        maturity = date.fromisoformat(row["maturity_date"])
        days_remaining = (maturity - today).days

        if days_remaining < 0:
            alert_type = "Matured"
        elif days_remaining <= 30:
            alert_type = "Upcoming"
        else:
            continue

        alerts.append({
            "id": row["id"],
            "certificate_number": row["certificate_number"],
            "institution_name": row["institution_name"],
            "maturity_date": row["maturity_date"],
            "days_remaining": days_remaining,
            "alert_type": alert_type,
            "status": row["status"],
        })

    return {
        "today": today.isoformat(),
        "total_alerts": len(alerts),
        "alerts": alerts,
    }


# AUTOMATIC DAILY MATURITY CHECK
def check_maturity_dates():
    today = date.today()

    try:
        with closing(get_connection()) as connection:
            rows = connection.execute(
                """
                SELECT id, certificate_number, institution_name,
                       maturity_date, status
                FROM fdrs
                WHERE status = 'Verified'
                ORDER BY maturity_date ASC
                """
            ).fetchall()

        alerts_found = 0

        for row in rows:
            maturity = date.fromisoformat(row["maturity_date"])
            days_remaining = (maturity - today).days

            # Include matured FDRs and those maturing in 7 days
            if days_remaining <= 7:
                alerts_found += 1

                if days_remaining < 0:
                    message = (
                        f"OVERDUE by {abs(days_remaining)} day(s)"
                    )
                elif days_remaining == 0:
                    message = "MATURES TODAY"
                else:
                    message = f"Matures in {days_remaining} day(s)"

                logging.warning(
                    "FDR MATURITY ALERT | Certificate: %s | "
                    "Institution: %s | Maturity: %s | %s",
                    row["certificate_number"],
                    row["institution_name"],
                    row["maturity_date"],
                    message,
                )

        logging.info(
            "Daily maturity check completed. Alerts found: %s",
            alerts_found,
        )

    except Exception:
        logging.exception("Daily FDR maturity check failed")


# Scheduler setup
scheduler = BackgroundScheduler()


@app.on_event("startup")
def start_maturity_scheduler():
    if not scheduler.running:
        scheduler.add_job(
            check_maturity_dates,
            trigger="cron",
            hour=9,
            minute=0,
            id="daily_fdr_maturity_check",
            replace_existing=True,
        )

        scheduler.start()

    logging.info(
        "FDR maturity scheduler started. Daily check scheduled for 09:00."
    )

    # Run once at startup to test immediately
    check_maturity_dates()


@app.on_event("shutdown")
def stop_maturity_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logging.info("FDR maturity scheduler stopped.")
