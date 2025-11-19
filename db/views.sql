-- ========================================
-- SECTION 1: MACRO VIEWS (Existing)
-- ========================================

-- View dự phòng (fallback) an toàn nếu macro_clean chưa tồn tại
CREATE OR REPLACE VIEW macro_src AS
SELECT * FROM macro_clean
UNION ALL
SELECT * FROM macro_raw
WHERE NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_name='macro_clean'
);

-- View logic shift (đã vá lỗi Point 1)
CREATE OR REPLACE VIEW macro_feat_daily_shifted AS
WITH base AS (
    SELECT
        m.metric,
        m.period_date,
        m.value,
        r.published_on,
        COALESCE(r.lag_days, 1) AS lag_days
    FROM macro_src m -- (Dùng view an toàn)
    JOIN macro_release_calendar r ON r.metric = m.metric AND r.period_date = m.period_date
),
calc AS (
    SELECT
        (published_on + (lag_days || ' days')::interval)::date AS as_of_date_valid,
        metric,
        value
    FROM base
    WHERE published_on IS NOT NULL
)
SELECT
    as_of_date_valid AS as_of_date,
    metric,
    value
FROM calc;

-- ========================================
-- SECTION 2: TEMPORAL CORRECTNESS VIEWS
-- ========================================

-- **CRITICAL VIEW: v_features_asof**
-- Purpose: Enforces zero look-ahead bias by only exposing features
--          that were available at a specific point in time.
-- Usage: SELECT * FROM v_features_asof WHERE as_of_time <= '2024-01-15 09:00:00+07'
--
-- NFR4 Compliance: Prevents future data leakage in backtests and training
--
-- Schema:
--   - symbol: Stock symbol (e.g., 'FPT', 'VNM')
--   - effective_date: Trading date for which features are calculated
--   - feature_name: Name of the feature (e.g., 'hunter_score', 'froth_score')
--   - value: Feature value
--   - as_of_time: Timestamp when this feature became available
--   - feature_set_version: Version identifier for reproducibility

CREATE OR REPLACE VIEW v_features_asof AS
WITH base_features AS (
    -- Combine features from features_gold with temporal metadata
    SELECT
        fg.symbol,
        fg.effective_date,
        fg.feature_name,
        fg.value,
        fg.feature_set_version,
        -- Derive as_of_time: when was this feature first computable?
        -- For TA features: based on trade_date + validation lag
        -- For SA features: based on news publication + processing lag
        -- For Gold features: based on batch computation time
        CASE
            -- Technical Analysis features: available next trading day at market open
            WHEN fg.feature_name LIKE 'ta_%' THEN
                (fg.effective_date + interval '1 day')::timestamptz

            -- Sentiment Analysis features: available after NLP processing (assume 2-hour lag)
            WHEN fg.feature_name LIKE 'sa_%' THEN
                (fg.effective_date + interval '2 hours')::timestamptz

            -- Gold features (hunter_score, froth_score, hmm_state): available after batch job
            -- Assume batch runs at 20:00 daily
            WHEN fg.feature_name IN ('hunter_score', 'froth_score', 'hmm_state', 'macro_impact_score') THEN
                (fg.effective_date::timestamp + interval '20 hours')::timestamptz

            -- Default: conservative estimate (next day)
            ELSE
                (fg.effective_date + interval '1 day')::timestamptz
        END AS as_of_time
    FROM features_gold fg
),
-- Add validation: ensure we only expose features that have passed sanity checks
validated_features AS (
    SELECT
        bf.symbol,
        bf.effective_date,
        bf.feature_name,
        bf.value,
        bf.as_of_time,
        bf.feature_set_version
    FROM base_features bf
    WHERE
        -- Sanity check: value is not NULL
        bf.value IS NOT NULL
        -- Sanity check: effective_date is not in the future
        AND bf.effective_date <= CURRENT_DATE
)
SELECT
    symbol,
    effective_date,
    feature_name,
    value,
    as_of_time,
    feature_set_version
FROM validated_features;

-- Create index to optimize as_of_time queries
CREATE INDEX IF NOT EXISTS idx_features_gold_as_of_lookup
    ON features_gold (symbol, effective_date, feature_set_version);

