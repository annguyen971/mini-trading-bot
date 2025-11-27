/*
 * Migration 015: Upgrade Model Registry for Hybrid Sniper
 * ========================================================
 * Date: 2025-11-24
 * Description: Adds support for strategy versioning, model lifecycle management,
 *              and Predator scenario tracking for the Hybrid Sniper approach.
 */

-- ==============================================================================
-- STEP 1: Extend model_registry table
-- ==============================================================================

ALTER TABLE model_registry 
ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'candidate',
ADD COLUMN IF NOT EXISTS win_rate_oos FLOAT,
ADD COLUMN IF NOT EXISTS active_scenarios JSONB;

-- Add constraint for status field
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'chk_model_status'
    ) THEN
        ALTER TABLE model_registry
        ADD CONSTRAINT chk_model_status 
        CHECK (status IN ('candidate', 'canary', 'active', 'archived'));
    END IF;
END $$;

COMMENT ON COLUMN model_registry.status IS 
'Lifecycle stage: candidate (newly trained), canary (testing), active (production), archived (deprecated)';

COMMENT ON COLUMN model_registry.win_rate_oos IS 
'Out-of-sample win rate from backtest (0.0 to 1.0)';

COMMENT ON COLUMN model_registry.active_scenarios IS 
'JSON array of Predator scenario names that were active during training, e.g. ["Sniper_RSI_Divergence", "Sniper_Vol_Breakout"]';

-- ==============================================================================
-- STEP 2: Create model_drift_logs table
-- ==============================================================================

CREATE TABLE IF NOT EXISTS model_drift_logs (
    id BIGSERIAL PRIMARY KEY,
    log_time TIMESTAMPTZ DEFAULT NOW(),
    model_version TEXT NOT NULL,
    psi_score FLOAT,
    ic_degradation FLOAT,
    warnings TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_drift_logs_model ON model_drift_logs(model_version);
CREATE INDEX IF NOT EXISTS idx_drift_logs_time ON model_drift_logs(log_time DESC);

COMMENT ON TABLE model_drift_logs IS 
'Tracks model performance degradation over time. PSI > 0.25 or IC drop > 50% triggers alerts.';

-- ==============================================================================
-- STEP 3: Create predator_signals table
-- ==============================================================================

CREATE TABLE IF NOT EXISTS predator_signals (
    symbol TEXT NOT NULL,
    signal_date DATE NOT NULL,
    active_scenarios JSONB NOT NULL,
    scenario_count INT NOT NULL DEFAULT 0,
    confidence FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, signal_date)
);

CREATE INDEX IF NOT EXISTS idx_predator_signals_date ON predator_signals(signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_predator_signals_count ON predator_signals(scenario_count DESC);

COMMENT ON TABLE predator_signals IS 
'Stores daily Predator rule activations. Only symbols with active scenarios proceed to ML validation.';

COMMENT ON COLUMN predator_signals.active_scenarios IS 
'JSON array of scenario objects, e.g. [{"name": "Sniper_RSI_Divergence", "strength": 0.8}]';

-- ==============================================================================
-- STEP 4: Create hybrid_predictions table
-- ==============================================================================

CREATE TABLE IF NOT EXISTS hybrid_predictions (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    prediction_date DATE NOT NULL,
    model_version TEXT NOT NULL,
    
    -- Predator signals
    predator_scenarios JSONB,
    predator_score FLOAT,
    
    -- ML scores
    ml_probability FLOAT,
    ml_class INT,
    
    -- Final decision
    final_signal BOOLEAN,
    final_confidence FLOAT,
    
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    UNIQUE(symbol, prediction_date, model_version)
);

CREATE INDEX IF NOT EXISTS idx_hybrid_predictions_date ON hybrid_predictions(prediction_date DESC);
CREATE INDEX IF NOT EXISTS idx_hybrid_predictions_signal ON hybrid_predictions(final_signal) WHERE final_signal = true;

COMMENT ON TABLE hybrid_predictions IS 
'Stores Hybrid Sniper predictions. final_signal=true only when both Predator AND ML agree.';

-- ==============================================================================
-- STEP 5: Verification
-- ==============================================================================

DO $$
DECLARE
    col_count INT;
    table_count INT;
BEGIN
    -- Check model_registry columns
    SELECT COUNT(*) INTO col_count
    FROM information_schema.columns
    WHERE table_name = 'model_registry' 
    AND column_name IN ('status', 'win_rate_oos', 'active_scenarios');
    
    IF col_count < 3 THEN
        RAISE EXCEPTION 'Migration 015 failed: model_registry columns not added';
    END IF;
    
    -- Check new tables
    SELECT COUNT(*) INTO table_count
    FROM information_schema.tables
    WHERE table_name IN ('model_drift_logs', 'predator_signals', 'hybrid_predictions');
    
    IF table_count < 3 THEN
        RAISE EXCEPTION 'Migration 015 failed: Tables not created';
    END IF;
    
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration 015 Verification:';
    RAISE NOTICE '  model_registry: 3 new columns added';
    RAISE NOTICE '  model_drift_logs: Created';
    RAISE NOTICE '  predator_signals: Created';
    RAISE NOTICE '  hybrid_predictions: Created';
    RAISE NOTICE '  Indexes: 5 total';
    RAISE NOTICE '========================================';
END $$;

RAISE NOTICE 'Migration 015 completed successfully';
RAISE NOTICE 'Hybrid Sniper database schema ready';
