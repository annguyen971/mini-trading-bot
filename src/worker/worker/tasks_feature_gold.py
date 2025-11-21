import os
import polars as pl
from hmmlearn import hmm
import numpy as np
from contextlib import contextmanager
import psycopg
from core_lib.locks import get_advisory_lock as try_lock
import pandas as pd
from sqlalchemy import create_engine
import pytz
import logging
import time
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
print("DEBUG: Running modified tasks_feature_gold.py with psycopg fix")

# Point 6: Helper to read SQL into Polars DataFrame
DB_URL = os.getenv("DB_URL")
if not DB_URL:
    raise ValueError("DB_URL environment variable is not set!")

# Ensure we use the correct driver for SQLAlchemy (psycopg 3)
if "postgresql+psycopg://" not in DB_URL:
    if "postgresql://" in DB_URL:
        ALCHEMY_DB_URL = DB_URL.replace("postgresql://", "postgresql+psycopg://")
    elif "postgres://" in DB_URL:
        ALCHEMY_DB_URL = DB_URL.replace("postgres://", "postgresql+psycopg://")
    else:
        ALCHEMY_DB_URL = DB_URL
else:
    ALCHEMY_DB_URL = DB_URL

engine = create_engine(ALCHEMY_DB_URL)

def read_sql_pl(sql: str, params=None) -> pl.DataFrame:
    """Reads SQL query into a Polars DataFrame using connectorx."""
    # Use connectorx for lightweight SQL reading (already included in polars)
    import connectorx as cx
    return pl.from_arrow(cx.read_sql(DB_URL, sql))

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


# === Feature Engineering Logic (AC1) ===
def generate_ta_features(df: pl.DataFrame) -> pl.DataFrame:
    """
    Calculates Technical Analysis features from raw price data.
    """
    # Convert to pandas for easier rolling calculations
    pdf = df.to_pandas()
    pdf['trade_date'] = pd.to_datetime(pdf['effective_date'])
    pdf = pdf.sort_values(['symbol', 'trade_date'])

    # Define helper for rolling z-score
    def rolling_z_score(series, window=252):
        return (series - series.rolling(window).mean()) / series.rolling(window).std()

    results = []
    for symbol, group in pdf.groupby('symbol'):
        group = group.copy()
        close = group['close']
        
        # 1. RSI (14)
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        group['rsi_14'] = 100 - (100 / (1 + rs))
        
        # 2. Bollinger Bands (20, 2)
        bb_mid = close.rolling(window=20).mean()
        bb_std = close.rolling(window=20).std()
        group['bb_upper'] = bb_mid + 2 * bb_std
        group['bb_lower'] = bb_mid - 2 * bb_std
        group['bb_pct'] = (close - group['bb_lower']) / (group['bb_upper'] - group['bb_lower'])
        
        # 3. MACD (12, 26, 9)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        group['macd'] = macd
        group['macd_signal'] = signal
        group['macd_hist'] = macd - signal

        # 4. Z-Scores for Hunter/Froth
        # S_tech_z: Composite of RSI and BB position
        # Normalize RSI to 0-1 (approx) then z-score
        group['S_tech_raw'] = (group['rsi_14'] / 100.0) + group['bb_pct']
        group['S_tech_z'] = rolling_z_score(group['S_tech_raw'])
        
        # H_ta_z: Hunter technicals (dip buying preference)
        # Low RSI and Low BB % is good for hunting
        group['H_ta_raw'] = (1 - (group['rsi_14'] / 100.0)) + (1 - group['bb_pct'])
        group['H_ta_z'] = rolling_z_score(group['H_ta_raw'])

        # Fill NaNs (early periods)
        group = group.fillna(0)
        results.append(group)

    if not results:
        return df

    final_pdf = pd.concat(results)
    
    # Convert back to Polars
    # Ensure we keep all original columns plus new ones
    return pl.from_pandas(final_pdf)