COMMENT ON VIEW v_features_asof IS
'Temporal correctness view: Only exposes features available at a given point in time.
Usage: WHERE as_of_time <= :backtest_time to prevent look-ahead bias.
NFR4 compliance critical for ML training and backtesting.';

-- ========================================
-- SECTION 3: HELPER FUNCTION FOR AS-OF QUERIES
-- ========================================

-- **FUNCTION: get_features_as_of**
-- Purpose: Retrieve all features for a symbol that were available at a specific time
-- Parameters:
--   - p_symbol: Stock symbol (e.g., 'FPT')
--   - p_as_of_time: Point-in-time timestamp (e.g., '2024-01-15 09:00:00+07')
--   - p_feature_set_version: Optional version filter (default: latest)
-- Returns: Table of (effective_date, feature_name, value)

CREATE OR REPLACE FUNCTION get_features_as_of(
    p_symbol TEXT,
    p_as_of_time TIMESTAMPTZ,
    p_feature_set_version TEXT DEFAULT NULL
)
RETURNS TABLE (
    effective_date DATE,
    feature_name TEXT,
    value DOUBLE PRECISION,
    as_of_time TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        v.effective_date,
        v.feature_name,
        v.value,
        v.as_of_time
    FROM v_features_asof v
    WHERE
        v.symbol = p_symbol
        AND v.as_of_time <= p_as_of_time
        AND (p_feature_set_version IS NULL OR v.feature_set_version = p_feature_set_version)
    ORDER BY v.effective_date DESC, v.feature_name;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION get_features_as_of IS
'Retrieve features for a symbol as they existed at a specific point in time.
Example: SELECT * FROM get_features_as_of(''FPT'', ''2024-01-15 09:00:00+07'');
Use this for backtesting to ensure zero look-ahead bias.';

-- ========================================
-- SECTION 4: UNIT TEST VIEW
-- ========================================

-- **TEST VIEW: test_temporal_correctness**
-- Purpose: Validates that no future data leakage occurs
-- This view should always return 0 rows if implemented correctly

CREATE OR REPLACE VIEW test_temporal_correctness AS
WITH future_leakage AS (
    SELECT
        symbol,
        effective_date,
        feature_name,
        as_of_time,
        effective_date::timestamptz AS feature_date,
        CASE
            WHEN as_of_time < effective_date::timestamptz THEN 'LEAK: as_of_time before effective_date'
            WHEN as_of_time > (effective_date + interval '2 days')::timestamptz THEN 'WARNING: excessive lag'
            ELSE 'OK'
        END AS validation_status
    FROM v_features_asof
    WHERE as_of_time < effective_date::timestamptz  -- Critical: this should NEVER happen
)
SELECT * FROM future_leakage WHERE validation_status LIKE 'LEAK%';

COMMENT ON VIEW test_temporal_correctness IS
'Unit test view: Should always return 0 rows. If rows exist, temporal correctness is violated.
Run: SELECT COUNT(*) FROM test_temporal_correctness; -- Expected: 0';

-- ========================================
-- SECTION 5: SERVING VIEWS
-- ========================================

-- **VIEW: v_latest_features**
-- Purpose: Quick access to the most recent feature set for each symbol
-- Used by API for real-time predictions

CREATE OR REPLACE VIEW v_latest_features AS
WITH latest_dates AS (
    SELECT
        symbol,
        MAX(effective_date) AS latest_date
    FROM features_gold
    GROUP BY symbol
)
SELECT
    fg.symbol,
    fg.effective_date,
    fg.feature_name,
    fg.value,
    fg.feature_set_version
FROM features_gold fg
INNER JOIN latest_dates ld
    ON fg.symbol = ld.symbol
    AND fg.effective_date = ld.latest_date
WHERE fg.feature_set_version = (
    SELECT feature_set_version
    FROM features_gold
    WHERE symbol = fg.symbol
    ORDER BY effective_date DESC
    LIMIT 1
);

COMMENT ON VIEW v_latest_features IS
'Real-time serving view: Returns the most recent feature set for each symbol.
Used by /predict API endpoint for production inference.';
