-- Script to reset market data for a fresh 6-month ingestion
-- Usage: psql -f scripts/reset_data.sql

BEGIN;

-- 1. Clear Silver Layer (Market Data & News)
TRUNCATE TABLE ta_silver CASCADE;
TRUNCATE TABLE sa_silver CASCADE;
TRUNCATE TABLE catalyst_flags CASCADE;

-- 2. Clear Bronze Layer (Raw Data)
TRUNCATE TABLE raw_bronze CASCADE;
TRUNCATE TABLE raw_bronze_bad CASCADE;

-- 3. Clear Gold Layer (Features & Serving)
TRUNCATE TABLE features_gold CASCADE;
TRUNCATE TABLE features_gold_serving CASCADE;

-- 4. Clear Labels & Analysis
TRUNCATE TABLE labels_silver CASCADE;
TRUNCATE TABLE labels_golden CASCADE;
TRUNCATE TABLE labels_silver_sandbox CASCADE;
TRUNCATE TABLE al_queue CASCADE;

-- 5. Clear Macro & Sector Data (Real-time only, keep dimensions)
TRUNCATE TABLE macro_impact_rt CASCADE;
TRUNCATE TABLE sector_perf CASCADE;

-- 6. Reset Scraper State to force re-crawl
TRUNCATE TABLE scraper_state CASCADE;

-- 7. Clear Task Queue to prevent stale tasks from running
TRUNCATE TABLE task_q CASCADE;
TRUNCATE TABLE task_q_dlq CASCADE;

-- 8. Clear Event Logs (Optional, but good for clean slate)
TRUNCATE TABLE event_log CASCADE;

COMMIT;

-- Verify
SELECT count(*) as ta_silver_count FROM ta_silver;
SELECT count(*) as sa_silver_count FROM sa_silver;
SELECT count(*) as features_count FROM features_gold_serving;
