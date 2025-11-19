"""
Legal Guard - robots.txt Compliance Checker
============================================
Implements PRD Story 1.1 AC7: "Mọi Spider phải có cơ chế kiểm tra robots.txt
và tuân thủ rate limit khai báo."

This module ensures Stock Hunter AI scraper complies with:
1. robots.txt directives
2. Crawl-delay specifications
3. Legal/ToS requirements
"""

import urllib.parse
import urllib.request
import urllib.robotparser
from typing import Tuple, Optional
from datetime import datetime, timedelta
import psycopg2
import logging

logger = logging.getLogger(__name__)

USER_AGENT = "StockHunterBot/1.0 (+https://github.com/yourorg/stock-hunter)"
CACHE_TTL_DAYS = 7  # Refresh robots.txt weekly


class LegalGuard:
    """
    Checks and enforces robots.txt compliance before scraping.

    Caches robots.txt in database to avoid repeated fetches.
    """

    def __init__(self, db_connection):
        """
        Initialize Legal Guard with database connection.

        Args:
            db_connection: PostgreSQL connection for caching robots.txt
        """
        self.conn = db_connection
        self.user_agent = USER_AGENT

    def can_fetch(self, url: str) -> Tuple[bool, str, int]:
        """
        Check if we're allowed to fetch the given URL per robots.txt.

        Args:
            url: The URL to check (e.g., "https://example.com/news/article")

        Returns:
            Tuple of (allowed: bool, reason: str, crawl_delay_seconds: int)

        Examples:
            >>> guard = LegalGuard(conn)
            >>> allowed, reason, delay = guard.can_fetch("https://example.com/news")
            >>> if allowed:
            ...     time.sleep(delay)
            ...     # proceed with scraping
        """
        try:
            # Parse domain from URL
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc

            if not domain:
                return False, "Invalid URL - no domain", 0

            # Check cache first
            cached = self._get_cached_robots(domain)

            if cached:
                is_allowed, crawl_delay = cached
                if is_allowed:
                    return True, "Allowed per cached robots.txt", crawl_delay
                else:
                    return False, f"Disallowed per robots.txt for {self.user_agent}", crawl_delay

            # Fetch and parse robots.txt
            robots_url = f"{parsed.scheme}://{domain}/robots.txt"
            is_allowed, crawl_delay, robots_content = self._fetch_and_parse_robots(robots_url, url)

            # Cache result
            self._cache_robots(domain, robots_content, crawl_delay, is_allowed)

            if is_allowed:
                return True, f"Allowed per robots.txt (crawl-delay: {crawl_delay}s)", crawl_delay
            else:
                return False, f"Disallowed per robots.txt for {self.user_agent}", crawl_delay

        except Exception as e:
            logger.error(f"Legal guard error for {url}: {e}")
            # Fail-safe: allow with conservative 2s delay
            return True, f"robots.txt check failed, allowing with conservative delay: {e}", 2

    def _get_cached_robots(self, domain: str) -> Optional[Tuple[bool, int]]:
        """
        Retrieve cached robots.txt rules from database.

        Args:
            domain: The domain to check (e.g., "example.com")

        Returns:
            Tuple of (is_allowed, crawl_delay) or None if not cached/expired
        """
        try:
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    SELECT is_fetch_allowed, crawl_delay_seconds
                    FROM robots_txt_cache
                    WHERE domain = %s
                      AND expires_at > NOW()
                """, (domain,))

                row = cursor.fetchone()
                if row:
                    return (row[0], row[1])
                else:
                    return None
        except Exception as e:
            logger.warning(f"Failed to read robots cache for {domain}: {e}")
            return None

    def _fetch_and_parse_robots(self, robots_url: str, target_url: str) -> Tuple[bool, int, str]:
        """
        Fetch robots.txt and parse it.

        Args:
            robots_url: URL to robots.txt (e.g., "https://example.com/robots.txt")
            target_url: The actual URL we want to scrape

        Returns:
            Tuple of (is_allowed, crawl_delay_seconds, robots_content)
        """
        try:
            # Fetch robots.txt
            req = urllib.request.Request(
                robots_url,
                headers={"User-Agent": self.user_agent}
            )

            with urllib.request.urlopen(req, timeout=5) as response:
                robots_content = response.read().decode('utf-8')

            # Parse using urllib.robotparser
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(robots_content.splitlines())

            # Check if our user agent can fetch the target URL
            is_allowed = rp.can_fetch(self.user_agent, target_url)

            # Extract Crawl-delay (not standardized, best-effort)
            crawl_delay = self._extract_crawl_delay(robots_content)

            return is_allowed, crawl_delay, robots_content

        except urllib.error.HTTPError as e:
            if e.code == 404:
                # No robots.txt = allowed with default 1s delay
                logger.info(f"No robots.txt found at {robots_url}, allowing with 1s delay")
                return True, 1, ""
            else:
                logger.warning(f"HTTP error fetching {robots_url}: {e}")
                return True, 2, ""  # Conservative default
        except Exception as e:
            logger.warning(f"Error parsing robots.txt from {robots_url}: {e}")
            return True, 2, ""  # Conservative default

    def _extract_crawl_delay(self, robots_content: str) -> int:
        """
        Extract Crawl-delay directive from robots.txt.

        Note: Crawl-delay is not part of the robots.txt standard but is
        widely supported. Format: "Crawl-delay: 10"

        Args:
            robots_content: Full robots.txt content

        Returns:
            Crawl delay in seconds (default: 1)
        """
        try:
            for line in robots_content.splitlines():
                line = line.strip().lower()
                if line.startswith('crawl-delay'):
                    # Extract number after colon
                    parts = line.split(':')
                    if len(parts) >= 2:
                        delay = int(float(parts[1].strip()))
                        # Cap at reasonable max (60s)
                        return min(delay, 60)
        except Exception as e:
            logger.debug(f"Could not parse Crawl-delay: {e}")

        return 1  # Default 1 second

    def _cache_robots(self, domain: str, robots_content: str, crawl_delay: int, is_allowed: bool):
        """
        Cache robots.txt rules in database.

        Args:
            domain: The domain (e.g., "example.com")
            robots_content: Full robots.txt content
            crawl_delay: Extracted crawl delay in seconds
            is_allowed: Whether fetching is allowed
        """
        try:
            expires_at = datetime.now() + timedelta(days=CACHE_TTL_DAYS)

            with self.conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO robots_txt_cache
                        (domain, robots_content, crawl_delay_seconds, is_fetch_allowed, user_agent, last_fetched, expires_at)
                    VALUES (%s, %s, %s, %s, %s, NOW(), %s)
                    ON CONFLICT (domain)
                    DO UPDATE SET
                        robots_content = EXCLUDED.robots_content,
                        crawl_delay_seconds = EXCLUDED.crawl_delay_seconds,
                        is_fetch_allowed = EXCLUDED.is_fetch_allowed,
                        last_fetched = NOW(),
                        expires_at = EXCLUDED.expires_at
                """, (domain, robots_content, crawl_delay, is_allowed, self.user_agent, expires_at))

            self.conn.commit()
            logger.info(f"Cached robots.txt for {domain} (allowed={is_allowed}, delay={crawl_delay}s)")

        except Exception as e:
            logger.error(f"Failed to cache robots.txt for {domain}: {e}")
            self.conn.rollback()


# Convenience function for backward compatibility
def check_robots_txt(url: str, db_connection) -> Tuple[bool, str, int]:
    """
    Convenience function to check robots.txt compliance.

    Args:
        url: URL to check
        db_connection: PostgreSQL connection

    Returns:
        Tuple of (allowed, reason, crawl_delay_seconds)

    Example:
        >>> allowed, reason, delay = check_robots_txt("https://example.com/news", conn)
        >>> if allowed:
        ...     time.sleep(delay)
        ...     response = requests.get(url)
    """
    guard = LegalGuard(db_connection)
    return guard.can_fetch(url)
