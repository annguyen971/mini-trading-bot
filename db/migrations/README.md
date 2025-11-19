# Database Migrations Guide

## Overview
This directory contains SQL migration scripts for the Stock Hunter AI database schema.

## Migration Naming Convention
```
<number>_<descriptive_name>.sql

Example: 001_add_temporal_correctness_views.sql
```

## How to Apply Migrations

### Method 1: Manual Application (Development)
```bash
# Connect to PostgreSQL container
docker exec -it mini-trading-db-1 psql -U postgres -d stockhunter

# Apply migration
\i /docker-entrypoint-initdb.d/migrations/001_add_temporal_correctness_views.sql
```

### Method 2: Auto-apply on Container Restart (Recommended)
Migrations are automatically applied when the database container starts if they are in `/docker-entrypoint-initdb.d/`.

```bash
# Restart database to apply all migrations
docker-compose restart db
```

### Method 3: From Host Machine
```bash
# Apply specific migration
docker exec -i mini-trading-db-1 psql -U postgres -d stockhunter < db/migrations/001_add_temporal_correctness_views.sql
```

## Migration Status: Phase 1.1 - Temporal Correctness

### ✅ 001_add_temporal_correctness_views.sql
**Status**: Ready for deployment
**Purpose**: Implements NFR4 - Zero Look-Ahead Bias
**PRD Reference**: Architecture V3.1, NFR4

**What it adds**:
- `v_features_asof` view - Temporal correctness enforcement
- `get_features_as_of()` function - Time-travel queries for backtesting
- `test_temporal_correctness` view - Unit test for leak detection
- `v_latest_features` view - Real-time serving helper
- `idx_features_gold_as_of_lookup` index - Query optimization

**Breaking Changes**: None (additive only)

**Rollback**:
```sql
DROP VIEW IF EXISTS v_latest_features;
DROP VIEW IF EXISTS test_temporal_correctness;
DROP FUNCTION IF EXISTS get_features_as_of;
DROP VIEW IF EXISTS v_features_asof;
DROP INDEX IF EXISTS idx_features_gold_as_of_lookup;
```

## Testing Migrations

Run the test suite to verify migration success:

```bash
# Run all tests
docker exec -i mini-trading-db-1 psql -U postgres -d stockhunter < db/tests/test_temporal_correctness.sql
```

Expected output:
```
NOTICE:  ========================================
NOTICE:  ALL TESTS PASSED! ✓
NOTICE:  Phase 1.1 (As-of View) - COMPLETE
```

## Migration Checklist

Before deploying a migration:
- [ ] Migration is idempotent (safe to run multiple times)
- [ ] Migration has rollback procedure documented
- [ ] Migration includes verification block
- [ ] Test script exists in `db/tests/`
- [ ] Migration tested locally
- [ ] No breaking changes OR deprecation plan exists
- [ ] PRD reference documented

## Upcoming Migrations

### Phase 1.2: Silver Worker (Planned)
- Add materialized views for Silver sanity checks
- Add performance indexes on ta_silver/sa_silver

### Phase 2: Feature Gold (Planned)
- Add HMM state transition tables
- Add feature versioning triggers

### Phase 3: ML Pipeline (Planned)
- Add model serving tables
- Add backtest results storage

## Best Practices

1. **Always use transactions**: Wrap migrations in BEGIN/COMMIT
2. **Add verification**: Use `DO $$ ... END $$` blocks to verify success
3. **Document dependencies**: List required tables/views in comments
4. **Use IF NOT EXISTS**: Make migrations idempotent
5. **Test rollback**: Ensure rollback procedure works

## Troubleshooting

### Migration fails with "relation already exists"
- This is OK if migration is idempotent (uses `IF NOT EXISTS`)
- Re-run migration to ensure all steps complete

### Migration fails with "column does not exist"
- Check init.sql schema matches migration expectations
- Ensure previous migrations applied successfully

### Test fails
- Check sample data exists in tables
- Verify all views/functions created
- Review test error messages for specific failures

## Contact
For migration issues, refer to PRD V2.7.2 or contact the BMad PM Agent.