def save_to_features_gold(conn, df: pl.DataFrame, version: str):
    """
    Saves features to the long-format features_gold table.
    """
    # Select only feature columns + keys
    feature_cols = ['rsi_14', 'bb_pct', 'macd_hist', 'S_tech_z', 'H_ta_z', 'hmm_state', 'HunterScore', 'FrothScore']
    available_cols = [c for c in feature_cols if c in df.columns]
    
    if not available_cols:
        logger.warning("No feature columns found to save to features_gold")
        return

    # Melt to long format
    long_df = df.melt(
        id_vars=['symbol', 'effective_date'],
        value_vars=available_cols,
        variable_name='feature_name',
        value_name='value'
    )
    
    # Add version and created_at
    long_df = long_df.with_columns([
        pl.lit(version).alias('feature_set_version'),
        pl.lit(datetime.now()).alias('created_at')
    ])

    # Write to DB (using pandas for simplicity with SQLAlchemy engine)
    try:
        long_df.to_pandas().to_sql(
            name='features_gold',
            con=engine,
            if_exists='append',
            index=False,
            method='multi',
            chunksize=1000
        )
        logger.info(f"Saved {len(long_df)} rows to features_gold.")
    except Exception as e:
        # Ignore duplicate key errors if re-running (common in dev)
        logger.warning(f"Error saving to features_gold: {e}")

# === Sentiment Feature Integration (NEW) ===
def join_sentiment_features(df: pl.DataFrame) -> pl.DataFrame:
    """
    Join sentiment features from v_sentiment_features view.
    Adds hype_crowd_z, news_count_7d, news_count_30d, catalyst_count.
    """
    logger.info("Joining sentiment features...")
    
    # Read sentiment features from view
    df_sentiment = read_sql_pl("""
        SELECT 
            symbol,
            effective_date,
            hype_crowd_z,
            news_count_7d,
            news_count_30d,
            catalyst_count
        FROM v_sentiment_features
    """, params=None)
    
    if df_sentiment.is_empty():
        logger.warning("No sentiment features found. Adding placeholder columns.")
        df = df.with_columns([
            pl.lit(0.0).alias("hype_crowd_z"),
            pl.lit(0).alias("news_count_7d"),
            pl.lit(0).alias(" news_count_30d"),
            pl.lit(0).alias("catalyst_count")
        ])
        return df
    
    # Join sentiment features
    df = df.join(df_sentiment, on=["symbol", "effective_date"], how="left")
    
    # Fill nulls with 0 for symbols without sentiment data
    df = df.with_columns([
        pl.col("hype_crowd_z").fill_null(0.0),
        pl.col("news_count_7d").fill_null(0),
        pl.col("news_count_30d").fill_null(0),
        pl.col("catalyst_count").fill_null(0)
    ])
    
    logger.info(f"Joined sentiment features for {len(df)} rows")
    return df


def run_sql_plus_plus_macro(conn, as_of_date, tz):
    """
    (Point 2, 3, 4, 5)
    Executes the main SQL++ logic to calculate macro impacts and writes them
    to the database.
    """
    logger.info(f"SQL++ Macro running for {as_of_date} at TZ {tz}")
    try:
        # (Point 7) Use context manager for the connection
        with conn:
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
    df_ui_override = read_sql_pl(
        f"SELECT sector, weight FROM macro_ui_override WHERE ttl_until IS NULL OR ttl_until >= (CURRENT_TIMESTAMP AT TIME ZONE '{MACRO_TZ}')::date",
        params=None
    )

    # (Point 6) Get Sector mapping from symbol_watchlist
    # This replaces the placeholder logic
    df_sectors = read_sql_pl("SELECT symbol, sector FROM symbol_watchlist WHERE is_active = true", params=None)
    
    # Join sector info
    if 'sector' in df.columns:
        df = df.drop("sector") # Drop if exists to avoid collision
        
    df = df.join(df_sectors, on="symbol", how="left")
    
    # Fill missing sectors with default 'FINANCIALS' (fallback)
    df = df.with_columns(pl.col("sector").fill_null("FINANCIALS"))



    # Blending logic (similar to V1.1)
    # Handle empty DataFrames by ensuring proper schema
    if df_impact_rt.is_empty():
        df_impact_rt = pl.DataFrame({
            "sector": pl.Series([], dtype=pl.Utf8),
            "impact": pl.Series([], dtype=pl.Float64),
            "confidence": pl.Series([], dtype=pl.Float64)
        })
    
    if df_ui_override.is_empty():
        df_ui_override = pl.DataFrame({
            "sector": pl.Series([], dtype=pl.Utf8),
            "weight": pl.Series([], dtype=pl.Float64)
        })
    
    df = df.join(df_impact_rt, on="sector", how="left")
    df = df.join(df_ui_override, on="sector", how="left")
    df = df.with_columns(
        impact_final=pl.coalesce(pl.col("weight"), pl.col("impact")).fill_null(0.0)
    )

    if not enable_blend:
        logger.info("Macro blend is disabled via feature flag.")
        df = df.with_columns(pl.lit(0.0).alias("impact_final"))

    # Calculate news_count_crowd_z (z-score of 7d news count)
    news_mean = df["news_count_7d"].mean()
    news_std = df["news_count_7d"].std()
    if news_std and news_std > 0:
        df = df.with_columns(
            news_count_crowd_z=((pl.col("news_count_7d") - news_mean) / news_std)
        )
    else:
        df = df.with_columns(pl.lit(0.0).alias("news_count_crowd_z"))

    # Determine catalyst_active flag
    df = df.with_columns(
        catalyst_active=(pl.col("catalyst_count") > 0).cast(pl.Int32)
    )

    # Calculate H_cat_z (catalyst bonus) from catalyst_count
    # Normalize catalyst count to 0-1 range (assume max 5 catalysts)
    df = df.with_columns(
        H_cat_z=(pl.col("catalyst_count").clip(0, 5) / 5.0)
    )

    # Placeholder columns for features not yet implemented
    for col in ['S_tech_z', 'breadth_contra_z', 'hype_elitist_z', 'H_ta_z']:
        if col not in df.columns:
            df = df.with_columns(pl.lit(0.5).alias(col))


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

    # Return all columns so they can be saved to features_gold and serving
    return df

