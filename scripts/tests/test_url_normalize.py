"""Tests for URL normalization in dedup module."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dedup import normalize_url


class TestNormalizeUrl:
    def test_strips_utm_params(self):
        url = "https://example.com/article?utm_source=twitter&utm_medium=social&id=123"
        result = normalize_url(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "id=123" in result

    def test_strips_tracking_params(self):
        url = "https://example.com/page?fbclid=abc123&gclid=xyz&ref=home"
        result = normalize_url(url)
        assert "fbclid" not in result
        assert "gclid" not in result
        assert "ref=" not in result

    def test_resolves_http_to_https(self):
        url = "http://example.com/article"
        result = normalize_url(url)
        assert result.startswith("https://")

    def test_lowercases_scheme_and_host(self):
        url = "HTTPS://EXAMPLE.COM/Article"
        result = normalize_url(url)
        assert result.startswith("https://example.com/")
        # Path case should be preserved
        assert "/Article" in result

    def test_removes_trailing_slash(self):
        url = "https://example.com/article/"
        result = normalize_url(url)
        assert not result.endswith("/")

    def test_removes_fragment(self):
        url = "https://example.com/article#section1"
        result = normalize_url(url)
        assert "#" not in result

    def test_preserves_meaningful_params(self):
        url = "https://example.com/search?q=ai+news&page=2"
        result = normalize_url(url)
        assert "q=ai+news" in result or "q=ai%20news" in result
        assert "page=2" in result

    def test_handles_no_params(self):
        url = "https://example.com/article"
        result = normalize_url(url)
        assert result == "https://example.com/article"

    def test_handles_empty_string(self):
        result = normalize_url("")
        assert result == ""

    def test_handles_none(self):
        result = normalize_url(None)
        assert result == ""

    def test_strips_all_utm_variants(self):
        url = "https://example.com/p?utm_campaign=launch&utm_content=hero&utm_term=ai"
        result = normalize_url(url)
        assert "utm_" not in result
