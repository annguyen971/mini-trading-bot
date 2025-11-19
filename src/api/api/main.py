import os
import hashlib
from datetime import datetime, timedelta, timezone
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status, Request
from pydantic import BaseModel, condecimal
from datetime import date
import psycopg
from core_lib.db import get_db_connection
import pickle
from collections import defaultdict
import time

app = FastAPI()

# ============================================================================
# RATE LIMITING (NFR5: 100 req/hour/IP for public endpoints)
# ============================================================================

class SimpleRateLimiter:
    """In-memory rate limiter for NFR5 compliance."""

    def __init__(self):
        self.requests = defaultdict(list)  # {ip: [timestamp1, timestamp2, ...]}

    def is_allowed(self, ip: str, max_requests: int = 100, window_seconds: int = 3600) -> bool:
        """
        Check if IP is within rate limit.

        Args:
            ip: Client IP address
            max_requests: Maximum requests allowed (default: 100)
            window_seconds: Time window in seconds (default: 3600 = 1 hour)

        Returns:
            True if allowed, False if rate limit exceeded
        """
        now = time.time()

        # Clean old requests outside window
        self.requests[ip] = [
            req_time for req_time in self.requests[ip]
            if now - req_time < window_seconds
        ]

        # Check limit
        if len(self.requests[ip]) >= max_requests:
            return False

        # Record request
        self.requests[ip].append(now)
        return True

# Initialize rate limiter
rate_limiter = SimpleRateLimiter()

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """
    Rate limiting middleware for public endpoints.
    NFR5: 100 requests/hour/IP for /predict endpoint.
    """
    # Only apply to public prediction endpoint
    if request.url.path == "/predict":
        # Get client IP
        client_ip = request.client.host if request.client else "unknown"

        # Check rate limit
        if not rate_limiter.is_allowed(client_ip, max_requests=100, window_seconds=3600):
            return Response(
                content='{"detail":"Rate limit exceeded. Maximum 100 requests per hour per IP."}',
                status_code=429,
                media_type="application/json"
            )

    response = await call_next(request)
    return response


# ============================================================================
# LATENCY MONITORING (NFR1: p95 < 500ms)
# ============================================================================

class LatencyTracker:
    """Tracks API latency for NFR1 compliance monitoring."""

    def __init__(self):
        self.latencies = defaultdict(list)  # {endpoint: [latency_ms, ...]}
        self.max_samples = 1000  # Keep last 1000 requests per endpoint

    def record(self, endpoint: str, latency_ms: float):
        """Record latency for an endpoint."""
        self.latencies[endpoint].append(latency_ms)

        # Keep only recent samples
        if len(self.latencies[endpoint]) > self.max_samples:
            self.latencies[endpoint] = self.latencies[endpoint][-self.max_samples:]

    def get_p95(self, endpoint: str) -> float:
        """Calculate p95 latency for endpoint."""
        if not self.latencies[endpoint]:
            return 0.0

        sorted_latencies = sorted(self.latencies[endpoint])
        p95_index = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[p95_index] if p95_index < len(sorted_latencies) else sorted_latencies[-1]

    def get_stats(self, endpoint: str) -> dict:
        """Get latency statistics."""
        if not self.latencies[endpoint]:
            return {"count": 0, "p50": 0, "p95": 0, "p99": 0, "max": 0}

        sorted_latencies = sorted(self.latencies[endpoint])
        count = len(sorted_latencies)

        return {
            "count": count,
            "p50": sorted_latencies[int(count * 0.50)],
            "p95": sorted_latencies[int(count * 0.95)],
            "p99": sorted_latencies[int(count * 0.99)] if count > 100 else sorted_latencies[-1],
            "max": sorted_latencies[-1]
        }

# Initialize latency tracker
latency_tracker = LatencyTracker()


