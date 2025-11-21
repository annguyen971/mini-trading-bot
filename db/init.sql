-- ==== HÀNG ĐỢI & KIỂM SOÁT ====
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS task_q (
  id BIGSERIAL PRIMARY KEY,
  kind TEXT NOT NULL,
  payload JSONB NOT NULL,
  next_run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  retry_count INT NOT NULL DEFAULT 0,
  max_retries INT NOT NULL DEFAULT 3,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_task_q_next_run_at ON task_q (next_run_at);

CREATE TABLE IF NOT EXISTS task_q_dlq (LIKE task_q INCLUDING ALL, moved_at TIMESTAMPTZ DEFAULT now());

CREATE TABLE IF NOT EXISTS control_flags (
  flag TEXT PRIMARY KEY,
  enabled BOOL NOT NULL DEFAULT true,
  reason TEXT,
  updated_at TIMESTAMPTZ
);
-- (Seed: 'KILL_SWITCH', 'BEGINNER_MODE', 'SCRAPE_SLOW')

-- ==== DỮ LIỆU & ĐẶC TRƯNG (CORE) ====
CREATE TABLE IF NOT EXISTS raw_bronze (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_name TEXT NOT NULL,
  file_path TEXT, -- Dùng cho dữ liệu > 64KB
  payload_json JSONB, -- Dùng cho dữ liệu < 64KB
  content_hash TEXT NOT NULL,
  publisher_time TIMESTAMPTZ,
  first_seen_time TIMESTAMPTZ NOT NULL DEFAULT now(),
  as_of_time TIMESTAMPTZ NOT NULL, -- greatest(publisher, first_seen)
  created_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT chk_payload_storage CHECK (
    (file_path IS NOT NULL AND payload_json IS NULL) OR
    (file_path IS NULL AND payload_json IS NOT NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_raw_bronze_as_of_time ON raw_bronze (as_of_time);
CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_bronze_content_hash ON raw_bronze (content_hash);


-- ==== DỮ LIỆU "SẠCH" (SILVER) ====
CREATE TABLE IF NOT EXISTS ta_silver (
  symbol TEXT NOT NULL,
  trade_date DATE NOT NULL,
  open DOUBLE PRECISION NOT NULL,
  high DOUBLE PRECISION NOT NULL,
  low DOUBLE PRECISION NOT NULL,
  close DOUBLE PRECISION NOT NULL,
  volume BIGINT NOT NULL,
  turnover DOUBLE PRECISION,
  as_of_time TIMESTAMPTZ NOT NULL,
  bronze_ref_id UUID REFERENCES raw_bronze(id),
  validated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS sa_silver (
  url_canonical TEXT PRIMARY KEY,
  source_name TEXT NOT NULL,
  publisher_time_utc TIMESTAMPTZ,
  as_of_time TIMESTAMPTZ NOT NULL,
  text_norm_hash TEXT NOT NULL,
  text_len INT NOT NULL,
  language TEXT,
  bronze_ref_id UUID REFERENCES raw_bronze(id),
  sentiment_score REAL,         -- Từ Gemini API
  validated_at TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sa_silver_text_norm_hash ON sa_silver (text_norm_hash);

-- Bảng dữ liệu rác (MỚI V3.1)
CREATE TABLE IF NOT EXISTS raw_bronze_bad (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_name TEXT,
  payload_json JSONB,
  reason TEXT,                 -- 'INVALID_TIMESTAMP', 'TITLE_TOO_SHORT'
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Bảng trạng thái Scraper (MỚI V3.1)
CREATE TABLE IF NOT EXISTS scraper_state (
    source_id TEXT PRIMARY KEY,
    last_cursor TEXT,           -- last_id hoặc last_timestamp
    last_run_at TIMESTAMPTZ
);


CREATE TABLE IF NOT EXISTS catalyst_flags (
  sa_silver_ref_id TEXT REFERENCES sa_silver(url_canonical),
  flag_name TEXT NOT NULL, -- e.g., 'M&A', 'EARNINGS_SURPRISE'
  value REAL, -- Optional value, e.g., surprise percentage
  as_of_time TIMESTAMPTZ NOT NULL,
  validated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (sa_silver_ref_id, flag_name)
);


CREATE TABLE IF NOT EXISTS features_gold (
  symbol TEXT NOT NULL,
  effective_date DATE NOT NULL,
  feature_set_version TEXT NOT NULL,
  feature_name TEXT NOT NULL,
  value DOUBLE PRECISION,
  PRIMARY KEY (symbol, effective_date, feature_set_version, feature_name)
) PARTITION BY RANGE (effective_date);
CREATE TABLE IF NOT EXISTS features_gold_default PARTITION OF features_gold DEFAULT;

-- Bảng Serving (dạng rộng, được hoán đổi nguyên tử)
CREATE TABLE IF NOT EXISTS features_gold_serving (
    symbol TEXT,
    effective_date DATE,
    hmm_state INT,
    HunterScore REAL,
    FrothScore REAL,
    -- ... các features khác ...
    PRIMARY KEY (symbol, effective_date)
);

-- ==== ML & NHÃN (ELITIST) ====
CREATE TABLE IF NOT EXISTS dim_source (
  source_id TEXT PRIMARY KEY,
  platform TEXT, domain TEXT,
  vip_flag BOOLEAN DEFAULT FALSE,
  base_weight REAL DEFAULT 0.5
);

CREATE TABLE IF NOT EXISTS dim_account (
  account_id TEXT PRIMARY KEY,
  source_id TEXT REFERENCES dim_source(source_id),
  cred_score REAL DEFAULT 0.5, -- ELO-like
  stability_penalty REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS account_weights (
  account_id TEXT REFERENCES dim_account(account_id),
  weight REAL NOT NULL,
  as_of_time TIMESTAMPTZ NOT NULL,
  PRIMARY KEY(account_id, as_of_time)
);

CREATE TABLE IF NOT EXISTS labels_silver (
  symbol TEXT NOT NULL,
  effective_date DATE NOT NULL,
  state_hmm INT,
  state_snorkel INT,
  probability DOUBLE PRECISION,
  entropy DOUBLE PRECISION,
  source JSONB, -- Nguồn (votes)
  is_golden BOOLEAN DEFAULT false,
  PRIMARY KEY (symbol, effective_date)
);
CREATE INDEX IF NOT EXISTS idx_labels_entropy ON labels_silver (effective_date, entropy DESC);

CREATE TABLE IF NOT EXISTS labels_golden (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  effective_date DATE NOT NULL,
  old_label INT,
  new_label INT NOT NULL,
  actor TEXT DEFAULT 'admin',
  created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE labels_silver ADD COLUMN IF NOT EXISTS golden_ref_id BIGINT REFERENCES labels_golden(id);

CREATE TABLE IF NOT EXISTS labels_silver_sandbox (LIKE labels_golden INCLUDING ALL);

CREATE TABLE IF NOT EXISTS al_queue (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  effective_date DATE NOT NULL,
  reason TEXT, -- 'entropy' hoặc 'disagreement'
  status TEXT DEFAULT 'pending',
  dedup_key TEXT UNIQUE -- (ví dụ: symbol:effective_date:yearweek)
);

CREATE TABLE IF NOT EXISTS model_registry (
  model_version TEXT PRIMARY KEY,
  model_type TEXT NOT NULL,
  file_path TEXT NOT NULL,
  artifact_sha256 TEXT, -- Dùng để xác thực
  state_map JSONB, -- Mapping (Hungarian Matching)
  metrics JSONB, -- Sharpe, Drawdown, PSI, ICs, 95% CI
  is_active BOOLEAN DEFAULT false,
  promotion_suggestion TEXT, -- 'ready_for_canary', 'rejected_by_guardrail'
  metadata JSONB -- (config_hash, feature_set_version, seed, v.v.)
);
CREATE INDEX IF NOT EXISTS idx_model_registry_active ON model_registry (is_active) WHERE is_active = true;

CREATE TABLE IF NOT EXISTS model_promotion_history (
  id BIGSERIAL PRIMARY KEY,
  model_version TEXT REFERENCES model_registry(model_version),
  action TEXT NOT NULL, -- 'APPROVE_CANARY', 'PROMOTE_PROD', 'ROLLBACK'
  actor TEXT DEFAULT 'admin',
  evidence_hash TEXT, -- Hash của metrics bundle
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS monitoring_logs (
  log_time TIMESTAMPTZ PRIMARY KEY DEFAULT now(),
  metric_name TEXT NOT NULL, -- 'psi_top_10', 'flip_rate_hmm'
  value DOUBLE PRECISION,
  metadata JSONB
);

-- (MỚI) Bảng Telemetry cho UI Events
CREATE TABLE IF NOT EXISTS event_log (
  id BIGSERIAL PRIMARY KEY,
  event_time TIMESTAMPTZ NOT NULL DEFAULT now(),
  event_name TEXT NOT NULL,     -- signal_view, sts_submitted, ai_copy_md, ai_copy_json, ai_pack_dl
  actor TEXT DEFAULT 'admin',
  meta JSONB,                   -- size, latency, context_hash, model_version, etc.
  ip INET
);
CREATE INDEX IF NOT EXISTS idx_event_log_time ON event_log(event_time);
CREATE INDEX IF NOT EXISTS idx_event_log_name ON event_log(event_name);

-- ==== VIEW AS-OF (BẮT BUỘC) ====
-- (Logic view sẽ được định nghĩa trong db/views.sql)

-- (Point 1) Bảng sector, nguồn chân lý cho các ngành
CREATE TABLE IF NOT EXISTS dim_sector (
    sector text PRIMARY KEY,
    display_name text
);

-- (Point 2) Bảng lịch giao dịch, thay thế cho generate_series
CREATE TABLE IF NOT EXISTS trade_calendar (
    tdate date PRIMARY KEY,
    is_trading boolean NOT NULL
);

-- (Point 1) Bảng lịch công bố dữ liệu vĩ mô
CREATE TABLE IF NOT EXISTS macro_release_calendar (
    metric text NOT NULL,
    period_date date NOT NULL,
    published_on date NOT NULL,
    lag_days int NOT NULL DEFAULT 1,
    PRIMARY KEY (metric, period_date)
);
CREATE INDEX IF NOT EXISTS idx_release_metric_period ON macro_release_calendar(metric, period_date);
CREATE INDEX IF NOT EXISTS idx_release_published ON macro_release_calendar(published_on);

-- (Point 1) Bảng hiệu suất ngành
CREATE TABLE IF NOT EXISTS sector_perf (
    as_of_date date NOT NULL,
    sector text NOT NULL,
    y_excess_20d double precision, -- (Sẽ được tính ở Task 2)
    PRIMARY KEY (as_of_date, sector)
);
CREATE INDEX IF NOT EXISTS idx_sector_perf_date ON sector_perf(as_of_date);
CREATE INDEX IF NOT EXISTS idx_sector_perf_sector ON sector_perf(sector);

-- (Point 1) Bảng tác động vĩ mô (real-time)
CREATE TABLE IF NOT EXISTS macro_impact_rt (
    as_of_date date NOT NULL,
    sector text NOT NULL,
    horizon_days int NOT NULL DEFAULT 20,
    impact double precision,
    confidence double precision,
    model_version text,
    created_at timestamptz default now(),
    PRIMARY KEY (as_of_date, sector, horizon_days)
);
ALTER TABLE macro_impact_rt ADD CONSTRAINT chk_conf_range CHECK (confidence BETWEEN 0 AND 1);
CREATE INDEX IF NOT EXISTS idx_macro_impact_rt_date ON macro_impact_rt(as_of_date);
CREATE INDEX IF NOT EXISTS idx_macro_impact_rt_sector ON macro_impact_rt(sector);

-- (Point 1) Bảng ghi đè UI
CREATE TABLE IF NOT EXISTS macro_ui_override (
    sector text PRIMARY KEY,
    weight double precision,
    ttl_until date,
    updated_at timestamptz default now()
);
ALTER TABLE macro_ui_override ADD CONSTRAINT chk_weight_range CHECK (weight BETWEEN -1.0 AND 1.0);
ALTER TABLE macro_ui_override ADD CONSTRAINT chk_ttl_future CHECK (ttl_until IS NULL OR ttl_until >= CURRENT_DATE);


-- (Point 7) Retention Policy: Lên lịch (schedule) một cron job (ví dụ: hàng tháng) để chạy DELETE FROM macro_impact_rt WHERE as_of_date < (CURRENT_DATE - INTERVAL '1095 days');

-- ==== USER & WATCHLIST MANAGEMENT (P0 - Dynamic Watchlist) ====
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    role TEXT DEFAULT 'admin',  -- Single admin user for MVP
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS symbol_watchlist (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    sector TEXT,  -- 'technology', 'banking', 'consumer_goods', 'industrial', 'real_estate', 'utilities'
    market_cap_tier TEXT,  -- 'large', 'mid', 'small' (optional)
    is_active BOOLEAN DEFAULT true,
    added_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_symbol_watchlist_user_active ON symbol_watchlist(user_id, is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_symbol_watchlist_symbol ON symbol_watchlist(symbol);

-- Seed admin user
INSERT INTO users (user_id, username, role)
VALUES ('admin', 'admin', 'admin')
ON CONFLICT (user_id) DO NOTHING;

-- Seed initial watchlist (Vietnamese market blue-chips)
INSERT INTO symbol_watchlist (user_id, symbol, sector, is_active)
VALUES
    ('admin', 'FPT', 'technology', true),
    ('admin', 'TCB', 'banking', true),
    ('admin', 'VNM', 'consumer_goods', true),
    ('admin', 'HPG', 'industrial', true),
    ('admin', 'VHM', 'real_estate', true),
    ('admin', 'VIC', 'real_estate', true),
    ('admin', 'VCB', 'banking', true),
    ('admin', 'MBB', 'banking', true),
    ('admin', 'VPB', 'banking', true),
    ('admin', 'GAS', 'utilities', true)
ON CONFLICT (user_id, symbol) DO NOTHING;

-- ==== LEGAL COMPLIANCE (P0 - robots.txt tracking) ====
CREATE TABLE IF NOT EXISTS robots_txt_cache (
    domain TEXT PRIMARY KEY,
    robots_content TEXT,  -- Full robots.txt content
    crawl_delay_seconds INT DEFAULT 1,  -- Extracted Crawl-delay
    is_fetch_allowed BOOLEAN DEFAULT true,  -- Can we scrape this domain?
    user_agent TEXT DEFAULT '*',  -- Which user-agent rules apply
    last_fetched TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '7 days')  -- Refresh weekly
);
CREATE INDEX IF NOT EXISTS idx_robots_cache_expires ON robots_txt_cache(expires_at);

-- Seed known sources (vnstock API - no robots.txt needed as it's an API wrapper)
INSERT INTO robots_txt_cache (domain, crawl_delay_seconds, is_fetch_allowed, user_agent, last_fetched)
VALUES
    ('vnstock-api', 1, true, 'StockHunterBot/1.0', NOW())
ON CONFLICT (domain) DO NOTHING;
