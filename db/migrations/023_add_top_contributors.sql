-- Top Contributors Feature: Add column to track which stocks drive sector movements

ALTER TABLE sector_stats
ADD COLUMN IF NOT EXISTS top_contributors TEXT;

COMMENT ON COLUMN sector_stats.top_contributors IS 'Top 3 stocks by turnover contribution, formatted as "SYMBOL(XX%), SYMBOL(YY%), SYMBOL(ZZ%)"';