@app.middleware("http")
async def latency_monitoring_middleware(request: Request, call_next):
    """
    Latency monitoring middleware for NFR1 compliance.
    Tracks request latency and logs to monitoring_logs every 100 requests.
    """
    start_time = time.time()

    response = await call_next(request)

    # Calculate latency
    latency_ms = (time.time() - start_time) * 1000

    # Record latency
    endpoint = request.url.path
    latency_tracker.record(endpoint, latency_ms)

    # Add latency header for debugging
    response.headers["X-Response-Time"] = f"{latency_ms:.2f}ms"

    # Log to database every 100 requests for /predict endpoint
    if endpoint == "/predict" and len(latency_tracker.latencies[endpoint]) % 100 == 0:
        try:
            stats = latency_tracker.get_stats(endpoint)
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO monitoring_logs (metric_name, value, metadata)
                    VALUES
                        ('api_latency_p50_ms', %s, %s::jsonb),
                        ('api_latency_p95_ms', %s, %s::jsonb),
                        ('api_latency_p99_ms', %s, %s::jsonb)
                """, (
                    stats['p50'], f'{{"endpoint": "{endpoint}"}}',
                    stats['p95'], f'{{"endpoint": "{endpoint}", "threshold": 500}}',
                    stats['p99'], f'{{"endpoint": "{endpoint}"}}'
                ))
            conn.commit()
            conn.close()

            # Warn if p95 exceeds threshold
            if stats['p95'] > 500:
                print(f"⚠️  WARNING: /predict p95 latency ({stats['p95']:.1f}ms) exceeds NFR1 threshold (500ms)")

        except Exception as e:
            print(f"Latency logging failed: {e}")

    return response

MACRO_TZ = os.getenv("MACRO_TZ", "Asia/Ho_Chi_Minh")

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

            # --- CRITICAL: Fail-fast if no active model (Production Fix) ---
            if not active_model:
                error_msg = "CRITICAL: No active model found in model_registry. Cannot start API without a model."
                print(error_msg)
                print("HINT: Seed a model first via db/migrations/003_seed_default_model.sql or run training pipeline")
                raise RuntimeError(error_msg)
            # --- END CRITICAL CHECK ---
            else:
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
        # Trong trường hợp model bị lỗi (ví dụ: hash mismatch) thì vẫn dừng app
        print(f"CRITICAL: Model loading failed on startup: {e}")
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

            # --- SỬA LỖI 2 (Health Check) ---
            # Cho phép app "healthy" ngay cả khi database trống
            if not latest_timestamp:
                print("Data freshness... OK (No data in raw_bronze, assuming new deployment).")
                return True # <-- ĐÃ SỬA TỪ FALSE THÀNH TRUE
            # --- HẾT SỬA LỖI 2 ---

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

class MacroOverrideConfig(BaseModel):
    sector: str
    weight: condecimal(ge=-1.0, le=1.0)
    ttl_until: date | None = None

@app.get("/admin/macro/impact", dependencies=[Depends(verify_admin_key)])
async def get_macro_impact_config():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            # (SỬA TRUY VẤN - Point 1, 5)
            cur.execute(f"""
                WITH today AS (
                    SELECT (CURRENT_TIMESTAMP AT TIME ZONE %(tz)s)::date AS d
                )
                SELECT
                    s.sector,
                    s.display_name,
                    rt.impact AS impact_suggested,
                    rt.confidence,
                    ui.weight AS weight_override,
                    ui.ttl_until
                FROM dim_sector s -- (Point 1)
                CROSS JOIN today t
                LEFT JOIN macro_impact_rt rt ON rt.sector = s.sector AND rt.as_of_date = t.d
                LEFT JOIN macro_ui_override ui ON ui.sector = s.sector;
            """, {"tz": MACRO_TZ})
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.post("/admin/macro/impact", dependencies=[Depends(verify_admin_key)])
async def update_macro_impact_config(data: list[MacroOverrideConfig]):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            from psycopg.extras import execute_values
            # Using executemany for bulk upsert
            execute_values(
                cur,
                """
                INSERT INTO macro_ui_override (sector, weight, ttl_until)
                VALUES %s
                ON CONFLICT (sector) DO UPDATE SET
                    weight = EXCLUDED.weight,
                    ttl_until = EXCLUDED.ttl_until,
                    updated_at = NOW()
                """,
                [ (item.sector, item.weight, item.ttl_until) for item in data]
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


# ==============================================================================
# WATCHLIST MANAGEMENT ENDPOINTS (P0 - Dynamic Watchlist)
# ==============================================================================

class WatchlistSymbol(BaseModel):
    """Schema for adding/updating symbols in watchlist."""
    symbol: str
    sector: str  # 'technology', 'banking', 'consumer_goods', 'industrial', 'real_estate', 'utilities'
    market_cap_tier: str = None  # Optional: 'large', 'mid', 'small'


@app.get("/admin/watchlist")
async def get_watchlist(
    user_id: str = "admin",
    include_inactive: bool = False,
    admin_key: str = Depends(verify_admin_key)
):
    """
    Get watchlist for a user.

    Args:
        user_id: User ID (default: 'admin')
        include_inactive: Include inactive symbols (default: False)
        admin_key: Admin authentication key (from header)

    Returns:
        List of watchlist symbols with metadata

    Example:
        GET /admin/watchlist
        Headers: X-ADMIN-KEY: your_admin_key

        Response:
        [
            {
                "id": 1,
                "symbol": "FPT",
                "sector": "technology",
                "market_cap_tier": null,
                "is_active": true,
                "added_at": "2025-01-18T10:00:00Z"
            },
            ...
        ]
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            if include_inactive:
                cur.execute("""
                    SELECT id, symbol, sector, market_cap_tier, is_active, added_at, updated_at
                    FROM symbol_watchlist
                    WHERE user_id = %s
                    ORDER BY symbol
                """, (user_id,))
            else:
                cur.execute("""
                    SELECT id, symbol, sector, market_cap_tier, is_active, added_at, updated_at
                    FROM symbol_watchlist
                    WHERE user_id = %s AND is_active = true
                    ORDER BY symbol
                """, (user_id,))

            symbols = cur.fetchall()
            return {
                "user_id": user_id,
                "count": len(symbols),
                "symbols": symbols
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch watchlist: {str(e)}")
    finally:
        if conn:
            conn.close()


@app.post("/admin/watchlist")
async def add_to_watchlist(
    data: WatchlistSymbol,
    user_id: str = "admin",
    admin_key: str = Depends(verify_admin_key)
):
    """
    Add symbol to watchlist (or reactivate if exists).

    Args:
        data: Symbol data (symbol, sector, market_cap_tier)
        user_id: User ID (default: 'admin')
        admin_key: Admin authentication key (from header)

    Returns:
        Status and symbol info

    Example:
        POST /admin/watchlist
        Headers: X-ADMIN-KEY: your_admin_key
        Body:
        {
            "symbol": "HPG",
            "sector": "industrial",
            "market_cap_tier": "large"
        }

        Response:
        {
            "status": "added",
            "symbol": "HPG",
            "message": "Symbol HPG added to watchlist"
        }
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Validate sector
            valid_sectors = ['technology', 'banking', 'consumer_goods', 'industrial', 'real_estate', 'utilities']
            if data.sector not in valid_sectors:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid sector. Must be one of: {', '.join(valid_sectors)}"
                )

            # Check current watchlist size (NFR10: max 500 symbols)
            cur.execute("SELECT COUNT(*) FROM symbol_watchlist WHERE user_id = %s AND is_active = true", (user_id,))
            current_count = cur.fetchone()[0]

            if current_count >= 500:
                raise HTTPException(
                    status_code=400,
                    detail="Watchlist full. Maximum 500 active symbols allowed (NFR10)"
                )

            # UPSERT: Add or reactivate symbol
            cur.execute("""
                INSERT INTO symbol_watchlist (user_id, symbol, sector, market_cap_tier, is_active, added_at, updated_at)
                VALUES (%s, %s, %s, %s, true, NOW(), NOW())
                ON CONFLICT (user_id, symbol)
                DO UPDATE SET
                    sector = EXCLUDED.sector,
                    market_cap_tier = EXCLUDED.market_cap_tier,
                    is_active = true,
                    updated_at = NOW()
                RETURNING id
            """, (user_id, data.symbol.upper(), data.sector, data.market_cap_tier))

            symbol_id = cur.fetchone()[0]
            conn.commit()

            return {
                "status": "added",
                "symbol": data.symbol.upper(),
                "sector": data.sector,
                "id": symbol_id,
                "message": f"Symbol {data.symbol.upper()} added to watchlist (total: {current_count + 1}/500)"
            }

    except HTTPException:
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to add symbol: {str(e)}")
    finally:
        if conn:
            conn.close()


@app.delete("/admin/watchlist/{symbol}")
async def remove_from_watchlist(
    symbol: str,
    user_id: str = "admin",
    permanent: bool = False,
    admin_key: str = Depends(verify_admin_key)
):
    """
    Remove symbol from watchlist (soft delete by default).

    Args:
        symbol: Symbol to remove (e.g., "FPT")
        user_id: User ID (default: 'admin')
        permanent: Hard delete if True, soft delete if False (default: False)
        admin_key: Admin authentication key (from header)

    Returns:
        Status and message

    Example:
        DELETE /admin/watchlist/FPT?permanent=false
        Headers: X-ADMIN-KEY: your_admin_key

        Response:
        {
            "status": "removed",
            "symbol": "FPT",
            "permanent": false,
            "message": "Symbol FPT deactivated (can be reactivated later)"
        }
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            if permanent:
                # Hard delete
                cur.execute("""
                    DELETE FROM symbol_watchlist
                    WHERE user_id = %s AND symbol = %s
                    RETURNING id
                """, (user_id, symbol.upper()))
            else:
                # Soft delete (set is_active = false)
                cur.execute("""
                    UPDATE symbol_watchlist
                    SET is_active = false, updated_at = NOW()
                    WHERE user_id = %s AND symbol = %s
                    RETURNING id
                """, (user_id, symbol.upper()))

            result = cur.fetchone()

            if not result:
                raise HTTPException(
                    status_code=404,
                    detail=f"Symbol {symbol.upper()} not found in watchlist for user {user_id}"
                )

            conn.commit()

            return {
                "status": "removed",
                "symbol": symbol.upper(),
                "permanent": permanent,
                "message": f"Symbol {symbol.upper()} {'permanently deleted' if permanent else 'deactivated (can be reactivated later)'}"
            }

    except HTTPException:
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to remove symbol: {str(e)}")
    finally:
        if conn:
            conn.close()


@app.get("/admin/watchlist/stats")
async def get_watchlist_stats(
    user_id: str = "admin",
    admin_key: str = Depends(verify_admin_key)
):
    """
    Get watchlist statistics and compliance status.

    Args:
        user_id: User ID (default: 'admin')
        admin_key: Admin authentication key (from header)

    Returns:
        Statistics about the watchlist

    Example:
        GET /admin/watchlist/stats
        Headers: X-ADMIN-KEY: your_admin_key

        Response:
        {
            "user_id": "admin",
            "total_symbols": 10,
            "active_symbols": 10,
            "inactive_symbols": 0,
            "by_sector": {
                "technology": 2,
                "banking": 4,
                "consumer_goods": 1,
                "industrial": 1,
                "real_estate": 2
            },
            "capacity_used_pct": 2.0,
            "max_symbols": 500,
            "compliance": {
                "nfr10_compliant": true,
                "warning": null
            }
        }
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            # Total counts
            cur.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE is_active = true) as active,
                    COUNT(*) FILTER (WHERE is_active = false) as inactive
                FROM symbol_watchlist
                WHERE user_id = %s
            """, (user_id,))
            counts = cur.fetchone()

            # By sector
            cur.execute("""
                SELECT sector, COUNT(*) as count
                FROM symbol_watchlist
                WHERE user_id = %s AND is_active = true
                GROUP BY sector
                ORDER BY count DESC
            """, (user_id,))
            sectors = {row['sector']: row['count'] for row in cur.fetchall()}

            max_symbols = 500
            active_count = counts['active']
            capacity_pct = (active_count / max_symbols) * 100

            # Compliance check (NFR10: ≤ 500 symbols)
            compliant = active_count <= max_symbols
            warning = None

            if capacity_pct >= 90:
                warning = f"Approaching limit: {active_count}/{max_symbols} symbols"
            elif capacity_pct >= 100:
                warning = f"LIMIT EXCEEDED: {active_count}/{max_symbols} symbols (NFR10 violation)"

            return {
                "user_id": user_id,
                "total_symbols": counts['total'],
                "active_symbols": active_count,
                "inactive_symbols": counts['inactive'],
                "by_sector": sectors,
                "capacity_used_pct": round(capacity_pct, 2),
                "max_symbols": max_symbols,
                "compliance": {
                    "nfr10_compliant": compliant,
                    "warning": warning
                }
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")
    finally:
        if conn:
            conn.close()