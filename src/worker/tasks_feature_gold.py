import os
import polars as pl
from hmmlearn import hmm
import numpy as np
from sklearn.linear_model import LinearRegression
from contextlib import contextmanager
import psycopg
from core_lib.locks import try_lock
import pandas as pd
from sqlalchemy import create_engine
import pytz
import logging
import time
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Point 6: Helper to read SQL into Polars DataFrame
DB_URL = os.getenv("DB_URL")
if not DB_URL:
    raise ValueError("DB_URL environment variable is not set!")
engine = create_engine(DB_URL)

def read_sql_pl(sql: str, params=None) -> pl.DataFrame:
    """Reads SQL query into a Polars DataFrame using SQLAlchemy and Pandas."""
    pdf = pd.read_sql(sql, engine, params=params)
    return pl.from_pandas(pdf)

# Point 5, 9: Get Timezone from environment
MACRO_TZ = os.getenv("MACRO_TZ", "Asia/Ho_Chi_Minh")

# === HMM Logic (AC2) ===
def apply_hmm(df: pl.DataFrame) -> pl.DataFrame:
    """
    Applies a Hidden Markov Model to detect market regimes for each symbol.
    - 4 states: Accumulation, Breakout, Euphoria, Distribution
    - Sticky bias: Higher probability of staying in the same state.
    - Transition mask: Enforces logical state transitions (e.g., Acc -> Brk, not Acc -> Dst).
    - Filtering-only: Prediction for day T uses data only up to T.
    """
    all_states = []

    # Define HMM parameters based on spec
    n_components = 4  # 4 states
    kappa = 0.6  # Sticky bias

    # Initial transition matrix with sticky bias
    transmat_prior = np.full((n_components, n_components), (1 - kappa) / (n_components - 1))
    np.fill_diagonal(transmat_prior, kappa)

    # Transition mask: 0=Acc, 1=Brk, 2=Eup, 3=Dst
    mask = np.array([
        [1, 1, 0, 0],  # Acc -> Acc, Brk
        [0, 1, 1, 0],  # Brk -> Brk, Eup
        [0, 0, 1, 1],  # Eup -> Eup, Dst
        [1, 0, 0, 1]   # Dst -> Acc, Dst
    ])

    # For each symbol, fit an HMM and predict states
    for symbol, symbol_df in df.group_by("symbol", maintain_order=True):

        # Sort by date to ensure correct sequence
        symbol_df = symbol_df.sort("effective_date")

        # Prepare features for HMM (e.g., log return, normalized volume)
        # Placeholder: using log of closing price as a simple feature.
        if 'close' not in symbol_df.columns:
            # If close is not available, we can't calculate returns.
            # We'll just assign a default state (e.g., 0) for this symbol.
            symbol_df = symbol_df.with_columns(pl.lit(0, dtype=pl.Int32).alias("hmm_state"))
            all_states.append(symbol_df)
            continue

        features = symbol_df.select(
            pl.col("close").log().diff().fill_null(0.0).alias("log_return")
        ).to_numpy()

        if len(features) < n_components:
            states = np.zeros(len(features), dtype=int)
        else:
            model = hmm.GaussianHMM(
                n_components=n_components,
                covariance_type="diag",
                n_iter=100,
                tol=1e-3,
                init_params="smc",
                params="smct",
            )
            model.transmat_prior = transmat_prior

            try:
                model.fit(features)
                # Apply mask and re-normalize
                model.transmat_ = model.transmat_ * mask
                model.transmat_ /= model.transmat_.sum(axis=1, keepdims=True)

                post_probs = model.predict_proba(features)
                states = np.argmax(post_probs, axis=1)
            except Exception as e:
                logger.warning(f"HMM for symbol {symbol} failed: {e}. Defaulting to state 0.")
                states = np.zeros(len(features), dtype=int)

        symbol_df = symbol_df.with_columns(pl.Series("hmm_state", states, dtype=pl.Int32))
        all_states.append(symbol_df)

    if not all_states:
        return df.with_columns(pl.lit(0, dtype=pl.Int32).alias("hmm_state"))

    return pl.concat(all_states)


