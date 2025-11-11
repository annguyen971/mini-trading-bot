from fastapi import FastAPI, Response, status, Depends, Header, HTTPException
from pydantic import BaseModel
from typing import List
import psycopg
from psycopg.rows import dict_row

from core_lib.db import get_db_connection

app = FastAPI()

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

# --- Admin Endpoints for Task 16 ---

class ModelActivation(BaseModel):
    model_version: str
    mode: str

class MacroImpactRow(BaseModel):
    factor: str
    sector: str
    impact: str

class MacroImpactUpdate(BaseModel):
    data: List[MacroImpactRow]

@app.get("/admin/models", dependencies=[Depends(verify_admin_key)])
def get_models():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("SELECT * FROM model_registry ORDER BY created_at DESC;")
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.post("/admin/model/activate", status_code=200, dependencies=[Depends(verify_admin_key)])
def activate_model(activation: ModelActivation):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            if activation.mode == 'promote':
                cur.execute("UPDATE model_registry SET is_active = FALSE;")
                cur.execute("UPDATE model_registry SET is_active = TRUE WHERE model_version = %s;", (activation.model_version,))
                action = 'PROMOTE_PROD'
            elif activation.mode == 'rollback':
                cur.execute("UPDATE model_registry SET is_active = FALSE WHERE model_version = %s;", (activation.model_version,))
                action = 'ROLLBACK'
            else: # canary
                cur.execute("UPDATE model_registry SET promotion_suggestion = 'canary_active' WHERE model_version = %s;", (activation.model_version,))
                action = 'APPROVE_CANARY'
            cur.execute("INSERT INTO model_promotion_history (model_version, action, actor) VALUES (%s, %s, 'admin');", (activation.model_version, action))
        conn.commit()
        return {"status": "ok"}
    except psycopg.Error as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.get("/admin/health/macro_stats", dependencies=[Depends(verify_admin_key)])
def get_macro_stats():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("SELECT metric_name, value, delta FROM macro_clean;")
            macro_hub = cur.fetchall()
            cur.execute("SELECT sector, momentum, breadth FROM sector_stats;")
            sector_heatmap = cur.fetchall()
        return {"macro_mini_hub": macro_hub, "sector_heatmap": sector_heatmap}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.get("/admin/macro/impact", dependencies=[Depends(verify_admin_key)])
def get_macro_impact_config():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("SELECT factor, sector, impact FROM dim_macro_sector_impact;")
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.post("/admin/macro/impact", status_code=200, dependencies=[Depends(verify_admin_key)])
def update_macro_impact(update_data: MacroImpactUpdate):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            upsert_query = """
                INSERT INTO dim_macro_sector_impact (factor, sector, impact)
                VALUES (%(factor)s, %(sector)s, %(impact)s)
                ON CONFLICT (factor, sector) DO UPDATE SET impact = EXCLUDED.impact;
            """
            cur.executemany(upsert_query, [row.dict() for row in update_data.data])
        conn.commit()
        return {"status": "ok"}
    except psycopg.Error as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()
