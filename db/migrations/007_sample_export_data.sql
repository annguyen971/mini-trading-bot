-- Insert sample data for data pack export testing
INSERT INTO macro_clean (metric_name, effective_date, value) VALUES
('GDP_GROWTH', '2025-11-01', 6.5),
('INFLATION_RATE', '2025-11-01', 3.2),
('INTEREST_RATE', '2025-11-01', 4.5)
ON CONFLICT (metric_name, effective_date) DO NOTHING;

INSERT INTO breadth_stats (as_of_date, advancing, declining, unchanged, advance_decline_ratio, new_highs, new_lows) VALUES  
('2025-11-20', 450, 350, 50, 1.29, 25, 10),
('2025-11-19', 420, 380, 50, 1.11, 20, 15)
ON CONFLICT (as_of_date) DO NOTHING;
