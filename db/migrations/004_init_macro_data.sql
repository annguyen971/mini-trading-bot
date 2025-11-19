/*
 * Migration 004: Initialize Macro Data Pipeline
 * ===============================================
 * Date: 2025-01-18
 * Description: Seeds initial macro data tables and calendar
 *
 * Initializes:
 * - macro_release_calendar with publication lags
 * - Seed sectors in dim_sector
 * - Sample macro_clean bootstrap data
 *
 * This allows the system to run even if macro scraper is not yet active.
 */

-- ==============================================================================
-- STEP 1: Seed Sectors
-- ==============================================================================

INSERT INTO dim_sector (sector, display_name)
VALUES
    ('technology', 'Technology'),
    ('banking', 'Banking & Finance'),
    ('consumer_goods', 'Consumer Goods'),
    ('industrial', 'Industrial & Materials'),
    ('real_estate', 'Real Estate'),
    ('utilities', 'Utilities & Energy')
ON CONFLICT (sector) DO UPDATE SET display_name = EXCLUDED.display_name;

RAISE NOTICE 'Seeded 6 sectors';

-- ==============================================================================
-- STEP 2: Seed Macro Release Calendar (Publication Lags)
-- ==============================================================================

-- GDP: Published quarterly, 45 days after quarter end
INSERT INTO macro_release_calendar (metric, period_date, published_on, lag_days)
VALUES
    ('GDP_YOY', '2024-09-30', '2024-11-14', 45),
    ('GDP_YOY', '2024-06-30', '2024-08-14', 45),
    ('GDP_YOY', '2024-03-31', '2024-05-15', 45)
ON CONFLICT (metric, period_date) DO NOTHING;

-- CPI: Published monthly, 10 days after month end
INSERT INTO macro_release_calendar (metric, period_date, published_on, lag_days)
SELECT
    'CPI_YOY',
    period_date,
    period_date + INTERVAL '10 days',
    10
FROM generate_series(
    '2024-01-31'::date,
    '2024-12-31'::date,
    '1 month'::interval
) AS period_date
ON CONFLICT (metric, period_date) DO NOTHING;

-- Policy Rate: Published irregularly (central bank meetings)
INSERT INTO macro_release_calendar (metric, period_date, published_on, lag_days)
VALUES
    ('POLICY_RATE', '2024-11-01', '2024-11-01', 0),
    ('POLICY_RATE', '2024-09-01', '2024-09-01', 0),
    ('POLICY_RATE', '2024-07-01', '2024-07-01', 0)
ON CONFLICT (metric, period_date) DO NOTHING;

RAISE NOTICE 'Seeded macro release calendar';

-- ==============================================================================
-- STEP 3: Create macro_clean table if not exists
-- ==============================================================================

CREATE TABLE IF NOT EXISTS macro_clean (
    metric_name TEXT PRIMARY KEY,
    value DOUBLE PRECISION,
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

-- ==============================================================================
-- STEP 4: Seed Bootstrap Macro Data (Vietnam market estimates)
-- ==============================================================================

INSERT INTO macro_clean (metric_name, value, last_updated)
VALUES
    ('GDP_YOY', 6.82, NOW()),           -- Vietnam GDP growth ~6.8% (2024 est)
    ('CPI_YOY', 3.25, NOW()),           -- Inflation ~3.25%
    ('POLICY_RATE', 4.50, NOW()),       -- State Bank rate
    ('USDVND', 24500.0, NOW()),         -- Exchange rate
    ('VNIndex', 1250.0, NOW()),         -- Stock index
    ('VN30_VOLUME', 15000000.0, NOW()), -- Average volume
    ('USD_INDEX', 104.2, NOW())         -- Dollar strength
ON CONFLICT (metric_name)
DO UPDATE SET
    value = EXCLUDED.value,
    last_updated = NOW();

RAISE NOTICE 'Seeded 7 macro metrics with bootstrap values';

-- ==============================================================================
-- STEP 5: Initialize Trade Calendar (2024-2025)
-- ==============================================================================

-- Create trade calendar if not exists
CREATE TABLE IF NOT EXISTS trade_calendar (
    tdate DATE PRIMARY KEY,
    is_trading BOOLEAN NOT NULL
);

-- Seed 2024-2025 trading days (exclude weekends)
INSERT INTO trade_calendar (tdate, is_trading)
SELECT
    d::date,
    EXTRACT(DOW FROM d) NOT IN (0, 6)  -- Not Sunday(0) or Saturday(6)
FROM generate_series(
    '2024-01-01'::date,
    '2025-12-31'::date,
    '1 day'::interval
) AS d
ON CONFLICT (tdate) DO NOTHING;

-- Mark known holidays as non-trading (Vietnam major holidays)
UPDATE trade_calendar
SET is_trading = false
WHERE tdate IN (
    '2024-01-01',  -- New Year
    '2024-02-08', '2024-02-09', '2024-02-10', '2024-02-11', '2024-02-12',  -- Tet 2024
    '2024-04-18',  -- Hung Kings
    '2024-04-30',  -- Reunification Day
    '2024-05-01',  -- Labor Day
    '2024-09-02',  -- National Day
    '2025-01-01',  -- New Year 2025
    '2025-01-28', '2025-01-29', '2025-01-30', '2025-01-31', '2025-02-01'   -- Tet 2025 (est)
);

RAISE NOTICE 'Seeded trade calendar (2024-2025) with holidays';

-- ==============================================================================
-- STEP 6: Verification
-- ==============================================================================

DO $$
DECLARE
    v_sector_count INT;
    v_macro_count INT;
    v_calendar_count INT;
BEGIN
    SELECT COUNT(*) INTO v_sector_count FROM dim_sector;
    SELECT COUNT(*) INTO v_macro_count FROM macro_clean;
    SELECT COUNT(*) INTO v_calendar_count FROM trade_calendar WHERE is_trading = true;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration 004 Verification:';
    RAISE NOTICE '  Sectors: %', v_sector_count;
    RAISE NOTICE '  Macro metrics: %', v_macro_count;
    RAISE NOTICE '  Trading days: %', v_calendar_count;
    RAISE NOTICE '========================================';
END $$;

-- ==============================================================================
-- STEP 7: Migration Tracking
-- ==============================================================================

INSERT INTO schema_migrations (migration_id, description)
VALUES ('004_init_macro_data', 'Initialize macro data pipeline with bootstrap values')
ON CONFLICT (migration_id) DO UPDATE SET applied_at = NOW();

RAISE NOTICE 'Migration 004 completed';
RAISE NOTICE 'Macro pipeline initialized with bootstrap data';
RAISE NOTICE 'Run macro scraper to get real-time values';
