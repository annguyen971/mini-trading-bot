/*
 * Rollback Migration 002: P0 Critical Features - Dynamic Watchlist + Legal Guard
 * ==============================================================================
 * Date: 2025-01-18
 * Description: Rollback script for migration 002
 *
 * WARNING: This will permanently delete:
 * - All users (including admin)
 * - All watchlist data
 * - All robots.txt cache
 *
 * Use with caution in production!
 */

-- ==============================================================================
-- BACKUP REMINDER
-- ==============================================================================

DO $$
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'ROLLBACK WARNING:';
    RAISE NOTICE 'This will delete users, watchlist, and robots cache!';
    RAISE NOTICE 'Ensure you have a database backup before proceeding.';
    RAISE NOTICE '========================================';
END $$;

-- ==============================================================================
-- STEP 1: Drop Indexes
-- ==============================================================================

DROP INDEX IF EXISTS idx_symbol_watchlist_user_active;
DROP INDEX IF EXISTS idx_symbol_watchlist_symbol;
DROP INDEX IF EXISTS idx_robots_cache_expires;

RAISE NOTICE 'Dropped indexes';

-- ==============================================================================
-- STEP 2: Drop Tables (in reverse dependency order)
-- ==============================================================================

DROP TABLE IF EXISTS robots_txt_cache CASCADE;
RAISE NOTICE 'Dropped table: robots_txt_cache';

DROP TABLE IF EXISTS symbol_watchlist CASCADE;
RAISE NOTICE 'Dropped table: symbol_watchlist';

DROP TABLE IF EXISTS users CASCADE;
RAISE NOTICE 'Dropped table: users';

-- ==============================================================================
-- STEP 3: Remove Migration Tracking
-- ==============================================================================

DELETE FROM schema_migrations WHERE migration_id = '002_add_p0_watchlist_legal_guard';
RAISE NOTICE 'Removed migration tracking record';

-- ==============================================================================
-- VERIFICATION
-- ==============================================================================

DO $$
DECLARE
    v_users_exists BOOLEAN;
    v_watchlist_exists BOOLEAN;
    v_robots_exists BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'users'
    ) INTO v_users_exists;

    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'symbol_watchlist'
    ) INTO v_watchlist_exists;

    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'robots_txt_cache'
    ) INTO v_robots_exists;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Rollback Verification:';
    RAISE NOTICE '  users table exists: % (expected: false)', v_users_exists;
    RAISE NOTICE '  symbol_watchlist table exists: % (expected: false)', v_watchlist_exists;
    RAISE NOTICE '  robots_txt_cache table exists: % (expected: false)', v_robots_exists;
    RAISE NOTICE '========================================';

    IF v_users_exists OR v_watchlist_exists OR v_robots_exists THEN
        RAISE WARNING 'Some tables still exist after rollback!';
    ELSE
        RAISE NOTICE 'Rollback completed successfully - all tables removed';
    END IF;
END $$;

RAISE NOTICE 'Migration 002 rollback complete.';
RAISE NOTICE 'To re-apply, run: 002_add_p0_watchlist_legal_guard.sql';
