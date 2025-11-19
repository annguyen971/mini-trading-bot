# 🚀 Production-Ready Deployment Guide
**Stock Hunter AI - Post-Critical-Fixes**

**Date:** 2025-01-18
**Status:** ✅ PRODUCTION-READY (After Fixes)

---

## 🎉 **Critical Fixes Completed**

All production-blocking issues have been **FIXED**:

1. ✅ **tasks_nlp.py** - Now reads from `sa_silver` and `raw_bronze` (no more placeholder code)
2. ✅ **Batch Orchestrator** - New routing system for ML pipeline tasks
3. ✅ **API Model Loading** - Fail-fast if no model (prevents crashes)
4. ✅ **Baseline Model** - Seeder script + migration for bootstrap
5. ✅ **Macro Data Pipeline** - Bootstrap data + calendar initialization
6. ✅ **Integration Test** - Comprehensive smoke test script

---

## 📋 **Pre-Deployment Checklist**

Before running `docker compose up -d`:

- [ ] `.env` file configured with ADMIN_KEY and GEMINI_API_KEY
- [ ] Docker and Docker Compose installed (20.10+, 2.0+)
- [ ] Ports 5432, 8000, 8501 available
- [ ] At least 4GB RAM and 2 CPU cores available
- [ ] 50GB+ disk space

---

## 🚀 **Deployment Steps**

### **Step 1: Initial Setup**

```bash
cd /home/duongtran/an-pj/mini-trading/source-code

# Verify .env exists
ls -la .env

# Example .env content:
# DB_URL=postgresql://hunter:your_password@db:5432/hunter
# ADMIN_KEY=your_secure_admin_key_here
# GEMINI_API_KEY=your_gemini_api_key_here
```

---

### **Step 2: Start Database and Run Migrations**

```bash
# Start database
docker compose up -d db

# Wait for DB to be ready
sleep 10

# Run all migrations
docker compose exec db psql -U hunter -d hunter -f /docker-entrypoint-initdb.d/migrations/002_add_p0_watchlist_legal_guard.sql
docker compose exec db psql -U hunter -d hunter -f /docker-entrypoint-initdb.d/migrations/003_seed_default_model.sql
docker compose exec db psql -U hunter -d hunter -f /docker-entrypoint-initdb.d/migrations/004_init_macro_data.sql

# Verify
docker compose exec db psql -U hunter -d hunter -c "SELECT COUNT(*) FROM symbol_watchlist;"
# Expected: 10

docker compose exec db psql -U hunter -d hunter -c "SELECT COUNT(*) FROM macro_clean;"
# Expected: 7
```

---

### **Step 3: Create Baseline Model**

```bash
# Build worker container first
docker compose build worker

# Create baseline model artifact
docker compose exec worker python -m worker.create_baseline_model

# Verify model exists
docker compose exec worker ls -lh /opt/artifacts/baseline_v1.pkl
# Expected: File exists with ~1KB size

# Verify model in registry
docker compose exec db psql -U hunter -d hunter -c \
  "SELECT model_version, is_active FROM model_registry WHERE model_version='baseline_v1';"
# Expected: baseline_v1 | t (true)
```

---

### **Step 4: Build and Start All Services**

```bash
# Build all containers
docker compose build --no-cache

# Start all services
docker compose up -d

# Check status
docker compose ps
# Expected: All services "Up" except maybe scraper (one-shot)
```

---

### **Step 5: Run Integration Test**

```bash
# Run comprehensive smoke test
./scripts/integration_test.sh

# Expected output:
# ========================================
# Stock Hunter AI - Integration Test
# ========================================
# ✓ Database connection OK
# ✓ Database schema complete (27 tables)
# ✓ Watchlist populated (10 symbols)
# ✓ Active model exists in registry
# ✓ Macro data initialized (7 metrics)
# ✓ API service healthy
# ✓ Dashboard accessible
# ✓ Worker service running
# ✓ NLP pipeline reads from database
# ✓ Batch orchestrator created
# ✓ Baseline model seeder created
# ✓ All migrations present
# ========================================
# Passed: 13
# Failed: 0
# ✓ All tests passed! System ready for deployment.
```

---

### **Step 6: Start Cron Jobs (Optional)**

```bash
# Start cron service for batch jobs
docker compose --profile cron up -d

# Verify cron is running
docker compose ps cron
# Expected: "Up"

# Check cron logs
docker compose logs cron
```

---

## 🔍 **Verification**

### **Test 1: Dashboard Access**

```bash
# Open browser to:
http://localhost:8501

# You should see 7 pages:
# 1. Overview (Symbol Explorer)
# 2. Active Learning
# 3. Model Registry
# 4. System Health
# 5. Macro Impact Config
# 6. Observability
# 7. Watchlist Management ⭐ (NEW)
```

### **Test 2: API Endpoints**

