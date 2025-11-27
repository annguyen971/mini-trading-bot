/*
 * Migration 020: Update Daily Sector Prices View
 * ===============================================
 * Date: 2025-11-27
 * Description: Updates daily_sector_prices view to include volume and turnover
 *              required for RRG 2.0 Market X-Ray metrics.
 */

CREATE OR REPLACE VIEW daily_sector_prices AS
SELECT
    t.trade_date,
    s.sector,
    AVG(t.close) AS close,
    SUM(t.volume) AS volume,
    SUM(t.close * t.volume) AS turnover
FROM ta_silver t
JOIN symbol_watchlist s ON t.symbol = s.symbol
WHERE s.is_active = true
GROUP BY t.trade_date, s.sector;

INSERT INTO schema_migrations (migration_id, description)
VALUES ('020_update_daily_sector_prices', 'Add volume and turnover to daily_sector_prices view')
ON CONFLICT (migration_id) DO NOTHING;
