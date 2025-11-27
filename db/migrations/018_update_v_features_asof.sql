-- Migration 018: Update v_features_asof View (Add Volume & Adjust Placeholders)
-- Priority: P0 (Hotfix for Deja Vu Logic)

DROP VIEW IF EXISTS v_features_asof CASCADE;

CREATE OR REPLACE VIEW v_features_asof AS
SELECT
    t.symbol,
    t.trade_date AS effective_date,
    t.close,
    t.volume, -- Added Volume for OBV calculation
    s.sector,
    -- Placeholders updated to simulate Hunter Score > 55
    0.4::float AS hype_crowd_z,
    0.6::float AS news_count_crowd_z,
    0.6::float AS S_tech_z,
    0.6::float AS breadth_contra_z,
    0.8::float AS hype_elitist_z,
    0.7::float AS H_ta_z,
    0.6::float AS H_cat_z,
    0::int AS catalyst_active
FROM ta_silver t
JOIN symbol_watchlist s ON t.symbol = s.symbol;
