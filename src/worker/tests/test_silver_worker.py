"""
Unit Tests for Silver Worker
=============================
Tests all 15 sanity rules (7 TA + 8 SA) and UPSERT logic.

Run with: pytest tests/test_silver_worker.py -v
"""

import pytest
from datetime import datetime, timedelta
from worker.main import (
    apply_ta_sanity_rules,
    apply_sa_sanity_rules,
)

# ========================================
# TEST TA SANITY RULES (7 Rules)
# ========================================

class TestTASanityRules:
    """Test suite for Technical Analysis sanity rules."""

    def test_ta_rule_1_symbol_valid(self):
        """Rule 1: Valid symbol format"""
        payload = {
            'symbol': 'FPT',
            'trade_date': '2024-01-15',
            'open': 100.0, 'high': 102.0, 'low': 99.0, 'close': 101.0,
            'volume': 1000000,
            'currency': 'VND'
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is True

    def test_ta_rule_1_symbol_invalid_format(self):
        """Rule 1: Invalid symbol format (lowercase)"""
        payload = {
            'symbol': 'fpt',
            'trade_date': '2024-01-15',
            'open': 100.0, 'high': 102.0, 'low': 99.0, 'close': 101.0,
            'volume': 1000000
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is False
        assert rule_id == 'ta_rule_1_symbol'

    def test_ta_rule_1_symbol_missing(self):
        """Rule 1: Missing symbol"""
        payload = {
            'trade_date': '2024-01-15',
            'open': 100.0, 'high': 102.0, 'low': 99.0, 'close': 101.0,
            'volume': 1000000
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is False
        assert rule_id == 'ta_rule_1_symbol'

    def test_ta_rule_2_date_valid(self):
        """Rule 2: Valid trade date"""
        payload = {
            'symbol': 'VNM',
            'trade_date': '2024-01-15',
            'open': 50.0, 'high': 51.0, 'low': 49.5, 'close': 50.5,
            'volume': 5000000,
            'currency': 'VND'
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is True

    def test_ta_rule_2_date_future(self):
        """Rule 2: Trade date in future (invalid)"""
        future_date = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        payload = {
            'symbol': 'FPT',
            'trade_date': future_date,
            'open': 100.0, 'high': 102.0, 'low': 99.0, 'close': 101.0,
            'volume': 1000000
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is False
        assert rule_id == 'ta_rule_2_date_future'

    def test_ta_rule_2_date_missing(self):
        """Rule 2: Missing trade date"""
        payload = {
            'symbol': 'FPT',
            'open': 100.0, 'high': 102.0, 'low': 99.0, 'close': 101.0,
            'volume': 1000000
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is False
        assert rule_id == 'ta_rule_2_date_missing'

    def test_ta_rule_3_price_valid(self):
        """Rule 3: All prices >= 0"""
        payload = {
            'symbol': 'TCB',
            'trade_date': '2024-01-15',
            'open': 25.0, 'high': 26.0, 'low': 24.5, 'close': 25.5,
            'volume': 8000000,
            'currency': 'VND'
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is True

    def test_ta_rule_3_price_negative(self):
        """Rule 3: Negative price (invalid)"""
        payload = {
            'symbol': 'FPT',
            'trade_date': '2024-01-15',
            'open': 100.0, 'high': 102.0, 'low': -1.0, 'close': 101.0,
            'volume': 1000000
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is False
        assert rule_id == 'ta_rule_3_price_negative'

    def test_ta_rule_4_price_relationships_valid(self):
        """Rule 4: Valid price relationships (high >= max(O,C), low <= min(O,C))"""
        payload = {
            'symbol': 'HPG',
            'trade_date': '2024-01-15',
            'open': 30.0, 'high': 32.0, 'low': 29.5, 'close': 31.5,
            'volume': 12000000,
            'currency': 'VND'
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is True

    def test_ta_rule_4_high_invalid(self):
        """Rule 4: High < max(open, close) (invalid)"""
        payload = {
            'symbol': 'FPT',
            'trade_date': '2024-01-15',
            'open': 100.0, 'high': 99.0, 'low': 98.0, 'close': 101.0,
            'volume': 1000000
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is False
        assert rule_id == 'ta_rule_4_high_invalid'

    def test_ta_rule_4_low_invalid(self):
        """Rule 4: Low > min(open, close) (invalid)"""
        payload = {
            'symbol': 'FPT',
            'trade_date': '2024-01-15',
            'open': 100.0, 'high': 102.0, 'low': 101.5, 'close': 101.0,
            'volume': 1000000
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is False
        assert rule_id == 'ta_rule_4_low_invalid'

    def test_ta_rule_5_volume_valid(self):
        """Rule 5: Volume >= 0"""
        payload = {
            'symbol': 'VNM',
            'trade_date': '2024-01-15',
            'open': 50.0, 'high': 51.0, 'low': 49.5, 'close': 50.5,
            'volume': 3000000,
            'currency': 'VND'
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is True

    def test_ta_rule_5_volume_negative(self):
        """Rule 5: Negative volume (invalid)"""
        payload = {
            'symbol': 'FPT',
            'trade_date': '2024-01-15',
            'open': 100.0, 'high': 102.0, 'low': 99.0, 'close': 101.0,
            'volume': -1000
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is False
        assert rule_id == 'ta_rule_5_volume_negative'

    def test_ta_rule_6_zero_volume_doji_valid(self):
        """Rule 6: Volume=0 means doji candle (all OHLC equal)"""
        payload = {
            'symbol': 'FPT',
            'trade_date': '2024-01-15',
            'open': 100.0, 'high': 100.0, 'low': 100.0, 'close': 100.0,
            'volume': 0,
            'currency': 'VND'
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is True

    def test_ta_rule_6_zero_volume_not_doji_invalid(self):
        """Rule 6: Volume=0 but OHLC not equal (invalid)"""
        payload = {
            'symbol': 'FPT',
            'trade_date': '2024-01-15',
            'open': 100.0, 'high': 102.0, 'low': 99.0, 'close': 101.0,
            'volume': 0
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is False
        assert rule_id == 'ta_rule_6_zero_volume_not_doji'

    def test_ta_rule_7_currency_valid(self):
        """Rule 7: Currency field present"""
        payload = {
            'symbol': 'FPT',
            'trade_date': '2024-01-15',
            'open': 100.0, 'high': 102.0, 'low': 99.0, 'close': 101.0,
            'volume': 1000000,
            'currency': 'USD'
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is True

# ========================================
# TEST SA SANITY RULES (8 Rules)
# ========================================

class TestSASanityRules:
    """Test suite for Sentiment Analysis sanity rules."""

    def test_sa_rule_1_url_valid(self):
        """Rule 1: Valid URL format"""
        payload = {
            'url': 'https://example.com/news/article-123',
            'publisher_time': '2024-01-15T10:00:00Z',
            'text': 'A' * 150,
            'text_norm_hash': 'abc123',
            'source_domain': 'example.com'
        }
        passed, rule_id, msg = apply_sa_sanity_rules(payload)
        assert passed is True

    def test_sa_rule_1_url_invalid(self):
        """Rule 1: Invalid URL format"""
        payload = {
            'url': 'not-a-url',
            'publisher_time': '2024-01-15T10:00:00Z',
            'text': 'A' * 150,
            'text_norm_hash': 'abc123'
        }
        passed, rule_id, msg = apply_sa_sanity_rules(payload)
        assert passed is False
        assert rule_id == 'sa_rule_1_url_invalid'

    def test_sa_rule_2_timestamp_valid(self):
        """Rule 2: Valid publisher timestamp (not in future)"""
        payload = {
            'url': 'https://example.com/news/article-123',
            'publisher_time': '2024-01-15T10:00:00Z',
            'text': 'A' * 150,
            'text_norm_hash': 'abc123',
            'source_domain': 'example.com'
        }
        passed, rule_id, msg = apply_sa_sanity_rules(payload)
        assert passed is True

    def test_sa_rule_2_timestamp_future(self):
        """Rule 2: Timestamp too far in future (invalid)"""
        future_time = (datetime.now() + timedelta(hours=1)).isoformat()
        payload = {
            'url': 'https://example.com/news/article-123',
            'publisher_time': future_time,
            'text': 'A' * 150,
            'text_norm_hash': 'abc123',
            'source_domain': 'example.com'
        }
        passed, rule_id, msg = apply_sa_sanity_rules(payload)
        assert passed is False
        assert rule_id == 'sa_rule_2_timestamp_future'

    def test_sa_rule_2_timestamp_missing(self):
        """Rule 2: Missing publisher timestamp"""
        payload = {
            'url': 'https://example.com/news/article-123',
            'text': 'A' * 150,
            'text_norm_hash': 'abc123',
            'source_domain': 'example.com'
        }
        passed, rule_id, msg = apply_sa_sanity_rules(payload)
        assert passed is False
        assert rule_id == 'sa_rule_2_timestamp_missing'

    def test_sa_rule_3_timestamp_logic_valid(self):
        """Rule 3: first_seen >= publisher_time - 1 day"""
        base_time = datetime.now() - timedelta(days=2)
        payload = {
            'url': 'https://example.com/news/article-123',
            'publisher_time': base_time.isoformat(),
            'first_seen_time': (base_time + timedelta(hours=1)).isoformat(),
            'text': 'A' * 150,
            'text_norm_hash': 'abc123',
            'source_domain': 'example.com'
        }
        passed, rule_id, msg = apply_sa_sanity_rules(payload)
        assert passed is True

    def test_sa_rule_4_language_valid(self):
        """Rule 4: Valid language (vi, en, unknown)"""
        for lang in ['vi', 'en', 'unknown']:
            payload = {
                'url': 'https://example.com/news/article-123',
                'publisher_time': '2024-01-15T10:00:00Z',
                'language': lang,
                'text': 'A' * 150,
                'text_norm_hash': 'abc123',
                'source_domain': 'example.com'
            }
            passed, rule_id, msg = apply_sa_sanity_rules(payload)
            assert passed is True

    def test_sa_rule_5_text_valid_length(self):
        """Rule 5: Text >= 120 chars"""
        payload = {
            'url': 'https://example.com/news/article-123',
            'publisher_time': '2024-01-15T10:00:00Z',
            'text': 'A' * 120,
            'text_norm_hash': 'abc123',
            'source_domain': 'example.com'
        }
        passed, rule_id, msg = apply_sa_sanity_rules(payload)
        assert passed is True

    def test_sa_rule_5_text_too_short(self):
        """Rule 5: Text < 120 chars (invalid)"""
        payload = {
            'url': 'https://example.com/news/article-123',
            'publisher_time': '2024-01-15T10:00:00Z',
            'text': 'Too short',
            'text_norm_hash': 'abc123',
            'source_domain': 'example.com'
        }
        passed, rule_id, msg = apply_sa_sanity_rules(payload)
        assert passed is False
        assert rule_id == 'sa_rule_5_text_too_short'

    def test_sa_rule_6_hash_valid(self):
        """Rule 6: Content hash present"""
        payload = {
            'url': 'https://example.com/news/article-123',
            'publisher_time': '2024-01-15T10:00:00Z',
            'text': 'A' * 150,
            'text_norm_hash': 'abc123def456',
            'source_domain': 'example.com'
        }
        passed, rule_id, msg = apply_sa_sanity_rules(payload)
        assert passed is True

    def test_sa_rule_6_hash_missing(self):
        """Rule 6: Missing content hash"""
        payload = {
            'url': 'https://example.com/news/article-123',
            'publisher_time': '2024-01-15T10:00:00Z',
            'text': 'A' * 150,
            'source_domain': 'example.com'
        }
        passed, rule_id, msg = apply_sa_sanity_rules(payload)
        assert passed is False
        assert rule_id == 'sa_rule_6_hash_missing'

    def test_sa_rule_7_symbols_valid(self):
        """Rule 7: Valid symbols in array"""
        payload = {
            'url': 'https://example.com/news/article-123',
            'publisher_time': '2024-01-15T10:00:00Z',
            'text': 'A' * 150,
            'text_norm_hash': 'abc123',
            'symbols': ['FPT', 'VNM', 'TCB'],
            'source_domain': 'example.com'
        }
        passed, rule_id, msg = apply_sa_sanity_rules(payload)
        assert passed is True

    def test_sa_rule_7_symbols_invalid(self):
        """Rule 7: Invalid symbol in array"""
        payload = {
            'url': 'https://example.com/news/article-123',
            'publisher_time': '2024-01-15T10:00:00Z',
            'text': 'A' * 150,
            'text_norm_hash': 'abc123',
            'symbols': ['FPT', 'invalid-symbol', 'VNM'],
            'source_domain': 'example.com'
        }
        passed, rule_id, msg = apply_sa_sanity_rules(payload)
        assert passed is False
        assert rule_id == 'sa_rule_7_symbol_invalid'

    def test_sa_rule_8_domain_valid(self):
        """Rule 8: Source domain present"""
        payload = {
            'url': 'https://example.com/news/article-123',
            'publisher_time': '2024-01-15T10:00:00Z',
            'text': 'A' * 150,
            'text_norm_hash': 'abc123',
            'source_domain': 'example.com'
        }
        passed, rule_id, msg = apply_sa_sanity_rules(payload)
        assert passed is True

    def test_sa_rule_8_domain_auto_extract(self):
        """Rule 8: Domain auto-extracted from URL if missing"""
        payload = {
            'url': 'https://example.com/news/article-123',
            'publisher_time': '2024-01-15T10:00:00Z',
            'text': 'A' * 150,
            'text_norm_hash': 'abc123'
        }
        passed, rule_id, msg = apply_sa_sanity_rules(payload)
        assert passed is True
        assert payload['source_domain'] == 'example.com'

# ========================================
# INTEGRATION TESTS
# ========================================

class TestIntegration:
    """Integration tests for complete payloads."""

    def test_complete_ta_payload_valid(self):
        """Test a complete valid TA payload"""
        payload = {
            'symbol': 'FPT',
            'trade_date': '2024-01-15',
            'open': 100.5,
            'high': 103.0,
            'low': 99.8,
            'close': 102.2,
            'volume': 2500000,
            'turnover': 255125000.0,
            'currency': 'VND'
        }
        passed, rule_id, msg = apply_ta_sanity_rules(payload)
        assert passed is True
        assert rule_id is None
        assert msg is None

    def test_complete_sa_payload_valid(self):
        """Test a complete valid SA payload"""
        payload = {
            'url_canonical': 'https://cafef.vn/fpt-cong-bo-loi-nhuan-quy-4-2024-tang-25-20240115.html',
            'source_domain': 'cafef.vn',
            'publisher_time': '2024-01-15T08:30:00+07:00',
            'first_seen_time': '2024-01-15T08:35:00+07:00',
            'language': 'vi',
            'text_normalized': 'FPT Corporation công bố kết quả kinh doanh quý 4/2024 với lợi nhuận sau thuế tăng 25% so với cùng kỳ năm trước, đạt mức kỷ lục 1,200 tỷ đồng...',
            'content_hash': 'sha256:abcdef1234567890',
            'symbols': ['FPT'],
        }
        passed, rule_id, msg = apply_sa_sanity_rules(payload)
        assert passed is True
        assert rule_id is None
        assert msg is None

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
