-- =====================================================
-- Test Suite: Temporal Correctness (NFR4 Compliance)
-- Description: Validates v_features_asof view prevents look-ahead bias
-- Author: BMad PM Agent
-- Date: 2025-11-18
-- =====================================================

-- Prerequisites: Run after migration 001 and with sample data

BEGIN;

-- ========================================
-- TEST 1: View Existence
-- ========================================

DO $$
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'TEST 1: Checking view existence...';
    RAISE NOTICE '========================================';

    IF NOT EXISTS (SELECT 1 FROM information_schema.views WHERE table_name = 'v_features_asof') THEN
        RAISE EXCEPTION 'FAIL: v_features_asof view does not exist';
    END IF;

    RAISE NOTICE 'PASS: v_features_asof view exists';
END $$;

-- ========================================
-- TEST 2: Insert Sample Data
-- ========================================

-- Clean up any existing test data
DELETE FROM features_gold WHERE symbol = 'TEST_SYMBOL';

-- Insert sample features for testing
INSERT INTO features_gold (symbol, effective_date, feature_set_version, feature_name, value)
VALUES
    ('TEST_SYMBOL', '2024-01-10', 'v1.0', 'ta_rsi_14', 65.5),
    ('TEST_SYMBOL', '2024-01-10', 'v1.0', 'sa_sentiment_score', 0.75),
    ('TEST_SYMBOL', '2024-01-10', 'v1.0', 'hunter_score', 72.3),
    ('TEST_SYMBOL', '2024-01-11', 'v1.0', 'ta_rsi_14', 68.2),
    ('TEST_SYMBOL', '2024-01-11', 'v1.0', 'froth_score', 45.8);

DO $$
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'TEST 2: Sample data inserted';
    RAISE NOTICE '========================================';
END $$;

-- ========================================
-- TEST 3: Verify as_of_time Logic
-- ========================================

DO $$
DECLARE
    ta_lag INTERVAL;
    sa_lag INTERVAL;
    gold_lag INTERVAL;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'TEST 3: Verifying as_of_time calculation...';
    RAISE NOTICE '========================================';

    -- Check TA feature lag (should be ~1 day)
    SELECT (as_of_time - effective_date::timestamptz) INTO ta_lag
    FROM v_features_asof
    WHERE symbol = 'TEST_SYMBOL' AND feature_name = 'ta_rsi_14'
    LIMIT 1;

    IF ta_lag != interval '1 day' THEN
        RAISE EXCEPTION 'FAIL: TA feature lag incorrect. Expected 1 day, got %', ta_lag;
    END IF;

    -- Check SA feature lag (should be ~2 hours)
    SELECT (as_of_time - effective_date::timestamptz) INTO sa_lag
    FROM v_features_asof
    WHERE symbol = 'TEST_SYMBOL' AND feature_name = 'sa_sentiment_score'
    LIMIT 1;

    IF sa_lag != interval '2 hours' THEN
        RAISE EXCEPTION 'FAIL: SA feature lag incorrect. Expected 2 hours, got %', sa_lag;
    END IF;

    -- Check Gold feature lag (should be ~20 hours)
    SELECT (as_of_time - effective_date::timestamptz) INTO gold_lag
    FROM v_features_asof
    WHERE symbol = 'TEST_SYMBOL' AND feature_name = 'hunter_score'
    LIMIT 1;

    IF gold_lag != interval '20 hours' THEN
        RAISE EXCEPTION 'FAIL: Gold feature lag incorrect. Expected 20 hours, got %', gold_lag;
    END IF;

    RAISE NOTICE 'PASS: as_of_time lags correct for all feature types';
    RAISE NOTICE '  - TA features: % lag', ta_lag;
    RAISE NOTICE '  - SA features: % lag', sa_lag;
    RAISE NOTICE '  - Gold features: % lag', gold_lag;
END $$;

-- ========================================
-- TEST 4: Function get_features_as_of()
-- ========================================

