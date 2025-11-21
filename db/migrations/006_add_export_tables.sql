-- Migration: Add missing tables for API data pack export
-- Date: 2025-11-20
-- Purpose: Fix 500 error in /export/pack endpoint

-- Create sector_stats table with correct schema
CREATE TABLE IF NOT EXISTS sector_stats (
    sector TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    momentum DOUBLE PRECISION,
    breadth DOUBLE PRECISION,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (sector, as_of_date)
);

CREATE INDEX IF NOT EXISTS idx_sector_stats_date ON sector_stats(as_of_date DESC);

-- Create breadth_stats table
CREATE TABLE IF NOT EXISTS breadth_stats (
    as_of_date DATE PRIMARY KEY,
    advancing INT,
    declining INT,
    unchanged INT,
    advance_decline_ratio DOUBLE PRECISION,
    new_highs INT,
    new_lows INT,
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_breadth_stats_date ON breadth_stats(as_of_date DESC);

-- Create macro_clean table (if not exists from previous migrations)
CREATE TABLE IF NOT EXISTS macro_clean (
    metric_name TEXT NOT NULL,
    effective_date DATE NOT NULL,
    value DOUBLE PRECISION,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (metric_name, effective_date)
);

CREATE INDEX IF NOT EXISTS idx_macro_clean_date ON macro_clean(effective_date DESC);

COMMENT ON TABLE sector_stats IS 'Sector performance statistics for data pack export';
COMMENT ON TABLE breadth_stats IS 'Market breadth statistics for data pack export';
COMMENT ON TABLE macro_clean IS 'Macro economic indicators for data pack export';
