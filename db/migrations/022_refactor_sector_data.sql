-- Phase 3 Refactor: Sector Index & Confidence

-- 1. Add confidence score to sector_stats
ALTER TABLE sector_stats
ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION;

-- 2. Create table for Return-based Sector Index (Base 100)
CREATE TABLE IF NOT EXISTS daily_sector_index (
    sector TEXT NOT NULL,
    trade_date DATE NOT NULL,
    index_value DOUBLE PRECISION, -- The calculated index (Base 100)
    daily_return DOUBLE PRECISION, -- The average daily return of constituents
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (sector, trade_date)
);

-- Index for faster querying
CREATE INDEX IF NOT EXISTS idx_sector_index_date ON daily_sector_index(trade_date);
