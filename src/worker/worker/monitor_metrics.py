"""
Metrics Monitoring Collector - Stock Hunter AI
===============================================
Collects and stores system metrics for observability.

Runs periodically to capture:
- Data quality metrics (DLQ rates, validation pass rates)
- Model performance metrics (IC, Sharpe, predictions)
- System health metrics (queue sizes, processing times)
- Source health metrics (scraper success rates, circuit breaker states)

Stores in monitoring_logs table for dashboard visualization.
"""

import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import numpy as np

from core_lib.db import get_db_connection

# --- Constants ---
METRIC_RETENTION_DAYS = 90  # Keep metrics for 90 days

# --- Data Quality Metrics ---

def collect_data_quality_metrics(conn) -> Dict:
    """
    Collects data quality metrics for the last 24 hours.

    Metrics:
    - DLQ rate (% of tasks moved to DLQ)
    - Validation pass rate (Silver layer)
    - Feature completeness (% of symbols with all features)
    - Label coverage (% of samples with labels)
    """
    metrics = {}

    with conn.cursor() as cursor:
        # DLQ Rate
        cursor.execute("""
            SELECT
                COUNT(*) FILTER (WHERE moved_at > NOW() - interval '24 hours') as dlq_count,
                (SELECT COUNT(*) FROM ta_silver WHERE validated_at > NOW() - interval '24 hours') +
                (SELECT COUNT(*) FROM sa_silver WHERE validated_at > NOW() - interval '24 hours') as processed_count
            FROM task_q_dlq
        """)
        row = cursor.fetchone()
        dlq_count = row[0] or 0
        processed_count = row[1] or 0
        total = dlq_count + processed_count

        metrics['dlq_rate'] = (dlq_count / total * 100) if total > 0 else 0.0

        # Feature Completeness
        cursor.execute("""
            WITH symbol_feature_counts AS (
                SELECT symbol, COUNT(DISTINCT feature_name) as feature_count
                FROM features_gold
                WHERE effective_date = CURRENT_DATE - 1
                GROUP BY symbol
            )
            SELECT
                COUNT(*) FILTER (WHERE feature_count >= 4) * 100.0 / NULLIF(COUNT(*), 0) as completeness_pct
            FROM symbol_feature_counts
        """)
        row = cursor.fetchone()
        metrics['feature_completeness_pct'] = row[0] or 0.0

        # Label Coverage
        cursor.execute("""
            WITH symbols_with_features AS (
                SELECT DISTINCT symbol FROM features_gold
                WHERE effective_date = CURRENT_DATE - 1
            ),
            symbols_with_labels AS (
                SELECT DISTINCT symbol FROM labels_silver
                WHERE effective_date = CURRENT_DATE - 1
            )
            SELECT
                (SELECT COUNT(*) FROM symbols_with_labels) * 100.0 /
                NULLIF((SELECT COUNT(*) FROM symbols_with_features), 0) as coverage_pct
        """)
        row = cursor.fetchone()
        metrics['label_coverage_pct'] = row[0] or 0.0

        # Sanity Rule Hit Rate (which rules fail most)
        cursor.execute("""
            SELECT reason, COUNT(*) as count
            FROM task_q_dlq
            WHERE moved_at > NOW() - interval '7 days'
            GROUP BY reason
            ORDER BY count DESC
            LIMIT 5
        """)
        top_failures = cursor.fetchall()
        metrics['top_failure_reasons'] = {row[0]: row[1] for row in top_failures}

    return metrics

# --- Model Performance Metrics ---

