"""
Scraper State Manager - Incremental Crawl Support
==================================================
Implements PRD Story 1.5: News Backfill & Incremental Engine

Features:
- Tracks last crawl position per source (cursor/timestamp/ID)
- Enables incremental crawling (only fetch new data)
- Supports historical backfill with progress tracking
- Prevents duplicate scraping

PRD Requirements (AC1.5.1, AC1.5.2):
- Store cursor in scraper_state table
- Read state before crawling
- Update state after successful crawl
"""

from datetime import datetime, timezone
from typing import Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class ScraperStateManager:
    """
    Manages scraper state for incremental crawling.

    Uses scraper_state table to track:
    - last_cursor: Last ID/timestamp processed
    - last_run_at: When the scraper last ran
    """

    def __init__(self, db_connection):
        """
        Initialize state manager with database connection.

        Args:
            db_connection: PostgreSQL connection
        """
        self.conn = db_connection

    def get_cursor(self, source_id: str) -> Optional[str]:
        """
        Get the last cursor for a source.

        Args:
            source_id: Unique identifier for the source
                      (e.g., 'vnstock_news_FPT', 'cafef_news')

        Returns:
            Last cursor value (ID/timestamp), or None if never crawled

        Example:
            >>> manager = ScraperStateManager(conn)
            >>> cursor = manager.get_cursor('vnstock_news_FPT')
            >>> if cursor:
            ...     # Fetch only articles after this cursor
            ...     articles = source.fetch_news_since(cursor)
            ... else:
            ...     # First run - fetch all available
            ...     articles = source.fetch_all_news()
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    SELECT last_cursor, last_run_at
                    FROM scraper_state
                    WHERE source_id = %s
                """, (source_id,))

                row = cur.fetchone()

                if row:
                    last_cursor, last_run_at = row
                    logger.info(f"State for {source_id}: cursor={last_cursor}, last_run={last_run_at}")
                    return last_cursor
                else:
                    logger.info(f"No state found for {source_id} - first run")
                    return None

        except Exception as e:
            logger.error(f"Failed to get cursor for {source_id}: {e}")
            return None

    def update_cursor(self, source_id: str, new_cursor: str) -> bool:
        """
        Update the cursor for a source after successful crawl.

        Args:
            source_id: Source identifier
            new_cursor: New cursor value (latest ID/timestamp processed)

        Returns:
            True if successful, False otherwise

        Example:
            >>> manager = ScraperStateManager(conn)
            >>> articles = fetch_news()
            >>> if articles:
            ...     latest_id = articles[-1]['id']
            ...     manager.update_cursor('vnstock_news_FPT', latest_id)
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO scraper_state (source_id, last_cursor, last_run_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (source_id)
                    DO UPDATE SET
                        last_cursor = EXCLUDED.last_cursor,
                        last_run_at = NOW()
                """, (source_id, new_cursor))

            self.conn.commit()
            logger.info(f"Updated cursor for {source_id}: {new_cursor}")
            return True

        except Exception as e:
            logger.error(f"Failed to update cursor for {source_id}: {e}")
            self.conn.rollback()
            return False

    def reset_cursor(self, source_id: str) -> bool:
        """
        Reset cursor for a source (for re-crawling from scratch).

        Args:
            source_id: Source identifier

        Returns:
            True if successful, False otherwise

        Use Case:
            - Source API changed format
            - Need to re-backfill historical data
            - Manual reset requested by admin
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM scraper_state
                    WHERE source_id = %s
                """, (source_id,))

            self.conn.commit()
            logger.info(f"Reset cursor for {source_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to reset cursor for {source_id}: {e}")
            self.conn.rollback()
            return False

    def get_all_states(self) -> list:
        """
        Get all scraper states (for monitoring/debugging).

        Returns:
            List of dicts with source_id, last_cursor, last_run_at

        Example:
            >>> manager = ScraperStateManager(conn)
            >>> states = manager.get_all_states()
            >>> for state in states:
            ...     print(f"{state['source_id']}: {state['last_run_at']}")
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    SELECT source_id, last_cursor, last_run_at
                    FROM scraper_state
                    ORDER BY last_run_at DESC
                """)

                rows = cur.fetchall()
                return [
                    {
                        'source_id': row[0],
                        'last_cursor': row[1],
                        'last_run_at': row[2]
                    }
                    for row in rows
                ]

        except Exception as e:
            logger.error(f"Failed to get all states: {e}")
            return []

    def is_stale(self, source_id: str, max_hours: int = 24) -> bool:
        """
        Check if a source hasn't been crawled recently.

        Args:
            source_id: Source identifier
            max_hours: Maximum hours before considered stale (default: 24)

        Returns:
            True if stale (needs crawling), False otherwise

        Use Case:
            Monitor which sources need attention
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    SELECT last_run_at
                    FROM scraper_state
                    WHERE source_id = %s
                """, (source_id,))

                row = cur.fetchone()

                if not row:
                    return True  # Never crawled = stale

                last_run_at = row[0]

                if last_run_at.tzinfo is None:
                    last_run_at = last_run_at.replace(tzinfo=timezone.utc)

                now = datetime.now(timezone.utc)
                hours_since_last_run = (now - last_run_at).total_seconds() / 3600

                return hours_since_last_run > max_hours

        except Exception as e:
            logger.error(f"Failed to check staleness for {source_id}: {e}")
            return True  # Assume stale on error


def create_source_id(source_name: str, symbol: str, data_type: str = 'news') -> str:
    """
    Create a standardized source ID for state tracking.

    Args:
        source_name: Name of the source (e.g., 'vnstock', 'cafef')
        symbol: Stock symbol (e.g., 'FPT')
        data_type: Type of data ('news', 'price', 'macro')

    Returns:
        Standardized source_id string

    Example:
        >>> create_source_id('vnstock', 'FPT', 'news')
        'vnstock_news_FPT'

        >>> create_source_id('cafef', 'TCB', 'news')
        'cafef_news_TCB'
    """
    return f"{source_name}_{data_type}_{symbol}"


# Convenience functions for common operations

def get_news_cursor(conn, source_name: str, symbol: str) -> Optional[str]:
    """
    Get cursor for news crawling (convenience wrapper).

    Example:
        >>> cursor = get_news_cursor(conn, 'vnstock', 'FPT')
        >>> news = fetch_news_since(cursor)
    """
    source_id = create_source_id(source_name, symbol, 'news')
    manager = ScraperStateManager(conn)
    return manager.get_cursor(source_id)


def update_news_cursor(conn, source_name: str, symbol: str, new_cursor: str) -> bool:
    """
    Update cursor for news crawling (convenience wrapper).

    Example:
        >>> articles = fetch_news()
        >>> if articles:
        ...     latest_id = articles[-1]['id']
        ...     update_news_cursor(conn, 'vnstock', 'FPT', latest_id)
    """
    source_id = create_source_id(source_name, symbol, 'news')
    manager = ScraperStateManager(conn)
    return manager.update_cursor(source_id, new_cursor)


def reset_all_cursors(conn) -> int:
    """
    Reset all cursors (for complete re-crawl).

    Returns:
        Number of cursors reset

    WARNING: Use with caution - forces re-crawl of all sources
    """
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM scraper_state")
            count = cur.rowcount
        conn.commit()
        logger.info(f"Reset {count} cursors")
        return count
    except Exception as e:
        logger.error(f"Failed to reset all cursors: {e}")
        conn.rollback()
        return 0
