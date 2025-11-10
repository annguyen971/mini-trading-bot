-- ==== HÀNG ĐỢI & KIỂM SOÁT ====
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS task_q (
  id BIGSERIAL PRIMARY KEY,
  task_type TEXT NOT NULL,   -- 'ta.bar' | 'sa.article'
  payload JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'ready',
  priority INT NOT NULL DEFAULT 100,
  first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_attempt TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_task_q_status ON task_q(status, priority, first_seen) WHERE status = 'ready';

CREATE TABLE IF NOT EXISTS task_q_dlq (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT,
  reason TEXT NOT NULL,
  rule_id TEXT,
  payload JSONB,
  error_msg TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS control_flags (
  name TEXT PRIMARY KEY,
  value BOOLEAN NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
  low  DOUBLE PRECISION NOT NULL,
  close DOUBLE PRECISION NOT NULL,
  volume BIGINT NOT NULL,
  vwap DOUBLE PRECISION,
  adj_close DOUBLE PRECISION,
  currency TEXT NOT NULL DEFAULT 'VND',
  price_multiplier DOUBLE PRECISION NOT NULL DEFAULT 1.0,
  source TEXT,
  first_seen_time TIMESTAMPTZ,
  ingest_time TIMESTAMPTZ NOT NULL DEFAULT now(),
  content_hash TEXT,
  PRIMARY KEY(symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS sa_silver (
  url_canonical TEXT UNIQUE NOT NULL, -- Ưu tiên UNIQUE NOT NULL cho khóa chính
  source_domain TEXT NOT NULL,
  publisher_time TIMESTAMPTZ NOT NULL,
  first_seen_time TIMESTAMPTZ NOT NULL,
  language TEXT,
  title TEXT,
  text_normalized TEXT,
  content_hash TEXT NOT NULL,
  symbols TEXT[],
  author TEXT,
  topic_tags TEXT[],
  hype_raw DOUBLE PRECISION,
  hype_crowd DOUBLE PRECISION,
  hype_elitist DOUBLE PRECISION,
  account_weights_applied BOOLEAN,
  ingest_time TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (url_canonical) -- Đơn giản hóa khóa
);
-- Chỉ mục (Index)
CREATE INDEX IF NOT EXISTS idx_sa_silver_pub ON sa_silver(publisher_time);
CREATE INDEX IF NOT EXISTS idx_sa_silver_symbols ON sa_silver USING GIN (symbols);
CREATE INDEX IF NOT EXISTS idx_sa_silver_hash_fallback ON sa_silver(source_domain, content_hash);

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