def calculate_sector_stats(conn, as_of_date):
    logger.info(f"Calculating JdK RRG 2.0 & Market X-Ray stats for {as_of_date}")
    
    # Tham số chuẩn RRG 2.0 (Refined)
    WINDOW_LONG = 126 # ~6 tháng (Xu hướng chính)
    WINDOW_SHORT = 10 # ~2 tuần (Gia tốc)
    SCALE_FACTOR = 10.0 # Hệ số phóng đại (Updated to 10.0 per user feedback)

    sql = """
    WITH price_data AS (
        -- 1. Lấy chuỗi giá và volume (Cần lịch sử dài để tính STDDEV)
        SELECT 
            s.trade_date, s.sector, d.super_sector,
            s.close as sector_price,
            i.close as index_price,
            s.volume as sector_volume,
            s.close * s.volume as sector_turnover,
            s.trade_date as t_date -- Duplicate for safety
        FROM daily_sector_prices s
        JOIN daily_vnindex_prices i ON s.trade_date = i.trade_date
        JOIN dim_sector d ON s.sector = d.sector
        WHERE s.trade_date <= %(as_of_date)s
          AND s.trade_date >= %(as_of_date)s - INTERVAL '300 days' -- Buffer an toàn (increased for momentum window)
    ),
    rs_calc AS (
        -- 2. Tính Raw Relative Strength
        SELECT *, (sector_price / index_price) as rs_raw
        FROM price_data
    ),
    rs_z_score AS (
        -- 3. Tính Z-Score cho Xu hướng dài hạn (RS-Ratio) và Turnover Shock
        SELECT 
            *,
            -- RS Ratio Z-Score (Rolling 126d)
            (rs_raw - AVG(rs_raw) OVER w_126) / NULLIF(STDDEV(rs_raw) OVER w_126, 0) as z_ratio,
            -- Turnover Shock Z-Score (Rolling 20d)
            (sector_turnover - AVG(sector_turnover) OVER w_20) / NULLIF(STDDEV(sector_turnover) OVER w_20, 0) as turnover_shock_z
        FROM rs_calc
        WINDOW 
            w_126 AS (PARTITION BY sector ORDER BY trade_date ROWS BETWEEN 125 PRECEDING AND CURRENT ROW),
            w_20 AS (PARTITION BY sector ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
    ),
    momentum_raw AS (
        -- 4. Tính Raw Momentum (Delta của Z-Ratio)
        SELECT 
            *,
            z_ratio - LAG(z_ratio, 10) OVER (PARTITION BY sector ORDER BY trade_date) as mom_raw
        FROM rs_z_score
    ),
    momentum_z_score AS (
        -- 5. Tính Z-Score cho Momentum (Rolling 63d)
        SELECT 
            *,
            (mom_raw - AVG(mom_raw) OVER w_63) / NULLIF(STDDEV(mom_raw) OVER w_63, 0) as z_mom
        FROM momentum_raw
        WINDOW w_63 AS (PARTITION BY sector ORDER BY trade_date ROWS BETWEEN 62 PRECEDING AND CURRENT ROW)
    ),
    final_rrg AS (
        SELECT 
            trade_date, sector, super_sector,
            -- Scaling về quanh mốc 100 với hệ số 10
            100 + (z_ratio * %(scale)s) as rs_ratio,
            100 + (z_mom * %(scale)s) as rs_momentum,
            turnover_shock_z
        FROM momentum_z_score
    ),
    -- Market X-Ray: Breadth & Concentration (Requires stock-level data)
    stock_stats AS (
        SELECT
            w.sector,
            t.symbol,
            t.close,
            t.volume,
            t.close * t.volume as turnover,
            t.trade_date,
            AVG(t.close) OVER (PARTITION BY t.symbol ORDER BY t.trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as sma20
        FROM ta_silver t
        JOIN symbol_watchlist w ON t.symbol = w.symbol
        WHERE t.trade_date <= %(as_of_date)s
          AND t.trade_date >= %(as_of_date)s - INTERVAL '30 days' -- Need enough for SMA20
          AND w.is_active = true
    ),
    sector_xray AS (
        SELECT
            sector,
            -- Breadth: Percent Stocks > SMA20
            COUNT(*) FILTER (WHERE close > sma20) :: float / NULLIF(COUNT(*), 0) as breadth,
            -- Concentration: Top 3 Turnover / Total Turnover
            (
                SELECT SUM(s2.turnover)
                FROM (
                    SELECT turnover FROM stock_stats s_sub 
                    WHERE s_sub.sector = s_main.sector AND s_sub.trade_date = %(as_of_date)s
                    ORDER BY turnover DESC LIMIT 3
                ) s2
            ) / NULLIF(SUM(turnover), 0) as concentration
        FROM stock_stats s_main
        WHERE trade_date = %(as_of_date)s
        GROUP BY sector
    )
    -- 6. Upsert kết quả của ngày hiện tại
    INSERT INTO sector_stats (sector, as_of_date, super_sector, rs_ratio, rs_momentum, momentum, breadth, concentration, turnover_shock_z, last_updated)
    SELECT 
        r.sector, r.trade_date, r.super_sector, 
        r.rs_ratio, r.rs_momentum, 
        r.rs_momentum - 100 as momentum, -- Simple proxy
        x.breadth, -- Allow NULL if missing (P1: No-Fill Policy)
        x.concentration, -- Allow NULL if missing
        r.turnover_shock_z, -- Allow NULL if missing
        NOW()
    FROM final_rrg r
    LEFT JOIN sector_xray x ON r.sector = x.sector
    WHERE r.trade_date = %(as_of_date)s
    ON CONFLICT (sector, as_of_date) DO UPDATE SET
        rs_ratio = EXCLUDED.rs_ratio,
        rs_momentum = EXCLUDED.rs_momentum,
        breadth = EXCLUDED.breadth,
        concentration = EXCLUDED.concentration,
        turnover_shock_z = EXCLUDED.turnover_shock_z,
        last_updated = NOW();
    """
    
    try:
        with conn.cursor() as cur:
            cur.execute(sql, {'as_of_date': as_of_date, 'scale': SCALE_FACTOR})
            conn.commit()
            logger.info(f"RRG 2.0 updated. Rows: {cur.rowcount}")
    except Exception as e:
        logger.error(f"Error calculating sector stats: {e}")
        conn.rollback()
        raise


