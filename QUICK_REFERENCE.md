# Stock Hunter AI - Quick Reference Card

**Version:** 1.0 | **Last Updated:** 2025-11-18

---

## 🚀 Quick Start

```bash
# Start all services
cd /home/duongtran/an-pj/mini-trading/source-code
docker-compose up -d

# Check system health
docker-compose ps
curl http://localhost:8000/health

# Access dashboard
open http://localhost:8501  # or visit in browser
```

---

## 📊 Common Operations

### Dashboard Access

| Dashboard Page | URL | Purpose |
|---------------|-----|---------|
| **Home** | `http://localhost:8501` | System overview |
| **Symbol Explorer** | `http://localhost:8501/Symbol_Explorer` | Individual stock analysis |
| **Feature Viewer** | `http://localhost:8501/Feature_Viewer` | Feature inspection |
| **System Health** | `http://localhost:8501/System_Health` | Macro/sector stats |
| **Active Learning** | `http://localhost:8501/Active_Learning` | Label review queue |
| **Observability** | `http://localhost:8501/Observability` | **⭐ Main monitoring hub** |

### API Endpoints

```bash
# Health check
curl http://localhost:8000/health

# Admin ping (requires ADMIN_KEY)
curl http://localhost:8000/admin/health/ping \
  -H "X-ADMIN-KEY: your_admin_key"

# Queue statistics
curl http://localhost:8000/admin/health/queue_stats \
  -H "X-ADMIN-KEY: your_admin_key"

# Macro statistics
curl http://localhost:8000/admin/health/macro_stats \
  -H "X-ADMIN-KEY: your_admin_key"
```

---

## 🔧 Troubleshooting Commands

### Check Service Status

```bash
# View all container status
docker-compose ps

# Follow worker logs
docker-compose logs -f worker

# Follow API logs
docker-compose logs -f api

# Check last 50 lines of all logs
docker-compose logs --tail=50

# Check for errors in last 100 lines
docker-compose logs --tail=100 | grep -i error
```

### Database Operations

```bash
# Connect to database
docker-compose exec db psql -U hunter -d hunter

# Quick queries (run inside psql)
# ==================================

# Check queue backlog
SELECT COUNT(*) FROM task_q WHERE next_run_at <= NOW();

# Check DLQ failures (last 24h)
SELECT reason, COUNT(*) as count
FROM task_q_dlq
WHERE moved_at > NOW() - interval '24 hours'
GROUP BY reason
ORDER BY count DESC
LIMIT 10;

# Check latest metrics
SELECT metric_name, value, log_time
FROM monitoring_logs
WHERE log_time > NOW() - interval '1 hour'
ORDER BY log_time DESC
LIMIT 20;

# Check active model
SELECT model_version, metrics->>'sharpe' as sharpe,
       metrics->>'ic' as ic, created_at
FROM model_registry
WHERE is_active = true;

# Check backpressure status
SELECT flag, enabled, reason, updated_at
FROM control_flags
WHERE flag = 'SCRAPE_SLOW';

# Database size
SELECT pg_size_pretty(pg_database_size('hunter')) as size;

# Table sizes
SELECT schemaname || '.' || tablename AS table,
       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
LIMIT 10;
```

### Worker Management

```bash
# Restart worker
docker-compose restart worker

# Stop worker
docker-compose stop worker

# Start worker
docker-compose start worker

# View worker resource usage
docker stats source-code-worker-1

# Check if worker is stuck (no activity for 10+ mins)
docker-compose logs worker --since 10m | grep -c "Processing"
# If 0, worker may be stuck - restart it
```

### Manual Job Execution

```bash
# Run feature gold pipeline manually
docker-compose exec worker python -m worker.tasks_feature_gold

# Run weak supervision labeling manually
docker-compose exec worker python -m worker.tasks_label

# Run model training manually
docker-compose exec worker python -m worker.tasks_train

# Run metrics collection manually
docker-compose exec worker python -m worker.monitor_metrics

# Test Silver worker (process pending tasks)
docker-compose exec worker python -m worker.main
```

---

## 🚨 Emergency Procedures

### High DLQ Rate (> 15%)

```bash
# 1. Check top failure reasons
docker-compose exec db psql -U hunter -d hunter -c "
SELECT reason, COUNT(*) as count
FROM task_q_dlq
WHERE moved_at > NOW() - interval '24 hours'
GROUP BY reason
ORDER BY count DESC
LIMIT 5;
"

# 2. Inspect failed payload
docker-compose exec db psql -U hunter -d hunter -c "
SELECT payload, reason, moved_at
FROM task_q_dlq
WHERE reason = 'your_top_reason'
LIMIT 1;
"

# 3. If data source issue, pause scraper temporarily
docker-compose exec db psql -U hunter -d hunter -c "
UPDATE control_flags
SET enabled = true, reason = 'Manual pause - DLQ investigation'
WHERE flag = 'SCRAPE_SLOW';
"
```

