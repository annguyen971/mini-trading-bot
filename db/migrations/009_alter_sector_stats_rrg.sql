-- Migration 009: Alter sector_stats for RRG (Relative Rotation Graph)
-- Date: 2025-11-21
-- Purpose: Add columns to support RRG visualization at sub-sector level

-- Add new columns to sector_stats
ALTER TABLE sector_stats ADD COLUMN IF NOT EXISTS super_sector TEXT;
ALTER TABLE sector_stats ADD COLUMN IF NOT EXISTS rs_ratio DOUBLE PRECISION;
ALTER TABLE sector_stats ADD COLUMN IF NOT EXISTS rs_momentum DOUBLE PRECISION;

-- Create index for efficient querying by super_sector
CREATE INDEX IF NOT EXISTS idx_sector_stats_super_sector ON sector_stats(super_sector);

-- Add comments for documentation
COMMENT ON COLUMN sector_stats.super_sector IS 'Super sector classification (4 Pillars + 1) for UI grouping/coloring';
COMMENT ON COLUMN sector_stats.rs_ratio IS 'Relative Strength Ratio vs VN-Index (60-day smoothed, represents long-term trend)';
COMMENT ON COLUMN sector_stats.rs_momentum IS 'Rate of change of RS-Ratio (short-term momentum)';

-- Verification
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'sector_stats' 
        AND column_name IN ('super_sector', 'rs_ratio', 'rs_momentum')
    ) THEN
        RAISE EXCEPTION 'Migration 009 failed: Columns not added';
    END IF;
    
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration 009 Verification:';
    RAISE NOTICE '  sector_stats.super_sector: Added';
    RAISE NOTICE '  sector_stats.rs_ratio: Added';
    RAISE NOTICE '  sector_stats.rs_momentum: Added';
    RAISE NOTICE '========================================';
END $$;

RAISE NOTICE 'Migration 009 completed successfully';
RAISE NOTICE 'sector_stats table ready for RRG data';
