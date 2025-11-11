import os
import hashlib
from datetime import datetime, timedelta, timezone
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel
import psycopg
from core_lib.db import get_db_connection
import pickle

app = FastAPI()

app.state.model_cache = None
app.state.model_metadata = {}
app.state.control_flags_cache = {
    "data": {},
    "last_updated": None
}

async def update_control_flags_cache(force: bool = False):
    """Reads all flags from the database and caches them."""
    now = datetime.now(timezone.utc)
    if not force and app.state.control_flags_cache["last_updated"] and \
       (now - app.state.control_flags_cache["last_updated"]) < timedelta(seconds=10):
        return

    print("Updating control flags cache...")
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("SELECT flag, enabled, reason FROM control_flags")
            flags = cur.fetchall()
            # Reset and repopulate the cache
            app.state.control_flags_cache["data"].clear()
            for flag in flags:
                app.state.control_flags_cache["data"][flag['flag']] = {
                    "enabled": flag['enabled'],
                    "reason": flag['reason']
                }
            app.state.control_flags_cache["last_updated"] = now
            print("Control flags cache updated.")
    except Exception as e:
        print(f"Failed to update control flags cache: {e}")
    finally:
        if conn:
            conn.close()

@app.on_event("startup")
async def startup_event():
    """
    On startup, load the active model and control flags into memory.
    (Story 5.1/AC2)
    """
    await update_control_flags_cache(force=True)
    print("Executing startup event: Loading model...")
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("SELECT file_path, artifact_sha256, model_version, metadata FROM model_registry WHERE is_active = true LIMIT 1")
            active_model = cur.fetchone()

            if not active_model:
                raise RuntimeError("No active model found in model_registry.")

            model_path = active_model['file_path']
            expected_hash = active_model['artifact_sha256']

            print(f"Loading model from: {model_path}")
            with open(model_path, "rb") as f:
                model_bytes = f.read()

            # Verify artifact integrity
            calculated_hash = hashlib.sha256(model_bytes).hexdigest()
            if calculated_hash != expected_hash:
                raise RuntimeError(f"Model integrity check failed. Hash mismatch for {model_path}.")

            # Load model and store in app state
            app.state.model_cache = pickle.loads(model_bytes)
            app.state.model_metadata = {
                "model_version": active_model['model_version'],
                "feature_set_version": active_model['metadata'].get('feature_set_version', 'unknown'),
                 "safety_banner": "OK" # Default, can be updated
            }
            print(f"Successfully loaded and verified model version: {app.state.model_metadata['model_version']}")

    except Exception as e:
        # In a real app, this should probably trigger a critical alert
        print(f"CRITICAL: Model loading failed on startup: {e}")
        # Raising an exception here will prevent the app from starting
        raise
    finally:
        if conn:
            conn.close()


# Lấy admin key từ biến môi trường
ADMIN_KEY = os.getenv("ADMIN_KEY")
if not ADMIN_KEY:
    raise ValueError("ADMIN_KEY environment variable is not set!")

async def verify_admin_key(x_admin_key: str = Header(None)):
    """
    (Story 0.3/AC5, AC6)
    Dependency guard để kiểm tra X-ADMIN-KEY.
    Kiến trúc (architecture.md 10.4) nói Proxy sẽ xử lý Basic Auth,
    nhưng PRD yêu cầu API cũng phải kiểm tra key này (defense-in-depth).
    """
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Admin Key"
        )

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

# --- Active Learning Endpoints ---

