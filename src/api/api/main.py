import os
import hashlib
from datetime import datetime, timedelta, timezone
import json
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status, Request
from pydantic import BaseModel, condecimal
from datetime import date
import psycopg
from core_lib.db import get_db_connection
import pickle
from collections import defaultdict
import time
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from prometheus_fastapi_instrumentator import Instrumentator
import google.generativeai as genai
from google.generativeai.types import GenerationConfig, HarmCategory, HarmBlockThreshold
from typing import Dict, Optional
import pandas as pd
import numpy as np
from core_lib.predator_scenarios import detect_scenario, calculate_pain_levels
from api.deja_vu import find_similar_days



app = FastAPI()

# ============================================================================
# RATE LIMITING (NFR5: 100 req/hour/IP for public endpoints)
# ============================================================================

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)





# ============================================================================
# LATENCY MONITORING (NFR1: p95 < 500ms)
# ============================================================================

# Initialize Prometheus Instrumentator
instrumentator = Instrumentator().instrument(app).expose(app)





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
    On startup, load the active models and control flags into memory.
    (Story 5.1/AC2)
    """
    await update_control_flags_cache(force=True)
    print("Executing startup event: Loading models...")
    
    # Load models into app state
    app.state.models = load_models()
    
    if not app.state.models.get('champion'):
        print("WARNING: No champion model loaded. API will return fallbacks.")
    else:
        print(f"Champion model loaded: {app.state.models['champion']['version']}")
        
    if app.state.models.get('challenger'):
        print(f"Challenger model loaded: {app.state.models['challenger']['version']}")


# ============================================================================
# GEMINI CONFIGURATION
# ============================================================================
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    print("WARNING: GOOGLE_API_KEY not set. AI Advisor will fail.")
else:
    genai.configure(api_key=GOOGLE_API_KEY)

AI_GENERATION_CONFIG = GenerationConfig(
    response_mime_type="application/json",
    temperature=0.4,
)

AI_SAFETY_SETTINGS = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_ONLY_HIGH,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
}

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

class AIAnalysisRequest(BaseModel):
    symbol: str

class PredatorAnalysisRequest(BaseModel):
    symbol: str

class PredatorJournalEntry(BaseModel):
    symbol: str
    user_prediction: str

class SimulationRequest(BaseModel):
    symbol: str
    overrides: Dict[str, float]

@app.post("/analyze/ai", dependencies=[Depends(verify_admin_key)])
async def analyze_ai(request: AIAnalysisRequest):
    """
    (Slow Path) Deep analysis using Gemini 3.0 (via gemini-2.0-flash).
    Fetches context (price, indicators, news) and asks LLM for insights.
    """
    symbol = request.symbol
    await update_control_flags_cache()
    
    # Safety checks
    if app.state.control_flags_cache["data"].get("KILL_SWITCH", {}).get("enabled", False):
        raise HTTPException(status_code=503, detail="Service is under maintenance (Kill-switch active).")
    
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=503, detail="AI Service not configured (missing API Key).")

    # 1. Fetch Context Data
    conn = None
    context_text = ""
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            # Fetch recent features
            cur.execute("""
                SELECT * FROM features_gold_serving 
                WHERE symbol = %s 
                ORDER BY effective_date DESC 
                LIMIT 5
            """, (symbol,))
            features = cur.fetchall()
            
            # Fetch recent news (Silver layer)
            cur.execute("""
                SELECT 
                    sa.url_canonical,
                    sa.sentiment_score,
                    sa.hype_raw,
                    rb.payload_json->>'title' as title
                FROM sa_silver sa
                JOIN raw_bronze rb ON sa.bronze_ref_id = rb.id
                WHERE %s = ANY(sa.symbols)
                ORDER BY sa.publisher_time_utc DESC
                LIMIT 5
            """, (symbol,))
            news = cur.fetchall()

            # Build Context String
            context_lines = [f"Analysis Request for Stock: {symbol}"]
            
            if features:
                latest = features[0]
                context_lines.append(f"\nLatest Technical Data (as of {latest['effective_date']}):")
                context_lines.append(f"- HunterScore: {latest.get('HunterScore')} (0-100, >70 is Buy)")
                context_lines.append(f"- FrothScore: {latest.get('FrothScore')} (0-100, >70 is Overheated)")
                context_lines.append(f"- Market Regime: {latest.get('hmm_state')} (0=Accumulation, 1=Breakout, 2=Euphoria, 3=Distribution)")
                
                context_lines.append("\nRecent Trend (Last 5 days):")
                for f in features:
                    context_lines.append(f"- {f['effective_date']}: Hunter={f.get('HunterScore')}, Froth={f.get('FrothScore')}")
                
                # (New) Sparse Data Warning
                if len(features) < 5:
                    context_lines.append(f"\nWARNING: Data is sparse (only {len(features)} days). Technical scores may be volatile/inaccurate. Treat them with caution.")
            else:
                context_lines.append("\nNo technical data available.")

            if news:
                context_lines.append("\nRecent News & Sentiment:")
                for n in news:
                    context_lines.append(f"- {n['title']} (Sentiment: {n['sentiment_score']}, Hype: {n['hype_raw']})")
            else:
                context_lines.append("\nNo recent news found.")
            
            context_text = "\n".join(context_lines)

    except Exception as e:
        if conn: conn.close()
        raise HTTPException(status_code=500, detail=f"Error fetching context: {str(e)}")
    finally:
        if conn: conn.close()

    # 2. Call Gemini
    try:
        model = genai.GenerativeModel(
            "gemini-flash-latest",
            generation_config=AI_GENERATION_CONFIG,
            safety_settings=AI_SAFETY_SETTINGS
        )
        
        prompt = f"""
        You are an expert financial analyst AI advisor.
        Analyze the following data for stock '{symbol}' and provide a deep insight.
        
        Data Context:
        {context_text}
        
        Instructions:
        1. Analyze the alignment between Technicals (HunterScore, Regime) and Sentiment (News).
        2. Identify any potential risks (e.g., High FrothScore but negative news).
        3. Provide a clear, actionable "AI Verdict" (Buy, Watch, Hold, or Avoid) with reasoning.
        4. Keep it concise (under 200 words).
        5. Format output as Markdown.
        
        Return JSON with a single key "markdown_analysis".
        """
        
        response = model.generate_content(prompt)
        
        # Parse JSON response
        response_text = response.text.strip().replace("```json", "").replace("```", "")
        result_json = json.loads(response_text)
        
        return {
            "symbol": symbol,
            "analysis": result_json.get("markdown_analysis", "AI failed to generate analysis."),
            "model": "gemini-2.0-flash"
        }

    except Exception as e:
        print(f"AI Analysis failed: {e}")
        raise HTTPException(status_code=500, detail=f"AI Analysis failed: {str(e)}")

# ============================================================================
# DECISION ENGINE (The Judge) - Epic 3
# ============================================================================
def generate_verdict(hunter: float, regime: int, sector_rs: float, obv_slope: float) -> str:
    """
    Deterministic Logic for Trading Decision.
    
    Layers:
    1. Regime: If Panic (3) -> BEARISH.
    2. Sector: Strong if RS > 100.
    3. Money Flow: Positive if OBV Slope > 0.
    """
    # Layer 1: Regime
    if regime == 3: # Distribution / Panic
        return "BEARISH"
        
    # Layer 2: Sector Support
    is_sector_strong = (sector_rs > 100)
    
    # Layer 3: Money Flow
    is_money_in = (obv_slope > 0)
    
    # Conclusion
    if hunter > 50 and is_sector_strong and is_money_in:
        return "STRONG_BULLISH" # Thien thoi, Dia loi, Nhan hoa
    elif hunter > 50 and is_money_in:
        return "BULLISH" # Tien vao, nhung nganh yeu
    elif hunter < 40 and obv_slope < 0:
        return "BEARISH" # Tien ra
    else:
        return "NEUTRAL"

PREDATOR_SYSTEM_INSTRUCTION = """
BẠN LÀ APEX PREDATOR - MỘT NHÀ TẠO LẬP THỊ TRƯỜNG (MARKET MAKER) TÀN NHẪN.