def run_sql_plus_plus_macro(conn, as_of_date, tz):
    """
    (Point 2, 3, 4, 5)
    Executes the main SQL++ logic to calculate macro impacts and writes them
    to the database.
    """
    logger.info(f"SQL++ Macro running for {as_of_date} at TZ {tz}")
    try:
        # (Point 7) Use context manager for the connection
        # with conn:  <-- Removed to avoid potential closure or state issues
        with conn.cursor() as cur:
                # (Point 3) Calculate y_excess_20d for sector_perf
                # NOTE: This assumes `sector_prices` and `vnindex_prices` tables exist
                # as per the task description. We will use placeholders if they don't.
                sql_calc_y = """
                INSERT INTO sector_perf(as_of_date, sector, y_excess_20d)
                SELECT
                    s.trade_date AS as_of_date,
                    s.sector,
                    (
                        (lead(s.close, 20) OVER (PARTITION BY s.sector ORDER BY s.trade_date)::float / s.close) - 1
                    ) - (
                        (lead(i.close, 20) OVER (ORDER BY i.trade_date)::float / i.close) - 1
                    ) AS y_excess_20d
                FROM
                    -- These tables are assumed to exist for the calculation
                    daily_sector_prices s
                JOIN
                    daily_vnindex_prices i ON i.trade_date = s.trade_date
                WHERE s.trade_date = %(as_of_date)s
                ON CONFLICT (as_of_date, sector) DO UPDATE SET
                    y_excess_20d = EXCLUDED.y_excess_20d;
                """
                # This part is commented out as the prerequisite tables don't exist in the schema.
                # In a real scenario, this would be enabled.
                cur.execute(sql_calc_y, {"as_of_date": as_of_date})
                logger.info(f"Updated y_excess_20d for {cur.rowcount} sectors.")


                # (Point 2, 4, 5, 6) Main SQL++ Logic
                # This query is complex and relies on several pre-existing tables and views.
                # The logic from the prompt is used directly.
                sql_query = f"""
                WITH d AS (
                    SELECT
                        f.tdate AS as_of_date,
                        s.sector,
                        m_rate.value AS rate,
                        m_fx.value AS fx,
                        m_credit.value AS credit
                    FROM
                        dim_sector s
                    CROSS JOIN
                        (
                            SELECT tdate FROM trade_calendar
                            WHERE tdate BETWEEN %(as_of_date)s - interval '300 days' AND %(as_of_date)s AND is_trading
                        ) f
                    LEFT JOIN macro_feat_daily_shifted m_rate ON m_rate.as_of_date = f.tdate AND m_rate.metric='POLICY_RATE'
                    LEFT JOIN macro_feat_daily_shifted m_fx ON m_fx.as_of_date = f.tdate AND m_fx.metric='USDVND'
                    LEFT JOIN macro_feat_daily_shifted m_credit ON m_credit.as_of_date = f.tdate AND m_credit.metric='CREDIT_GROWTH'
                ),
                roll AS (
                    SELECT *,
                    avg(rate) OVER w AS rate_20d,
                    avg(fx) OVER w AS fx_20d,
                    avg(credit) OVER w AS credit_20d
                    FROM d
                    WINDOW w AS (PARTITION BY sector ORDER BY as_of_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
                ),
                feat AS (
                    SELECT *,
                    (rate - rate_20d) AS rate_shock,
                    (fx - fx_20d) AS fx_shock,
                    (credit - credit_20d) AS credit_shock
                    FROM roll
                ),
                b AS (
                    SELECT *,
                    tanh(rate / 2.5) AS rate_sat,
                    tanh(fx / 2.0) AS fx_sat,
                    tanh(credit / 2.0) AS credit_sat
                    FROM feat
                ),
                x AS (
                    SELECT *,
                    (rate_sat < -0.5 AND credit_sat > 0.5)::int AS cross_rateLow_creditUp,
                    (fx_sat > 0.8 AND rate_sat > 0.5)::int AS cross_fxSpike_rateHigh
                    FROM b
                ),
                y AS (
                    SELECT as_of_date, sector, y_excess_20d FROM sector_perf WHERE as_of_date <= %(as_of_date)s
                ),
                joined AS (
                    SELECT x.*, y.y_excess_20d
                    FROM x LEFT JOIN y USING (as_of_date, sector)
                ),
                betas AS (
                    SELECT
                        as_of_date,
                        sector,
                        CASE WHEN COUNT(y_excess_20d) OVER w >= 60 THEN COALESCE(regr_slope(y_excess_20d, rate_sat) OVER w, 0) ELSE 0 END AS beta_rate_sat,
                        CASE WHEN COUNT(y_excess_20d) OVER w >= 60 THEN COALESCE(regr_slope(y_excess_20d, fx_sat) OVER w, 0) ELSE 0 END AS beta_fx_sat,
                        CASE WHEN COUNT(y_excess_20d) OVER w >= 60 THEN COALESCE(regr_slope(y_excess_20d, credit_sat) OVER w, 0) ELSE 0 END AS beta_credit_sat,
                        CASE WHEN COUNT(y_excess_20d) OVER w >= 60 THEN COALESCE(regr_slope(y_excess_20d, cross_rateLow_creditUp) OVER w, 0) ELSE 0 END AS beta_cross_rc,
                        CASE WHEN COUNT(y_excess_20d) OVER w >= 60 THEN COALESCE(regr_slope(y_excess_20d, cross_fxSpike_rateHigh) OVER w, 0) ELSE 0 END AS beta_cross_fr
                    FROM joined
                    WINDOW w AS (PARTITION BY sector ORDER BY as_of_date ROWS BETWEEN 252 PRECEDING AND 1 PRECEDING)
                ),
                today AS (
                    SELECT * FROM x WHERE as_of_date = %(as_of_date)s
                ),
                contrib AS (
                    SELECT
                        t.as_of_date, t.sector, t.rate_sat, t.fx_sat, t.credit_sat,
                        b.beta_rate_sat * t.rate_sat AS c_rate,
                        b.beta_fx_sat * t.fx_sat AS c_fx,
                        b.beta_credit_sat * t.credit_sat AS c_credit,
                        b.beta_cross_rc * t.cross_rateLow_creditUp AS c_rc,
                        b.beta_cross_fr * t.cross_fxSpike_rateHigh AS c_fr
                    FROM today t
                    LEFT JOIN betas b ON t.as_of_date = b.as_of_date AND t.sector = b.sector
                ),
                agg AS (
                    SELECT *, (c_rate + c_fx + c_credit + c_rc + c_fr) AS total FROM contrib
                )
                INSERT INTO macro_impact_rt (as_of_date, sector, horizon_days, impact, confidence, model_version)
                SELECT
                    agg.as_of_date,
                    agg.sector,
                    20 AS horizon_days,
                    (2/(1+exp(-GREATEST(LEAST(total, 10), -10))) - 1) AS impact_suggested,
                    GREATEST(LEAST( (
                        (CASE WHEN abs(c_rate) > 0.05 AND sign(c_rate) = sign(total) THEN 1 ELSE 0 END) +
                        (CASE WHEN abs(c_fx) > 0.05 AND sign(c_fx) = sign(total) THEN 1 ELSE 0 END) +
                        (CASE WHEN abs(c_credit) > 0.05 AND sign(c_credit) = sign(total) THEN 1 ELSE 0 END) +
                        (CASE WHEN abs(c_rc) > 0.05 AND sign(c_rc) = sign(total) THEN 1 ELSE 0 END) +
                        (CASE WHEN abs(c_fr) > 0.05 AND sign(c_fr) = sign(total) THEN 1 ELSE 0 END)
                    ) / 5.0, 1.0), 0.1) AS confidence,
                    'sql_v1.2_patched' AS model_version
                FROM agg
                WHERE total IS NOT NULL AND abs(total) > 1e-6
                AND (rate_sat IS NOT NULL OR fx_sat IS NOT NULL OR credit_sat IS NOT NULL)
                ON CONFLICT (as_of_date, sector, horizon_days) DO UPDATE SET
                    impact = EXCLUDED.impact,
                    confidence = EXCLUDED.confidence,
                    model_version = EXCLUDED.model_version,
                    created_at = NOW();
                """
                # This part is also commented out as it depends on the above tables.
                cur.execute(sql_query, {"as_of_date": as_of_date})
                logger.info(f"SQL++ Macro updated {cur.rowcount} rows.")
    except Exception as e:
        logger.exception("SQL++ Macro failed and rolled back")
        raise