### Queue Overflow (> 50,000 tasks)

```bash
# 1. Check queue size
docker-compose exec db psql -U hunter -d hunter -c "
SELECT COUNT(*) FROM task_q WHERE next_run_at <= NOW();
"

# 2. Enable backpressure (if not auto-enabled)
docker-compose exec db psql -U hunter -d hunter -c "
UPDATE control_flags
SET enabled = true, reason = 'Manual - Queue overflow'
WHERE flag = 'SCRAPE_SLOW';
"

# 3. Scale workers (if infrastructure allows)
# Edit docker-compose.yml and add:
# worker:
#   deploy:
#     replicas: 3
# Then: docker-compose up -d --scale worker=3

# 4. Emergency purge of old tasks (CAUTION!)
docker-compose exec db psql -U hunter -d hunter -c "
DELETE FROM task_q
WHERE next_run_at < NOW() - interval '7 days';
"
```

### Model IC Drop (< 0.02)

```bash
# 1. Check current model metrics
docker-compose exec db psql -U hunter -d hunter -c "
SELECT model_version, metrics, created_at
FROM model_registry
WHERE is_active = true;
"

# 2. Check recent predictions vs actuals
# (Requires custom query based on your schema)

# 3. Trigger immediate retraining
docker-compose exec worker python -m worker.tasks_train

# 4. If new model doesn't improve, rollback to previous
docker-compose exec db psql -U hunter -d hunter << 'EOF'
-- Deactivate current model
UPDATE model_registry SET is_active = false WHERE is_active = true;

-- Activate previous champion
UPDATE model_registry
SET is_active = true
WHERE model_version = (
    SELECT model_version
    FROM model_registry
    WHERE created_at < (SELECT created_at FROM model_registry WHERE is_active = true)
    ORDER BY created_at DESC
    LIMIT 1
);
EOF
```

### Database Disk Full (> 90%)

```bash
# 1. Check disk usage
df -h

# 2. Check database size
docker-compose exec db psql -U hunter -d hunter -c "
SELECT pg_size_pretty(pg_database_size('hunter'));
"

# 3. Vacuum to reclaim space
docker-compose exec db psql -U hunter -d hunter -c "VACUUM FULL;"

# 4. Archive old metrics (> 60 days)
docker-compose exec db psql -U hunter -d hunter -c "
DELETE FROM monitoring_logs
WHERE log_time < NOW() - interval '60 days';
"

# 5. Archive old DLQ records (> 30 days)
docker-compose exec db psql -U hunter -d hunter -c "
DELETE FROM task_q_dlq
WHERE moved_at < NOW() - interval '30 days';
"

# 6. Backup and drop old data (last resort)
docker-compose exec db pg_dump -U hunter hunter > backup_$(date +%Y%m%d).sql
# Then manually drop old partitions or archive tables
```

### Worker Stuck / Advisory Lock

```bash
# 1. Check for stuck locks
docker-compose exec db psql -U hunter -d hunter -c "
SELECT * FROM pg_locks WHERE locktype = 'advisory';
"

# 2. Release all advisory locks
docker-compose exec db psql -U hunter -d hunter -c "
SELECT pg_advisory_unlock_all();
"

# 3. Clear worker_locks table (if corrupted)
docker-compose exec db psql -U hunter -d hunter -c "
TRUNCATE worker_locks;
"

# 4. Restart worker
docker-compose restart worker
```

---

## 📈 Monitoring Thresholds

| Metric | Green | Yellow | Red | Action |
|--------|-------|--------|-----|--------|
| **DLQ Rate** | < 5% | 5-10% | > 10% | Investigate top failure reasons |
| **Queue Size** | < 5,000 | 5K-10K | > 10K | Enable backpressure / scale workers |
| **IC** | > 0.05 | 0.02-0.05 | < 0.02 | Retrain model / rollback |
| **Sharpe Ratio** | > 1.0 | 0.5-1.0 | < 0.5 | Review model / retrain |
| **Price Staleness** | < 48h | 48-96h | > 96h | Check scraper health |
| **News Staleness** | < 24h | 24-72h | > 72h | Check scraper health |
| **Database Size** | < 50GB | 50-80GB | > 80GB | Archive old data / expand storage |
| **Model Age** | < 30d | 30-60d | > 60d | Trigger retraining |

---

## 🔐 Security Checklist