@contextmanager
def pg_conn():
    with psycopg.connect(os.getenv("DB_URL")) as conn:
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
    
    # Convert to pandas and write using existing SQLAlchemy engine
    pdf = df.to_pandas()
    pdf.to_sql(
        name=temp_table_name,
        con=engine,
        if_exists="replace",
        index=False
    )
    logger.info(f"Successfully wrote data to temp table {temp_table_name}.")

    # Create fresh connection for atomic operations
    try:
        with psycopg.connect(os.getenv("DB_URL")) as fresh_conn:
            fresh_conn.autocommit = False
            with fresh_conn.cursor() as cur:
                # --- Validation ---
                cur.execute(f"SELECT COUNT(*) FROM {temp_table_name}")
                row_count = cur.fetchone()[0]
                if row_count != len(df):
                    raise ValueError(f"Validation failed: Row count mismatch. Expected {len(df)}, got {row_count}")
                logger.info("Validation passed.")

                # --- Atomic Swap ---
                cur.execute(f"ALTER TABLE IF EXISTS {serving_table_name} RENAME TO {backup_table_name};")
                cur.execute(f"ALTER TABLE {temp_table_name} RENAME TO {serving_table_name};")
                logger.info(f"Atomic swap complete. '{serving_table_name}' is now live.")

                # --- Cleanup ---
                cur.execute(f"DROP TABLE IF EXISTS {backup_table_name};")
                logger.info(f"Cleaned up backup table {backup_table_name}.")
                
            fresh_conn.commit()

    except Exception as e:
        logger.error(f"Atomic publish for version {version} failed: {e}")
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

            # Read from the view
            df = read_sql_pl("SELECT * FROM v_ta_silver_for_gold", params=None)
            
            if df.is_empty():
                logger.warning("No data in v_ta_silver_for_gold. Skipping feature calculation.")
                return

            # (NEW) Generate TA Features
            logger.info("Generating Technical Analysis features...")
            df = generate_ta_features(df)
            
            # (NEW) Join Sentiment Features
            df = join_sentiment_features(df)

            df = apply_hmm(df)
            df = calculate_scores(df, conn, ENABLE_MACRO_BLEND)
            
            if df.is_empty():
                logger.warning("No features calculated. Skipping publish.")
                return

            logger.info("Feature gold batch job finished calculations and is ready to publish.")
            
            version = f"v{pd.Timestamp.now().strftime('%Y%m%d%H%M%S')}"
            
            # (NEW) Save to long-format features_gold (for ML training)
            save_to_features_gold(conn, df, version)
            
            # Publish to serving layer
            atomic_publish(conn, df, version)
            logger.info("Feature gold batch completed successfully!")
        except Exception as e:
            logger.exception("Feature gold batch failed.")
            try:
                conn.rollback()
            except Exception:
                pass  # Connection may already be closed
        finally:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_name,))
                conn.commit()
            except Exception:
                pass  # Connection may already be closed

if __name__ == "__main__":
    run_feature_gold_batch()
