/*
 * Migration 003: Seed Default Model for API Startup
 * ===================================================
 * Date: 2025-01-18
 * Description: Creates a placeholder "baseline" model to allow API to start
 *
 * This is a bootstrap model that allows the system to run before
 * the first training job completes. It provides baseline predictions.
 *
 * The model will be replaced by trained models via the ML pipeline.
 *
 * Rollback: DELETE FROM model_registry WHERE model_version = 'baseline_v1';
 */

-- ==============================================================================
-- STEP 1: Create Baseline Model Entry
-- ==============================================================================

INSERT INTO model_registry (
    model_version,
    model_type,
    file_path,
    artifact_sha256,
    metrics,
    is_active,
    promotion_suggestion,
    metadata
)
VALUES (
    'baseline_v1',
    'BaselineHeuristic',
    '/opt/artifacts/baseline_v1.pkl',  -- Will be created via script
    'placeholder_hash',  -- Placeholder - real hash will be updated after file creation
    '{
        "sharpe": 0.0,
        "ic": 0.0,
        "max_drawdown": 0.0,
        "description": "Bootstrap baseline model - provides neutral predictions until first trained model is available"
    }'::jsonb,
    true,  -- Set as active
    'baseline_bootstrap',
    '{
        "feature_set_version": "v1",
        "is_baseline": true,
        "created_by": "migration_003"
    }'::jsonb
)
ON CONFLICT (model_version) DO NOTHING;

-- ==============================================================================
-- STEP 2: Verification
-- ==============================================================================

DO $$
DECLARE
    v_model_count INT;
BEGIN
    SELECT COUNT(*) INTO v_model_count
    FROM model_registry
    WHERE model_version = 'baseline_v1';

    IF v_model_count > 0 THEN
        RAISE NOTICE 'Baseline model seeded successfully';
        RAISE NOTICE 'Model version: baseline_v1';
        RAISE NOTICE 'Status: ACTIVE (placeholder)';
        RAISE NOTICE '';
        RAISE NOTICE 'IMPORTANT: Create the actual model file:';
        RAISE NOTICE '  docker compose exec worker python -m worker.create_baseline_model';
    ELSE
        RAISE WARNING 'Failed to seed baseline model';
    END IF;
END $$;

-- ==============================================================================
-- STEP 3: Migration Tracking
-- ==============================================================================

INSERT INTO schema_migrations (migration_id, description)
VALUES ('003_seed_default_model', 'Seed baseline model for API bootstrap')
ON CONFLICT (migration_id) DO UPDATE SET applied_at = NOW();

RAISE NOTICE 'Migration 003 completed';
