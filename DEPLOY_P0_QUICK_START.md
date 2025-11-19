# P0 Quick Deployment Guide

**Time Required:** 5-10 minutes
**Prerequisites:** Docker + Docker Compose running

---

## 🚀 **Quick Deploy (3 Steps)**

### **Step 1: Database Setup (2 min)**

```bash
cd /home/duongtran/an-pj/mini-trading/source-code

# Option A: Fresh start (recommended)
docker-compose down -v  # Remove old volumes
docker-compose up -d db
sleep 10  # Wait for DB initialization

# Option B: Existing DB (run migration)
docker-compose exec db psql -U hunter -d hunter \
  -f /docker-entrypoint-initdb.d/migrations/002_add_p0_watchlist_legal_guard.sql
```

**Verify:**
```bash
docker-compose exec db psql -U hunter -d hunter -c \
  "SELECT COUNT(*) FROM symbol_watchlist WHERE is_active = true;"
# Expected: 10
```

---

### **Step 2: Build & Start Services (3 min)**

```bash
# Rebuild services with new code
docker-compose build --no-cache scraper api dash

# Start all services
docker-compose up -d

# Wait for startup
sleep 5
```

**Verify:**
```bash
docker-compose ps
# All services should show "Up"
```

---

### **Step 3: Smoke Test (2 min)**

```bash
# Test 1: Check scraper loads watchlist
docker-compose logs scraper | grep "Loaded.*symbols"
# Expected: "✓ Loaded 10 symbols from database"

# Test 2: Check API endpoint
curl -s http://localhost:8000/admin/watchlist \
  -H "X-ADMIN-KEY: supersecretkey" | jq '.count'
# Expected: 10

# Test 3: Check Dashboard
curl -I http://localhost:8501/Watchlist_Management 2>&1 | grep "200 OK"
# Expected: HTTP/1.1 200 OK
```

---

## ✅ **Success!**

If all 3 tests pass:
- ✅ Dynamic Watchlist: OPERATIONAL
- ✅ Legal Guard: OPERATIONAL
- ✅ Dashboard UI: OPERATIONAL
- ✅ NFR10 Compliance: MONITORED

**Access Dashboard:**
```
http://localhost:8501/Watchlist_Management
```

---

## 🔍 **Troubleshooting**

### **Problem: Scraper shows "No symbols to scrape"**

```bash
# Check database
docker-compose exec db psql -U hunter -d hunter -c \
  "SELECT symbol FROM symbol_watchlist WHERE is_active = true;"

# If empty, seed manually
docker-compose exec db psql -U hunter -d hunter << 'EOF'
INSERT INTO symbol_watchlist (user_id, symbol, sector, is_active)
VALUES ('admin', 'FPT', 'technology', true)
ON CONFLICT DO NOTHING;
EOF
```

### **Problem: API returns 401 Unauthorized**

```bash
# Check ADMIN_KEY environment variable
docker-compose exec api env | grep ADMIN_KEY

# If missing, add to .env file
echo "ADMIN_KEY=supersecretkey" >> .env
docker-compose restart api
```

### **Problem: Dashboard page not found**

```bash
# Check file exists
ls -la src/dash/pages/07_Watchlist_Management.py

# Restart dashboard
docker-compose restart dash
sleep 5

# Check logs
docker-compose logs dash | tail -20
```

---

## 📋 **Post-Deployment Checklist**

- [ ] 10 symbols loaded in scraper
- [ ] Legal Guard initialized
- [ ] API endpoints responding
- [ ] Dashboard page accessible
- [ ] Can add symbol via UI
- [ ] Can remove symbol via UI
- [ ] NFR10 stats showing 2% (10/500)

**All checked?** ✅ P0 deployment COMPLETE!

---

## 🎯 **Next Actions**

1. **Add more symbols** via Dashboard UI (up to 500)
2. **Run scraper** to test with 10+ symbols
3. **Monitor** NFR10 compliance in Dashboard
4. **Optional:** Implement P1 features (News Backfill, Incremental Crawl)

---

**Need help?** See [P0_IMPLEMENTATION_SUMMARY.md](P0_IMPLEMENTATION_SUMMARY.md) for detailed documentation.
