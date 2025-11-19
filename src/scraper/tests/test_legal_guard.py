"""
Tests for Legal Guard (robots.txt compliance checker)
======================================================
PRD Story 1.1 AC7: Legal compliance testing
"""

import pytest
from unittest.mock import Mock, patch
from scraper.legal_guard import LegalGuard
from datetime import datetime, timedelta


class TestLegalGuard:
    """Test suite for Legal Guard functionality."""

    @pytest.fixture
    def mock_db_conn(self):
        """Mock database connection."""
        conn = Mock()
        cursor = Mock()
        conn.cursor.return_value.__enter__.return_value = cursor
        conn.cursor.return_value.__exit__.return_value = None
        return conn

    @pytest.fixture
    def legal_guard(self, mock_db_conn):
        """Create LegalGuard instance with mocked DB."""
        return LegalGuard(mock_db_conn)

    def test_can_fetch_allowed_url(self, legal_guard, mock_db_conn):
        """Test that allowed URLs return True."""
        # Mock cached robots.txt that allows fetching
        cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (True, 1)  # is_allowed=True, crawl_delay=1

        allowed, reason, delay = legal_guard.can_fetch("https://example.com/news")

        assert allowed is True
        assert delay == 1
        assert "Allowed" in reason

    def test_can_fetch_disallowed_url(self, legal_guard, mock_db_conn):
        """Test that disallowed URLs return False."""
        # Mock cached robots.txt that disallows fetching
        cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (False, 2)  # is_allowed=False, crawl_delay=2

        allowed, reason, delay = legal_guard.can_fetch("https://example.com/admin")

        assert allowed is False
        assert delay == 2
        assert "Disallowed" in reason

    def test_can_fetch_no_cache(self, legal_guard, mock_db_conn):
        """Test fetching when no cache exists (should fetch robots.txt)."""
        # Mock no cache
        cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None  # No cache

        # Mock robots.txt fetch (will use fail-safe in actual implementation)
        with patch('urllib.request.urlopen') as mock_urlopen:
            # Simulate 404 (no robots.txt)
            from urllib.error import HTTPError
            mock_urlopen.side_effect = HTTPError(None, 404, "Not Found", None, None)

            allowed, reason, delay = legal_guard.can_fetch("https://example.com/news")

            # No robots.txt = allowed by default
            assert allowed is True
            assert delay >= 1  # Should have default delay

    def test_extract_crawl_delay(self, legal_guard):
        """Test extracting Crawl-delay from robots.txt content."""
        robots_content = """
User-agent: *
Crawl-delay: 5
Disallow: /admin/
        """

        delay = legal_guard._extract_crawl_delay(robots_content)
        assert delay == 5

    def test_extract_crawl_delay_default(self, legal_guard):
        """Test default crawl delay when not specified."""
        robots_content = """
User-agent: *
Disallow: /admin/
        """

        delay = legal_guard._extract_crawl_delay(robots_content)
        assert delay == 1  # Default

    def test_extract_crawl_delay_max_cap(self, legal_guard):
        """Test that crawl delay is capped at 60 seconds."""
        robots_content = """
User-agent: *
Crawl-delay: 120
        """

        delay = legal_guard._extract_crawl_delay(robots_content)
        assert delay == 60  # Capped at max

    def test_cache_robots(self, legal_guard, mock_db_conn):
        """Test caching robots.txt data."""
        cursor = mock_db_conn.cursor.return_value.__enter__.return_value

        legal_guard._cache_robots(
            domain="example.com",
            robots_content="User-agent: *\nDisallow: /admin/",
            crawl_delay=2,
            is_allowed=True
        )

        # Verify INSERT was called
        cursor.execute.assert_called_once()
        sql = cursor.execute.call_args[0][0]
        assert "INSERT INTO robots_txt_cache" in sql
        assert "ON CONFLICT" in sql

        # Verify commit
        mock_db_conn.commit.assert_called_once()

    def test_invalid_url(self, legal_guard):
        """Test handling of invalid URLs."""
        allowed, reason, delay = legal_guard.can_fetch("not-a-valid-url")

        assert allowed is False
        assert "Invalid URL" in reason

    def test_fail_safe_on_error(self, legal_guard, mock_db_conn):
        """Test fail-safe behavior when robots.txt check fails."""
        # Mock database error
        cursor = mock_db_conn.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = Exception("DB connection failed")

        allowed, reason, delay = legal_guard.can_fetch("https://example.com/news")

        # Should fail-safe to allowed with conservative delay
        assert allowed is True
        assert delay == 2  # Conservative default
        assert "failed" in reason.lower()


# Integration test (requires actual DB - skip in CI)
@pytest.mark.integration
class TestLegalGuardIntegration:
    """Integration tests requiring real database."""

    def test_full_flow_with_real_db(self):
        """Test complete flow with real database (manual test)."""
        # This would require actual DB connection
        # Run manually during development
        pytest.skip("Integration test - run manually with real DB")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
