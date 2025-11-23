/*
 * Backfill sector_perf (y_excess_20d)
 * ===================================
 * Calculates y_excess_20d for all available history in daily_sector_prices
 * to enable macro beta calculations.
 */

INSERT INTO sector_perf (as_of_date, sector, y_excess_20d)
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
ON CONFLICT (as_of_date, sector) DO UPDATE SET
    y_excess_20d = EXCLUDED.y_excess_20d;
