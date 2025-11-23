"""
Guardrail & Auto-Rollback Task - Stock Hunter AI
================================================
Monitors system health and automatically rolls back models if critical issues are detected.

Story 4.3: Auto-Rollback
- Checks for critical alerts (PSI > 0.25, IC Drop, etc.)
- Reverts to previous safe model if needed.
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional
import psycopg2
from core_lib.db import get_db_connection

# --- Constants ---
CRITICAL_PSI_THRESHOLD = 0.25
CRITICAL_IC_THRESHOLD = 0.01
MAX_HMM_STATE_CONCENTRATION = 95.0  # % of stocks in one state

def check_for_critical_issues(conn) -> List[str]:
    """
    Checks for critical issues that require rollback.
    """
    issues = []
    
    with conn.cursor() as cursor:
        # 1. Check recent monitoring logs for critical alerts
        cursor.execute("""
            SELECT metric_name, value, metadata
            FROM monitoring_logs
            WHERE log_time > NOW() - interval '1 hour'
        """)
        
        rows = cursor.fetchall()
        for row in rows:
            metric, value, meta = row
            
            # PSI Check
            if metric == 'psi_top_10' and value > CRITICAL_PSI_THRESHOLD:
                issues.append(f"Critical PSI Drift: {value:.2f} > {CRITICAL_PSI_THRESHOLD}")
                
            # IC Check
            if metric == 'live_ic_20d' and value < CRITICAL_IC_THRESHOLD:
                issues.append(f"Critical IC Drop: {value:.3f} < {CRITICAL_IC_THRESHOLD}")
                
            # HMM Collapse Check
            if metric.startswith('hmm_') and metric.endswith('_pct') and value > MAX_HMM_STATE_CONCENTRATION:
                issues.append(f"HMM State Collapse: {metric}={value:.1f}%")

    return issues

def get_safe_model_version(conn, current_version: str) -> Optional[str]:
    """
    Finds the last known safe model (previous champion).
    """
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT model_version
            FROM model_promotion_history
            WHERE action = 'PROMOTE_PROD'
              AND model_version != %s
            ORDER BY created_at DESC
            LIMIT 1
        """, (current_version,))
        
        row = cursor.fetchone()
        if row:
            return row[0]
            
        # Fallback: Find any active model before current
        cursor.execute("""
            SELECT model_version
            FROM model_registry
            WHERE is_active = false
              AND model_version != %s
              AND metrics->>'ic' IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 1
        """, (current_version,))
        row = cursor.fetchone()
        return row[0] if row else None

def execute_rollback(conn, current_version: str, safe_version: str, reason: str):
    """
    Executes the rollback: deactivates current, activates safe.
    """
    print(f"!!! EXECUTING ROLLBACK: {current_version} -> {safe_version} !!!")
    print(f"Reason: {reason}")
    
    with conn.cursor() as cursor:
        # Deactivate current
        cursor.execute("""
            UPDATE model_registry
            SET is_active = false,
                promotion_suggestion = 'rolled_back'
            WHERE model_version = %s
        """, (current_version,))
        
        # Activate safe
        cursor.execute("""
            UPDATE model_registry
            SET is_active = true
        """, (safe_version,))
        
        # Log history
        evidence = {'reason': reason, 'rolled_back_from': current_version}
        cursor.execute("""
            INSERT INTO model_promotion_history (model_version, action, actor, evidence_hash, created_at)
            VALUES (%s, 'ROLLBACK', 'system_guardrail', %s, NOW())
        """, (safe_version, json.dumps(evidence)))
        
    conn.commit()
    print("Rollback successful.")

def run_guardrail_check():
    """Main entry point."""
    print("Running Guardrail Check...")
    
    try:
        conn = get_db_connection()
        
        # 1. Check for issues
        issues = check_for_critical_issues(conn)
        if not issues:
            print("System healthy. No critical issues.")
            return
            
        print(f"CRITICAL ISSUES DETECTED: {issues}")
        
        # 2. Get current active model
        with conn.cursor() as cursor:
            cursor.execute("SELECT model_version FROM model_registry WHERE is_active = true")
            row = cursor.fetchone()
            
        if not row:
            print("No active model to rollback.")
            return
            
        current_version = row[0]
        
        # 3. Find safe model
        safe_version = get_safe_model_version(conn, current_version)
        if not safe_version:
            print("FATAL: No previous safe model found! Cannot rollback.")
            return
            
        # 4. Execute Rollback
        execute_rollback(conn, current_version, safe_version, "; ".join(issues))
        
    except Exception as e:
        print(f"Guardrail failed: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    run_guardrail_check()
