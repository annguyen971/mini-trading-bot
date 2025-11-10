-- Corrected as-of view to prevent circular dependency.
-- This view joins the silver tables to provide a base for feature engineering.
CREATE OR REPLACE VIEW v_features_asof AS
WITH sa_agg AS (
    SELECT
        s.symbols[1] AS symbol,
        date_trunc('day', s.publisher_time_utc) AS trade_date,
        -- Aggregate sentiment/hype scores for each symbol per day.
        -- Using placeholders for now as the exact logic is not specified.
        AVG(s.text_len) AS avg_text_len
    FROM sa_silver s
    WHERE s.symbols IS NOT NULL AND array_length(s.symbols, 1) > 0
    GROUP BY 1, 2
)
SELECT
    t.*,
    sa.avg_text_len
FROM ta_silver t
LEFT JOIN sa_agg sa ON t.symbol = sa.symbol AND t.trade_date = sa.trade_date;
