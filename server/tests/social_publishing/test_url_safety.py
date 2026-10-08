"""Tests for URL safety checks including DNS-level SSRF hardening."""

import pytest
from unittest.mock import patch, AsyncMock

from app.social_publishing.integrations.url_safety import (
    is_safe_url,
    is_safe_url_async,
    _is_private_ip,
)


class TestSyncUrlSafety:
    def test_https_public_url_safe(self):
        assert is_safe_url("https://cdn.example.com/image.jpg") is True

    def test_http_public_url_safe(self):
        assert is_safe_url("http://cdn.example.com/image.jpg") is True

    def test_ftp_blocked(self):
        assert is_safe_url("ftp://server.com/file") is False

    def test_localhost_blocked(self):
        assert is_safe_url("http://localhost/admin") is False

    def test_127_0_0_1_blocked(self):
        assert is_safe_url("http://127.0.0.1/secret") is False

    def test_metadata_endpoint_blocked(self):
        assert is_safe_url("http://169.254.169.254/latest/meta-data/") is False

    def test_private_10_range_blocked(self):
        assert is_safe_url("http://10.0.0.1/internal") is False

    def test_private_192_168_blocked(self):
        assert is_safe_url("http://192.168.1.1/router") is False

    def test_private_172_16_blocked(self):
        assert is_safe_url("http://172.16.0.1/internal") is False

    def test_internal_suffix_blocked(self):
        assert is_safe_url("http://service.internal/api") is False

    def test_local_suffix_blocked(self):
        assert is_safe_url("http://printer.local/status") is False

    def test_corp_suffix_blocked(self):
        assert is_safe_url("http://wiki.corp/page") is False

    def test_empty_url_blocked(self):
        assert is_safe_url("") is False

    def test_no_host_blocked(self):
        assert is_safe_url("https:///path") is False

    def test_ipv6_loopback_blocked(self):
        assert is_safe_url("http://[::1]/admin") is False

    def test_zero_ip_blocked(self):
        assert is_safe_url("http://0.0.0.0/") is False

    def test_metadata_google_blocked(self):
        assert is_safe_url("http://metadata.google.internal/v1/") is False


class TestAsyncUrlSafety:
    @pytest.mark.asyncio
    async def test_public_url_passes_dns_check(self):
        with patch("app.social_publishing.integrations.url_safety._resolve_hostname") as mock_resolve:
            mock_resolve.return_value = ["93.184.216.34"]  # example.com public IP
            result = await is_safe_url_async("https://cdn.example.com/image.jpg")
        assert result is True

    @pytest.mark.asyncio
    async def test_hostname_resolving_to_private_ip_blocked(self):
        with patch("app.social_publishing.integrations.url_safety._resolve_hostname") as mock_resolve:
            mock_resolve.return_value = ["10.0.0.5"]  # Private IP!
            result = await is_safe_url_async("https://attacker.com/evil")
        assert result is False

    @pytest.mark.asyncio
    async def test_hostname_resolving_to_loopback_blocked(self):
        with patch("app.social_publishing.integrations.url_safety._resolve_hostname") as mock_resolve:
            mock_resolve.return_value = ["127.0.0.1"]
            result = await is_safe_url_async("https://evil.example.com/redirect")
        assert result is False

    @pytest.mark.asyncio
    async def test_hostname_resolving_to_link_local_blocked(self):
        with patch("app.social_publishing.integrations.url_safety._resolve_hostname") as mock_resolve:
            mock_resolve.return_value = ["169.254.169.254"]
            result = await is_safe_url_async("https://metadata-proxy.evil.com/")
        assert result is False

    @pytest.mark.asyncio
    async def test_dns_resolution_failure_blocked(self):
        with patch("app.social_publishing.integrations.url_safety._resolve_hostname") as mock_resolve:
            mock_resolve.side_effect = Exception("DNS timeout")
            result = await is_safe_url_async("https://nonexistent.tld/img.jpg")
        assert result is False  # Fail-closed

    @pytest.mark.asyncio
    async def test_multiple_ips_one_private_blocked(self):
        with patch("app.social_publishing.integrations.url_safety._resolve_hostname") as mock_resolve:
            mock_resolve.return_value = ["93.184.216.34", "192.168.1.1"]  # Mixed
            result = await is_safe_url_async("https://mixed.example.com/img.jpg")
        assert result is False  # Any private → blocked

    @pytest.mark.asyncio
    async def test_explicit_ip_in_url_skips_dns(self):
        with patch("app.social_publishing.integrations.url_safety._resolve_hostname") as mock_resolve:
            result = await is_safe_url_async("https://93.184.216.34/image.jpg")
        # Should not call DNS resolver for explicit IPs
        mock_resolve.assert_not_called()
        assert result is True

    @pytest.mark.asyncio
    async def test_sync_checks_run_first(self):
        """localhost should be blocked before DNS is even attempted."""
        with patch("app.social_publishing.integrations.url_safety._resolve_hostname") as mock_resolve:
            result = await is_safe_url_async("http://localhost/admin")
        mock_resolve.assert_not_called()
        assert result is False


class TestPrivateIpDetection:
    def test_10_range(self):
        assert _is_private_ip("10.0.0.1") is True

    def test_172_16_range(self):
        assert _is_private_ip("172.16.5.5") is True

    def test_192_168_range(self):
        assert _is_private_ip("192.168.0.1") is True

    def test_loopback(self):
        assert _is_private_ip("127.0.0.1") is True

    def test_link_local(self):
        assert _is_private_ip("169.254.169.254") is True

    def test_public_ip(self):
        assert _is_private_ip("8.8.8.8") is False

    def test_invalid_string(self):
        assert _is_private_ip("not-an-ip") is False
