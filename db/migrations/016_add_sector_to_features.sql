-- 1. Add sector column to features_gold_serving
ALTER TABLE features_gold_serving ADD COLUMN IF NOT EXISTS sector TEXT;

-- 2. Backfill data (Current snapshot assigned to past - Accepted for MVP)
UPDATE features_gold_serving f
SET sector = s.sector
FROM symbol_watchlist s
WHERE f.symbol = s.symbol AND f.sector IS NULL;

-- 3. Create Composite Index optimized for search
CREATE INDEX IF NOT EXISTS idx_dejavu_context
ON features_gold_serving (hmm_state, sector, effective_date DESC);
