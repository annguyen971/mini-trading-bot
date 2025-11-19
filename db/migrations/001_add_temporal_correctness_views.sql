-- =====================================================
-- Migration: 001_add_temporal_correctness_views.sql
-- Description: Adds v_features_asof view and helper functions
--              to enforce NFR4 temporal correctness
-- Author: BMad PM Agent
-- Date: 2025-11-18
-- PRD Reference: NFR4 - Zero Look-Ahead Bias
-- =====================================================

-- This migration is IDEMPOTENT - safe to run multiple times

BEGIN;

-- ========================================
-- STEP 1: Create temporal correctness view
-- ========================================

CREATE OR REPLACE VIEW v_features_asof AS
WITH base_features AS (
    SELECT
        fg.symbol,
        fg.effective_date,
        fg.feature_name,
        fg.value,
        fg.feature_set_version,
        CASE
            WHEN fg.feature_name LIKE 'ta_%' THEN
                (fg.effective_date + interval '1 day')::timestamptz
            WHEN fg.feature_name LIKE 'sa_%' THEN
                (fg.effective_date + interval '2 hours')::timestamptz
            WHEN fg.feature_name IN ('hunter_score', 'froth_score', 'hmm_state', 'macro_impact_score') THEN
                (fg.effective_date::timestamp + interval '20 hours')::timestamptz
            ELSE
                (fg.effective_date + interval '1 day')::timestamptz
        END AS as_of_time
    FROM features_gold fg
),
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
        bf.value IS NOT NULL
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

-- ========================================
-- STEP 2: Create optimized index
-- ========================================

CREATE INDEX IF NOT EXISTS idx_features_gold_as_of_lookup
    ON features_gold (symbol, effective_date, feature_set_version);

-- ========================================
-- STEP 3: Create helper function
-- ========================================

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

-- ========================================
-- STEP 4: Create test validation view
-- ========================================

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
    WHERE as_of_time < effective_date::timestamptz
)
SELECT * FROM future_leakage WHERE validation_status LIKE 'LEAK%';

-- ========================================
-- STEP 5: Create serving view
-- ========================================

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

-- ========================================
-- STEP 6: Add metadata comments
-- ========================================

COMMENT ON VIEW v_features_asof IS
'Temporal correctness view: Only exposes features available at a given point in time.
Usage: WHERE as_of_time <= :backtest_time to prevent look-ahead bias.
NFR4 compliance critical for ML training and backtesting.';

COMMENT ON FUNCTION get_features_as_of IS
'Retrieve features for a symbol as they existed at a specific point in time.
Example: SELECT * FROM get_features_as_of(''FPT'', ''2024-01-15 09:00:00+07'');
Use this for backtesting to ensure zero look-ahead bias.';

COMMENT ON VIEW test_temporal_correctness IS
'Unit test view: Should always return 0 rows. If rows exist, temporal correctness is violated.
Run: SELECT COUNT(*) FROM test_temporal_correctness; -- Expected: 0';

COMMENT ON VIEW v_latest_features IS
'Real-time serving view: Returns the most recent feature set for each symbol.
Used by /predict API endpoint for production inference.';

-- ========================================
-- STEP 7: Verify migration success
-- ========================================

DO $$
DECLARE
    view_count INT;
    func_count INT;
BEGIN
    -- Check views created
    SELECT COUNT(*) INTO view_count
    FROM information_schema.views
    WHERE table_name IN ('v_features_asof', 'test_temporal_correctness', 'v_latest_features');

    -- Check function created
    SELECT COUNT(*) INTO func_count
    FROM pg_proc
    WHERE proname = 'get_features_as_of';

    IF view_count < 3 THEN
        RAISE EXCEPTION 'Migration failed: Expected 3 views, found %', view_count;
    END IF;

    IF func_count < 1 THEN
        RAISE EXCEPTION 'Migration failed: Function get_features_as_of not created';
    END IF;

    RAISE NOTICE 'Migration 001 completed successfully!';
    RAISE NOTICE '  - Created % views', view_count;
    RAISE NOTICE '  - Created get_features_as_of() function';
    RAISE NOTICE '  - Added idx_features_gold_as_of_lookup index';
END $$;

COMMIT;
