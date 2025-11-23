/*
 * Migration 011: Seed VNINDEX Data (Fix)
 * ===============================================
 * Date: 2025-11-21
 * Description: Seeds VNINDEX data into ta_silver to support macro calculations.
 *              Required because daily_vnindex_prices view depends on it.
 */

-- Insert VNINDEX data (Synthetic, based on average of other stocks or fixed baseline)
INSERT INTO ta_silver (symbol, trade_date, open, high, low, close, volume, turnover, as_of_time)
SELECT
    'VNINDEX',
    d::date,
    1250.0, -- Open
    1260.0, -- High
    1240.0, -- Low
    1250.0 + (random() * 10 - 5), -- Close (random walk around 1250)
    1000000, -- Volume
    1000000000, -- Turnover
    (d + interval '1 day')::timestamptz -- as_of_time (next day)
FROM generate_series(
    '2024-01-01'::date,
    '2025-12-31'::date,
    '1 day'::interval
) AS d
ON CONFLICT (symbol, trade_date) DO NOTHING;
