/*
 * Migration 005: Create Feature Views
 * ===================================
 * Date: 2025-11-20
 * Description: Creates views required for feature engineering (Gold Layer)
 */

-- 1. View: v_ta_silver_for_gold
-- Wraps ta_silver to provide a consistent interface for feature engineering
CREATE OR REPLACE VIEW v_ta_silver_for_gold AS
SELECT
    symbol,
    trade_date AS effective_date,
    open,
    high,
    low,
    close,
    volume,
    turnover
FROM ta_silver;

-- 2. View: daily_sector_prices
-- Aggregates stock prices by sector
CREATE OR REPLACE VIEW daily_sector_prices AS
SELECT
    t.trade_date,
    s.sector,
    AVG(t.close) AS close
FROM ta_silver t
JOIN symbol_watchlist s ON t.symbol = s.symbol
GROUP BY t.trade_date, s.sector;

-- 3. View: daily_vnindex_prices
-- Extracts VNINDEX prices (assuming VNINDEX is present in ta_silver or we use a proxy)
-- If VNINDEX is not in ta_silver, this will be empty, which is acceptable for now.
CREATE OR REPLACE VIEW daily_vnindex_prices AS
SELECT
    trade_date,
    close
FROM ta_silver
WHERE symbol = 'VNINDEX';

-- 4. View: macro_feat_daily_shifted
-- Projects current macro values across time (Bootstrap approach)
-- This allows the macro logic to run even without full historical macro data.
CREATE OR REPLACE VIEW macro_feat_daily_shifted AS
SELECT
    d.tdate AS as_of_date,
    m.metric_name AS metric,
    m.value
FROM trade_calendar d
CROSS JOIN macro_clean m
WHERE d.tdate <= CURRENT_DATE;

-- Verification
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_views WHERE viewname = 'v_ta_silver_for_gold') THEN
        RAISE EXCEPTION 'View v_ta_silver_for_gold not created';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_views WHERE viewname = 'daily_sector_prices') THEN
        RAISE EXCEPTION 'View daily_sector_prices not created';
    END IF;

    RAISE NOTICE 'Migration 005 completed successfully';
END $$;