DO $$
DECLARE
    feature_count_early INT;
    feature_count_late INT;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'TEST 4: Testing get_features_as_of() function...';
    RAISE NOTICE '========================================';

    -- Query as of 2024-01-10 12:00 (should only see SA features, not TA/Gold yet)
    SELECT COUNT(*) INTO feature_count_early
    FROM get_features_as_of('TEST_SYMBOL', '2024-01-10 12:00:00+07');

    IF feature_count_early != 1 THEN
        RAISE EXCEPTION 'FAIL: Expected 1 feature (only SA), got %', feature_count_early;
    END IF;

    -- Query as of 2024-01-12 00:00 (should see all features from 2024-01-10)
    SELECT COUNT(*) INTO feature_count_late
    FROM get_features_as_of('TEST_SYMBOL', '2024-01-12 00:00:00+07');

    IF feature_count_late < 3 THEN
        RAISE EXCEPTION 'FAIL: Expected >= 3 features, got %', feature_count_late;
    END IF;

    RAISE NOTICE 'PASS: get_features_as_of() filters correctly by time';
    RAISE NOTICE '  - As of 2024-01-10 12:00: % features', feature_count_early;
    RAISE NOTICE '  - As of 2024-01-12 00:00: % features', feature_count_late;
END $$;

-- ========================================
-- TEST 5: No Future Leakage (Critical)
-- ========================================

DO $$
DECLARE
    leak_count INT;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'TEST 5: Checking for future data leakage...';
    RAISE NOTICE '========================================';

    SELECT COUNT(*) INTO leak_count
    FROM test_temporal_correctness;

    IF leak_count > 0 THEN
        RAISE EXCEPTION 'FAIL: Future leakage detected! % violations found', leak_count;
    END IF;

    RAISE NOTICE 'PASS: No future data leakage detected';
END $$;

-- ========================================
-- TEST 6: NULL Value Filtering
-- ========================================

-- Insert a NULL feature
INSERT INTO features_gold (symbol, effective_date, feature_set_version, feature_name, value)
VALUES ('TEST_SYMBOL', '2024-01-12', 'v1.0', 'null_test', NULL);

DO $$
DECLARE
    null_count INT;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'TEST 6: Verifying NULL values are filtered...';
    RAISE NOTICE '========================================';

    SELECT COUNT(*) INTO null_count
    FROM v_features_asof
    WHERE symbol = 'TEST_SYMBOL' AND feature_name = 'null_test';

    IF null_count > 0 THEN
        RAISE EXCEPTION 'FAIL: NULL values should be filtered, found % rows', null_count;
    END IF;

    RAISE NOTICE 'PASS: NULL values correctly filtered';
END $$;

-- ========================================
-- TEST 7: v_latest_features View
-- ========================================

DO $$
DECLARE
    latest_date DATE;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'TEST 7: Testing v_latest_features view...';
    RAISE NOTICE '========================================';

    SELECT MAX(effective_date) INTO latest_date
    FROM v_latest_features
    WHERE symbol = 'TEST_SYMBOL';

    IF latest_date != '2024-01-11' THEN
        RAISE EXCEPTION 'FAIL: Expected latest date 2024-01-11, got %', latest_date;
    END IF;

    RAISE NOTICE 'PASS: v_latest_features returns correct latest date: %', latest_date;
END $$;

-- ========================================
-- CLEANUP
-- ========================================

DELETE FROM features_gold WHERE symbol = 'TEST_SYMBOL';

-- ========================================
-- TEST SUMMARY
-- ========================================

DO $$
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'ALL TESTS PASSED! ✓';
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Temporal correctness implementation verified:';
    RAISE NOTICE '  ✓ Views created correctly';
    RAISE NOTICE '  ✓ as_of_time logic accurate';
    RAISE NOTICE '  ✓ get_features_as_of() function works';
    RAISE NOTICE '  ✓ No future data leakage';
    RAISE NOTICE '  ✓ NULL filtering works';
    RAISE NOTICE '  ✓ Latest features view functional';
    RAISE NOTICE '';
    RAISE NOTICE 'Phase 1.1 (As-of View) - COMPLETE';
    RAISE NOTICE '========================================';
END $$;

ROLLBACK;  -- Rollback test transaction (remove this for real data)
