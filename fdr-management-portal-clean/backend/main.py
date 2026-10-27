

import logging
import sqlite3

from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


# Logging configuration
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="FDR Management Portal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = Path(__file__).parent / "fdr_portal.db"


# Database connection
def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
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

        connection.commit()


initialize_database()


# Pydantic models
class FDRCreate(BaseModel):
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
    ]
    review_note: str | None = None


class ReviewAction(BaseModel):
    action: Literal[
        "under_review",
        "verified",
        "correction_required",
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


# Submit FDR
@app.post("/fdrs/{fdr_id}/submit")
def submit_fdr(fdr_id: int):
    with closing(get_connection()) as connection:
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
                    detail="Please explain which corrections are required",
                )

            note = review.review_note.strip()

        status_map = {
            "under_review": "Under Review",
            "verified": "Verified",
            "correction_required": "Correction Required",
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
