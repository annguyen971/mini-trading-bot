-- View dự phòng (fallback) an toàn nếu macro_clean chưa tồn tại
CREATE OR REPLACE VIEW macro_src AS
SELECT * FROM macro_clean
UNION ALL
SELECT * FROM macro_raw
WHERE NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_name='macro_clean'
);

-- View logic shift (đã vá lỗi Point 1)
CREATE OR REPLACE VIEW macro_feat_daily_shifted AS
WITH base AS (
    SELECT
        m.metric,
        m.period_date,
        m.value,
        r.published_on,
        COALESCE(r.lag_days, 1) AS lag_days
    FROM macro_src m -- (Dùng view an toàn)
    JOIN macro_release_calendar r ON r.metric = m.metric AND r.period_date = m.period_date
),
calc AS (
    SELECT
        (published_on + (lag_days || ' days')::interval)::date AS as_of_date_valid,
        metric,
        value
    FROM base
    WHERE published_on IS NOT NULL
)
SELECT
    as_of_date_valid AS as_of_date,
    metric,
    value
FROM calc;
