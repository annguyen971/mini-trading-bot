-- Add columns for Market X-Ray Refinement
ALTER TABLE sector_stats
ADD COLUMN IF NOT EXISTS breadth_n INTEGER,
ADD COLUMN IF NOT EXISTS sector_n INTEGER,
ADD COLUMN IF NOT EXISTS tags TEXT[]; -- Array of strings for diagnosis tags
