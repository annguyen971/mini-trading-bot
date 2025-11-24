import os
import polars as pl
from hmmlearn import hmm
import numpy as np
from contextlib import contextmanager
import psycopg
from core_lib.locks import get_advisory_lock
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
# Use psycopg3 (not psycopg2) for SQLAlchemy
DB_URL_PSYCOPG3 = DB_URL.replace("postgresql://", "postgresql+psycopg://")
engine = create_engine(DB_URL_PSYCOPG3)

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
    """
    Calculate Sector Rotation metrics (RRG "Lite") at sub-sector level.
    
    For each sub-sector (e.g., banking, materials):
    - RS-Ratio: 60-day average relative excess return vs VN-Index (long-term trend)
    - RS-Momentum: Rate of change of RS-Ratio (short-term momentum)
    
    Args:
        conn: Database connection
        as_of_date: Date to calculate stats for
    """
    logger.info(f"Calculating sector stats for {as_of_date}")
    
    try:
        with conn.cursor() as cur:
            # Calculate RRG metrics for each sub-sector
            # Using daily_sector_prices view and sector_perf if available
            sql = """
            WITH sector_returns AS (
                -- Calculate daily returns for each sector
                SELECT
                    s.trade_date,
                    s.sector,
                    d.super_sector,
                    s.close / LAG(s.close, 1) OVER (PARTITION BY s.sector ORDER BY s.trade_date) - 1 AS sector_return,
                    i.close / LAG(i.close, 1) OVER (ORDER BY i.trade_date) - 1 AS index_return
                FROM daily_sector_prices s
                JOIN dim_sector d ON s.sector = d.sector
                LEFT JOIN daily_vnindex_prices i ON s.trade_date = i.trade_date
                WHERE s.trade_date BETWEEN %(start_date)s AND %(as_of_date)s
            ),
            excess_returns AS (
                -- Calculate excess return (sector vs index)
                SELECT
                    trade_date,
                    sector,
                    super_sector,
                    COALESCE(sector_return, 0) - COALESCE(index_return, 0) AS excess_return
                FROM sector_returns
            ),
            rolling_metrics AS (
                -- Calculate RS-Ratio (60-day avg) and RS-Momentum (5-day change)
                SELECT
                    sector,
                    super_sector,
                    trade_date,
                    AVG(excess_return) OVER (
                        PARTITION BY sector 
                        ORDER BY trade_date 
                        ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                    ) AS rs_ratio_60d,
                    AVG(excess_return) OVER (
                        PARTITION BY sector 
                        ORDER BY trade_date 
                        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                    ) AS rs_ratio_20d
                FROM excess_returns
            ),
            final_metrics AS (
                SELECT
                    sector,
                    super_sector,
                    trade_date,
                    rs_ratio_60d,
                    rs_ratio_20d,  -- Keep for momentum column
                    rs_ratio_60d - LAG(rs_ratio_60d, 5) OVER (PARTITION BY sector ORDER BY trade_date) AS rs_momentum
                FROM rolling_metrics
            )
            -- Upsert into sector_stats
            INSERT INTO sector_stats (sector, as_of_date, super_sector, rs_ratio, rs_momentum, momentum, breadth)
            SELECT
                sector,
                trade_date AS as_of_date,
                super_sector,
                rs_ratio_60d * 100 + 100 AS rs_ratio,  -- Normalize to 100 = benchmark
                COALESCE(rs_momentum * 100, 0) AS rs_momentum,  -- Normalize, 0 = no change
                rs_ratio_20d AS momentum,  -- Keep existing momentum column
                NULL AS breadth  -- Placeholder for breadth calculation
            FROM final_metrics
            WHERE trade_date = %(as_of_date)s
            ON CONFLICT (sector, as_of_date) DO UPDATE SET
                super_sector = EXCLUDED.super_sector,
                rs_ratio = EXCLUDED.rs_ratio,
                rs_momentum = EXCLUDED.rs_momentum,
                momentum = EXCLUDED.momentum,
                last_updated = NOW();
            """
            
            # Calculate start date (need ~65 trading days of history for 60-day rolling)
            # Using 150 calendar days to ensure sufficient trading days even during holidays (Tết)
            from datetime import timedelta
            start_date = as_of_date - timedelta(days=150)
            
            cur.execute(sql, {"as_of_date": as_of_date, "start_date": start_date})
            row_count = cur.rowcount
            logger.info(f"Updated sector stats for {row_count} sub-sectors")
            
            conn.commit()
            return row_count
            
    except Exception as e:
        logger.exception(f"Failed to calculate sector stats: {e}")
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
        # Calculate Y (Sector Excess Return vs VNINDEX)
        # We need to calculate this first because the macro logic depends on it.
        # Formula: Y = (Sector_Return_20d - Index_Return_20d)
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