def collect_model_performance_metrics(conn) -> Dict:
    """
    Collects model performance metrics.

    Metrics:
    - Current IC (Information Coefficient)
    - Prediction distribution (mean, std)
    - Win rate (if we have forward returns)
    - Model age (days since last training)
    """
    metrics = {}

    with conn.cursor() as cursor:
        # Get active model metrics
        cursor.execute("""
            SELECT model_version, metrics, created_at
            FROM model_registry
            WHERE is_active = true
            ORDER BY created_at DESC
            LIMIT 1
        """)
        active_model = cursor.fetchone()

        if active_model:
            import json
            model_metrics = json.loads(active_model[1])

            metrics['active_model_version'] = active_model[0]
            metrics['model_sharpe'] = model_metrics.get('sharpe', 0.0)
            metrics['model_ic'] = model_metrics.get('ic', 0.0)
            metrics['model_max_drawdown'] = model_metrics.get('max_drawdown', 0.0)
            metrics['model_age_days'] = (datetime.now() - active_model[2]).days
        else:
            metrics['active_model_version'] = None
            metrics['model_sharpe'] = 0.0
            metrics['model_ic'] = 0.0
            metrics['model_max_drawdown'] = 0.0
            metrics['model_age_days'] = 999

        # Label distribution
        cursor.execute("""
            SELECT
                state_snorkel,
                COUNT(*) as count,
                AVG(probability) as avg_prob,
                AVG(entropy) as avg_entropy
            FROM labels_silver
            WHERE effective_date > CURRENT_DATE - 7
            GROUP BY state_snorkel
        """)
        label_dist = cursor.fetchall()
        metrics['label_distribution'] = {
            f'label_{row[0]}': {
                'count': row[1],
                'avg_prob': float(row[2]) if row[2] else 0.0,
                'avg_entropy': float(row[3]) if row[3] else 0.0
            }
            for row in label_dist
        }

        # AL Queue metrics
        cursor.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'pending') as pending,
                COUNT(*) FILTER (WHERE status = 'reviewed') as reviewed,
                COUNT(*) FILTER (WHERE status = 'snoozed') as snoozed
            FROM al_queue
            WHERE created_at > NOW() - interval '7 days'
        """)
        row = cursor.fetchone()
        metrics['al_queue_pending'] = row[0] or 0
        metrics['al_queue_reviewed'] = row[1] or 0
        metrics['al_queue_snoozed'] = row[2] or 0

    return metrics

# --- System Health Metrics ---

def collect_system_health_metrics(conn) -> Dict:
    """
    Collects system health metrics.

    Metrics:
    - Queue sizes (task_q, al_queue)
    - Processing latency (time from scrape to silver)
    - Backpressure status
    - Worker lock states
    """
    metrics = {}

    with conn.cursor() as cursor:
        # Queue sizes
        cursor.execute("SELECT COUNT(*) FROM task_q WHERE next_run_at <= NOW()")
        metrics['task_q_ready'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM task_q WHERE next_run_at > NOW()")
        metrics['task_q_scheduled'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM al_queue WHERE status = 'pending'")
        metrics['al_queue_size'] = cursor.fetchone()[0]

        # Backpressure status
        cursor.execute("""
            SELECT enabled, reason
            FROM control_flags
            WHERE flag = 'SCRAPE_SLOW'
        """)
        row = cursor.fetchone()
        if row:
            metrics['backpressure_enabled'] = row[0]
            metrics['backpressure_reason'] = row[1]
        else:
            metrics['backpressure_enabled'] = False
            metrics['backpressure_reason'] = 'N/A'

        # Processing latency (average time from bronze to silver)
        cursor.execute("""
            SELECT
                AVG(EXTRACT(EPOCH FROM (validated_at - as_of_time))) as avg_latency_seconds
            FROM ta_silver
            WHERE validated_at > NOW() - interval '24 hours'
        """)
        row = cursor.fetchone()
        metrics['avg_processing_latency_seconds'] = float(row[0]) if row[0] else 0.0

        # Database size
        cursor.execute("""
            SELECT
                pg_size_pretty(pg_database_size(current_database())) as db_size,
                pg_database_size(current_database()) as db_size_bytes
        """)
        row = cursor.fetchone()
        metrics['database_size'] = row[0]
        metrics['database_size_bytes'] = row[1]

    return metrics

# --- Source Health Metrics ---

def collect_source_health_metrics(conn) -> Dict:
    """
    Collects scraper and data source health metrics.

    Metrics:
    - Success rate by source
    - Last successful scrape time
    - Circuit breaker states
    - Rate limiter pressure
    """
    metrics = {}

    with conn.cursor() as cursor:
        # Data freshness
        cursor.execute("""
            SELECT
                MAX(trade_date) as latest_ta,
                MAX(validated_at) as latest_ta_ingested
            FROM ta_silver
        """)
        row = cursor.fetchone()
        if row and row[0]:
            metrics['latest_price_data_date'] = row[0].isoformat()
            metrics['latest_price_ingested'] = row[1].isoformat() if row[1] else None
            metrics['price_data_staleness_hours'] = (datetime.now().date() - row[0]).days * 24
        else:
            metrics['price_data_staleness_hours'] = 999

        cursor.execute("""
            SELECT
                MAX(publisher_time_utc) as latest_sa,
                MAX(validated_at) as latest_sa_ingested
            FROM sa_silver
        """)
        row = cursor.fetchone()
        if row and row[0]:
            metrics['latest_news_data_time'] = row[0].isoformat()
            metrics['latest_news_ingested'] = row[1].isoformat() if row[1] else None
            staleness = (datetime.now() - row[0].replace(tzinfo=None)).total_seconds() / 3600
            metrics['news_data_staleness_hours'] = staleness
        else:
            metrics['news_data_staleness_hours'] = 999

        # Source diversity (how many unique sources)
        cursor.execute("""
            SELECT COUNT(DISTINCT source_name) as unique_sources
            FROM sa_silver
            WHERE validated_at > NOW() - interval '7 days'
        """)
        row = cursor.fetchone()
        metrics['unique_news_sources_7d'] = row[0] or 0

    return metrics

# --- HMM State Distribution (for market regime monitoring) ---

def collect_hmm_state_distribution(conn) -> Dict:
    """
    Collects HMM state distribution for market regime monitoring.

    Returns percentage of stocks in each state.
    """
    metrics = {}

    with conn.cursor() as cursor:
        cursor.execute("""
            WITH latest_states AS (
                SELECT symbol, value as state
                FROM features_gold
                WHERE feature_name = 'hmm_state'
                  AND effective_date = CURRENT_DATE - 1
            )
            SELECT
                state,
                COUNT(*) as count,
                COUNT(*) * 100.0 / SUM(COUNT(*)) OVER () as pct
            FROM latest_states
            GROUP BY state
            ORDER BY state
        """)

        state_names = {0: 'Accumulation', 1: 'Breakout', 2: 'Euphoria', 3: 'Distribution'}

        for row in cursor.fetchall():
            state = int(row[0])
            state_name = state_names.get(state, f'Unknown_{state}')
            metrics[f'hmm_{state_name.lower()}_pct'] = float(row[2])

    return metrics

# --- Main Metrics Collection ---

def collect_all_metrics(conn) -> Dict:
    """
    Collects all metrics and returns as a single dict.
    """
    all_metrics = {}

    print("Collecting metrics...")

    # Data Quality
    print("  - Data quality...")
    all_metrics.update(collect_data_quality_metrics(conn))

    # Model Performance
    print("  - Model performance...")
    all_metrics.update(collect_model_performance_metrics(conn))

    # System Health
    print("  - System health...")
    all_metrics.update(collect_system_health_metrics(conn))

    # Source Health
    print("  - Source health...")
    all_metrics.update(collect_source_health_metrics(conn))

    # HMM Distribution
    print("  - HMM state distribution...")
    all_metrics.update(collect_hmm_state_distribution(conn))

    print(f"✓ Collected {len(all_metrics)} metrics")

    return all_metrics

# --- Metrics Storage ---

def store_metrics(conn, metrics: Dict):
    """
    Stores metrics in monitoring_logs table.

    Each metric is stored as a separate row for time-series analysis.
    """
    with conn.cursor() as cursor:
        for metric_name, value in metrics.items():
            # Skip nested dicts (store them as JSON)
            if isinstance(value, dict):
                cursor.execute("""
                    INSERT INTO monitoring_logs (log_time, metric_name, metadata)
                    VALUES (NOW(), %s, %s::jsonb)
                """, (metric_name, json.dumps(value)))
            else:
                # Store scalar values
                try:
                    numeric_value = float(value) if not isinstance(value, bool) else (1.0 if value else 0.0)
                except (ValueError, TypeError):
                    # If not numeric, store in metadata
                    cursor.execute("""
                        INSERT INTO monitoring_logs (log_time, metric_name, metadata)
                        VALUES (NOW(), %s, %s::jsonb)
                    """, (metric_name, json.dumps({'value': str(value)})))
                    continue

                cursor.execute("""
                    INSERT INTO monitoring_logs (log_time, metric_name, value)
                    VALUES (NOW(), %s, %s)
                """, (metric_name, numeric_value))

    conn.commit()
    print(f"✓ Stored {len(metrics)} metrics to monitoring_logs")

# --- Cleanup Old Metrics ---

def cleanup_old_metrics(conn):
    """Removes metrics older than METRIC_RETENTION_DAYS."""
    with conn.cursor() as cursor:
        cursor.execute("""
            DELETE FROM monitoring_logs
            WHERE log_time < NOW() - interval '%s days'
        """, (METRIC_RETENTION_DAYS,))

        deleted = cursor.rowcount

    conn.commit()

    if deleted > 0:
        print(f"✓ Cleaned up {deleted} old metric records")

# --- Main Entry Point ---

def run_metrics_collector():
    """
    Main entry point for metrics collection.

    Run this periodically (e.g., every hour via cron).
    """
    print("=" * 60)
    print("Metrics Collector starting...")
    print("=" * 60)

    conn = None

    try:
        conn = get_db_connection()

        # Collect all metrics
        metrics = collect_all_metrics(conn)

        # Store metrics
        import json
        store_metrics(conn, metrics)

        # Cleanup old metrics
        cleanup_old_metrics(conn)

        print("=" * 60)
        print("Metrics collection complete!")
        print("=" * 60)

        return 0

    except Exception as e:
        print(f"ERROR: Metrics collection failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    import sys
    sys.exit(run_metrics_collector())
