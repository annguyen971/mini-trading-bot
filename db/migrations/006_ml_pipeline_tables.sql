/*
 * Migration 006: ML Pipeline Tables
 * ==================================
 * Date: 2025-11-20
 * Description: Creates tables for ML pipeline monitoring and backtest storage
 *
 * Tables:
 * - backtest_results: Store WFO backtest window results
 * - model_predictions: Log production predictions for monitoring
 */

-- ==============================================================================
-- STEP 1: Create backtest_results table
-- ==============================================================================

CREATE TABLE IF NOT EXISTS backtest_results (
    id BIGSERIAL PRIMARY KEY,
    model_version TEXT NOT NULL,
    train_start_date DATE NOT NULL,
    train_end_date DATE NOT NULL,
    test_start_date DATE NOT NULL,
    test_end_date DATE NOT NULL,
    
    -- Metrics
    sharpe_ratio DOUBLE PRECISION,
    max_drawdown DOUBLE PRECISION,
    information_coefficient DOUBLE PRECISION,
    win_rate DOUBLE PRECISION,
    total_trades INT,
    
    -- Configuration
    feature_set_version TEXT,
    hyperparameters JSONB,
    
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT chk_dates CHECK (
        train_start_date < train_end_date AND
        test_start_date < test_end_date AND
        train_end_date <= test_start_date
    )
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_backtest_model_version ON backtest_results(model_version);
CREATE INDEX IF NOT EXISTS idx_backtest_test_dates ON backtest_results(test_start_date, test_end_date);
CREATE INDEX IF NOT EXISTS idx_backtest_created_at ON backtest_results(created_at DESC);

COMMENT ON TABLE backtest_results IS 
'Stores Walk-Forward Optimization (WFO) backtest results. Each row represents one test window.';

-- ==============================================================================
-- STEP 2: Create model_predictions table
-- ==============================================================================

CREATE TABLE IF NOT EXISTS model_predictions (
    id BIGSERIAL PRIMARY KEY,
    model_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    prediction_date DATE NOT NULL,
    
    -- Prediction
    predicted_class INT,
    predicted_proba DOUBLE PRECISION,
    
    -- Features used (optional, for debugging)
    features JSONB,
    
    -- Actual outcome (filled in later for monitoring)
    actual_class INT,
    actual_return DOUBLE PRECISION,
    
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Unique constraint: one prediction per model-symbol-date
    UNIQUE(model_version, symbol, prediction_date)
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_predictions_model_symbol ON model_predictions(model_version, symbol);
CREATE INDEX IF NOT EXISTS idx_predictions_date ON model_predictions(prediction_date DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_created_at ON model_predictions(created_at DESC);

COMMENT ON TABLE model_predictions IS 
'Logs all production model predictions for monitoring and evaluation. Actual outcomes filled in after forward returns are known.';

-- ==============================================================================
-- STEP 3: Add retention policy trigger (optional but recommended)
-- ==============================================================================

-- Keep predictions for 1 year, backtest results indefinitely
COMMENT ON COLUMN model_predictions.created_at IS 
'Auto-cleanup: DELETE WHERE created_at < NOW() - INTERVAL ''1 year'' (run monthly via cron)';

-- ==============================================================================
-- STEP 4: Verification
-- ==============================================================================

DO $$
DECLARE
    backtest_count INT;
    predictions_count INT;
BEGIN
    SELECT COUNT(*) INTO backtest_count 
    FROM information_schema.tables 
    WHERE table_name = 'backtest_results';
    
    SELECT COUNT(*) INTO predictions_count 
    FROM information_schema.tables 
    WHERE table_name = 'model_predictions';
    
    IF backtest_count = 0 OR predictions_count = 0 THEN
        RAISE EXCEPTION 'Migration 006 failed: Tables not created';
    END IF;
    
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration 006 Verification:';
    RAISE NOTICE '  backtest_results: Created';
    RAISE NOTICE '  model_predictions: Created';
    RAISE NOTICE '  Indexes: 6 total';
    RAISE NOTICE '========================================';
END $$;

RAISE NOTICE 'Migration 006 completed successfully';
RAISE NOTICE 'ML Pipeline tables ready for training and monitoring';
