WITH d AS (
    SELECT
        f.tdate AS as_of_date,
        s.sector,
        m_rate.value AS rate,
        m_fx.value AS fx,
        m_credit.value AS credit
    FROM
        dim_sector s
    CROSS JOIN
        (
            SELECT tdate FROM trade_calendar
            WHERE tdate BETWEEN '2025-11-21'::date - interval '300 days' AND '2025-11-21'::date AND is_trading
        ) f
    LEFT JOIN macro_feat_daily_shifted m_rate ON m_rate.as_of_date = f.tdate AND m_rate.metric='POLICY_RATE'
    LEFT JOIN macro_feat_daily_shifted m_fx ON m_fx.as_of_date = f.tdate AND m_fx.metric='USDVND'
    LEFT JOIN macro_feat_daily_shifted m_credit ON m_credit.as_of_date = f.tdate AND m_credit.metric='CREDIT_GROWTH'
)
SELECT count(*) FROM d;