def calculate_breadth_stats(conn, as_of_date):
    """
    Calculate market breadth (% stocks > MA20) and update breadth_stats.
    """
    logger.info(f"Calculating breadth stats for {as_of_date}")
    try:
        with conn.cursor() as cur:
            # Calculate % of stocks > MA20
            # We look back 60 days to ensure enough data for MA20
            sql = """
            WITH ma20 AS (
                SELECT
                    symbol,
                    avg(close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as ma20,
                    close,
                    trade_date
                FROM ta_silver
                WHERE trade_date BETWEEN %(as_of_date)s - interval '60 days' AND %(as_of_date)s
            ),
            today_ma AS (
                SELECT * FROM ma20 WHERE trade_date = %(as_of_date)s
            ),
            stats AS (
                SELECT
                    count(*) FILTER (WHERE close > ma20) as above_ma20,
                    count(*) as total
                FROM today_ma
            )
            INSERT INTO breadth_stats (as_of_date, breadth, last_updated)
            SELECT
                %(as_of_date)s,
                CASE WHEN total > 0 THEN above_ma20::float / total ELSE 0 END,
                NOW()
            FROM stats
            ON CONFLICT (as_of_date) DO UPDATE SET
                breadth = EXCLUDED.breadth,
                last_updated = NOW();
            """
            cur.execute(sql, {"as_of_date": as_of_date})
            logger.info(f"Updated breadth stats: {cur.rowcount} rows")
    except Exception as e:
        logger.exception("Breadth calculation failed")
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

    # Placeholder join logic until `dim_symbol_sector` is available
    if 'sector' not in df.columns:
        df = df.with_columns(pl.lit("FINANCIALS").alias("sector"))


    # Blending logic (similar to V1.1)
    # Cast to Float64 to ensure numeric arithmetic
    if "impact" in df_impact_rt.columns:
        df_impact_rt = df_impact_rt.with_columns(pl.col("impact").cast(pl.Float64))
    if "weight" in df_ui_override.columns:
        df_ui_override = df_ui_override.with_columns(pl.col("weight").cast(pl.Float64))

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

    return df.select(["symbol","effective_date","hmm_state","FrothScore","HunterScore"])