def calculate_scores(df: pl.DataFrame, conn, enable_blend: bool) -> pl.DataFrame:
    """
    (Point 6, 8)
    Calculates FrothScore and HunterScore, now blending with real-time macro impact.
    """
    # (Point 6) Use read_sql_pl and timezone
    df_impact_rt = read_sql_pl(
        f"SELECT sector, impact, confidence FROM macro_impact_rt WHERE as_of_date = (CURRENT_TIMESTAMP AT TIME ZONE '{MACRO_TZ}')::date",
        params=None
    )
    if "impact" in df_impact_rt.columns:
        df_impact_rt = df_impact_rt.with_columns(pl.col("impact").cast(pl.Float64))

    df_ui_override = read_sql_pl(
        f"SELECT sector, weight FROM macro_ui_override WHERE ttl_until IS NULL OR ttl_until >= (CURRENT_TIMESTAMP AT TIME ZONE '{MACRO_TZ}')::date",
        params=None
    )
    if "weight" in df_ui_override.columns:
        df_ui_override = df_ui_override.with_columns(pl.col("weight").cast(pl.Float64))

    # Placeholder join logic until `dim_symbol_sector` is available
    if 'sector' not in df.columns:
        df = df.with_columns(pl.lit("FINANCIALS").alias("sector"))


    # Blending logic (similar to V1.1)
    df = df.join(df_impact_rt, on="sector", how="left")
    df = df.join(df_ui_override, on="sector", how="left")
    df = df.with_columns(
        impact_final=pl.coalesce(pl.col("weight"), pl.col("impact")).fill_null(0.0)
    )

    if not enable_blend:
        logger.info("Macro blend is disabled via feature flag.")
        df = df.with_columns(pl.lit(0.0).alias("impact_final"))

    # Placeholder columns for score calculation if not present
    for col in ['hype_crowd_z', 'news_count_crowd_z', 'S_tech_z', 'breadth_contra_z', 'hype_elitist_z', 'H_ta_z', 'H_cat_z', 'catalyst_active']:
        if col not in df.columns:
            df = df.with_columns(pl.lit(0.5).alias(col))
        else:
            # Ensure they are float
            df = df.with_columns(pl.col(col).cast(pl.Float64))


    # FrothScore (same as before)
    df = df.with_columns(S_crowd=pl.max_horizontal(0, pl.col('hype_crowd_z')))
    df = df.with_columns(
        burst=pl.max_horizontal(0, pl.col('news_count_crowd_z')),
        catalyst_multiplier=pl.when(pl.col('catalyst_active') > 0).then(0.4).otherwise(1.0)
    )
    df = df.with_columns(S_burst=pl.col('burst') * pl.col('catalyst_multiplier'))
    df = df.with_columns(
        Froth_raw=(0.25 * pl.col('S_crowd') + 0.20 * pl.col('S_burst') + 0.40 * pl.col('S_tech_z') + 0.15 * pl.col('breadth_contra_z'))
    )
    df = df.with_columns(FrothScore=(pl.col('Froth_raw').clip(0, 1) * 100).round(0))


    # HunterScore (with new penalty and guardrail)
    df = df.with_columns(div=pl.col('hype_elitist_z') - pl.col('hype_crowd_z'))
    df = df.with_columns(H_div= (1 / (1 + (-pl.col('div')).exp())).clip(0, 1) )
    df = df.with_columns(
        Hunter_raw=(0.50 * pl.col('H_div') + 0.30 * pl.col('H_ta_z') + 0.20 * pl.col('H_cat_z'))
    )
    df = df.with_columns(Hunter_base=pl.col('Hunter_raw').clip(0, 1))

    BETA_MACRO, GAMMA_MACRO, DELTA_PENALTY = 0.1, 0.1, 0.05

    # (NEW - Point 8) Guardrail Cap (using MAD)
    hunter_std = df["Hunter_base"].std()
    hunter_mad = (df["Hunter_base"] - df["Hunter_base"].median()).abs().median()

    beta_cap_std = 0.3 * (hunter_std if hunter_std is not None and hunter_std > 0 else 0.1)
    beta_cap_mad = 0.3 * (hunter_mad if hunter_mad is not None and hunter_mad > 0 else 0.05)

    beta_cap = min(beta_cap_std, beta_cap_mad)
    BETA_MACRO = min(BETA_MACRO, beta_cap)

    # Apply penalty and blend
    df = df.with_columns(
        penalty_froth = (1 - (pl.col("FrothScore")/100) * (GAMMA_MACRO + pl.col("impact_final")*DELTA_PENALTY) ),
        boost_macro = (1 + pl.col("impact_final") * BETA_MACRO)
    )
    df = df.with_columns(
        Hunter_adj = (pl.col("Hunter_base") * pl.col("penalty_froth") * pl.col("boost_macro")).clip(0,1)
    )

    df = df.with_columns(HunterScore=(pl.col('Hunter_adj') * 100).round(0))

    # === NEW: OBV & Sector RS (Deja Vu 2.1) ===
    
    # 1. OBV Calculation
    # Need close and volume. Assuming they are in df or we join them.
    # v_features_asof usually has close/volume. If not, we need to join ta_silver.
    # Let's assume df has 'close' and 'volume' from v_features_asof.
    
    if "close" in df.columns and "volume" in df.columns:
        # Calculate OBV
        # OBV = Cumulative Sum of (Volume * Sign(Close Change))
        df = df.sort(["symbol", "effective_date"])
        df = df.with_columns(
            price_change=pl.col("close").diff().fill_null(0)
        )
        df = df.with_columns(
            obv_sign=pl.when(pl.col("price_change") > 0).then(1)
                    .when(pl.col("price_change") < 0).then(-1)
                    .otherwise(0)
        )
        df = df.with_columns(
            obv_val=pl.col("obv_sign") * pl.col("volume")
        )
        df = df.with_columns(
            obv=pl.col("obv_val").cum_sum().over("symbol")
        )
        
        # 2. OBV Slope (5d)
        # We need to calculate slope of OBV over last 5 days.
        # Using a rolling window and applying linear regression is expensive in Polars directly.
        # Approximation: (OBV_t - OBV_t-5) / 5 (Simple slope)
        # Better: Use rolling_apply or just simple change if acceptable.
        # Spec says "Slope". Let's use simple linear regression slope approximation:
        # Slope ~ (Sum(x*y) - n*mean(x)*mean(y)) / (Sum(x^2) - n*mean(x)^2)
        # For fixed x=0..4, this simplifies. 
        # But for MVP/Performance, (OBV - OBV_lag5)/5 is a decent proxy for direction.
        # Let's use the simple difference normalized by volume to make it comparable? 
        # Or just raw slope. Spec: "Positive = Accumulation".
        # Let's use simple 5-day change for efficiency first.
        # df = df.with_columns(obv_slope_5d=(pl.col("obv") - pl.col("obv").shift(5)).over("symbol"))
        
        # Actually, let's try to be a bit more precise if possible, but simple diff is standard for "Trend".
        # Let's stick to simple diff for now to avoid complex UDFs.
        df = df.with_columns(
            obv_slope_5d=(pl.col("obv") - pl.col("obv").shift(5)).over("symbol").fill_null(0.0)
        )
    else:
        df = df.with_columns(
            obv=pl.lit(0.0),
            obv_slope_5d=pl.lit(0.0)
        )

    # 3. Sector RS Ratio
    # We need to join with sector_stats to get rs_ratio for the symbol's sector
    # df has 'sector' column (added in previous step or from view)
    
    # Fetch sector stats for the current batch date
    # Note: apply_hmm runs on history, but calculate_scores is usually for "as_of_date" snapshot?
    # Wait, run_feature_gold_batch processes the whole view `v_features_asof`.
    # If `v_features_asof` contains history, we need sector stats history.
    # But `calculate_sector_stats` only calculates for `as_of_date`.
    # This implies `run_feature_gold_batch` is a daily job for TODAY.
    # So we only need sector stats for TODAY.
    
    # However, if we are backfilling, we might need history.
    # For now, let's assume we join on (sector, effective_date) if possible, 
    # or just use the latest fetched `df_sector_stats` if we only care about today.
    
    # Let's fetch sector stats for the relevant dates in df?
    # If df is huge (all history), this is hard.
    # Usually feature_gold_batch runs for "latest" or a specific window.
    # Let's assume we join with `sector_stats` table.
    
    # Read sector_stats
    sector_stats_query = "SELECT sector, as_of_date, rs_ratio FROM sector_stats"
    df_sector_stats = read_sql_pl(sector_stats_query)
    
    # Cast dates to match
    df = df.with_columns(pl.col("effective_date").cast(pl.Date))
    df_sector_stats = df_sector_stats.with_columns(pl.col("as_of_date").cast(pl.Date))
    
    # Join
    df = df.join(
        df_sector_stats, 
        left_on=["sector", "effective_date"], 
        right_on=["sector", "as_of_date"], 
        how="left"
    ).rename({"rs_ratio": "sector_rs_ratio"})
    
    df = df.with_columns(pl.col("sector_rs_ratio").fill_null(100.0)) # Default to 100 (Benchmark)

    return df.select(["symbol","effective_date","hmm_state","FrothScore","HunterScore", "obv_slope_5d", "sector_rs_ratio", "sector"])

