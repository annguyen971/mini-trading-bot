-- Migration 017: Add OBV Slope and Sector RS Ratio to Features Gold
-- Priority: P0 (Deja Vu 2.1)

ALTER TABLE features_gold_serving ADD COLUMN IF NOT EXISTS obv_slope_5d FLOAT;
ALTER TABLE features_gold_serving ADD COLUMN IF NOT EXISTS sector_rs_ratio FLOAT;

-- Create index for OBV Slope to support fast filtering if needed
CREATE INDEX IF NOT EXISTS idx_features_obv ON features_gold_serving (obv_slope_5d);