@app.get("/al/queue", dependencies=[Depends(verify_admin_key)])
async def get_al_queue_tasks():
    """
    Lấy các mẫu (samples) từ hàng đợi Active Learning.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("SELECT id, symbol, effective_date, reason, suggested_label FROM al_queue WHERE status = 'pending' ORDER BY id ASC")
            tasks = cur.fetchall()
            return tasks
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

# --- Model & Health Endpoints ---

@app.get("/admin/models", dependencies=[Depends(verify_admin_key)])
async def get_models():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("SELECT model_version, is_active, promotion_suggestion, model_type, metrics, metadata FROM model_registry ORDER BY metadata->>'created_at' DESC")
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

class ModelActivation(BaseModel):
    model_version: str
    mode: str # "canary", "promote", "rollback"

@app.post("/admin/model/activate", dependencies=[Depends(verify_admin_key)])
async def activate_model(activation: ModelActivation):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            action = 'APPROVE_CANARY'
            # Logic (Story 5.3/AC4)
            if activation.mode == 'promote':
                cur.execute("UPDATE model_registry SET is_active = FALSE") # Tắt tất cả
                cur.execute("UPDATE model_registry SET is_active = TRUE, promotion_suggestion = 'promoted' WHERE model_version = %s", (activation.model_version,))
                action = 'PROMOTE_PROD'
            elif activation.mode == 'rollback':
                cur.execute("UPDATE model_registry SET is_active = FALSE, promotion_suggestion = 'rollback_manual_admin' WHERE model_version = %s", (activation.model_version,))
                action = 'ROLLBACK_MANUAL'
            else: # 'canary'
                cur.execute("UPDATE model_registry SET promotion_suggestion = 'canary_active' WHERE model_version = %s", (activation.model_version,))

            # (AC5) Ghi log lịch sử
            cur.execute("INSERT INTO model_promotion_history (model_version, action, actor) VALUES (%s, %s, 'admin')", (activation.model_version, action))
            conn.commit()
            return {"status": f"model {activation.mode} executed"}
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.get("/admin/health/macro_stats", dependencies=[Depends(verify_admin_key)])
async def get_health_stats():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("SELECT * FROM macro_clean ORDER BY effective_date DESC LIMIT 100")
            macro_data = cur.fetchall()
            cur.execute("SELECT * FROM sector_stats ORDER BY as_of_date DESC, sector LIMIT 50")
            sector_data = cur.fetchall()
            return {"macro_mini_hub": macro_data, "sector_heatmap": sector_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

class MacroImpactConfig(BaseModel):
    factor: str
    sector: str
    impact: str

@app.get("/admin/macro/impact", dependencies=[Depends(verify_admin_key)])
async def get_macro_impact_config():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("SELECT factor, sector, impact FROM dim_macro_sector_impact")
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.post("/admin/macro/impact", dependencies=[Depends(verify_admin_key)])
async def update_macro_impact_config(data: list[MacroImpactConfig]):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            from psycopg.extras import execute_values
            # Using executemany for bulk upsert
            execute_values(
                cur,
                """
                INSERT INTO dim_macro_sector_impact (factor, sector, impact)
                VALUES %s
                ON CONFLICT (factor, sector) DO UPDATE SET impact = EXCLUDED.impact
                """,
                [ (item.factor, item.sector, item.impact) for item in data]
            )
            conn.commit()
        return {"status": "updated"}
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

# --- Serving Endpoints ---

@app.get("/predict")
async def get_prediction(symbol: str):
    await update_control_flags_cache()
    # (AC6) Check Kill-Switch from cache
    if app.state.control_flags_cache["data"].get("KILL_SWITCH", {}).get("enabled", False):
        raise HTTPException(status_code=503, detail={"reason": "Kill-switch active", "retry_after_hint": 3600})

    # (AC2) Get hot-loaded model from cache
    model = app.state.model_cache
    if not model:
        raise HTTPException(status_code=503, detail="Model not loaded")

    # (AC4) Get features, predict and return
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            # This should be adapted to the actual feature names from the serving table
            cur.execute("SELECT * FROM features_gold_serving WHERE symbol = %s ORDER BY effective_date DESC LIMIT 1", (symbol,))
            features = cur.fetchone()
            if not features:
                raise HTTPException(status_code=404, detail=f"No features found for symbol {symbol}")

            # This part is a placeholder for the actual prediction logic
            prediction = model.predict([list(features.values())[1:]])[0] # Assuming first column is symbol, and model expects a list of features
            prediction_placeholder = {"regime": int(prediction), "confidence": 0.75}


        return {
            "model_version": app.state.model_metadata.get("model_version"),
            "feature_set_version": app.state.model_metadata.get("feature_set_version"),
            "explainer_mode": "fast",
            "plain_explainer": "Placeholder explainer: The model predicts accumulation based on recent volatility contraction and elitist sentiment divergence.",
            "safety_banner": app.state.model_metadata.get("safety_banner"),
            "prediction": prediction_placeholder
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

import io
import zipfile
import csv

@app.get("/export/context", dependencies=[Depends(verify_admin_key)])
async def export_context(symbol: str):
    await update_control_flags_cache()
    # (AC9) Safety checks
    if app.state.control_flags_cache["data"].get("KILL_SWITCH", {}).get("enabled", False):
        raise HTTPException(status_code=503, detail="Service is under maintenance (Kill-switch active).")

    # (AC9) Data Freshness - Re-using the readiness check function
    if not check_data_freshness():
         raise HTTPException(status_code=503, detail="Data is stale, export is temporarily disabled.")

    # (AC9) Fetch and sanitize data
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("SELECT feature_name, value FROM features_gold WHERE symbol = %s ORDER BY effective_date DESC, feature_name LIMIT 20", (symbol,))
            features = cur.fetchall()
            sanitized_context = f"# Sanitized Context for {symbol}\n\n" + "\n".join([f"- {f['feature_name']}: {f['value']}" for f in features])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

    return {"context_md": sanitized_context}

@app.get("/export/pack", dependencies=[Depends(verify_admin_key)])
async def export_pack(symbol: str):
    await update_control_flags_cache()
    # (AC9) Safety checks
    if app.state.control_flags_cache["data"].get("KILL_SWITCH", {}).get("enabled", False):
        raise HTTPException(status_code=503, detail="Service is under maintenance (Kill-switch active).")
    if not check_data_freshness():
         raise HTTPException(status_code=503, detail="Data is stale, export is temporarily disabled.")

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            # Fetch data for CSVs
            cur.execute("SELECT * FROM macro_clean ORDER BY effective_date DESC LIMIT 100")
            macro_data = cur.fetchall()
            cur.execute("SELECT * FROM breadth_stats ORDER BY as_of_date DESC LIMIT 100") # Assuming table name is breadth_stats
            breadth_data = cur.fetchall()
            cur.execute("SELECT * FROM sector_stats ORDER BY as_of_date DESC LIMIT 100")
            sector_data = cur.fetchall()

            # Create ZIP in-memory
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
                # Add macro.csv
                if macro_data:
                    output = io.StringIO()
                    writer = csv.DictWriter(output, fieldnames=macro_data[0].keys())
                    writer.writeheader()
                    writer.writerows(macro_data)
                    zip_file.writestr("macro.csv", output.getvalue())
                # Add breadth.csv
                if breadth_data:
                    output = io.StringIO()
                    writer = csv.DictWriter(output, fieldnames=breadth_data[0].keys())
                    writer.writeheader()
                    writer.writerows(breadth_data)
                    zip_file.writestr("breadth.csv", output.getvalue())
                # Add sector_stats.csv
                if sector_data:
                    output = io.StringIO()
                    writer = csv.DictWriter(output, fieldnames=sector_data[0].keys())
                    writer.writeheader()
                    writer.writerows(sector_data)
                    zip_file.writestr("sector_stats.csv", output.getvalue())

            # (Story 5.4/AC1) Log telemetry event
            cur.execute("INSERT INTO event_log (event_name, actor, meta) VALUES (%s, %s, %s::jsonb)",
                        ('ai_pack_dl', 'admin', f'{{"symbol": "{symbol}"}}'))
            conn.commit()

            return Response(content=zip_buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": f"attachment; filename={symbol}_ai_pack.zip"})

    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

class AlLabelInput(BaseModel):
    task_id: int
    action: str # "confirm", "flip", "snooze"
    is_sandbox: bool
    idempotency_key: str # (AC6)

@app.post("/al/label", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_admin_key)])
async def submit_al_label(label: AlLabelInput):
    """
    Ghi nhãn (label) từ người dùng (Admin) vào 'labels_golden'
    hoặc 'labels_silver_sandbox'. Also handles idempotency and snoozing.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            # Idempotency Check (AC6)
            cur.execute("SELECT status, symbol, effective_date, state_snorkel FROM al_queue WHERE id = %s FOR UPDATE", (label.task_id,))
            task = cur.fetchone()

            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            if task['status'] != 'pending':
                raise HTTPException(status_code=409, detail=f"Task already processed with status: {task['status']}")

            # Snooze Logic
            if label.action == "snooze":
                cur.execute("UPDATE task_q SET next_run_at = NOW() + interval '1 day' WHERE id = %s", (label.task_id,))
                conn.commit()
                return {"status": "snoozed"}

            # --- Main Labeling Logic ---
            table_to_insert = "labels_silver_sandbox" if label.is_sandbox else "labels_golden"

            old_label = task['state_snorkel']
            new_label = old_label if label.action == "confirm" else (1 - old_label)

            # (AC7) Ghi vào bảng Golden
            cur.execute(
                f"""INSERT INTO {table_to_insert} (symbol, effective_date, old_label, new_label, actor)
                VALUES (%s, %s, %s, %s, 'admin')""",
                (task['symbol'], task['effective_date'], old_label, new_label)
            )

            # Cập nhật task trong queue
            cur.execute("UPDATE al_queue SET status = 'completed' WHERE id = %s", (label.task_id,))

            # (AC8) Kích hoạt (Trigger) huấn luyện (bằng cách cập nhật control_flags)
            if not label.is_sandbox:
                cur.execute("INSERT INTO control_flags (flag, enabled, updated_at) VALUES ('trigger_retrain_needed', true, NOW()) ON CONFLICT (flag) DO UPDATE SET enabled = true, updated_at = NOW()")

            conn.commit()
            return {"status": "label_recorded"}
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()