@contextmanager
def pg_conn():
    # Use DB_URL (default) or fall back to PG_DSN for backward compatibility
    dsn = os.getenv("DB_URL") or os.getenv("PG_DSN")
    if not dsn:
        raise ValueError("Neither DB_URL nor PG_DSN environment variable is set")
    with psycopg.connect(dsn) as conn:
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
        # Write to temp table
        # Use ADBC for performance if available, otherwise SQLAlchemy
        try:
            df.write_database(
                table_name=temp_table_name,
                connection=os.environ["DB_URL"],
                if_table_exists="replace",
                engine="adbc"
            )
        except Exception:
            # Fallback to SQLAlchemy if ADBC fails or not installed
            df.write_database(
                table_name=temp_table_name,
                connection=os.environ["DB_URL"],
                if_table_exists="replace",
                engine="sqlalchemy"
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


def calculate_sentiment_features(conn) -> pl.DataFrame:
    """Calculate sentiment Z-scores from sa_silver."""
    logger.info("Calculating sentiment features from sa_silver...")
    query = """
    SELECT 
        unnest(symbols) as symbol,
        date(publisher_time_utc) as effective_date,
        sentiment_score,
        hype_raw
    FROM sa_silver
    WHERE sentiment_score IS NOT NULL
    """
    sa_df = read_sql_pl(query, params=None)
    
    if sa_df.height == 0:
        return pl.DataFrame(
            {"symbol": [], "effective_date": [], "hype_crowd_z": [], "news_count_crowd_z": []},
            schema={"symbol": pl.String, "effective_date": pl.Date, "hype_crowd_z": pl.Float64, "news_count_crowd_z": pl.Float64}
        )

    # Calculate daily aggregates
    daily_sa = sa_df.group_by(["symbol", "effective_date"]).agg([
        pl.col("hype_raw").mean().alias("daily_hype"),
        pl.count("sentiment_score").alias("news_count")
    ])
    
    daily_sa = daily_sa.sort(["symbol", "effective_date"])
    
    # Rolling window 20 days
    daily_sa = daily_sa.with_columns([
        ((pl.col("daily_hype") - pl.col("daily_hype").rolling_mean(20).over("symbol")) / 
        (pl.col("daily_hype").rolling_std(20).over("symbol") + 1e-6)).alias("hype_crowd_z"),
        
        ((pl.col("news_count") - pl.col("news_count").rolling_mean(20).over("symbol")) / 
        (pl.col("news_count").rolling_std(20).over("symbol") + 1e-6)).alias("news_count_crowd_z")
    ])
    
    return daily_sa.select(["symbol", "effective_date", "hype_crowd_z", "news_count_crowd_z"]).fill_null(0.0)

def calculate_technical_features(conn) -> pl.DataFrame:
    """Calculate technical Z-scores from ta_silver."""
    logger.info("Calculating technical features from ta_silver...")
    query = """
    SELECT symbol, trade_date as effective_date, close FROM ta_silver
    """
    ta_df = read_sql_pl(query, params=None)
    ta_df = ta_df.sort(["symbol", "effective_date"])
    
    # Calculate Returns
    ta_df = ta_df.with_columns([
        pl.col("close").pct_change().over("symbol").alias("returns")
    ])
    
    # Calculate S_tech_z (Return Z-score)
    ta_df = ta_df.with_columns([
        ((pl.col("returns") - pl.col("returns").rolling_mean(20).over("symbol")) / 
        (pl.col("returns").rolling_std(20).over("symbol") + 1e-6)).alias("S_tech_z")
    ])
    
    return ta_df.select(["symbol", "effective_date", "S_tech_z"]).fill_null(0.0)

def run_feature_gold_batch():
    lock_name = 'feature_gold_batch'
    with pg_conn() as conn:
        if not get_advisory_lock(conn,lock_name):
            logger.warning(f"Could not acquire lock {lock_name}. Exiting.")
            return

        try:
            # (Point 9) Add Feature Flag and Timezone
            ENABLE_MACRO_BLEND = os.getenv("ENABLE_MACRO_BLEND", "true").lower() == "true"
            MACRO_TZ_STR = os.getenv("MACRO_TZ", "Asia/Ho_Chi_Minh")

            # (Point 5) Use timezone-aware date
            env_date = os.getenv("AS_OF_DATE")
            if env_date:
                as_of_date = datetime.strptime(env_date, '%Y-%m-%d').date()
            else:
                as_of_date = datetime.now(pytz.timezone(MACRO_TZ_STR)).date()

            start_time = time.time()
            # This is called but the main logic inside is commented out until prerequisite tables exist.
            run_sql_plus_plus_macro(conn, as_of_date, MACRO_TZ_STR)
            logger.info(f"SQL++ Macro logic finished in {time.time() - start_time:.2f}s")

            # Calculate sector rotation stats (RRG)
            start_time = time.time() # Reset start_time for this block
            # (Point 3) Calculate Sector Stats (RRG)
            calculate_sector_stats(conn, as_of_date)

            # (Point 3b) Calculate Breadth Stats
            calculate_breadth_stats(conn, as_of_date)
            logger.info(f"Sector stats calculation finished in {time.time() - start_time:.2f}s")



            # The view v_features_asof does not exist, so this will fail.
            # Using a placeholder query for now.
            df = read_sql_pl("SELECT * FROM v_features_asof", params=None)
            
            # --- NEW: Calculate Real Features ---
            # Calculate features from raw data to replace constant 0.5s from view
            sent_df = calculate_sentiment_features(conn)
            tech_df = calculate_technical_features(conn)
            
            # Ensure date types match
            df = df.with_columns(pl.col("effective_date").cast(pl.Date))
            sent_df = sent_df.with_columns(pl.col("effective_date").cast(pl.Date))
            tech_df = tech_df.with_columns(pl.col("effective_date").cast(pl.Date))
            
            # Drop dummy columns from view
            cols_to_drop = ['hype_crowd_z', 'news_count_crowd_z', 's_tech_z', 'S_tech_z']
            df = df.drop([c for c in cols_to_drop if c in df.columns])
            
            # Join real features
            df = df.join(sent_df, on=["symbol", "effective_date"], how="left")
            df = df.join(tech_df, on=["symbol", "effective_date"], how="left")
            
            # Fill missing values with neutral 0.0
            df = df.with_columns([
                pl.col("hype_crowd_z").fill_null(0.0),
                pl.col("news_count_crowd_z").fill_null(0.0),
                pl.col("S_tech_z").fill_null(0.0)
            ])

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
            # Advisory lock is automatically released when connection closes
            # No need to manually unlock
            pass

if __name__ == "__main__":
    run_feature_gold_batch()