```bash
# Health check
curl http://localhost:8000/health
# Expected: {"status":"healthy","database":"connected"}

# Watchlist
curl http://localhost:8000/admin/watchlist \
  -H "X-ADMIN-KEY: your_admin_key"
# Expected: {"user_id":"admin","count":10,"symbols":[...]}

# Model registry
curl http://localhost:8000/admin/models \
  -H "X-ADMIN-KEY: your_admin_key"
# Expected: [{"model_version":"baseline_v1","is_active":true,...}]
```

### **Test 3: Run Scraper**

```bash
# Run scraper manually
docker compose exec scraper python -m scraper.main

# Expected output:
# ✓ Loaded 10 symbols from database
# ✓ Legal Guard initialized
# ✓ State Manager initialized
# Processing 10 symbols...
# --- Processing symbol: FPT ---
# Fetching OHLCV for FPT...
# Fetching news for FPT (first run)...
# Successfully processed X/Y news articles for FPT
```

### **Test 4: Run Batch Jobs**

```bash
# Test NLP batch (should process pending news)
docker compose exec worker python -m worker.tasks_nlp

# Test Feature Gold (compute HMM, scores)
docker compose exec worker python -m worker.tasks_feature_gold

# Test Labeling (Snorkel)
docker compose exec worker python -m worker.tasks_label
```

---

## 📊 **What You'll See in the Dashboard**

### **Page 1: Overview**
```
📈 Stock Hunter AI - Overview

[Symbol Input: "FPT" ▼]

HunterScore: 50    FrothScore: 50    HMM State: 0 (Baseline)
Safety Banner: ⚠️  Baseline Model Active (Replace with trained model)

[Export Options Available]
```

### **Page 7: Watchlist Management** (NEW!)
```
📋 Watchlist Management

Statistics:
  Active Symbols: 10/500
  Capacity Used: 2.0%
  NFR10 Status: ✅ COMPLIANT

Current Watchlist:
  FPT, HPG, GAS, MBB, TCB, VCB, VHM, VIC, VNM, VPB

[Add Symbol] [Remove Symbol]
```

---

## 🎯 **Post-Deployment Tasks**

### **Day 1: Run Backfill (Optional)**

```bash
# Backfill 12 months of historical news
docker compose exec scraper python scripts/backfill_news.py \
  --months 12 --delay 3

# This will take 1-2 hours but provides ML training data
```

### **Day 2: Train First Model**

```bash
# Add some golden labels via Active Learning dashboard
# Then trigger training
docker compose exec worker python -m worker.tasks_train

# This creates your first real model!
```

### **Day 3: Monitor**

```bash
# Check Observability dashboard
# Monitor:
# - DLQ rate (should be <5%)
# - Model IC (should improve over baseline)
# - System health metrics
```

---

## ⚠️ **Troubleshooting**

### **Problem: API won't start - "No active model"**

```bash
# Solution: Create baseline model
docker compose exec worker python -m worker.create_baseline_model

# Restart API
docker compose restart api
```

### **Problem: Dashboard shows "No data"**

```bash
# Run scraper to collect data
docker compose exec scraper python -m scraper.main

# Run NLP to process news
docker compose exec worker python -m worker.tasks_nlp

# Run Feature Gold to compute scores
docker compose exec worker python -m worker.tasks_feature_gold
```

### **Problem: NLP batch fails - "No articles pending"**

```bash
# Normal - means scraper hasn't run yet
# Run scraper first:
docker compose exec scraper python -m scraper.main
```

---

## 📈 **Expected Timeline**

| Day | Task | Duration |
|-----|------|----------|
| **Day 1** | Deploy + verify | 1 hour |
| **Day 1** | Run backfill (optional) | 1-2 hours |
| **Day 2** | Collect data + label | 2 hours |
| **Day 2** | Train first model | 30 min |
| **Day 3+** | Monitor + iterate | Ongoing |

---

## 🎉 **Success Criteria**

✅ **All services running**
✅ **Dashboard accessible with 7 pages**
✅ **10 symbols in watchlist**
✅ **Baseline model active**
✅ **Scraper fetching data**
✅ **NLP processing news**
✅ **Integration test passing**

---

## 📞 **Need Help?**

**Documentation:**
- [P0 Implementation Summary](P0_IMPLEMENTATION_SUMMARY.md) - Watchlist + Legal Guard
- [P1 Implementation Summary](P1_IMPLEMENTATION_SUMMARY.md) - Backfill + Incremental
- [PRD](doc/prd v2.7.2.md) - Full requirements
- [Architecture](doc/architecture v3.1.md) - System design

**Common Commands:**
```bash
# Restart everything
docker compose down && docker compose up -d

# View logs
docker compose logs -f worker
docker compose logs -f api

# Run integration test
./scripts/integration_test.sh

# Check database
docker compose exec db psql -U hunter -d hunter
```

---

**🚀 System is now PRODUCTION-READY!**

**Go ahead and deploy with confidence:**
```bash
docker compose up -d
docker compose --profile cron up -d
open http://localhost:8501
```

**Enjoy your Stock Hunter AI system! 🎯**