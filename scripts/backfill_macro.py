import os
import psycopg
import pandas as pd
from datetime import date, timedelta
import numpy as np
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DB_URL")
if not DB_URL:
    raise ValueError("DB_URL environment variable is not set!")

def populate_dim_sector(conn):
    """Populates the dim_sector table with a predefined list of sectors."""
    logger.info("Populating dim_sector...")
    sectors = [
        ('FINANCIALS', 'Financial Services'),
        ('REAL_ESTATE', 'Real Estate'),
        ('IT', 'Information Technology'),
        ('CONSUMER_STAPLES', 'Consumer Staples'),
        ('INDUSTRIALS', 'Industrials'),
        ('HEALTHCARE', 'Health Care'),
        ('UTILITIES', 'Utilities'),
        ('MATERIALS', 'Materials')
    ]
    with conn.cursor() as cur:
        try:
            cur.executemany(
                "INSERT INTO dim_sector (sector, description) VALUES (%s, %s) ON CONFLICT (sector) DO NOTHING",
                sectors
            )
            conn.commit()
            logger.info(f"Successfully inserted/updated {cur.rowcount} sectors.")
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to populate dim_sector: {e}")
            raise

def populate_trade_calendar(conn, days=3*365):
    """Populates the trade_calendar for the last few years."""
    logger.info("Populating trade_calendar...")
    today = date.today()
    start_date = today - timedelta(days=days)
    # Generate all dates from start_date to today
    dates = pd.date_range(start_date, today, freq='D')
    # Filter for weekdays (Monday=0, Sunday=6)
    trading_dates = [(d.date(), d.dayofweek < 5) for d in dates]

    with conn.cursor() as cur:
        try:
            cur.executemany(
                "INSERT INTO trade_calendar (tdate, is_trading) VALUES (%s, %s) ON CONFLICT (tdate) DO NOTHING",
                trading_dates
            )
            conn.commit()
            logger.info(f"Successfully populated trade_calendar with {cur.rowcount} dates.")
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to populate trade_calendar: {e}")
            raise

def populate_macro_release_calendar(conn):
    """Populates macro_release_calendar with sample historical data."""
    logger.info("Populating macro_release_calendar...")
    # This is sample data. In a real scenario, this would come from a reliable source.
    releases = [
        (date(2023, 1, 15), 'POLICY_RATE', 'Central Bank Rate Decision'),
        (date(2023, 4, 15), 'POLICY_RATE', 'Central Bank Rate Decision'),
        (date(2023, 7, 15), 'POLICY_RATE', 'Central Bank Rate Decision'),
        (date(2023, 10, 15), 'POLICY_RATE', 'Central Bank Rate Decision'),
        (date(2024, 1, 15), 'POLICY_RATE', 'Central Bank Rate Decision'),
    ]
    with conn.cursor() as cur:
        try:
            cur.executemany(
                "INSERT INTO macro_release_calendar (release_date, metric, event) VALUES (%s, %s, %s) ON CONFLICT (release_date, metric) DO NOTHING",
                releases
            )
            conn.commit()
            logger.info(f"Successfully populated macro_release_calendar with {cur.rowcount} events.")
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to populate macro_release_calendar: {e}")
            raise

def generate_mock_price_data(conn, days=3*365):
    """Generates and inserts mock price data for sectors and the VN-Index."""
    logger.info("Generating mock price data...")
    with conn.cursor() as cur:
        # Create temporary tables
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_sector_prices (
                trade_date DATE NOT NULL,
                sector TEXT NOT NULL,
                close FLOAT NOT NULL,
                PRIMARY KEY (trade_date, sector)
            );
            CREATE TABLE IF NOT EXISTS daily_vnindex_prices (
                trade_date DATE NOT NULL PRIMARY KEY,
                close FLOAT NOT NULL
            );
        """)

        # Fetch sectors and trading dates
        cur.execute("SELECT sector FROM dim_sector")
        sectors = [row[0] for row in cur.fetchall()]
        cur.execute("SELECT tdate FROM trade_calendar WHERE is_trading AND tdate <= NOW()")
        trading_dates = [row[0] for row in cur.fetchall()]

        # Generate mock data
        sector_prices = []
        vnindex_prices = []
        base_price_vnindex = 1000
        base_prices_sector = {sector: np.random.uniform(80, 120) for sector in sectors}

        price_vnindex = base_price_vnindex
        prices_sector = base_prices_sector.copy()

        for tdate in sorted(trading_dates):
            # VNIndex
            price_vnindex *= (1 + np.random.normal(0.0005, 0.015))
            vnindex_prices.append((tdate, price_vnindex))
            # Sectors
            for sector in sectors:
                prices_sector[sector] *= (1 + np.random.normal(0.0006, 0.02))
                sector_prices.append((tdate, sector, prices_sector[sector]))

        # Insert data into temp tables
        cur.executemany("INSERT INTO daily_vnindex_prices (trade_date, close) VALUES (%s, %s) ON CONFLICT DO NOTHING", vnindex_prices)
        cur.executemany("INSERT INTO daily_sector_prices (trade_date, sector, close) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", sector_prices)
        conn.commit()
        logger.info("Mock price data generated.")


def populate_sector_perf(conn):
    """Calculates and backfills y_excess_20d into sector_perf."""
    logger.info("Populating sector_perf with historical y_excess_20d...")
    generate_mock_price_data(conn)

    with conn.cursor() as cur:
        try:
            # The calculation logic from the worker, adapted for backfilling
            sql_calc_y = """
            INSERT INTO sector_perf(as_of_date, sector, y_excess_20d)
            SELECT
                s.trade_date AS as_of_date,
                s.sector,
                (
                    (lead(s.close, 20) OVER (PARTITION BY s.sector ORDER BY s.trade_date)::float / s.close) - 1
                ) - (
                    (lead(i.close, 20) OVER (ORDER BY i.trade_date)::float / i.close) - 1
                ) AS y_excess_20d
            FROM
                daily_sector_prices s
            JOIN
                daily_vnindex_prices i ON i.trade_date = s.trade_date
            WHERE
                -- Ensure we have a 20-day future window for the calculation
                s.trade_date <= (SELECT MAX(trade_date) FROM daily_vnindex_prices) - INTERVAL '20 days'
            ON CONFLICT (as_of_date, sector) DO UPDATE SET
                y_excess_20d = EXCLUDED.y_excess_20d;
            """
            cur.execute(sql_calc_y)
            logger.info(f"Calculated and inserted {cur.rowcount} rows into sector_perf.")

            # Clean up temporary tables
            cur.execute("DROP TABLE daily_sector_prices; DROP TABLE daily_vnindex_prices;")
            logger.info("Cleaned up temporary price tables.")
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to populate sector_perf: {e}")
            raise


def main():
    """Main function to run all backfill steps."""
    try:
        with psycopg.connect(DB_URL) as conn:
            populate_dim_sector(conn)
            populate_trade_calendar(conn)
            populate_macro_release_calendar(conn)
            populate_sector_perf(conn)
            logger.info("Backfill script completed successfully!")
    except Exception as e:
        logger.error(f"An error occurred during the backfill process: {e}")

if __name__ == "__main__":
    main()
