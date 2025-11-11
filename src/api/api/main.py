import os
from datetime import datetime, timedelta, timezone
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel
import psycopg
from core_lib.db import get_db_connection

app = FastAPI()

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
    # (TODO: Jules triển khai logic đọc từ 'macro_clean' và 'sector_stats')
    # (Các bảng này đã có trong init.sql và được migration trong docker-compose.yml)
    return {"macro_mini_hub": [], "sector_heatmap": []} # Placeholder

@app.get("/admin/macro/impact", dependencies=[Depends(verify_admin_key)])
async def get_macro_impact_config():
    # (TODO: Jules SELECT * FROM dim_macro_sector_impact)
    return [] # Placeholder

@app.post("/admin/macro/impact", dependencies=[Depends(verify_admin_key)])
async def update_macro_impact_config(data: list): # (Jules nên tạo Pydantic model)
    # (TODO: Jules triển khai logic UPSERT)
    # Dùng: INSERT ... ON CONFLICT (factor, sector) DO UPDATE SET impact = EXCLUDED.impact;
    return {"status": "updated"} # Placeholder

# --- Serving Endpoints ---

@app.get("/predict")
async def get_prediction(symbol: str):
    # (AC6) Kiểm tra Kill-Switch
    # (TODO: Jules query 'control_flags' (nên cache) để kiểm tra KILL_SWITCH)
    # if kill_switch_is_on:
    # raise HTTPException(status_code=503, detail={"reason": "Kill-switch active", "retry_after_hint": 3600})

    # (AC2) Lấy model đã hot-load từ cache
    # model = app.state.model_cache

    # (AC4) Lấy feature, dự đoán và trả về
    # prediction = model.predict(...)

    return {
        "model_version": "v1.0-placeholder", # (AC4)
        "feature_set_version": "vfs1.0-placeholder", # (AC4)
        "explainer_mode": "fast", # (AC4)
        "plain_explainer": "Placeholder explainer text", # (AC4)
        "safety_banner": "CANARY" # (AC4)
    } # Placeholder

@app.get("/export/context", dependencies=[Depends(verify_admin_key)])
async def export_context(symbol: str):
    # (AC9) Kiểm tra Kill-Switch VÀ Data Freshness
    # (TODO: Jules triển khai các kiểm tra an toàn này)

    # (AC9) Lấy dữ liệu và "sanitize" (làm sạch) (không full text)
    return {"context_md": "# Dữ liệu đã làm sạch\n..."} # Placeholder

@app.get("/export/pack", dependencies=[Depends(verify_admin_key)])
async def export_pack(symbol: str):
    # (AC9) Kiểm tra an toàn tương tự
    # (TODO: Jules tạo file ZIP chứa macro.csv, breadth.csv, v.v.)
    # (AC4) Đảm bảo bao gồm macro.csv, breadth.csv, sector_stats.csv

    # (Story 5.4/AC1) Ghi log sự kiện telemetry
    # (TODO: Jules INSERT vào 'event_log' (event_name='ai_pack_dl'))

    return Response(content="fake_zip_bytes", media_type="application/zip") # Placeholder

class AlLabelInput(BaseModel):
    task_id: int
    action: str # "confirm", "flip", "snooze"
    is_sandbox: bool
    idempotency_key: str # (AC6)

@app.post("/al/label", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_admin_key)])
async def submit_al_label(label: AlLabelInput):
    """
    Ghi nhãn (label) từ người dùng (Admin) vào 'labels_golden'
    hoặc 'labels_silver_sandbox'.
    """
    # (TODO: Jules nên kiểm tra idempotency_key)

    if label.action == "snooze":
        # (TODO: Jules cập nhật task trong al_queue để đẩy về sau)
        return {"status": "snoozed"}

    table_to_insert = "labels_silver_sandbox" if label.is_sandbox else "labels_golden"

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Lấy thông tin từ task gốc
            cur.execute("SELECT symbol, effective_date, state_snorkel FROM al_queue WHERE id = %s", (label.task_id,))
            task_data = cur.fetchone()
            if not task_data:
                raise HTTPException(status_code=404, detail="Task not found")

            symbol, effective_date, old_label = task_data

            # Xác định nhãn mới
            new_label = old_label if label.action == "confirm" else (1 - old_label) # (Giả định flip nhị phân)

            # (AC7) Ghi vào bảng Golden
            cur.execute(
                f"""INSERT INTO {table_to_insert} (symbol, effective_date, old_label, new_label, actor)
                VALUES (%s, %s, %s, %s, 'admin')""",
                (symbol, effective_date, old_label, new_label)
            )

            # Cập nhật task trong queue
            cur.execute("UPDATE al_queue SET status = 'completed' WHERE id = %s", (label.task_id,))

            # (AC8) Kích hoạt (Trigger) huấn luyện (bằng cách cập nhật control_flags)
            if not label.is_sandbox:
                cur.execute("UPDATE control_flags SET updated_at = NOW() WHERE flag = 'trigger_retrain_needed'")

            conn.commit()
            return {"status": "label_recorded"}
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()