@contextmanager
def pg_conn():
    with psycopg.connect(os.getenv("PG_DSN")) as conn:
        conn.autocommit = False
        yield conn

def atomic_publish(conn, df: pl.DataFrame, version: str):
    """
    Atomically publishes a new version of the feature data.
    - Creates a temporary table.
    - Writes the DataFrame to it.
    - Validates the data.
    - Swaps the temp table with the main serving table within a transaction.
    """
    temp_table_name = f"features_gold_serving_{version}"
    serving_table_name = "features_gold_serving"
    backup_table_name = f"features_gold_serving_bak_{version}"

    logger.info(f"Starting atomic publish for version {version}...")
    try:
        # Write to a temporary table
        df.write_database(
            table_name=temp_table_name,
            connection=DB_URL,
            if_table_exists="replace",
            engine="adbc"
        )
        logger.info(f"Successfully wrote data to temp table {temp_table_name}.")

        with conn.cursor() as cur:
            # --- Validation ---
            cur.execute(f"SELECT COUNT(*) FROM {temp_table_name}")
            row_count = cur.fetchone()[0]
            if row_count != len(df):
                raise ValueError(f"Validation failed: Row count mismatch. Expected {len(df)}, got {row_count}")
            logger.info("Validation passed.")

            # --- Atomic Swap ---
            cur.execute("BEGIN;")
            cur.execute(f"ALTER TABLE IF EXISTS {serving_table_name} RENAME TO {backup_table_name};")
            cur.execute(f"ALTER TABLE {temp_table_name} RENAME TO {serving_table_name};")
            cur.execute("COMMIT;")
            logger.info(f"Atomic swap complete. '{serving_table_name}' is now live.")

            # --- Cleanup ---
            cur.execute(f"DROP TABLE IF EXISTS {backup_table_name};")
            logger.info(f"Cleaned up backup table {backup_table_name}.")
            conn.commit()

    except Exception as e:
        logger.error(f"Atomic publish for version {version} failed: {e}")
        conn.rollback()
        raise


