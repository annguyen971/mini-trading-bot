"""
Tests for Scraper State Manager (Incremental Crawl)
====================================================
PRD Story 1.5: News Backfill & Incremental Engine
"""

import pytest
from unittest.mock import Mock
from scraper.state_manager import (
    ScraperStateManager,
    create_source_id,
    get_news_cursor,
    update_news_cursor,
    reset_all_cursors
)
from datetime import datetime, timezone


class TestScraperStateManager:
    """Test suite for ScraperStateManager."""

    @pytest.fixture
    def mock_db_conn(self):
        """Mock database connection."""
        conn = Mock()
        cursor = Mock()
        conn.cursor.return_value.__enter__.return_value = cursor
        conn.cursor.return_value.__exit__.return_value = None
        return conn

    @pytest.fixture
    def state_manager(self, mock_db_conn):
        """Create StateManager instance with mocked DB."""
        return ScraperStateManager(mock_db_conn)

    def test_get_cursor_exists(self, state_manager, mock_db_conn):
        """Test getting existing cursor."""
        cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("article_12345", datetime.now(timezone.utc))

        result = state_manager.get_cursor("vnstock_news_FPT")

        assert result == "article_12345"
        cursor.execute.assert_called_once()

    def test_get_cursor_not_exists(self, state_manager, mock_db_conn):
        """Test getting cursor when none exists (first run)."""
        cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None

        result = state_manager.get_cursor("vnstock_news_FPT")

        assert result is None

    def test_update_cursor(self, state_manager, mock_db_conn):
        """Test updating cursor after crawl."""
        cursor = mock_db_conn.cursor.return_value.__enter__.return_value

        result = state_manager.update_cursor("vnstock_news_FPT", "article_99999")

        assert result is True
        cursor.execute.assert_called_once()
        mock_db_conn.commit.assert_called_once()

    def test_update_cursor_upsert(self, state_manager, mock_db_conn):
        """Test cursor update uses UPSERT logic."""
        cursor = mock_db_conn.cursor.return_value.__enter__.return_value

        state_manager.update_cursor("vnstock_news_FPT", "new_cursor")

        sql = cursor.execute.call_args[0][0]
        assert "INSERT INTO scraper_state" in sql
        assert "ON CONFLICT" in sql

    def test_reset_cursor(self, state_manager, mock_db_conn):
        """Test resetting cursor for re-crawl."""
        cursor = mock_db_conn.cursor.return_value.__enter__.return_value

        result = state_manager.reset_cursor("vnstock_news_FPT")

        assert result is True
        cursor.execute.assert_called_once()
        sql = cursor.execute.call_args[0][0]
        assert "DELETE FROM scraper_state" in sql

    def test_get_all_states(self, state_manager, mock_db_conn):
        """Test getting all scraper states."""
        cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [
            ("vnstock_news_FPT", "cursor1", datetime.now(timezone.utc)),
            ("vnstock_news_TCB", "cursor2", datetime.now(timezone.utc))
        ]

        states = state_manager.get_all_states()

        assert len(states) == 2
        assert states[0]['source_id'] == "vnstock_news_FPT"
        assert states[1]['source_id'] == "vnstock_news_TCB"

    def test_is_stale_never_crawled(self, state_manager, mock_db_conn):
        """Test staleness check for never-crawled source."""
        cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None

        result = state_manager.is_stale("vnstock_news_FPT", max_hours=24)

        assert result is True  # Never crawled = stale

    def test_is_stale_recently_crawled(self, state_manager, mock_db_conn):
        """Test staleness check for recently crawled source."""
        cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        recent_time = datetime.now(timezone.utc)
        cursor.fetchone.return_value = (recent_time,)

        result = state_manager.is_stale("vnstock_news_FPT", max_hours=24)

        assert result is False  # Recently crawled = not stale

    def test_is_stale_old_crawl(self, state_manager, mock_db_conn):
        """Test staleness check for old crawl."""
        from datetime import timedelta
        cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        old_time = datetime.now(timezone.utc) - timedelta(hours=48)
        cursor.fetchone.return_value = (old_time,)

        result = state_manager.is_stale("vnstock_news_FPT", max_hours=24)

        assert result is True  # 48 hours old > 24 hour threshold


class TestHelperFunctions:
    """Test helper functions."""

    def test_create_source_id_news(self):
        """Test source ID creation for news."""
        source_id = create_source_id("vnstock", "FPT", "news")
        assert source_id == "vnstock_news_FPT"

    def test_create_source_id_price(self):
        """Test source ID creation for price data."""
        source_id = create_source_id("vnstock", "TCB", "price")
        assert source_id == "vnstock_price_TCB"

    def test_create_source_id_macro(self):
        """Test source ID creation for macro data."""
        source_id = create_source_id("sbv", "GDP", "macro")
        assert source_id == "sbv_macro_GDP"

    def test_get_news_cursor_convenience(self):
        """Test convenience wrapper for get_news_cursor."""
        # This is an integration test - would need real DB
        pytest.skip("Integration test - requires real DB")

    def test_update_news_cursor_convenience(self):
        """Test convenience wrapper for update_news_cursor."""
        # This is an integration test - would need real DB
        pytest.skip("Integration test - requires real DB")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
