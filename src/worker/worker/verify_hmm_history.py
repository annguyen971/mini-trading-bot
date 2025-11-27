import os
import polars as pl
import numpy as np
from hmmlearn import hmm
from sklearn.preprocessing import RobustScaler
from core_lib.db import get_db_connection
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def apply_hmm(df: pl.DataFrame) -> pl.DataFrame:
    """
    HMM 2.0 (Smart Lite): Multivariate, Robust & Mapped.
    Inputs: LogReturn, NormVolume, NATR.
    Fixes: Crash on NaN/Inf, Zero Variance, Random State Labels.
    """
    all_states = []
    
    # Cấu hình HMM (GaussianHMM nhẹ nhàng nhưng ổn định)
    n_components = 4
    # Sticky bias: 0.7 để giữ trạng thái ổn định, tránh nhấp nháy
    transmat_prior = np.diag([0.7, 0.7, 0.7, 0.7]) + 0.1 
    
    for symbol, symbol_df in df.group_by("symbol", maintain_order=True):
        symbol_df = symbol_df.sort("effective_date")
        
        # --- 1. FEATURE ENGINEERING (Multivariate Inputs) ---
        
        # A. Log Return (Cắt gọt nhiễu > 10%)
        log_ret = symbol_df.select(
            pl.col("close").log().diff().fill_null(0.0).clip(-0.1, 0.1)
        ).to_numpy().flatten()
        
        # B. Normalized Volume (Log của tỷ lệ Vol/MA20)
        # Xử lý Vol=0 bằng cách +1
        vol = symbol_df.select(pl.col("volume").fill_null(0)).to_numpy().flatten()
        ma20_vol = np.convolve(vol, np.ones(20)/20, mode='same')
        # Tránh chia cho 0
        norm_vol = np.log((vol + 1) / (ma20_vol + 1))
        
        # C. NATR Proxy (High-Low / Close)
        high = symbol_df.select(pl.col("high")).to_numpy().flatten()
        low = symbol_df.select(pl.col("low")).to_numpy().flatten()
        close = symbol_df.select(pl.col("close")).to_numpy().flatten()
        natr = (high - low) / (close + 1e-6)
        
        # Gộp thành ma trận X (N_samples, 3 features)
        X = np.column_stack([log_ret, norm_vol, natr])
        
        # --- 2. ROBUSTNESS (Sanitization & Noise Injection) ---
        
        # Vệ sinh: Thay thế NaN/Inf bằng 0
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        
        # Bơm nhiễu: Cộng nhiễu trắng siêu nhỏ để tránh lỗi ma trận suy biến
        noise = np.random.normal(0, 1e-5, X.shape)
        X_robust = X + noise
        
        # --- 3. FIT & PREDICT ---
        
        if len(X) < 60: # Yêu cầu tối thiểu 3 tháng dữ liệu
            states = np.full(len(X), -1, dtype=int)
        else:
            try:
                # Chuẩn hóa dữ liệu (RobustScaler chống lại outliers tốt hơn StandardScaler)
                scaler = RobustScaler()
                X_scaled = scaler.fit_transform(X_robust)
                
                model = hmm.GaussianHMM(
                    n_components=n_components,
                    covariance_type="diag", # Diag ổn định hơn Full
                    n_iter=100,
                    tol=1e-2,
                    init_params="smc",
                    params="smct",
                    random_state=42
                )
                model.transmat_prior = transmat_prior
                model.fit(X_scaled)
                
                # Predict trạng thái
                hidden_states = model.predict(X_scaled)
                
                # --- 4. STATE MAPPING (Deterministic Logic) ---
                # Means_: [n_components, n_features] (0:Ret, 1:Vol, 2:NATR)
                means = model.means_
                
                # Quy tắc ánh xạ:
                # State 0 (Tích lũy): Biến động (NATR - cột 2) thấp nhất
                state_acc = np.argmin(means[:, 2])
                
                # State 3 (Panic/Sập): Return (Cột 0) thấp nhất (Âm sâu)
                state_panic = np.argmin(means[:, 0])
                
                # State 1 (Uptrend): Return (Cột 0) cao nhất
                state_uptrend = np.argmax(means[:, 0])
                
                # State 2 (Neutral/Sideway): Còn lại
                remaining = set(range(n_components)) - {state_acc, state_panic, state_uptrend}
                state_neutral = list(remaining)[0] if remaining else 0
                
                # Map: Old_ID -> New_ID chuẩn
                state_map = {
                    state_acc: 0,      # Accumulation
                    state_uptrend: 1,  # Uptrend
                    state_neutral: 2,  # Neutral
                    state_panic: 3     # Panic
                }
                
                # Remap kết quả
                states = np.array([state_map.get(s, 2) for s in hidden_states])
                
            except Exception as e:
                logger.warning(f"HMM failed for {symbol}: {e}")
                states = np.full(len(X), -1, dtype=int)

        symbol_df = symbol_df.with_columns(pl.Series("hmm_state", states, dtype=pl.Int32))
        all_states.append(symbol_df)

    if not all_states:
        return df.with_columns(pl.lit(-1, dtype=pl.Int32).alias("hmm_state"))

    return pl.concat(all_states)

def verify_history():
    conn = get_db_connection()
    
    # 1. Load Data
    print("Loading historical data...")
    query = """
    SELECT symbol, trade_date as effective_date, close, volume, high, low
    FROM ta_silver
    ORDER BY symbol, trade_date
    """
    df = pl.read_database(query, conn)
    print(f"Loaded {len(df)} rows.")
    
    # 2. Run HMM
    print("Running HMM 2.0...")
    df_hmm = apply_hmm(df)
    
    # 3. Analyze Periods
    periods = {
        "Bull Market (2021-06-01)": "2021-06-01",
        "Bear Panic (2022-11-15)": "2022-11-15",
        "Sideway (2023-06-01)": "2023-06-01"
    }
    
    for name, date_str in periods.items():
        print(f"\n--- Analyzing {name} ---")
        # Filter for specific date
        # Note: Polars filter needs date object or string match
        # Assuming effective_date is Date or Datetime
        
        # Convert string to date for comparison if needed, or just string match if read as string
        # Let's try string match first, if fails, cast.
        try:
            subset = df_hmm.filter(pl.col("effective_date").cast(pl.String) == date_str)
        except:
             subset = df_hmm.filter(pl.col("effective_date") == date_str)
             
        if len(subset) == 0:
            print(f"No data found for {date_str}")
            continue
            
        counts = subset["hmm_state"].value_counts().sort("hmm_state")
        print(counts)
        
        # Interpretation
        total = len(subset)
        state_0 = subset.filter(pl.col("hmm_state") == 0).height
        state_1 = subset.filter(pl.col("hmm_state") == 1).height
        state_3 = subset.filter(pl.col("hmm_state") == 3).height
        
        print(f"Accumulation (0): {state_0/total:.1%}")
        print(f"Uptrend (1):      {state_1/total:.1%}")
        print(f"Panic (3):        {state_3/total:.1%}")

if __name__ == "__main__":
    verify_history()
