"""
Observability Dashboard - Stock Hunter AI
==========================================
Comprehensive monitoring dashboard for system health, data quality, and model performance.

Displays:
- Data Quality Metrics (DLQ rates, validation, coverage)
- Model Performance (IC, Sharpe, predictions)
- System Health (queues, latency, backpressure)
- Source Health (scraper status, data freshness)
- HMM State Distribution (market regime)
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import psycopg2
import os

# --- Configuration ---
DB_URL = os.getenv("DB_URL", "postgresql://hunter:password@db:5432/hunter")

# --- Database Connection ---
@st.cache_resource
def get_db_connection():
    """Creates database connection (cached)."""
    return psycopg2.connect(DB_URL)

# --- Data Fetchers ---

@st.cache_data(ttl=300)  # Cache for 5 minutes
def fetch_latest_metrics():
    """Fetches the most recent metrics from monitoring_logs."""
    conn = get_db_connection()
    query = """
        WITH latest_metrics AS (
            SELECT
                metric_name,
                value,
                metadata,
                log_time,
                ROW_NUMBER() OVER (PARTITION BY metric_name ORDER BY log_time DESC) as rn
            FROM monitoring_logs
            WHERE log_time > NOW() - interval '1 hour'
        )
        SELECT metric_name, value, metadata, log_time
        FROM latest_metrics
        WHERE rn = 1
        ORDER BY metric_name
    """

    df = pd.read_sql(query, conn)
    return df

@st.cache_data(ttl=300)
def fetch_metric_timeseries(metric_name: str, hours: int = 24):
    """Fetches time-series data for a specific metric."""
    conn = get_db_connection()
    query = """
        SELECT log_time, value
        FROM monitoring_logs
        WHERE metric_name = %s
          AND log_time > NOW() - interval '%s hours'
        ORDER BY log_time
    """

    df = pd.read_sql(query, conn, params=(metric_name, hours))
    return df

@st.cache_data(ttl=300)
def fetch_hmm_distribution():
    """Fetches HMM state distribution over time."""
    conn = get_db_connection()
    query = """
        SELECT log_time, metric_name, value
        FROM monitoring_logs
        WHERE metric_name LIKE 'hmm_%_pct'
          AND log_time > NOW() - interval '7 days'
        ORDER BY log_time
    """

    df = pd.read_sql(query, conn)
    return df

@st.cache_data(ttl=300)
def fetch_top_failure_reasons():
    """Fetches top DLQ failure reasons."""
    conn = get_db_connection()
    query = """
        SELECT reason, COUNT(*) as count
        FROM task_q_dlq
        WHERE moved_at > NOW() - interval '7 days'
        GROUP BY reason
        ORDER BY count DESC
        LIMIT 10
    """

    df = pd.read_sql(query, conn)
    return df

# --- UI Layout ---
st.set_page_config(layout="wide", page_title="Observability Dashboard")
st.title("📊 Observability Dashboard")
st.caption("Real-time monitoring of system health, data quality, and model performance")

# Fetch latest metrics
try:
    latest_metrics = fetch_latest_metrics()

    if latest_metrics.empty:
        st.warning("No metrics available. Run the metrics collector: `python -m worker.monitor_metrics`")
        st.stop()

    # Convert to dict for easy lookup
    metrics_dict = {}
    for _, row in latest_metrics.iterrows():
        if pd.notna(row['value']):
            metrics_dict[row['metric_name']] = row['value']
        elif pd.notna(row['metadata']):
            metrics_dict[row['metric_name']] = row['metadata']

except Exception as e:
    st.error(f"Error fetching metrics: {e}")
    st.stop()

# --- Section 1: Data Quality ---
st.header("1️⃣ Data Quality Metrics")

col1, col2, col3, col4 = st.columns(4)

with col1:
    dlq_rate = metrics_dict.get('dlq_rate', 0)
    delta_color = "inverse"  # Lower is better
    st.metric(
        "DLQ Rate",
        f"{dlq_rate:.1f}%",
        delta=None,
        help="Percentage of tasks moved to Dead Letter Queue (lower is better)"
    )

with col2:
    feature_completeness = metrics_dict.get('feature_completeness_pct', 0)
    st.metric(
        "Feature Completeness",
        f"{feature_completeness:.1f}%",
        delta=None,
        help="Percentage of symbols with all 4 features (HMM, Hunter, Froth, Macro)"
    )

with col3:
    label_coverage = metrics_dict.get('label_coverage_pct', 0)
    st.metric(
        "Label Coverage",
        f"{label_coverage:.1f}%",
        delta=None,
        help="Percentage of symbols with probabilistic labels"
    )

with col4:
    al_pending = metrics_dict.get('al_queue_pending', 0)
    st.metric(
        "AL Queue (Pending)",
        f"{int(al_pending)}",
        delta=None,
        help="Number of samples waiting for human review"
    )

# DLQ Rate Time Series
st.subheader("DLQ Rate Trend (24h)")
dlq_ts = fetch_metric_timeseries('dlq_rate', hours=24)

if not dlq_ts.empty:
    fig = px.line(dlq_ts, x='log_time', y='value', title='DLQ Rate Over Time')
    fig.update_layout(xaxis_title='Time', yaxis_title='DLQ Rate (%)')
    fig.add_hline(y=10.0, line_dash="dash", line_color="red",
                  annotation_text="Warning Threshold (10%)")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No DLQ rate time-series data available")

# Top Failure Reasons
st.subheader("Top DLQ Failure Reasons (Last 7 Days)")
failure_reasons = fetch_top_failure_reasons()

if not failure_reasons.empty:
    fig = px.bar(failure_reasons, x='reason', y='count',
                 title='Most Common Sanity Rule Failures')
    fig.update_layout(xaxis_title='Failure Reason', yaxis_title='Count')
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No failure data available")

st.divider()

# --- Section 2: Model Performance ---
st.header("2️⃣ Model Performance Metrics")

col1, col2, col3, col4 = st.columns(4)

with col1:
    model_ic = metrics_dict.get('model_ic', 0)
    st.metric(
        "Information Coefficient (IC)",
        f"{model_ic:.3f}",
        delta=None,
        help="Spearman correlation between predictions and outcomes (higher is better)"
    )

with col2:
    model_sharpe = metrics_dict.get('model_sharpe', 0)
    st.metric(
        "Sharpe Ratio",
        f"{model_sharpe:.2f}",
        delta=None,
        help="Risk-adjusted returns (higher is better, >1.0 is good)"
    )

with col3:
    model_dd = metrics_dict.get('model_max_drawdown', 0)
    st.metric(
        "Max Drawdown",
        f"{model_dd:.1%}",
        delta=None,
        delta_color="inverse",
        help="Maximum peak-to-trough decline (lower is better)"
    )

with col4:
    model_age = metrics_dict.get('model_age_days', 999)
    st.metric(
        "Model Age",
        f"{int(model_age)} days",
        delta=None,
        help="Days since last model training"
    )

# Model metrics time series
st.subheader("Model IC Trend (7 days)")
ic_ts = fetch_metric_timeseries('model_ic', hours=24*7)

if not ic_ts.empty:
    fig = px.line(ic_ts, x='log_time', y='value', title='Information Coefficient Over Time')
    fig.update_layout(xaxis_title='Time', yaxis_title='IC')
    fig.add_hline(y=0.05, line_dash="dash", line_color="green",
                  annotation_text="Target IC (0.05)")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No IC time-series data available")

st.divider()

# --- Section 3: System Health ---
st.header("3️⃣ System Health Metrics")

col1, col2, col3, col4 = st.columns(4)

with col1:
    task_q_ready = metrics_dict.get('task_q_ready', 0)
    st.metric(
        "Task Queue (Ready)",
        f"{int(task_q_ready)}",
        delta=None,
        help="Number of tasks ready to process"
    )

with col2:
    backpressure = metrics_dict.get('backpressure_enabled', False)
    st.metric(
        "Backpressure",
        "🔴 ENABLED" if backpressure else "🟢 DISABLED",
        delta=None,
        help="Scraper slowdown to prevent queue overflow"
    )

with col3:
    latency = metrics_dict.get('avg_processing_latency_seconds', 0)
    st.metric(
        "Avg Processing Latency",
        f"{latency:.0f}s",
        delta=None,
        help="Average time from data ingestion to Silver layer"
    )

with col4:
    db_size = metrics_dict.get('database_size', 'N/A')
    st.metric(
        "Database Size",
        db_size,
        delta=None,
        help="Total database size"
    )

# Queue size time series
st.subheader("Task Queue Size Trend (24h)")
queue_ts = fetch_metric_timeseries('task_q_ready', hours=24)

if not queue_ts.empty:
    fig = px.area(queue_ts, x='log_time', y='value', title='Task Queue Size Over Time')
    fig.update_layout(xaxis_title='Time', yaxis_title='Queue Size')
    fig.add_hline(y=10000, line_dash="dash", line_color="red",
                  annotation_text="Backpressure Threshold (10K)")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No queue size time-series data available")

st.divider()

# --- Section 4: Source Health ---
st.header("4️⃣ Source Health & Data Freshness")

col1, col2, col3 = st.columns(3)

with col1:
    price_staleness = metrics_dict.get('price_data_staleness_hours', 999)
    status = "🟢 Fresh" if price_staleness < 48 else "🟡 Stale" if price_staleness < 96 else "🔴 Very Stale"
    st.metric(
        "Price Data Staleness",
        f"{int(price_staleness)}h {status}",
        delta=None,
        help="Hours since latest price data"
    )

with col2:
    news_staleness = metrics_dict.get('news_data_staleness_hours', 999)
    status = "🟢 Fresh" if news_staleness < 24 else "🟡 Stale" if news_staleness < 72 else "🔴 Very Stale"
    st.metric(
        "News Data Staleness",
        f"{int(news_staleness)}h {status}",
        delta=None,
        help="Hours since latest news data"
    )

with col3:
    unique_sources = metrics_dict.get('unique_news_sources_7d', 0)
    st.metric(
        "Unique News Sources (7d)",
        f"{int(unique_sources)}",
        delta=None,
        help="Number of different news sources scraped in last 7 days"
    )

st.divider()

# --- Section 5: Market Regime (HMM Distribution) ---
st.header("5️⃣ Market Regime Distribution")

st.caption("Percentage of stocks in each HMM state (market regime)")

hmm_dist = fetch_hmm_distribution()

if not hmm_dist.empty:
    # Pivot for stacked area chart
    hmm_pivot = hmm_dist.pivot(index='log_time', columns='metric_name', values='value')

    # Rename columns for clarity
    column_mapping = {
        'hmm_accumulation_pct': 'Accumulation',
        'hmm_breakout_pct': 'Breakout',
        'hmm_euphoria_pct': 'Euphoria',
        'hmm_distribution_pct': 'Distribution'
    }

    hmm_pivot = hmm_pivot.rename(columns=column_mapping)

    # Create stacked area chart
    fig = go.Figure()

    for col in hmm_pivot.columns:
        fig.add_trace(go.Scatter(
            x=hmm_pivot.index,
            y=hmm_pivot[col],
            name=col,
            mode='lines',
            stackgroup='one',
            fillcolor=px.colors.qualitative.Plotly[list(hmm_pivot.columns).index(col)]
        ))

    fig.update_layout(
        title='HMM State Distribution Over Time (Stacked %)',
        xaxis_title='Time',
        yaxis_title='Percentage of Stocks (%)',
        hovermode='x unified',
        yaxis=dict(range=[0, 100])
    )

    st.plotly_chart(fig, use_container_width=True)

    # Current distribution (pie chart)
    latest_hmm = hmm_pivot.iloc[-1] if not hmm_pivot.empty else None

    if latest_hmm is not None:
        col1, col2 = st.columns([1, 1])

        with col1:
            fig_pie = px.pie(
                values=latest_hmm.values,
                names=latest_hmm.index,
                title='Current HMM State Distribution'
            )
            st.plotly_chart(fig_pie, use_container_width=True)

        with col2:
            st.subheader("Interpretation")
            st.markdown("""
            **Healthy Market** (40-50% Accumulation, 20-30% Breakout):
            - Most stocks building bases
            - Good entry opportunities

            **Risky Market** (>30% Euphoria, >20% Distribution):
            - Overextended stocks
            - Consider taking profits

            **Current Status:**
            """)

            accum_pct = latest_hmm.get('Accumulation', 0)
            euphoria_pct = latest_hmm.get('Euphoria', 0)

            if accum_pct > 40:
                st.success("✅ Healthy - Many stocks in accumulation")
            elif euphoria_pct > 30:
                st.error("⚠️ Risky - High euphoria levels")
            else:
                st.info("ℹ️ Neutral - Mixed market conditions")
else:
    st.info("No HMM distribution data available")

st.divider()

# --- Footer: Last Update ---
if not latest_metrics.empty:
    last_update = latest_metrics['log_time'].max()
    st.caption(f"📅 Last metrics update: {last_update}")

# Refresh button
if st.button("🔄 Refresh Metrics"):
    st.cache_data.clear()
    st.rerun()
