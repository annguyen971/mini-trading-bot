from fastapi import FastAPI, Response, status
from core_lib.db import get_db_connection

import pickle
import hashlib
from contextlib import asynccontextmanager

# --- Model Cache ---
model_cache = {
    "model": None,
    "model_version": None,
    "feature_set_version": None,
}

# --- Secure Hot-Load on Startup ---
def load_model():
    """
    (AC2) Securely loads the active model from the registry.
    - Reads the active model from the database.
    - Verifies the artifact hash against the loaded file.
    - Caches the model.
    """
    print("Attempting to hot-load model on startup...")
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT model_version, file_path, artifact_sha256, feature_set_version FROM model_registry WHERE is_active = true LIMIT 1;")
            model_info = cursor.fetchone()

        if not model_info:
            raise RuntimeError("No active model found in the registry.")

        model_version, file_path, db_hash, feature_set_version = model_info

        print(f"Found active model '{model_version}' at '{file_path}'. Verifying hash...")

        with open(file_path, "rb") as f:
            file_bytes = f.read()
            file_hash = hashlib.sha256(file_bytes).hexdigest()

        if file_hash != db_hash:
            raise SecurityError(f"Model artifact hash mismatch for '{model_version}'. DB: '{db_hash}', File: '{file_hash}'.")

        model = pickle.loads(file_bytes)

        # Update cache
        model_cache["model"] = model
        model_cache["model_version"] = model_version
        model_cache["feature_set_version"] = feature_set_version

        print(f"Successfully loaded and verified model '{model_version}'.")

    except Exception as e:
        print(f"CRITICAL: Model loading failed: {e}")
        # In a real app, you might want to prevent startup or enter a safe mode.
        model_cache["model"] = None
    finally:
        if conn:
            conn.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup
    load_model()
    yield
    # On shutdown
    model_cache.clear()

app = FastAPI(lifespan=lifespan)


# --- Updated Readiness Check ---

def check_db_connection():
    """(AC3) Checks if a connection to the database can be established."""
    # ... (existing implementation)
    return True # Simplified for brevity

def check_model_loaded():
    """Checks if the model is in the cache."""
    if model_cache.get("model") is not None:
        print("Checking model cache... OK")
        return True
    else:
        print("Checking model cache... FAILED")
        return False

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

from . import endpoints

app.include_router(endpoints.router)

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
