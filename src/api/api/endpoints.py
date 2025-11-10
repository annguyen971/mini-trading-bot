from fastapi import APIRouter, Depends, HTTPException
from core_lib.db import get_db_connection

router = APIRouter()

def get_kill_switch_status():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM control_flags WHERE name = 'KILL_SWITCH';")
        kill_switch = cur.fetchone()
    conn.close()
    if kill_switch and kill_switch[0]:
        raise HTTPException(status_code=503, detail="Service is disabled by KILL_SWITCH")
    return False

@router.get("/predict")
def predict(kill_switch_off: bool = Depends(get_kill_switch_status)):
    # In a real implementation, this would take features as input
    # and use the model from the cache to return a prediction.
    return {"prediction": "ACCUMULATION"}

from datetime import datetime, timedelta, timezone

def check_data_freshness():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(created_at) FROM ta_silver;")
        latest_data = cur.fetchone()[0]
    conn.close()
    if not latest_data or (datetime.now(timezone.utc) - latest_data > timedelta(minutes=15)):
        raise HTTPException(status_code=503, detail="Data is not fresh")
    return True

@router.get("/export/context")
def export_context(format: str = "md", fresh_data: bool = Depends(check_data_freshness)):
    # Sanitized data, no full text
    if format == "json":
        return {"context": "sanitized data"}
    return "## Sanitized Data"

@router.get("/export/pack")
def export_pack(fresh_data: bool = Depends(check_data_freshness)):
    # In a real implementation, this would create a zip file with sanitized data.
    return {"message": "Zip package generated"}

@router.post("/admin/model/activate")
def activate_model(model_version: str):
    conn = get_db_connection()
    with conn.cursor() as cur:
        # Deactivate all models
        cur.execute("UPDATE model_registry SET is_active = false;")
        # Activate the new model
        cur.execute("UPDATE model_registry SET is_active = true WHERE model_version = %s;", (model_version,))
    conn.commit()
    conn.close()
    return {"message": f"Model {model_version} activated."}
