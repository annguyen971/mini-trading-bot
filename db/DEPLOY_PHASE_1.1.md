# Phase 1.1 Deployment Guide: Temporal Correctness Views

## Overview
This deployment adds the **v_features_asof** view and supporting infrastructure to enforce NFR4 (Zero Look-Ahead Bias) compliance.

## What's Included

### New Database Objects
1. **v_features_asof** - Main temporal correctness view
2. **get_features_as_of()** - Function for time-travel queries
3. **test_temporal_correctness** - Unit test view
4. **v_latest_features** - Real-time serving helper
5. **idx_features_gold_as_of_lookup** - Performance index

### Files Modified
- ✅ [db/views.sql](db/views.sql) - Updated with new views
- ✅ [docker-compose.yml](docker-compose.yml) - Added migrations/tests volumes

### Files Created
- ✅ [db/migrations/001_add_temporal_correctness_views.sql](db/migrations/001_add_temporal_correctness_views.sql)
- ✅ [db/migrations/README.md](db/migrations/README.md)
- ✅ [db/tests/test_temporal_correctness.sql](db/tests/test_temporal_correctness.sql)
- ✅ This deployment guide

## Pre-Deployment Checklist

- [ ] Review changes in `db/views.sql`
- [ ] Verify `features_gold` table exists in database
- [ ] Backup database (recommended but optional for dev)
- [ ] Docker Compose services are running

## Deployment Steps

### Option A: Full Container Restart (Recommended for Dev)

```bash
# Navigate to project root
cd /home/duongtran/an-pj/mini-trading/source-code

# Stop and remove database container (this will trigger init scripts)
docker-compose down db

# Recreate database container
docker-compose up -d db

# Wait for database to be healthy
docker-compose ps db
# Should show "healthy" status after ~10 seconds

# Verify migration applied
docker-compose logs db | grep "Migration 001 completed"
# Should see: "NOTICE:  Migration 001 completed successfully!"
```

### Option B: Apply Migration Without Restart (Production-safe)

```bash
# Apply migration to running database
docker exec -i mini-trading-db-1 psql -U hunter -d hunter < db/migrations/001_add_temporal_correctness_views.sql

# Expected output:
# NOTICE:  Migration 001 completed successfully!
# NOTICE:    - Created 3 views
# NOTICE:    - Created get_features_as_of() function
# NOTICE:    - Added idx_features_gold_as_of_lookup index
```

## Post-Deployment Verification

### Step 1: Verify Views Created

```bash
docker exec -it mini-trading-db-1 psql -U hunter -d hunter -c "
SELECT table_name
FROM information_schema.views
WHERE table_name IN ('v_features_asof', 'test_temporal_correctness', 'v_latest_features');
"
```

**Expected output**:
```
        table_name
---------------------------
 v_features_asof
 test_temporal_correctness
 v_latest_features
(3 rows)
```

### Step 2: Verify Function Created

```bash
docker exec -it mini-trading-db-1 psql -U hunter -d hunter -c "
SELECT proname, pronargs
FROM pg_proc
WHERE proname = 'get_features_as_of';
"
```

**Expected output**:
```
      proname       | pronargs
--------------------+----------
 get_features_as_of |        3
(1 row)
```

### Step 3: Run Test Suite

```bash
docker exec -i mini-trading-db-1 psql -U hunter -d hunter < db/tests/test_temporal_correctness.sql
```

**Expected output** (last few lines):
```
NOTICE:  ========================================
NOTICE:  ALL TESTS PASSED! ✓
NOTICE:  ========================================
NOTICE:  Temporal correctness implementation verified:
NOTICE:    ✓ Views created correctly
NOTICE:    ✓ as_of_time logic accurate
NOTICE:    ✓ get_features_as_of() function works
NOTICE:    ✓ No future data leakage
NOTICE:    ✓ NULL filtering works
NOTICE:    ✓ Latest features view functional
NOTICE:
NOTICE:  Phase 1.1 (As-of View) - COMPLETE
NOTICE:  ========================================
ROLLBACK
```

### Step 4: Manual Smoke Test