```bash
# 1. Check .env file permissions (should be 600)
ls -la .env
# If not: chmod 600 .env

# 2. Verify ADMIN_KEY is not default
grep ADMIN_KEY .env
# Should NOT be "supersecretkey"

# 3. Check database password strength
grep DB_URL .env
# Should have 16+ character password

# 4. Rotate ADMIN_KEY quarterly
# Generate new key: openssl rand -hex 32
# Update .env and restart API: docker-compose restart api

# 5. Review exposed ports
docker-compose ps
netstat -tuln | grep -E '5432|8000|8501'
# Ensure only localhost or secured by firewall in production
```

---

## 📦 Backup & Restore

### Quick Backup

```bash
# Database backup (full)
docker-compose exec db pg_dump -U hunter hunter | gzip > backup_$(date +%Y%m%d_%H%M%S).sql.gz

# Model artifacts backup
tar -czf artifacts_backup_$(date +%Y%m%d).tar.gz artifacts/

# Config backup
cp .env .env.backup_$(date +%Y%m%d)
cp docker-compose.yml docker-compose.yml.backup_$(date +%Y%m%d)
```

### Quick Restore

```bash
# Restore database
gunzip < backup_20250118_120000.sql.gz | docker-compose exec -T db psql -U hunter -d hunter

# Restore model artifacts
tar -xzf artifacts_backup_20250118.tar.gz

# Verify restore
docker-compose exec db psql -U hunter -d hunter -c "SELECT COUNT(*) FROM ta_silver;"
```

---

## 🛠️ Maintenance Schedule

### Daily (Automated via Cron)
- ✅ Log rotation (old logs > 30 days deleted)
- ✅ Database backup (1 AM)

### Weekly (Automated via Cron)
- ✅ Weak supervision labeling (Monday 9 AM)
- ✅ Model training check (Monday 10 AM)
- ✅ Database vacuum (Sunday 2 AM)
- ✅ Docker cleanup (Sunday 4 AM)
- ✅ Model artifacts backup (Sunday 5 AM)

### Monthly (Manual)
- [ ] Review Observability dashboard trends
- [ ] Rotate ADMIN_KEY (quarterly)
- [ ] Review and archive old data (> 90 days)
- [ ] Update Docker images: `docker-compose pull`

---

## 📞 Quick Help

| Issue | Command |
|-------|---------|
| Worker not processing | `docker-compose restart worker` |
| High DLQ rate | Check `task_q_dlq` top reasons |
| Queue overflow | Enable backpressure or scale workers |
| Model IC dropped | Trigger retraining or rollback |
| Dashboard not loading | `docker-compose restart dash` |
| API not responding | `docker-compose restart api` |
| Database connection error | `docker-compose restart db` |
| Disk space low | Run vacuum + archive old data |

---

## 📚 Documentation Links

- **Full Deployment Guide:** `DEPLOYMENT_GUIDE.md`
- **Crontab Configuration:** `crontab_hunter.txt`
- **PRD:** `/home/duongtran/an-pj/mini-trading/doc/PRD_V2.7.2_FINAL.md`
- **Architecture:** `/home/duongtran/an-pj/mini-trading/doc/ARCH_V3.1_FINAL.md`

---

## 🎯 One-Line Commands (Copy & Paste)

```bash
# Full system restart
docker-compose down && docker-compose up -d && sleep 10 && docker-compose ps && curl http://localhost:8000/health

# Check all metrics now
docker-compose exec worker python -m worker.monitor_metrics && echo "Metrics updated - refresh dashboard"

# Emergency worker restart with lock clear
docker-compose exec db psql -U hunter -d hunter -c "SELECT pg_advisory_unlock_all();" && docker-compose restart worker

# Quick health summary
echo "=== Queue ===" && docker-compose exec db psql -U hunter -d hunter -tAc "SELECT COUNT(*) FROM task_q WHERE next_run_at <= NOW();" && echo "=== DLQ (24h) ===" && docker-compose exec db psql -U hunter -d hunter -tAc "SELECT COUNT(*) FROM task_q_dlq WHERE moved_at > NOW() - interval '24 hours';" && echo "=== Model IC ===" && docker-compose exec db psql -U hunter -d hunter -tAc "SELECT metrics->>'ic' FROM model_registry WHERE is_active = true;"

# Full backup now
mkdir -p backups && docker-compose exec db pg_dump -U hunter hunter | gzip > backups/emergency_backup_$(date +%Y%m%d_%H%M%S).sql.gz && tar -czf backups/artifacts_emergency_$(date +%Y%m%d).tar.gz artifacts/ && echo "Backup complete"

# View live metrics (refreshes every 5 seconds)
watch -n 5 'docker-compose exec db psql -U hunter -d hunter -tAc "SELECT metric_name, value FROM monitoring_logs WHERE log_time > NOW() - interval '\''5 minutes'\'' ORDER BY log_time DESC LIMIT 10;"'
```

---

**Quick Reference Card v1.0** | Keep this handy for day-to-day operations 🚀
