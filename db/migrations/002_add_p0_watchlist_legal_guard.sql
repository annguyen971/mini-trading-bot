/*
 * Migration 002: P0 Critical Features - Dynamic Watchlist + Legal Guard
 * ======================================================================
 * Date: 2025-01-18
 * Description: Implements P0 gaps identified in scraper gap analysis
 *
 * Features:
 * 1. User management (single admin for MVP)
 * 2. Dynamic watchlist (NFR10: ≤ 500 symbols)
 * 3. Legal compliance (robots.txt caching)
 *
 * PRD Story: Dynamic Watchlist Management (GAP #7)
 * PRD Story: Legal Guard (GAP #1) - Story 1.1 AC7
 *
 * Rollback: Run 002_rollback_p0_watchlist_legal_guard.sql
 */

-- ==============================================================================
-- STEP 1: Create Users Table
-- ==============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'users') THEN
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            role TEXT DEFAULT 'admin',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        RAISE NOTICE 'Created table: users';
    ELSE
        RAISE NOTICE 'Table already exists: users (skipping)';
    END IF;
END $$;

-- ==============================================================================
-- STEP 2: Create Symbol Watchlist Table
-- ==============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'symbol_watchlist') THEN
        CREATE TABLE symbol_watchlist (
            id BIGSERIAL PRIMARY KEY,
            user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            sector TEXT,
            market_cap_tier TEXT,
            is_active BOOLEAN DEFAULT true,
            added_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(user_id, symbol)
        );

        CREATE INDEX idx_symbol_watchlist_user_active ON symbol_watchlist(user_id, is_active) WHERE is_active = true;
        CREATE INDEX idx_symbol_watchlist_symbol ON symbol_watchlist(symbol);

        RAISE NOTICE 'Created table: symbol_watchlist with indexes';
    ELSE
        RAISE NOTICE 'Table already exists: symbol_watchlist (skipping)';
    END IF;
END $$;

-- ==============================================================================
-- STEP 3: Create robots.txt Cache Table (Legal Guard)
-- ==============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'robots_txt_cache') THEN
        CREATE TABLE robots_txt_cache (
            domain TEXT PRIMARY KEY,
            robots_content TEXT,
            crawl_delay_seconds INT DEFAULT 1,
            is_fetch_allowed BOOLEAN DEFAULT true,
            user_agent TEXT DEFAULT '*',
            last_fetched TIMESTAMPTZ DEFAULT NOW(),
            expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '7 days')
        );

        CREATE INDEX idx_robots_cache_expires ON robots_txt_cache(expires_at);

        RAISE NOTICE 'Created table: robots_txt_cache with index';
    ELSE
        RAISE NOTICE 'Table already exists: robots_txt_cache (skipping)';
    END IF;
END $$;

-- ==============================================================================
-- STEP 4: Seed Admin User
-- ==============================================================================

INSERT INTO users (user_id, username, role)
VALUES ('admin', 'admin', 'admin')
ON CONFLICT (user_id) DO NOTHING;

-- ==============================================================================
-- STEP 5: Seed Initial Watchlist (Vietnamese Blue-Chips)
-- ==============================================================================

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

-- ==============================================================================
-- STEP 6: Seed Known Sources (vnstock API wrapper - no robots.txt needed)
-- ==============================================================================

INSERT INTO robots_txt_cache (domain, crawl_delay_seconds, is_fetch_allowed, user_agent, last_fetched)
VALUES ('vnstock-api', 1, true, 'StockHunterBot/1.0', NOW())
ON CONFLICT (domain) DO NOTHING;

-- ==============================================================================
-- VERIFICATION QUERIES
-- ==============================================================================

DO $$
DECLARE
    v_users_count INT;
    v_watchlist_count INT;
    v_robots_count INT;
BEGIN
    SELECT COUNT(*) INTO v_users_count FROM users;
    SELECT COUNT(*) INTO v_watchlist_count FROM symbol_watchlist WHERE is_active = true;
    SELECT COUNT(*) INTO v_robots_count FROM robots_txt_cache;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration 002 Verification:';
    RAISE NOTICE '  Users: % (expected: ≥1)', v_users_count;
    RAISE NOTICE '  Active Symbols: % (expected: ≥10)', v_watchlist_count;
    RAISE NOTICE '  Robots Cache: % (expected: ≥1)', v_robots_count;
    RAISE NOTICE '========================================';

    IF v_users_count < 1 THEN
        RAISE WARNING 'No users found - admin user may not have been created';
    END IF;

    IF v_watchlist_count < 3 THEN
        RAISE WARNING 'Fewer than 3 symbols in watchlist - seed data may be incomplete';
    END IF;
END $$;

-- ==============================================================================
-- MIGRATION COMPLETE
-- ==============================================================================

-- Add migration tracking (optional - for future migration management)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'schema_migrations') THEN
        CREATE TABLE schema_migrations (
            migration_id TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT NOW(),
            description TEXT
        );
    END IF;

    INSERT INTO schema_migrations (migration_id, description)
    VALUES ('002_add_p0_watchlist_legal_guard', 'P0 Critical Features: Dynamic Watchlist + Legal Guard')
    ON CONFLICT (migration_id) DO UPDATE SET applied_at = NOW();
END $$;

RAISE NOTICE 'Migration 002 completed successfully!';
RAISE NOTICE 'Run scraper to verify dynamic watchlist loading.';
