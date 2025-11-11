-- Migration 02: Add tables for Admin UI (Task 16)

-- Table for System Health: Macro Mini-Hub
CREATE TABLE IF NOT EXISTS macro_clean (
    metric_name TEXT PRIMARY KEY,
    value DOUBLE PRECISION,
    delta DOUBLE PRECISION,
    last_updated TIMESTAMPTZ DEFAULT now()
);

-- Table for System Health: Sector Heatmap
CREATE TABLE IF NOT EXISTS sector_stats (
    sector TEXT PRIMARY KEY,
    momentum DOUBLE PRECISION,
    breadth DOUBLE PRECISION,
    last_updated TIMESTAMPTZ DEFAULT now()
);

-- Table for Macro Impact Config
CREATE TABLE IF NOT EXISTS dim_macro_sector_impact (
    factor TEXT NOT NULL,
    sector TEXT NOT NULL,
    impact TEXT NOT NULL, -- e.g., '++', '+', '-', '--'
    PRIMARY KEY (factor, sector)
);

-- Seed initial data so the UI has something to display
INSERT INTO macro_clean (metric_name, value, delta) VALUES
    ('MLI', 0.72, 0.02),
    ('z_cpi', 1.5, -0.2),
    ('z_fx', -0.8, 0.1)
ON CONFLICT (metric_name) DO NOTHING;

INSERT INTO sector_stats (sector, momentum, breadth) VALUES
    ('Technology', 0.85, 0.75),
    ('Finance', 0.62, 0.88),
    ('Consumer Goods', 0.45, 0.55),
    ('Industrials', 0.71, 0.66)
ON CONFLICT (sector) DO NOTHING;

INSERT INTO dim_macro_sector_impact (factor, sector, impact) VALUES
    ('CPI', 'Technology', '++'),
    ('CPI', 'Consumer Goods', '--'),
    ('Interest Rate', 'Finance', '+'),
    ('Interest Rate', 'Technology', '-')
ON CONFLICT (factor, sector) DO NOTHING;
