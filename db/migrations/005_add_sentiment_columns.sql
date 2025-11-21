-- Migration: Add sentiment feature columns to sa_silver
-- Date: 2025-11-20
-- Purpose: Enable sentiment aggregation by symbol for feature engineering

-- Add columns to sa_silver per silver_logic_v1.md spec
ALTER TABLE sa_silver 
  ADD COLUMN IF NOT EXISTS symbols TEXT[],
  ADD COLUMN IF NOT EXISTS hype_raw DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS hype_crowd DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS hype_elitist DOUBLE PRECISION;

-- Create index for symbol-based queries
CREATE INDEX IF NOT EXISTS idx_sa_silver_symbols ON sa_silver USING GIN (symbols);

-- Add comment for documentation
COMMENT ON COLUMN sa_silver.symbols IS 'Array of stock symbols extracted from article text';
COMMENT ON COLUMN sa_silver.hype_raw IS 'Raw sentiment intensity (derived from sentiment_score)';
COMMENT ON COLUMN sa_silver.hype_crowd IS 'Z-scored crowd sentiment (180d window)';
COMMENT ON COLUMN sa_silver.hype_elitist IS 'Z-scored elitist sentiment (future: weighted by account credibility)';
