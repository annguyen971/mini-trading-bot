# Stock Hunter AI - Complete Deployment Guide

**Version:** 1.0
**Last Updated:** 2025-11-18
**System Status:** Production-Ready (100% Core Features)

---

## Table of Contents

1. [Pre-Deployment Checklist](#1-pre-deployment-checklist)
2. [Infrastructure Setup](#2-infrastructure-setup)
3. [Database Initialization](#3-database-initialization)
4. [Application Deployment](#4-application-deployment)
5. [Scheduled Jobs Configuration](#5-scheduled-jobs-configuration)
6. [Monitoring & Observability](#6-monitoring--observability)
7. [Production Validation](#7-production-validation)
8. [Troubleshooting](#8-troubleshooting)
9. [Rollback Procedures](#9-rollback-procedures)

---

## 1. Pre-Deployment Checklist

### 1.1 Environment Requirements

```bash
# System Requirements
- OS: Linux (Ubuntu 20.04+ recommended) or macOS
- CPU: 4+ cores recommended
- RAM: 8GB minimum, 16GB recommended
- Disk: 50GB+ available space
- Network: Stable internet connection for scrapers

# Software Requirements
- Docker: 20.10+
- Docker Compose: 2.0+
- Python: 3.11+ (for local development/testing)
- PostgreSQL Client: 14+ (optional, for DB admin)
```

### 1.2 API Keys & Credentials

Create a `.env` file in the project root:

```bash
# Database Configuration
DB_URL=postgresql://hunter:your_secure_password@db:5432/hunter

# Google Gemini API (for NLP)
GEMINI_API_KEY=your_gemini_api_key_here

# Admin Credentials
ADMIN_KEY=your_secure_admin_key_here

# Optional: Proxy Configuration (for rate limiting)
# PROXY_URL=http://your-proxy:port

# Optional: Alerting (future enhancement)
# SLACK_WEBHOOK_URL=https://hooks.slack.com/services/your/webhook
```

**Security Notes:**
- Never commit `.env` to version control
- Use strong passwords (16+ chars, mixed case, symbols)
- Rotate `ADMIN_KEY` quarterly in production

### 1.3 Pre-Deployment Validation

```bash
# 1. Verify Docker installation
docker --version
docker-compose --version

# 2. Verify port availability
netstat -tuln | grep -E '5432|8000|8501'
# Ports 5432 (PostgreSQL), 8000 (API), 8501 (Dashboard) must be free

# 3. Check disk space
df -h
# Ensure at least 50GB free on deployment volume

# 4. Verify .env file exists
ls -la .env
cat .env  # Review (but don't share publicly!)
```

---

## 2. Infrastructure Setup

### 2.1 Docker Network & Volumes

```bash
cd /home/duongtran/an-pj/mini-trading/source-code

# Create necessary directories
mkdir -p artifacts
mkdir -p logs

# Set permissions
chmod 755 artifacts logs

# Review docker-compose.yml
cat docker-compose.yml
```

### 2.2 Build & Start Services

```bash
# Build all images (first-time setup)
docker-compose build --no-cache

# Start all services
docker-compose up -d

# Verify all containers are running
docker-compose ps

# Expected output:
# NAME                COMMAND                  SERVICE    STATUS
# source-code-db-1    "docker-entrypoint.s…"   db         Up
# source-code-api-1   "uvicorn main:app --…"   api        Up
# source-code-dash-1  "streamlit run app.p…"   dash       Up
# source-code-worker-1 "python -m worker.ma…"   worker     Up
```

### 2.3 Health Checks

```bash
# Check database connectivity
docker-compose exec db psql -U hunter -d hunter -c "SELECT version();"

# Check API health
curl http://localhost:8000/health
# Expected: {"status":"healthy","database":"connected"}

# Check Dashboard accessibility
curl -I http://localhost:8501
# Expected: HTTP/1.1 200 OK

# Check worker logs
docker-compose logs worker | tail -20
# Should see "Silver worker started successfully"
```

---

## 3. Database Initialization

### 3.1 Verify Schema

```bash
# Connect to database
docker-compose exec db psql -U hunter -d hunter

# Verify all tables exist (run inside psql)
\dt

# Expected tables (15 total):
# - ta_bronze, sa_bronze
# - ta_silver, sa_silver
# - features_gold, labels_silver
# - task_q, task_q_dlq, al_queue
# - control_flags, worker_locks
# - model_registry, monitoring_logs
# - users, symbol_watchlist
```

### 3.2 Run Temporal Correctness Migration

```bash
# Apply Phase 1.1 migration (temporal correctness)
docker-compose exec db psql -U hunter -d hunter -f /docker-entrypoint-initdb.d/migrations/001_add_temporal_correctness_views.sql

# Verify views created
docker-compose exec db psql -U hunter -d hunter -c "\dv"

# Expected views:
# - v_features_asof
# - v_labels_asof
# - v_training_dataset_asof
```

### 3.3 Run Temporal Correctness Tests

```bash
# Run all tests
docker-compose exec db psql -U hunter -d hunter -f /docker-entrypoint-initdb.d/tests/test_temporal_correctness.sql

# Check test results
docker-compose exec db psql -U hunter -d hunter -c "SELECT * FROM test_results_temporal_correctness ORDER BY test_name;"

# Expected: All tests should show status = 'PASS'
# If any test shows 'FAIL', review test_temporal_correctness.sql and fix data issues
```

### 3.4 Initialize Control Flags

```bash
# Set up control flags
docker-compose exec db psql -U hunter -d hunter << 'EOF'
-- Initialize scraper backpressure flag (disabled by default)
INSERT INTO control_flags (flag, enabled, reason, updated_at)
VALUES ('SCRAPE_SLOW', false, 'Initial setup - backpressure disabled', NOW())
ON CONFLICT (flag) DO NOTHING;

-- Verify
SELECT * FROM control_flags;
EOF
```

---

## 4. Application Deployment

### 4.1 Start Core Services

```bash
# If not already running from step 2.2
docker-compose up -d db api dash

# Wait for services to stabilize (30 seconds)
sleep 30

# Verify API endpoints
curl http://localhost:8000/admin/health/ping -H "X-ADMIN-KEY: your_secure_admin_key_here"
# Expected: {"status":"pong"}

curl http://localhost:8000/admin/health/queue_stats -H "X-ADMIN-KEY: your_secure_admin_key_here"
# Expected: JSON with queue statistics
```

### 4.2 Start Worker Processes

```bash
# Start the Silver worker (Bronze → Silver transformation)
docker-compose up -d worker

# Monitor worker logs
docker-compose logs -f worker

# You should see:
# "Silver worker started successfully"
# "Checking for tasks..."
# "Processing TA payload for symbol: ..."

# Press Ctrl+C to stop following logs
```

### 4.3 Dashboard Access

```bash
# Access dashboard via browser
echo "Dashboard URL: http://localhost:8501"

# Or via SSH tunnel (if deployed on remote server)
# ssh -L 8501:localhost:8501 user@your-server
# Then access: http://localhost:8501 on your local machine
```

**Dashboard Pages:**
1. **Home** - System overview
2. **Symbol Explorer** - Individual stock analysis
3. **Feature Viewer** - Feature inspection
4. **System Health** - Macro/sector stats
5. **Active Learning** - Label review queue
6. **Observability** - Comprehensive monitoring

---

## 5. Scheduled Jobs Configuration

### 5.1 Crontab Setup

Create a crontab file for automated tasks:

```bash
# Create crontab configuration
cat > /home/duongtran/an-pj/mini-trading/source-code/crontab_hunter.txt << 'EOF'
# Stock Hunter AI - Scheduled Jobs
# Format: minute hour day month weekday command

# === CRITICAL JOBS ===

# 1. Silver Worker (runs continuously via Docker, but backup cron for restarts)
*/5 * * * * cd /home/duongtran/an-pj/mini-trading/source-code && docker-compose exec -T worker python -m worker.main >> logs/worker.log 2>&1

# 2. Feature Gold Pipeline (daily at 8 PM after market close + data delay)
0 20 * * * cd /home/duongtran/an-pj/mini-trading/source-code && docker-compose exec -T worker python -m worker.tasks_feature_gold >> logs/feature_gold.log 2>&1

# === WEEKLY JOBS ===

# 3. Weak Supervision Labeling (Mondays at 9 AM)
0 9 * * 1 cd /home/duongtran/an-pj/mini-trading/source-code && docker-compose exec -T worker python -m worker.tasks_label >> logs/labeling.log 2>&1

# 4. Model Training Check (Mondays at 10 AM, after labeling)
0 10 * * 1 cd /home/duongtran/an-pj/mini-trading/source-code && docker-compose exec -T worker python -m worker.tasks_train >> logs/training.log 2>&1

# === HOURLY MONITORING ===

# 5. Metrics Collection (every hour)
0 * * * * cd /home/duongtran/an-pj/mini-trading/source-code && docker-compose exec -T worker python -m worker.monitor_metrics >> logs/metrics.log 2>&1

# === MAINTENANCE ===

# 6. Database Vacuum (Sundays at 2 AM)
0 2 * * 0 docker-compose exec -T db psql -U hunter -d hunter -c "VACUUM ANALYZE;" >> logs/vacuum.log 2>&1

# 7. Log Rotation (daily at 3 AM)
0 3 * * * find /home/duongtran/an-pj/mini-trading/source-code/logs -name "*.log" -mtime +30 -delete

# 8. Docker System Prune (weekly on Sundays at 4 AM)
0 4 * * 0 docker system prune -f >> logs/docker_prune.log 2>&1
EOF

# Install crontab
crontab /home/duongtran/an-pj/mini-trading/source-code/crontab_hunter.txt

# Verify installation
crontab -l
```

### 5.2 Manual Job Execution (for testing)

```bash
# Test Feature Gold pipeline
docker-compose exec worker python -m worker.tasks_feature_gold

# Test Weak Supervision labeling
docker-compose exec worker python -m worker.tasks_label

# Test Model Training
docker-compose exec worker python -m worker.tasks_train

# Test Metrics Collection
docker-compose exec worker python -m worker.monitor_metrics
```

---

## 6. Monitoring & Observability

### 6.1 Access Observability Dashboard

```bash
# Open browser to: http://localhost:8501/Observability

# Or use direct link
echo "Observability Dashboard: http://localhost:8501/06_Observability"
```

### 6.2 Key Metrics to Monitor

**Data Quality (Target: Green)**
- DLQ Rate: < 5% (warning at 10%)
- Feature Completeness: > 90%
- Label Coverage: > 60%

**Model Performance (Target: Healthy)**
- IC (Information Coefficient): > 0.05
- Sharpe Ratio: > 1.0
- Max Drawdown: < 20%
- Model Age: < 60 days

**System Health (Target: Stable)**
- Task Queue: < 10,000 (backpressure threshold)
- Backpressure: DISABLED (green)
- Avg Latency: < 60 seconds

**Source Health (Target: Fresh)**
- Price Data Staleness: < 48 hours (green)
- News Data Staleness: < 24 hours (green)
- Unique Sources (7d): > 5

**HMM Distribution (Target: Healthy Market)**
- Accumulation: 40-50% ✅
- Breakout: 20-30% ✅
- Euphoria: < 30% ⚠️ (risky if higher)
- Distribution: < 20% ⚠️ (risky if higher)

### 6.3 Log Monitoring

```bash
# Follow worker logs in real-time
docker-compose logs -f worker

# Check for errors in last 100 lines
docker-compose logs worker | tail -100 | grep -i error

# Monitor API logs
docker-compose logs -f api

# Monitor database logs
docker-compose logs -f db | grep -i error
```

### 6.4 Database Monitoring Queries

```bash
# Connect to database
docker-compose exec db psql -U hunter -d hunter

# Check queue backlog
SELECT COUNT(*) as ready_tasks FROM task_q WHERE next_run_at <= NOW();

# Check DLQ failures (last 24h)
SELECT reason, COUNT(*) as count
FROM task_q_dlq
WHERE moved_at > NOW() - interval '24 hours'
GROUP BY reason
ORDER BY count DESC;

# Check recent metrics
SELECT metric_name, value, log_time
FROM monitoring_logs
WHERE log_time > NOW() - interval '1 hour'
ORDER BY log_time DESC
LIMIT 20;

# Check active model
SELECT model_version, metrics, created_at, is_active
FROM model_registry
WHERE is_active = true;
```

---

## 7. Production Validation

### 7.1 End-to-End Smoke Test

```bash
#!/bin/bash
# Save as: test_production.sh

echo "=== Stock Hunter AI - Production Smoke Test ==="

# 1. Database connectivity
echo "1. Testing database..."
docker-compose exec -T db psql -U hunter -d hunter -c "SELECT 1;" > /dev/null 2>&1
if [ $? -eq 0 ]; then
    echo "   ✅ Database: PASS"
else
    echo "   ❌ Database: FAIL"
    exit 1
fi

# 2. API health
echo "2. Testing API..."
API_HEALTH=$(curl -s http://localhost:8000/health | grep "healthy")
if [ -n "$API_HEALTH" ]; then
    echo "   ✅ API: PASS"
else
    echo "   ❌ API: FAIL"
    exit 1
fi

# 3. Dashboard accessibility
echo "3. Testing Dashboard..."
DASH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8501)
if [ "$DASH_STATUS" = "200" ]; then
    echo "   ✅ Dashboard: PASS"
else
    echo "   ❌ Dashboard: FAIL (HTTP $DASH_STATUS)"
    exit 1
fi

# 4. Temporal correctness views
echo "4. Testing temporal correctness..."
VIEW_COUNT=$(docker-compose exec -T db psql -U hunter -d hunter -c "\dv" | grep -c "v_features_asof\|v_labels_asof\|v_training_dataset_asof")
if [ "$VIEW_COUNT" -ge 3 ]; then
    echo "   ✅ Temporal Views: PASS"
else
    echo "   ❌ Temporal Views: FAIL (found $VIEW_COUNT/3)"
    exit 1
fi

# 5. Worker process
echo "5. Testing worker..."
WORKER_STATUS=$(docker-compose ps worker | grep -c "Up")
if [ "$WORKER_STATUS" -ge 1 ]; then
    echo "   ✅ Worker: PASS"
else
    echo "   ❌ Worker: FAIL"
    exit 1
fi

# 6. Metrics collection
echo "6. Testing metrics..."
METRICS_COUNT=$(docker-compose exec -T db psql -U hunter -d hunter -c "SELECT COUNT(*) FROM monitoring_logs;" | grep -o '[0-9]*' | head -1)
if [ "$METRICS_COUNT" -gt 0 ]; then
    echo "   ✅ Metrics: PASS ($METRICS_COUNT records)"
else
    echo "   ⚠️  Metrics: WARNING (no data yet - run monitor_metrics.py)"
fi

echo ""
echo "=== Production Smoke Test: COMPLETE ==="
echo "All critical systems operational ✅"
```

Run the test:

```bash
chmod +x test_production.sh
./test_production.sh
```

### 7.2 Feature Pipeline Validation

```bash
# Run a test feature computation
docker-compose exec db psql -U hunter -d hunter << 'EOF'
-- Insert test TA data
INSERT INTO ta_silver (symbol, trade_date, open, high, low, close, volume, validated_at, as_of_time)
VALUES (
    'TEST_AAPL',
    CURRENT_DATE - 1,
    150.00, 152.00, 149.50, 151.50, 50000000,
    NOW(),
    NOW()
);

-- Manually trigger feature computation (normally done by worker)
-- This will be done via Python script in production
EOF

# Run feature gold task for TEST_AAPL
docker-compose exec worker python -c "
from worker.tasks_feature_gold import compute_features_for_symbol
from core_lib.db import get_db_connection
import sys

conn = get_db_connection()
try:
    compute_features_for_symbol(conn, 'TEST_AAPL', str((datetime.now().date() - timedelta(days=1))))
    print('✅ Feature computation: PASS')
except Exception as e:
    print(f'❌ Feature computation: FAIL - {e}')
    sys.exit(1)
finally:
    conn.close()
"

# Verify features were created
docker-compose exec db psql -U hunter -d hunter -c "
SELECT feature_name, value
FROM features_gold
WHERE symbol = 'TEST_AAPL'
ORDER BY feature_name;
"
```

---

## 8. Troubleshooting

### 8.1 Common Issues & Solutions

#### Issue 1: Worker not processing tasks

**Symptoms:**
- `task_q` table has rows with `next_run_at <= NOW()` but worker logs show no activity
- No entries in `ta_silver` or `sa_silver` despite data in `ta_bronze`/`sa_bronze`

**Diagnosis:**
```bash
# Check worker logs
docker-compose logs worker | grep -i error

# Check worker lock
docker-compose exec db psql -U hunter -d hunter -c "SELECT * FROM worker_locks;"
```

**Solutions:**
```bash
# Solution 1: Release stuck advisory lock
docker-compose exec db psql -U hunter -d hunter -c "SELECT pg_advisory_unlock_all();"

# Solution 2: Restart worker
docker-compose restart worker

# Solution 3: Clear worker_locks table (if corrupted)
docker-compose exec db psql -U hunter -d hunter -c "TRUNCATE worker_locks;"
```

#### Issue 2: High DLQ rate (> 10%)

**Symptoms:**
- Observability dashboard shows DLQ rate > 10%
- Many rows in `task_q_dlq` table

**Diagnosis:**
```bash
# Check top failure reasons
docker-compose exec db psql -U hunter -d hunter -c "
SELECT reason, COUNT(*) as count
FROM task_q_dlq
WHERE moved_at > NOW() - interval '24 hours'
GROUP BY reason
ORDER BY count DESC
LIMIT 10;
"
```

**Solutions:**
```bash
# If failures are due to ta_rule_3_price_negative:
# - Check scraper data source quality
# - Review ta_bronze data for symbol with failures

# If failures are due to sa_rule_5_duplicate_content:
# - Normal for news sources (same article republished)
# - No action needed unless rate > 20%

# If failures are due to ta_rule_7_gap_excessive:
# - Review gap threshold in apply_ta_sanity_rules()
# - Consider adjusting from 20% to 30% if false positives
```

#### Issue 3: Backpressure enabled (queue overflow)

**Symptoms:**
- Observability dashboard shows "Backpressure: 🔴 ENABLED"
- `task_q` has > 10,000 ready tasks

**Diagnosis:**
```bash
# Check queue size
docker-compose exec db psql -U hunter -d hunter -c "
SELECT COUNT(*) as ready_tasks FROM task_q WHERE next_run_at <= NOW();
"
```

**Solutions:**
```bash
# Solution 1: Scale up workers (if infrastructure allows)
# - Add more worker containers in docker-compose.yml
# - Update docker-compose.yml:
#   worker:
#     deploy:
#       replicas: 3  # Scale to 3 workers

# Solution 2: Adjust backpressure threshold (if queue is healthy)
# Edit worker/main.py: BACKPRESSURE_THRESHOLD = 20000

# Solution 3: Purge old tasks (if data is stale)
docker-compose exec db psql -U hunter -d hunter -c "
DELETE FROM task_q
WHERE next_run_at < NOW() - interval '7 days';
"
```

#### Issue 4: Model IC degradation

**Symptoms:**
- Observability dashboard shows IC < 0.02
- Model predictions not correlating with outcomes

**Diagnosis:**
```bash
# Check model metrics
docker-compose exec db psql -U hunter -d hunter -c "
SELECT model_version, metrics, created_at
FROM model_registry
WHERE is_active = true;
"

# Check PSI drift
# (Run via Python script - see tasks_train.py compute_psi())
```

**Solutions:**
```bash
# Solution 1: Trigger manual retraining
docker-compose exec worker python -m worker.tasks_train

# Solution 2: Review labeling quality
# - Check AL queue for mislabeled samples
# - Review weak supervision LF weights

# Solution 3: Increase training data
# - Add more golden labels via Active Learning dashboard
# - Adjust coverage threshold in tasks_label.py
```

#### Issue 5: Dashboard shows "No metrics available"

**Symptoms:**
- Observability dashboard shows warning: "No metrics available. Run the metrics collector"
- `monitoring_logs` table is empty

**Solutions:**
```bash
# Solution 1: Run metrics collector manually
docker-compose exec worker python -m worker.monitor_metrics

# Solution 2: Verify cron job is running
crontab -l | grep monitor_metrics

# Solution 3: Check metrics collector logs
docker-compose logs worker | grep -i metrics
```

### 8.2 Performance Tuning

#### Database Optimization

```bash
# Analyze query performance
docker-compose exec db psql -U hunter -d hunter << 'EOF'
-- Enable query logging
ALTER SYSTEM SET log_min_duration_statement = 1000;  -- Log queries > 1s
SELECT pg_reload_conf();

-- Check slow queries (after running for a while)
SELECT calls, mean_exec_time, query
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 10;
EOF

# Add missing indexes (if queries are slow)
docker-compose exec db psql -U hunter -d hunter << 'EOF'
-- Index on task_q for worker queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_task_q_next_run
ON task_q (next_run_at) WHERE next_run_at <= NOW();

-- Index on monitoring_logs for dashboard queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_monitoring_logs_time_metric
ON monitoring_logs (log_time DESC, metric_name);
EOF
```

#### Worker Performance

```bash
# Increase worker batch size (in worker/main.py)
# Change BATCH_SIZE from 100 to 500 if CPU allows

# Reduce sleep interval (in worker/main.py)
# Change time.sleep(10) to time.sleep(5) for faster task processing
```

---

## 9. Rollback Procedures

### 9.1 Application Rollback

If a deployment causes issues, rollback using Docker:

```bash
# 1. Stop current containers
docker-compose down

# 2. Checkout previous version (if using git)
git log --oneline | head -10  # Find previous commit
git checkout <previous_commit_hash>

# 3. Rebuild and restart
docker-compose build --no-cache
docker-compose up -d

# 4. Verify health
./test_production.sh
```

### 9.2 Database Rollback

If a migration causes issues:

```bash
# 1. Backup current database first
docker-compose exec db pg_dump -U hunter hunter > backup_$(date +%Y%m%d_%H%M%S).sql

# 2. Drop problematic views/tables
docker-compose exec db psql -U hunter -d hunter << 'EOF'
DROP VIEW IF EXISTS v_features_asof CASCADE;
DROP VIEW IF EXISTS v_labels_asof CASCADE;
DROP VIEW IF EXISTS v_training_dataset_asof CASCADE;
EOF

# 3. Restore from backup (if needed)
cat backup_20250118_120000.sql | docker-compose exec -T db psql -U hunter -d hunter

# 4. Re-apply working migration
docker-compose exec db psql -U hunter -d hunter -f /docker-entrypoint-initdb.d/migrations/001_add_temporal_correctness_views.sql
```

### 9.3 Model Rollback

If a new model performs poorly:

```bash
# 1. Identify previous champion model
docker-compose exec db psql -U hunter -d hunter << 'EOF'
SELECT model_version, metrics, created_at
FROM model_registry
WHERE created_at < (SELECT created_at FROM model_registry WHERE is_active = true)
ORDER BY created_at DESC
LIMIT 5;
EOF

# 2. Promote previous model
docker-compose exec db psql -U hunter -d hunter << 'EOF'
-- Deactivate current model
UPDATE model_registry SET is_active = false WHERE is_active = true;

-- Activate previous model (replace 'lr_20250110_120000' with actual version)
UPDATE model_registry
SET is_active = true, promotion_suggestion = 'rolled_back'
WHERE model_version = 'lr_20250110_120000';
EOF

# 3. Verify rollback
docker-compose exec db psql -U hunter -d hunter -c "
SELECT model_version, is_active, promotion_suggestion
FROM model_registry
ORDER BY created_at DESC
LIMIT 3;
"
```

---

## 10. Production Checklist

Before declaring production-ready, verify:

- [ ] All Docker containers running (`docker-compose ps` shows all "Up")
- [ ] Database temporal correctness tests passing (0 failures)
- [ ] API health endpoint responding (`/health`)
- [ ] Dashboard accessible at `http://localhost:8501`
- [ ] Cron jobs installed (`crontab -l` shows all jobs)
- [ ] Metrics collector running (`monitoring_logs` has recent data)
- [ ] Worker processing tasks (check `ta_silver`/`sa_silver` for recent `validated_at`)
- [ ] DLQ rate < 5% (check Observability dashboard)
- [ ] Backpressure disabled (check Observability dashboard)
- [ ] At least 1 active model in `model_registry`
- [ ] Log rotation configured (check crontab)
- [ ] Backup procedure tested (run `pg_dump` manually)
- [ ] `.env` file secured (not in git, proper permissions)
- [ ] Admin API key rotated from default

---

## 11. Support & Maintenance

### Daily Tasks
- [ ] Check Observability dashboard for red/yellow indicators
- [ ] Review worker logs for errors: `docker-compose logs worker | grep -i error`
- [ ] Monitor DLQ rate (should be < 5%)

### Weekly Tasks
- [ ] Review Active Learning queue and label high-value samples
- [ ] Check model performance (IC, Sharpe) after Monday retraining
- [ ] Review database size growth: `SELECT pg_size_pretty(pg_database_size('hunter'));`

### Monthly Tasks
- [ ] Rotate admin API key
- [ ] Review and archive old logs (> 30 days)
- [ ] Database vacuum and analyze: `VACUUM ANALYZE;`
- [ ] Update Docker images: `docker-compose pull`

### Quarterly Tasks
- [ ] Full database backup and store offsite
- [ ] Review and update PRD/Architecture docs
- [ ] Security audit (dependency updates, vulnerability scan)
- [ ] Capacity planning review (disk, CPU, RAM trends)

---

## 12. Contact & Escalation

**Project Documentation:**
- PRD: `/home/duongtran/an-pj/mini-trading/doc/PRD_V2.7.2_FINAL.md`
- Architecture: `/home/duongtran/an-pj/mini-trading/doc/ARCH_V3.1_FINAL.md`

**Deployment Artifacts:**
- Source Code: `/home/duongtran/an-pj/mini-trading/source-code/`
- Logs: `/home/duongtran/an-pj/mini-trading/source-code/logs/`
- Model Artifacts: `/home/duongtran/an-pj/mini-trading/source-code/artifacts/`

**Critical Thresholds for Escalation:**
- DLQ Rate > 20%: Data quality crisis - investigate scraper sources
- Queue Size > 50,000: Infrastructure overload - scale workers or reduce scraping
- Model IC < 0: Model failure - rollback to previous model immediately
- Database > 80% disk: Disk full risk - archive old data or expand storage

---

**End of Deployment Guide**

**Deployment Status:** Ready for Production ✅
**Last Updated:** 2025-11-18
**Version:** 1.0
