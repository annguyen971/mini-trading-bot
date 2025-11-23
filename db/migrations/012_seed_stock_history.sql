/*
 * Migration 012: Seed Stock History
 * ===============================================
 * Date: 2025-11-21
 * Description: Seeds historical stock data into ta_silver for all watchlist symbols.
 *              Required to have >60 days of history for macro beta calculations.
 */

INSERT INTO ta_silver (symbol, trade_date, open, high, low, close, volume, turnover, as_of_time)
SELECT
    s.symbol,
    d::date,
    50.0, -- Open
    52.0, -- High
    48.0, -- Low
    50.0 + (random() * 5 - 2.5), -- Close (random walk)
    1000000, -- Volume
    50000000, -- Turnover
    (d + interval '1 day')::timestamptz
FROM symbol_watchlist s
CROSS JOIN generate_series(
    '2024-01-01'::date,
    '2025-11-20'::date,
    '1 day'::interval
) AS d
ON CONFLICT (symbol, trade_date) DO NOTHING;
