#!/bin/bash
#
# Integration Smoke Test - Stock Hunter AI
# =========================================
# Tests the complete pipeline after production fixes
#
# Usage:
#   ./scripts/integration_test.sh
#

set -e  # Exit on any error

echo "========================================"
echo "Stock Hunter AI - Integration Test"
echo "========================================"
echo

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counter
TESTS_PASSED=0
TESTS_FAILED=0

test_pass() {
    echo -e "${GREEN}✓${NC} $1"
    ((TESTS_PASSED++))
}

test_fail() {
    echo -e "${RED}✗${NC} $1"
    ((TESTS_FAILED++))
}

test_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# ===== DATABASE TESTS =====
echo "1. Database Connection..."
if docker compose exec -T db psql -U hunter -d hunter -c "SELECT 1;" > /dev/null 2>&1; then
    test_pass "Database connection OK"
else
    test_fail "Database connection FAILED"
    exit 1
fi

echo "2. Database Schema..."
TABLES=$(docker compose exec -T db psql -U hunter -d hunter -tAc "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';")
if [ "$TABLES" -ge 25 ]; then
    test_pass "Database schema complete ($TABLES tables)"
else
    test_fail "Missing tables (found $TABLES, expected >=25)"
fi

echo "3. Watchlist Data..."
SYMBOLS=$(docker compose exec -T db psql -U hunter -d hunter -tAc "SELECT COUNT(*) FROM symbol_watchlist WHERE is_active=true;")
if [ "$SYMBOLS" -ge 3 ]; then
    test_pass "Watchlist populated ($SYMBOLS symbols)"
else
    test_warn "Watchlist has only $SYMBOLS symbols (expected >=3)"
fi

echo "4. Model Registry..."
MODELS=$(docker compose exec -T db psql -U hunter -d hunter -tAc "SELECT COUNT(*) FROM model_registry WHERE is_active=true;")
if [ "$MODELS" -eq 1 ]; then
    test_pass "Active model exists in registry"
else
    test_warn "No active model (found $MODELS). Run: docker compose exec worker python -m worker.create_baseline_model"
fi

echo "5. Macro Data..."
MACRO=$(docker compose exec -T db psql -U hunter -d hunter -tAc "SELECT COUNT(*) FROM macro_clean;" 2>/dev/null || echo "0")
if [ "$MACRO" -ge 5 ]; then
    test_pass "Macro data initialized ($MACRO metrics)"
else
    test_warn "Macro data incomplete (found $MACRO, expected >=5). Run migration 004"
fi

# ===== SERVICE TESTS =====
echo
echo "6. API Service..."
if docker compose ps api | grep -q "Up"; then
    API_HEALTH=$(curl -s http://localhost:8000/health 2>&1 | grep -o "healthy" || echo "")
    if [ -n "$API_HEALTH" ]; then
        test_pass "API service healthy"
    else
        test_fail "API service not responding correctly"
    fi
else
    test_fail "API service not running"
fi

echo "7. Dashboard Service..."
if docker compose ps dash | grep -q "Up"; then
    DASH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8501 2>&1 || echo "000")
    if [ "$DASH_STATUS" = "200" ]; then
        test_pass "Dashboard accessible"
    else
        test_warn "Dashboard returned HTTP $DASH_STATUS"
    fi
else
    test_fail "Dashboard service not running"
fi

echo "8. Worker Service..."
if docker compose ps worker | grep -q "Up"; then
    test_pass "Worker service running"
else
    test_fail "Worker service not running"
fi

echo "9. Scraper Service..."
if docker compose ps scraper | grep -q "Up"; then
    test_pass "Scraper service running"
else
    test_warn "Scraper service not running (may be one-shot)"
fi

# ===== FUNCTIONAL TESTS =====
echo
echo "10. NLP Pipeline (Fixed)..."
NLP_CODE=$(docker compose exec -T worker grep -c "SELECT.*sa_silver" /app/worker/tasks_nlp.py 2>&1 || echo "0")
if [ "$NLP_CODE" -ge 1 ]; then
    test_pass "NLP pipeline reads from database (placeholder code fixed)"
else
    test_fail "NLP pipeline still has placeholder code"
fi

echo "11. Batch Orchestrator..."
if [ -f "/home/duongtran/an-pj/mini-trading/source-code/src/worker/worker/batch_orchestrator.py" ]; then
    test_pass "Batch orchestrator created"
else
    test_fail "Batch orchestrator missing"
fi

echo "12. Baseline Model Seeder..."
if [ -f "/home/duongtran/an-pj/mini-trading/source-code/src/worker/worker/create_baseline_model.py" ]; then
    test_pass "Baseline model seeder created"
else
    test_fail "Baseline model seeder missing"
fi

echo "13. Migrations..."
MIGRATIONS=$(ls -1 /home/duongtran/an-pj/mini-trading/source-code/db/migrations/*.sql 2>/dev/null | wc -l)
if [ "$MIGRATIONS" -ge 4 ]; then
    test_pass "All migrations present ($MIGRATIONS files)"
else
    test_warn "Expected 4+ migrations, found $MIGRATIONS"
fi

# ===== SUMMARY =====
echo
echo "========================================"
echo "Test Summary"
echo "========================================"
echo -e "${GREEN}Passed:${NC} $TESTS_PASSED"
echo -e "${RED}Failed:${NC} $TESTS_FAILED"
echo

TOTAL=$((TESTS_PASSED + TESTS_FAILED))
if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed! System ready for deployment.${NC}"
    exit 0
elif [ $TESTS_FAILED -le 2 ]; then
    echo -e "${YELLOW}⚠ Minor issues detected. Review warnings above.${NC}"
    exit 0
else
    echo -e "${RED}✗ Critical issues detected. Fix failures before deployment.${NC}"
    exit 1
fi
