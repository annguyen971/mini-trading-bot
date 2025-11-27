/*
 * Migration 019: Add Market X-Ray Metrics
 * ===============================================
 * Date: 2025-11-27
 * Description: Adds columns for Market X-Ray metrics (Concentration, Turnover Shock)
 *              to the sector_stats table.
 */

ALTER TABLE sector_stats
ADD COLUMN IF NOT EXISTS concentration DOUBLE PRECISION,
ADD COLUMN IF NOT EXISTS turnover_shock_z DOUBLE PRECISION;

-- Add comment for documentation
COMMENT ON COLUMN sector_stats.concentration IS 'Top 3 Turnover Concentration (Proxy for Cap Concentration)';
COMMENT ON COLUMN sector_stats.turnover_shock_z IS 'Robust Z-Score of Sector Turnover (Money Flow Shock)';

INSERT INTO schema_migrations (migration_id, description)
VALUES ('019_add_market_xray_metrics', 'Add concentration and turnover_shock_z to sector_stats')
ON CONFLICT (migration_id) DO NOTHING;
