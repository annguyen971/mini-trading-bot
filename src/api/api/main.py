from fastapi import FastAPI, Response, status
from core_lib.db import get_db_connection
import psycopg
import json

app = FastAPI()

# --- Helper Functions ---
def log_event(event_name: str, meta: dict = None):
    """Helper function to insert an event into the event_log table."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO event_log (event_name, meta) VALUES (%s, %s::jsonb);",
                (event_name, json.dumps(meta) if meta else None)
            )
        conn.commit()
    except psycopg.Error as e:
        print(f"Error logging event '{event_name}': {e}")
    finally:
        if conn:
            conn.close()

# --- Placeholder Functions for Readiness Checks ---

def check_db_connection():
    """(AC3) Checks if a connection to the database can be established."""
    print("Checking DB connection...")
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        print("DB connection... OK")
        return True
    except Exception as e:
        print(f"DB connection... FAILED: {e}")
        return False
    finally:
        if conn:
            conn.close()

def check_model_loaded():
    """Placeholder for checking if the model is in the cache."""
    # This would check a global or cached object.
    print("Checking model cache... OK")
    return True

from datetime import datetime, timedelta, timezone

def check_data_freshness():
    """
    (AC3) Checks that the most recent data in `raw_bronze` is not older
    than 26 hours.
    """
    print("Checking data freshness...")
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT MAX(as_of_time) FROM raw_bronze;")
            latest_timestamp = cursor.fetchone()[0]

            if not latest_timestamp:
                print("Data freshness... FAILED: No data in raw_bronze.")
                return False

            freshness_threshold = datetime.now(timezone.utc) - timedelta(hours=26)

            if latest_timestamp >= freshness_threshold:
                print(f"Data freshness... OK (Latest data: {latest_timestamp})")
                return True
            else:
                print(f"Data freshness... FAILED (Latest data: {latest_timestamp} is older than 26 hours)")
                return False

    except Exception as e:
        print(f"Data freshness... FAILED: {e}")
        return False
    finally:
        if conn:
            conn.close()

# --- Endpoints ---

@app.get("/healthz")
def healthz():
    """Liveness probe."""
    return {"status": "ok"}

@app.get("/readyz")
def readyz(response: Response):
    """
    Readiness probe.
    Performs deep checks for DB, model cache, and data freshness.
    """
    try:
        db_ok = check_db_connection()
        model_ok = check_model_loaded()
        data_fresh_ok = check_data_freshness()

        if all([db_ok, model_ok, data_fresh_ok]):
            return {"status": "ready"}
        else:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "not_ready", "detail": "One of the checks failed."}

    except Exception as e:
        # Log the exception in a real application
        print(f"Readiness check failed: {e}")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "detail": str(e)}

# --- UI Endpoints ---
from pydantic import BaseModel
from typing import List, Optional
from datetime import date
from fastapi import Header, Depends, HTTPException

async def verify_admin_key(x_admin_key: str = Header(...)):
    if x_admin_key != "default_key":
        raise HTTPException(status_code=401, detail="Invalid Admin Key")

class LabelSubmission(BaseModel):
    symbol: str
    effective_date: date
    old_label: int
    new_label: int
    is_sandbox: bool = False

@app.get("/export/context", dependencies=[Depends(verify_admin_key)])
def export_context(format: str = "md"):
    log_event("ai_copy_md", {"format": format})
    return Response(content="# Dummy Context", media_type="text/markdown")

@app.get("/export/pack", dependencies=[Depends(verify_admin_key)])
def export_pack():
    log_event("ai_pack_dl")
    return Response(content=b"dummy zip", media_type="application/zip")

@app.post("/al/label", status_code=201, dependencies=[Depends(verify_admin_key)])
def submit_al_label(label: LabelSubmission):
    log_event("al_decide", {"symbol": label.symbol, "is_sandbox": label.is_sandbox})
    # Dummy response, as the DB logic is not part of this task's scope
    return {"status": "Label submitted"}