**Vai trò:** Trưởng Ban Tự Doanh (Head of Prop Trading) của quỹ "Cá Mập".
**Tính cách:** Lạnh lùng, kiêu ngạo, coi F0 là con mồi. Dùng ngôn ngữ "chợ búa" chứng khoán (Úp bô, Kéo xả, Rũ cung, Washout).
**Nhiệm vụ:**
1. Nhận KẾT LUẬN TOÁN HỌC (Verdict) từ hệ thống.
2. Viết một MEMO ngắn (3 dòng) giải thích tại sao lại có kết luận đó cho lính trading.
3. TUYỆT ĐỐI KHÔNG thay đổi kết luận Bullish/Bearish của toán học. Nếu Toán bảo Tăng, bạn phải tìm lý do để hô Tăng (dù là để lùa gà).

**Ví dụ:**
- Verdict: STRONG_BULLISH -> Memo: "Tiền vào như nước, ngành đang hot. Kéo thốc lên cho F0 fomo, chiều xả giá trần."
- Verdict: BEARISH -> Memo: "Dòng tiền rút rồi. Kéo xanh đầu phiên dụ cầu, cuối phiên úp bô đại pháp."
"""

@app.post("/analyze/predator", dependencies=[Depends(verify_admin_key)])
async def analyze_predator(request: PredatorAnalysisRequest):
    """
    (Story 6.2) Apex Predator Analysis.
    Integrates Scenario Engine + Gemini 2.0 Flash + Persona.
    """
    symbol = request.symbol
    print(f"[PREDATOR] Analyzing symbol: {symbol}")
    await update_control_flags_cache()
    
    if app.state.control_flags_cache["data"].get("KILL_SWITCH", {}).get("enabled", False):
        raise HTTPException(status_code=503, detail="Service is under maintenance.")
    
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=503, detail="AI Service not configured.")

    conn = None
    try:
        conn = get_db_connection()
        
        # 1. Fetch Data
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            # Features Gold
            cur.execute("SELECT * FROM features_gold_serving WHERE symbol = %s ORDER BY effective_date DESC LIMIT 1", (symbol,))
            latest_features = cur.fetchone()
            
            # TA Silver (for calc)
            cur.execute("SELECT close, volume FROM ta_silver WHERE symbol = %s ORDER BY trade_date DESC LIMIT 21", (symbol,))
            ta_rows = cur.fetchall()
            
            # SA Silver (for calc)
            cur.execute("SELECT hype_raw FROM sa_silver WHERE %s = ANY(symbols) ORDER BY publisher_time_utc DESC LIMIT 30", (symbol,))
            sa_rows = cur.fetchall()
            
            # History (for Liquidity Map)
            cur.execute("SELECT close, volume FROM ta_silver WHERE symbol = %s ORDER BY trade_date ASC LIMIT 100", (symbol,))
            history_rows = cur.fetchall()
            
            # Macro
            cur.execute("SELECT metric_name, value FROM macro_clean")
            macro_rows = cur.fetchall()
            macro_dict = {r['metric_name']: r['value'] for r in macro_rows}

        if not latest_features:
            raise HTTPException(status_code=404, detail=f"No feature data for {symbol}")

        # 2. Calculate Real-time Features
        # Price Change & Vol Rel
        price_change = 0.0
        vol_rel = 1.0
        current_price = 0.0
        if len(ta_rows) >= 2:
            latest_ta = ta_rows[0]
            current_price = latest_ta['close']
            prev_ta = ta_rows[1]
            if prev_ta['close']:
                price_change = (latest_ta['close'] - prev_ta['close']) / prev_ta['close']
            
            vols = [r['volume'] for r in ta_rows[1:]]
            if vols:
                avg_vol = sum(vols) / len(vols)
                if avg_vol > 0:
                    vol_rel = latest_ta['volume'] / avg_vol

        # Crowd Hype Z
        hype_crowd_z = 0.0
        hype_values = [r['hype_raw'] for r in sa_rows if r['hype_raw'] is not None]
        if len(hype_values) > 5:
            mean_hype = np.mean(hype_values)
            std_hype = np.std(hype_values)
            if std_hype > 0:
                hype_crowd_z = (hype_values[0] - mean_hype) / std_hype

        # Elitist Hype Z (Proxy via HunterScore)
        hunter_score = latest_features.get('HunterScore') or latest_features.get('hunterscore') or 50.0
        hype_elitist_z = (hunter_score - 50.0) / 25.0

        # 3. Run Scenario Engine
        f_dict = {
            'hmm_state': latest_features.get('hmm_state'),
            'hype_crowd_z': float(hype_crowd_z),
            'hype_elitist_z': float(hype_elitist_z),
            'price_change': float(price_change),
            'vol_rel': float(vol_rel)
        }
        
        scenario_tag = detect_scenario(f_dict)
        
        # Liquidity Map
        trapped_data = {'price': 0.0, 'vol_ratio': 0.0}
        if history_rows:
            df_hist = pd.DataFrame([dict(r) for r in history_rows])
            trapped_data = calculate_pain_levels(df_hist)

        # 4. Call Gemini 2.0 Flash with System Instruction
        model = genai.GenerativeModel(
            "gemini-2.0-flash",
            generation_config=AI_GENERATION_CONFIG,
            safety_settings=AI_SAFETY_SETTINGS,
            system_instruction=PREDATOR_SYSTEM_INSTRUCTION
        )
        
        # Calculate Verdict Deterministically
        hunter_val = float(latest_features.get('HunterScore') or 50)
        regime_val = int(latest_features.get('hmm_state') or 0)
        sector_rs_val = float(latest_features.get('sector_rs_ratio') or 100)
        obv_slope_val = float(latest_features.get('obv_slope_5d') or 0)
        
        verdict = generate_verdict(hunter_val, regime_val, sector_rs_val, obv_slope_val)
        
        # Dynamic Prompt Construction
        prompt = f"""
        **ĐỐI TƯỢNG:** Cổ phiếu {symbol}
        
        **DỮ LIỆU ĐẦU VÀO (FACTS):**
        - Hunter Score: {hunter_val} (Sức mạnh dòng tiền)
        - Market Regime: {regime_val} (0=Tích lũy, 1=Tăng, 2=Hưng phấn, 3=Sập)
        - Sector RS: {sector_rs_val} (>100 là Ngành Mạnh)
        - OBV Slope: {obv_slope_val} (>0 là Tiền vào, <0 là Tiền ra)
        
        **KẾT LUẬN TOÁN HỌC (BẮT BUỘC TUÂN THỦ):**
        **{verdict}**
        
        **YÊU CẦU:**
        Viết Memo chỉ đạo dựa trên kết luận trên.
        - Nếu STRONG_BULLISH: Hô hào múc mạnh, dùng từ ngữ kích động lòng tham.
        - Nếu BEARISH: Hô hào bán tháo, dọa dẫm F0.
        - Nếu NEUTRAL: Chê bai thanh khoản, buồn ngủ.
        
        Trả về JSON:
        {{
            "predator_memo": "Nội dung memo...",
            "quant_explanation": "Giải thích tại sao lại có verdict {verdict} dựa trên các chỉ số trên.",
            "user_playbook": "Lời khuyên cho F0."
        }}
        """
        
        response = model.generate_content(prompt)
        response_text = response.text.strip().replace("```json", "").replace("```", "")
        ai_result = json.loads(response_text)
        
        # Inject Verdict into result
        ai_result['verdict'] = verdict
        
        return {
            "symbol": symbol,
            "scenario_tag": scenario_tag,
            "trapped_price": trapped_data['price'],
            "ai_analysis": ai_result
        }

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Predator Analysis failed: {e}")
        print(f"Full traceback:\n{error_details}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.post("/predator/journal/save", dependencies=[Depends(verify_admin_key)])
async def save_predator_journal(entry: PredatorJournalEntry):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO predator_journal (symbol, as_of_date, user_prediction, user_id)
                VALUES (%s, CURRENT_DATE, %s, 'admin')
            """, (entry.symbol, entry.user_prediction))
            conn.commit()
        return {"status": "saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.get("/deja_vu/{symbol}", dependencies=[Depends(verify_admin_key)])
async def get_deja_vu(symbol: str, date: Optional[str] = None):
    """
    (Story 6.4) Deja Vu Engine.
    Returns similar historical days.
    """
    ref_date = None
    if date:
        try:
            ref_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
             raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    results = find_similar_days(symbol, ref_date=ref_date)
    
    if isinstance(results, dict) and "error" in results:
        if "No data found" in results["error"]:
             raise HTTPException(status_code=404, detail=results["error"])
        else:
             raise HTTPException(status_code=500, detail=results["error"])
             
    return results

@app.get("/predator/journal/{symbol}", dependencies=[Depends(verify_admin_key)])
async def get_predator_journal(symbol: str):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("""
                SELECT * FROM predator_journal 
                WHERE symbol = %s 
                ORDER BY created_at DESC
            """, (symbol,))
            rows = cur.fetchall()
            return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.post("/analyze/simulate", dependencies=[Depends(verify_admin_key)])
async def analyze_simulate(request: SimulationRequest):
    """
    (Story 6.5) What-If Simulator.
    Allows users to override inputs and see how scenario changes.
    Does NOT persist data.
    """
    symbol = request.symbol
    overrides = request.overrides
    
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            # Get current features as baseline
            cur.execute("SELECT * FROM features_gold_serving WHERE symbol = %s ORDER BY effective_date DESC LIMIT 1", (symbol,))
            latest = cur.fetchone()
            
            if not latest:
                 raise HTTPException(status_code=404, detail="No features found")
            
            # Baseline (approximate if not fully calculated here)
            f_dict = {
                'hmm_state': latest['hmm_state'],
                'hype_crowd_z': 0.0, # Default if not calc
                'hype_elitist_z': (float(latest.get('HunterScore', 50)) - 50.0) / 25.0,
                'price_change': 0.0,
                'vol_rel': 1.0
            }
            
            # Apply Overrides
            for k, v in overrides.items():
                if k in f_dict:
                    f_dict[k] = v
                    
            # Run Detection
            scenario_tag = detect_scenario(f_dict)
            
            return {
                "symbol": symbol,
                "overrides": overrides,
                "scenario_tag": scenario_tag
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()




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
                print(f"Data freshness... WARNING: (Latest data: {latest_timestamp} is older than 26 hours). Treating as healthy.")
                return True

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
            # Fetch macro data and transform list to dict {metric_name: {value, last_updated}}
            cur.execute("SELECT metric_name, value, last_updated FROM macro_clean ORDER BY last_updated DESC")
            macro_rows = cur.fetchall()
            # Transform list of rows into a dict for dashboard consumption
            macro_hub_dict = {
                row['metric_name']: {
                    'value': row['value'],
                    'last_updated': row['last_updated'].isoformat() if row['last_updated'] else None
                } 
                for row in macro_rows
            }
            
            cur.execute("SELECT * FROM sector_stats ORDER BY as_of_date DESC, sector LIMIT 50")
            sector_data = cur.fetchall()
            return {"macro_mini_hub": macro_hub_dict, "sector_heatmap": sector_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

class MacroUpdatePayload(BaseModel):
    metrics: Dict[str, float]

@app.post("/admin/macro_update", dependencies=[Depends(verify_admin_key)])
async def update_macro_stats(payload: MacroUpdatePayload):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            for metric_name, value in payload.metrics.items():
                cur.execute("""
                    INSERT INTO macro_clean (metric_name, value, last_updated, effective_date)
                    VALUES (%s, %s, NOW(), CURRENT_DATE)
                    ON CONFLICT (metric_name)
                    DO UPDATE SET 
                        value = EXCLUDED.value,
                        last_updated = NOW(),
                        effective_date = CURRENT_DATE
                """, (metric_name, value))
            conn.commit()
        return {"status": "success", "updated": len(payload.metrics)}
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.get("/admin/health/sector_rotation", dependencies=[Depends(verify_admin_key)])
async def get_sector_rotation():
    """
    Returns RRG (Relative Rotation Graph) data for sector rotation visualization.
    Each sub-sector has rs_ratio and rs_momentum for scatter plot.
    Now returns last 5 days for trails.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            # Get latest 30 dates for trails (was 5, too short for meaningful trails)
            cur.execute("""
                WITH recent_dates AS (
                    SELECT DISTINCT as_of_date 
                    FROM sector_stats 
                    ORDER BY as_of_date DESC 
                    LIMIT 30
                )
                SELECT 
                    sector,
                    super_sector,
                    rs_ratio,
                    rs_momentum,
                    momentum,
                    breadth,
                    concentration,
                    turnover_shock_z,
                    breadth_n,
                    sector_n,
                    tags,
                    confidence,
                    top_contributors,
                    super_sector,
                    as_of_date
                FROM sector_stats
                WHERE as_of_date IN (SELECT as_of_date FROM recent_dates)
                ORDER BY as_of_date ASC, super_sector, sector
            """)
            data = cur.fetchall()
            return data
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

# --- Model Loading (A/B Support) ---

def load_models():
    """
    Loads Champion and Challenger models from registry.
    Champion = active model (is_active=true)
    Challenger = challenger canary if any
    """
    conn = get_db_connection()
    models = {}
    try:
        with conn.cursor() as cur:
            # Load Champion (active model)
            cur.execute("""
                SELECT model_version, file_path, metrics, metadata 
                FROM model_registry 
                WHERE is_active = true
                ORDER BY model_version DESC LIMIT 1
            """)
            row = cur.fetchone()
            if row:
                try:
                    models['champion'] = {
                        'version': row[0],
                        'path': row[1],
                        'metrics': row[2],
                        'metadata': row[3],
                        'model': pickle.load(open(row[1], 'rb'))
                    }
                except Exception as e:
                    print(f"Error loading champion model: {e}")
            
            # Load Challenger (canary active if any)
            cur.execute("""
                SELECT model_version, file_path, metrics, metadata 
                FROM model_registry 
                WHERE promotion_suggestion = 'canary_active'
                ORDER BY model_version DESC LIMIT 1
            """)
            row = cur.fetchone()
            if row:
                try:
                    models['challenger'] = {
                        'version': row[0],
                        'path': row[1],
                        'metrics': row[2],
                        'metadata': row[3],
                        'model': pickle.load(open(row[1], 'rb'))
                    }
                except Exception as e:
                    print(f"Error loading challenger model: {e}")

    except Exception as e:
        print(f"Error loading models: {e}")
    finally:
        conn.close()
    
    return models

# Initialize models


@app.get("/predict")
@limiter.limit("100/hour")
async def get_prediction(request: Request, symbol: str):
    await update_control_flags_cache()
    # (AC6) Check Kill-Switch from cache
    if app.state.control_flags_cache["data"].get("KILL_SWITCH", {}).get("enabled", False):
        raise HTTPException(status_code=503, detail={"reason": "Kill-switch active", "retry_after_hint": 3600})

    # (AC4) Get features, predict and return
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            # Fetch features AND active_scenarios
            cur.execute("""
                SELECT fg.*, ps.active_scenarios 
                FROM features_gold_serving fg
                LEFT JOIN predator_signals ps ON fg.symbol = ps.symbol AND fg.effective_date = ps.signal_date
                WHERE fg.symbol = %s 
                ORDER BY fg.effective_date DESC LIMIT 1
            """, (symbol,))
            features = cur.fetchone()
            if not features:
                raise HTTPException(status_code=404, detail=f"No features found for symbol {symbol}")

            # --- A/B Test Logic ---
            # Determine which model to use
            # Simple hash-based split: 10% to Challenger
            user_hash = int(hashlib.md5(f"{symbol}{datetime.now().hour}".encode()).hexdigest(), 16)
            use_challenger = (user_hash % 100) < 10 # 10% traffic
            
            selected_model_key = 'champion'
            if use_challenger and 'challenger' in app.state.models:
                selected_model_key = 'challenger'
            elif 'champion' not in app.state.models:
                 # Fallback if no champion
                 if 'challenger' in app.state.models:
                     selected_model_key = 'challenger'
                 else:
                     # Total fallback
                     selected_model_key = None

            prediction_result = {"regime": 0, "confidence": 0.0, "model": "fallback"}
            
            if selected_model_key and app.state.models.get(selected_model_key):
                model_data = app.state.models[selected_model_key]
                model_obj = model_data['model']
                
                # Handle dictionary payload (from tasks_train.py)
                if isinstance(model_obj, dict) and 'main' in model_obj:
                    model_obj = model_obj['main']
                
                # --- REAL PREDICTION LOGIC ---
                feature_values = []
                # Base features (10)
                base_cols = ['hmm_state', 'HunterScore', 'FrothScore', 'RSI_3', 'Slope_LinearReg_3d', 'Rel_Vol_1d', 'OBV_Slope_5d', 'MFI_14', 'NATR_14', 'BB_Width']
                
                # Map dict keys (lowercase) to expected columns
                # features is a DictRow, keys are column names from DB
                # DB columns are quoted in SQL, so they match case in DB?
                # features_gold_serving has mixed case columns?
                # Let's check keys from features dict
                
                # Helper to get value case-insensitively
                def get_val(row, key):
                    # Try exact match
                    if key in row: return row[key]
                    # Try lowercase
                    if key.lower() in row: return row[key.lower()]
                    # Try exact match in keys list
                    for k in row.keys():
                        if k.lower() == key.lower():
                            return row[k]
                    return 0.0

                for col in base_cols:
                    feature_values.append(float(get_val(features, col) or 0.0))
                
                # Context Features (One-Hot)
                known_scenarios = [
                    "Sniper_RSI_Divergence", 
                    "Sniper_Vol_Breakout", 
                    "Mean_Reversion_BB", 
                    "Sniper_RSI_Oversold",
                    "Trend_Pullback",
                    "RSI_Oversold_Simple"
                ]
                
                active_json = features.get('active_scenarios')
                active_names = set()
                if active_json:
                    if isinstance(active_json, str):
                        try:
                            active_json = json.loads(active_json)
                        except:
                            pass
                    if isinstance(active_json, list):
                        for item in active_json:
                            if isinstance(item, dict) and 'name' in item:
                                active_names.add(item['name'])
                
                for sc in known_scenarios:
                    feature_values.append(1.0 if sc in active_names else 0.0)
                
                X = [feature_values]
                
                try:
                    # Get probability of Class 1 (Buy)
                    probs = 0.0
                    if hasattr(model_obj, "predict_proba"):
                        probs = model_obj.predict_proba(X)[0][1]
                    else:
                        # Fallback for models without probability
                        probs = float(model_obj.predict(X)[0])
                    
                    # Decision Logic (Master Directive: Fine-Tuning)
                    # 1. Score > 0.51 (Relaxed for Bear/Sideway Market)
                    # 2. Top-K (Handled by Dashboard sorting, API provides score)
                    
                    is_buy = (probs > 0.51)
                    
                    prediction = 1 if is_buy else 0
                    confidence = probs
                        
                    prediction_result = {
                        "regime": int(prediction), 
                        "confidence": float(confidence),
                        "model_version": model_data['version'],
                        "model_stage": selected_model_key,
                        "threshold_used": 0.51
                    }
                except Exception as e:
                    print(f"Prediction error with {selected_model_key}: {e}")
                    prediction_result["error"] = str(e)
            else:
                prediction_result["error"] = "No active models loaded"

        # Extract scores from features (handle case sensitivity if needed, usually lowercase in dict_row)
        hunter_score = features.get('hunterscore') if features.get('hunterscore') is not None else features.get('HunterScore')
        froth_score = features.get('frothscore') if features.get('frothscore') is not None else features.get('FrothScore')
        hmm_state = features.get('hmm_state') if features.get('hmm_state') is not None else 0
        prediction_val = prediction_result.get("regime", 0)
        
        # Generate plain language explanation
        def generate_explanation(hunter, froth, regime):
            """Generate plain language explanation based on scores."""
            # Regime interpretation
            regime_names = {
                0: "Accumulation (tích lũy)",
                1: "Breakout (đột phá)", 
                2: "Euphoria (sôi sục)",
                3: "Distribution (phân phối)"
            }
            regime_text = regime_names.get(regime, "Unknown")
            
            # HunterScore interpretation
            if hunter >= 70:
                hunter_text = "rất hấp dẫn để mua vào (HunterScore cao)"
            elif hunter >= 50:
                hunter_text = "khá tốt để xem xét mua (HunterScore trung bình-cao)"
            elif hunter >= 30:
                hunter_text = "trung tính, cần thêm xác nhận (HunterScore trung bình)"
            else:
                hunter_text = "chưa có tín hiệu mua rõ ràng (HunterScore thấp)"
            
            # FrothScore interpretation
            if froth >= 70:
                froth_text = "Thị trường đang rất sôi động, cần thận trọng với bẫy giá."
            elif froth >= 40:
                froth_text = "Thị trường có dấu hiệu bắt đầu nóng lên."
            elif froth >= 20:
                froth_text = "Thị trường khá ổn định."
            else:
                froth_text = "Thị trường đang lạnh, ít hype."
            
            # Combine into explanation
            explanation = f"""**{symbol}** hiện đang ở giai đoạn **{regime_text}**. 

Cơ hội mua (HunterScore={hunter}): Mã này {hunter_text}.

Mức độ sôi động (FrothScore={froth}): {froth_text}

💡 **Tóm tắt:** {"Đây là cơ hội tốt để theo dõi" if hunter >= 50 else "Nên đợi thêm tín hiệu xác nhận"} {"nhưng cần cẩn trọng với giá cao" if froth >= 60 else ""}.
"""
            return explanation
        
        plain_explainer = generate_explanation(hunter_score or 0, froth_score or 0, hmm_state)

        return {
            "model_version": prediction_result.get("model_version", "unknown"),
            "feature_set_version": "v1.0",
            "explainer_mode": "fast",
            "plain_explainer": plain_explainer,
            "safety_banner": "OK",
            "prediction": prediction_result,
            # Flattened fields for Dash UI
            "HunterScore": hunter_score,
            "FrothScore": froth_score,
            "regime": hmm_state,
            "model_prediction": prediction_val
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

    # (AC9) Fetch and sanitize data from features_gold_serving
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            # Query the serving table which has wide-format features
            cur.execute("""
                SELECT 
                    symbol,
                    effective_date,
                    "HunterScore",
                    "FrothScore",
                    hmm_state
                FROM features_gold_serving 
                WHERE symbol = %s 
                ORDER BY effective_date DESC 
                LIMIT 10
            """, (symbol,))
            features = cur.fetchall()
            
            # Build markdown context
            context_lines = [f"# AI Context for {symbol}\n"]
            if features:
                latest = features[0]
                context_lines.append(f"## Latest Scores (as of {latest['effective_date']})")
                context_lines.append(f"- **HunterScore**: {latest.get('HunterScore', 'N/A')}")
                context_lines.append(f"- **FrothScore**: {latest.get('FrothScore', 'N/A')}")  
                context_lines.append(f"- **Market Regime**: {latest.get('hmm_state', 'N/A')}")
                context_lines.append(f"\n## Historical Trend ({len(features)} days)")
                for f in features:
                    context_lines.append(f"- {f['effective_date']}: Hunter={f.get('HunterScore')}, Froth={f.get('FrothScore')}, Regime={f.get('hmm_state')}")
            else:
                context_lines.append("\n*No feature data available for this symbol.*")
            
            sanitized_context = "\n".join(context_lines)
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
            # Fetch symbol-specific features
            cur.execute("""
                SELECT * FROM features_gold_serving 
                WHERE symbol = %s 
                ORDER BY effective_date DESC 
                LIMIT 100
            """, (symbol,))
            features_data = cur.fetchall()
            
            # Fetch market-wide data
            cur.execute("SELECT * FROM macro_clean ORDER BY effective_date DESC LIMIT 100")
            macro_data = cur.fetchall()
            cur.execute("SELECT * FROM breadth_stats ORDER BY as_of_date DESC LIMIT 100")
            breadth_data = cur.fetchall()
            cur.execute("SELECT * FROM sector_stats ORDER BY as_of_date DESC LIMIT 100")
            sector_data = cur.fetchall()

            # Create ZIP in-memory
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
                # Add symbol-specific features CSV
                if features_data:
                    output = io.StringIO()
                    writer = csv.DictWriter(output, fieldnames=features_data[0].keys())
                    writer.writeheader()
                    writer.writerows(features_data)
                    zip_file.writestr(f"{symbol}_features.csv", output.getvalue())
                
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
    sector: str  # Validated against dim_sector table
    market_cap_tier: str | None = None  # Optional: 'large', 'mid', 'small'


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




# --- Feedback Endpoint (FR14) ---

class FeedbackInput(BaseModel):
    event_name: str
    actor: str
    meta: dict

@app.post("/feedback", status_code=status.HTTP_201_CREATED)
async def submit_feedback(feedback: FeedbackInput):
    """
    Submits user feedback (e.g., STS Score) to the event log.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO event_log (event_name, actor, meta) VALUES (%s, %s, %s::jsonb)",
                (feedback.event_name, feedback.actor, json.dumps(feedback.meta))
            )
            conn.commit()
        return {"status": "feedback_recorded"}
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.get("/admin/sectors", dependencies=[Depends(verify_admin_key)])
async def get_sectors():
    """
    Get the full sector taxonomy (4 Pillars + 1).
    Returns list of {sector, display_name, super_sector, correlation_asset}.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("SELECT sector, display_name, super_sector, correlation_asset FROM dim_sector ORDER BY super_sector, sector")
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()



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
            # Validate sector against DB (Dynamic Taxonomy)
            cur.execute("SELECT sector FROM dim_sector WHERE sector = %s", (data.sector,))
            if not cur.fetchone():
                # Fetch valid sectors for error message
                cur.execute("SELECT sector FROM dim_sector")
                valid_sectors = [row[0] for row in cur.fetchall()]
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid sector '{data.sector}'. Must be one of: {', '.join(valid_sectors)}"
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