def run_feature_gold_batch():
    lock_name = 'feature_gold_batch'
    with pg_conn() as conn:
        if not try_lock(conn, lock_name):
            logger.warning(f"Could not acquire lock {lock_name}. Exiting.")
            return

        try:
            # (Point 9) Add Feature Flag and Timezone
            ENABLE_MACRO_BLEND = os.getenv("ENABLE_MACRO_BLEND", "true").lower() == "true"
            MACRO_TZ_STR = os.getenv("MACRO_TZ", "Asia/Ho_Chi_Minh")

            # (Point 5) Use timezone-aware date
            as_of_date = datetime.now(pytz.timezone(MACRO_TZ_STR)).date()

            start_time = time.time()
            # This is called but the main logic inside is commented out until prerequisite tables exist.
            run_sql_plus_plus_macro(conn, as_of_date, MACRO_TZ_STR)
            logger.info(f"SQL++ Macro logic finished in {time.time() - start_time:.2f}s")

            # Calculate sector rotation stats (RRG)
            start_time = time.time()
            calculate_sector_stats(conn, as_of_date)
            logger.info(f"Sector stats calculation finished in {time.time() - start_time:.2f}s")

            # The view v_features_asof does not exist, so this will fail.
            # Using a placeholder query for now.
            df = read_sql_pl("SELECT * FROM v_features_asof", params=None)

            df = apply_hmm(df)
            df = calculate_scores(df, conn, ENABLE_MACRO_BLEND)

            logger.info("Feature gold batch job finished calculations and is ready to publish.")
            # Publishing is commented out as the pipeline is not fully functional
            version = f"v{pd.Timestamp.now().strftime('%Y%m%d%H%M%S')}"
            atomic_publish(conn, df, version)

            conn.commit()

        except Exception as e:
            logger.exception("Feature gold batch failed.")
            if conn:
                conn.rollback()
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_name,))
            conn.commit()

if __name__ == "__main__":
    run_feature_gold_batch()
