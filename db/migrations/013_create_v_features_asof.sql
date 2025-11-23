/*
 * Migration 013: Create v_features_asof View (Fix)
 * ===============================================
 * Date: 2025-11-21
 * Description: Creates the v_features_asof view required by the feature gold batch job.
 *              Aggregates price data and features (placeholders for now) for scoring.
 */

DROP VIEW IF EXISTS v_features_asof CASCADE;

CREATE OR REPLACE VIEW v_features_asof AS
SELECT
    t.symbol,
    t.trade_date AS effective_date,
    t.close,
    s.sector,
    -- Placeholders for features (to be replaced by real tables later)
    0.5::float AS hype_crowd_z,
    0.5::float AS news_count_crowd_z,
    0.5::float AS S_tech_z,
    0.5::float AS breadth_contra_z,
    0.5::float AS hype_elitist_z,
    0.5::float AS H_ta_z,
    0.5::float AS H_cat_z,
    0::int AS catalyst_active
FROM ta_silver t
JOIN symbol_watchlist s ON t.symbol = s.symbol;