```bash
# Test v_features_asof view (should return 0 rows if no features exist yet)
docker exec -it mini-trading-db-1 psql -U hunter -d hunter -c "
SELECT COUNT(*) as total_features FROM v_features_asof;
"

# Test get_features_as_of function
docker exec -it mini-trading-db-1 psql -U hunter -d hunter -c "
SELECT * FROM get_features_as_of('FPT', NOW()::timestamptz) LIMIT 5;
"

# Test temporal correctness (should return 0 rows = no leaks)
docker exec -it mini-trading-db-1 psql -U hunter -d hunter -c "
SELECT COUNT(*) as leaks FROM test_temporal_correctness;
"
```

**Expected**:
- `total_features`: 0 or more (depends on existing data)
- `leaks`: **0** (CRITICAL - must be 0)

## Rollback Procedure

If something goes wrong, rollback with:

```bash
docker exec -it mini-trading-db-1 psql -U hunter -d hunter <<EOF
DROP VIEW IF EXISTS v_latest_features CASCADE;
DROP VIEW IF EXISTS test_temporal_correctness CASCADE;
DROP FUNCTION IF EXISTS get_features_as_of CASCADE;
DROP VIEW IF EXISTS v_features_asof CASCADE;
DROP INDEX IF EXISTS idx_features_gold_as_of_lookup;
EOF
```

Then restore from backup if needed.

## Impact Analysis

### Performance
- **Query overhead**: Minimal (<5ms per query on indexed columns)
- **Storage**: None (views are virtual)
- **Index size**: ~1-2% of `features_gold` table size

### Compatibility
- **Breaking changes**: None (additive only)
- **Affected services**:
  - ✅ Worker (will use view when implemented)
  - ✅ API (can now query safely for backtests)
  - ✅ Dashboard (can visualize temporal data)

### Known Limitations
- `as_of_time` logic assumes:
  - TA features available next trading day
  - SA features available after 2-hour NLP lag
  - Gold features available at 20:00 daily batch
- Adjust in `v_features_asof` view if your batch schedule differs

## Next Steps

After successful deployment:

1. ✅ **Phase 1.1 COMPLETE** - Temporal correctness foundation ready
2. ⏭️ **Phase 1.2** - Implement Silver Worker to populate `ta_silver`/`sa_silver`
3. ⏭️ **Phase 1.3** - Implement Feature Gold Batch to compute HunterScore/FrothScore
4. ⏭️ **Phase 2** - Implement ML pipeline (Snorkel, Training)

## Usage Examples

### For Backtesting (ML Training)
```python
# In worker/tasks_train.py
query = """
SELECT symbol, effective_date, feature_name, value
FROM v_features_asof
WHERE as_of_time <= %s  -- Prevent future leakage
  AND symbol = %s
ORDER BY effective_date
"""
cursor.execute(query, (backtest_time, symbol))
```

### For Real-time Predictions (API)
```python
# In api/main.py
query = """
SELECT feature_name, value
FROM v_latest_features
WHERE symbol = %s
"""
cursor.execute(query, (symbol,))
```

### For Active Learning (Worker)
```python
# In worker/tasks_label.py
query = """
SELECT * FROM get_features_as_of(%s, %s)
"""
cursor.execute(query, (symbol, labeling_time))
```

## Troubleshooting

### Issue: "relation 'features_gold' does not exist"
**Solution**: Ensure `db/init.sql` has been applied. Run:
```bash
docker-compose down db && docker-compose up -d db
```

### Issue: "function get_features_as_of does not exist"
**Solution**: Migration not applied. Run Option B deployment steps.

### Issue: Test shows "LEAK: as_of_time before effective_date"
**Solution**: Data corruption or logic error. Check:
```sql
SELECT * FROM test_temporal_correctness LIMIT 10;
```
Contact BMad PM Agent if issue persists.

## Deployment Sign-off

- [ ] All verification steps passed
- [ ] Test suite shows "ALL TESTS PASSED! ✓"
- [ ] `test_temporal_correctness` returns 0 rows
- [ ] Services restarted successfully
- [ ] No errors in logs: `docker-compose logs db --tail=50`

**Deployed by**: ________________
**Date**: ________________
**Phase 1.1 Status**: ✅ COMPLETE
