/*
 * Migration 010: Seed Macro Data Fix
 * ===============================================
 * Date: 2025-11-21
 * Description: Re-seeds macro data and trade calendar to fix missing data issues.
 *              Ensures macro_feat_daily_shifted view has data to work with.
 */

-- 1. Create macro_raw if not exists (dependency for macro_src view)
CREATE TABLE IF NOT EXISTS macro_raw (
    metric TEXT,
    period_date DATE,
    value DOUBLE PRECISION,
    published_at TIMESTAMPTZ DEFAULT NOW(),
    source TEXT,
    PRIMARY KEY (metric, period_date, published_at)
);

-- 2. Seed Trade Calendar (2024-2025) - Re-run to ensure coverage
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

-- Mark holidays (ensure these are set)
UPDATE trade_calendar
SET is_trading = false
WHERE tdate IN (
    '2024-01-01',
    '2024-02-08', '2024-02-09', '2024-02-10', '2024-02-11', '2024-02-12',
    '2024-04-18', '2024-04-30', '2024-05-01', '2024-09-02',
    '2025-01-01',
    '2025-01-28', '2025-01-29', '2025-01-30', '2025-01-31', '2025-02-01'
);

-- 3. Seed Macro Clean Data (Bootstrap values)
INSERT INTO macro_clean (metric_name, value, last_updated)
VALUES
    ('GDP_YOY', 7.40, NOW()),           -- Q3 2024 strong growth
    ('CPI_YOY', 2.89, NOW()),           -- Oct 2024 inflation
    ('POLICY_RATE', 4.50, NOW()),       -- Stable
    ('USDVND', 25350.0, NOW()),         -- Recent exchange rate
    ('CREDIT_GROWTH', 9.0, NOW()),      -- YTD Credit growth estimate
    ('VNIndex', 1228.0, NOW())          -- Current market level
ON CONFLICT (metric_name)
DO UPDATE SET
    value = EXCLUDED.value,
    last_updated = NOW();

-- 4. Seed Macro Release Calendar (Ensure lags are defined)
INSERT INTO macro_release_calendar (metric, period_date, published_on, lag_days)
VALUES
    -- GDP
    ('GDP_YOY', '2024-09-30', '2024-10-06', 6),
    ('GDP_YOY', '2024-06-30', '2024-06-29', 0),
    -- CPI (Monthly)
    ('CPI_YOY', '2024-10-31', '2024-10-29', 0),
    ('CPI_YOY', '2024-09-30', '2024-09-29', 0),
    -- Policy Rate (As of dates)
    ('POLICY_RATE', '2024-11-01', '2024-11-01', 0),
    ('POLICY_RATE', '2024-10-01', '2024-10-01', 0),
    -- Credit Growth
    ('CREDIT_GROWTH', '2024-10-31', '2024-11-05', 5),
    ('CREDIT_GROWTH', '2024-09-30', '2024-10-05', 5),
    -- USDVND (Daily/Monthly proxy)
    ('USDVND', '2024-11-20', '2024-11-20', 0),
    ('USDVND', '2024-10-31', '2024-10-31', 0)
ON CONFLICT (metric, period_date) DO NOTHING;

-- 5. Ensure dim_sector has data
INSERT INTO dim_sector (sector, display_name)
VALUES
    ('technology', 'Technology'),
    ('banking', 'Banking & Finance'),
    ('consumer_goods', 'Consumer Goods'),
    ('industrial', 'Industrial & Materials'),
    ('real_estate', 'Real Estate'),
    ('utilities', 'Utilities & Energy'),
    ('materials', 'Materials'), -- Added missing from RRG
    ('energy', 'Energy')        -- Added missing from RRG
ON CONFLICT (sector) DO NOTHING;
