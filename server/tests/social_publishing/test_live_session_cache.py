"""Tests for the Hermes live-session cache (prepare → confirm reuse)."""

import pytest

from app.social_publishing.providers.hermes import live_session_cache as mod
from app.social_publishing.providers.hermes.live_session_cache import LiveSessionCache


class _FakeAdapter:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.fixture
def cache():
    return LiveSessionCache()


class TestLiveSessionCache:
    async def test_put_get_returns_live_adapter(self, cache):
        a = _FakeAdapter()
        await cache.put("c1", a, "ig_t_acc", "instagram")
        assert cache.get("c1") is a
        assert len(cache) == 1

    async def test_get_missing_returns_none(self, cache):
        assert cache.get("nope") is None

    async def test_evict_closes_and_removes(self, cache):
        a = _FakeAdapter()
        await cache.put("c1", a, "ig_t_acc", "instagram")
        await cache.evict("c1")
        assert cache.get("c1") is None
        assert a.closed is True

    async def test_evict_without_close(self, cache):
        a = _FakeAdapter()
        await cache.put("c1", a, "ig_t_acc", "instagram")
        await cache.evict("c1", close=False)
        assert cache.get("c1") is None
        assert a.closed is False

    async def test_one_live_context_per_profile(self, cache):
        # Putting a second session on the SAME profile closes+evicts the first
        # (Chromium locks the profile dir — only one live context allowed).
        a1 = _FakeAdapter()
        a2 = _FakeAdapter()
        await cache.put("c1", a1, "ig_t_acc", "instagram")
        await cache.put("c2", a2, "ig_t_acc", "instagram")   # same profile_key
        assert a1.closed is True            # first evicted+closed
        assert cache.get("c1") is None
        assert cache.get("c2") is a2        # second is live
        assert len(cache) == 1

    async def test_different_profiles_coexist(self, cache):
        a1 = _FakeAdapter(); a2 = _FakeAdapter()
        await cache.put("c1", a1, "ig_t_acc1", "instagram")
        await cache.put("c2", a2, "ig_t_acc2", "instagram")
        assert cache.get("c1") is a1 and cache.get("c2") is a2
        assert a1.closed is False and len(cache) == 2

    async def test_sweep_evicts_past_ttl(self, cache, monkeypatch):
        monkeypatch.setattr(mod, "LIVE_SESSION_TTL_S", 0.0, raising=False)
        a = _FakeAdapter()
        await cache.put("c1", a, "ig_t_acc", "instagram")
        n = await cache.sweep()           # TTL 0 → immediately past
        assert n == 1 and a.closed is True and cache.get("c1") is None

    async def test_sweep_keeps_fresh_entries(self, cache, monkeypatch):
        monkeypatch.setattr(mod, "LIVE_SESSION_TTL_S", 9999.0, raising=False)
        monkeypatch.setattr(mod, "LIVE_SESSION_IDLE_S", 9999.0, raising=False)
        a = _FakeAdapter()
        await cache.put("c1", a, "ig_t_acc", "instagram")
        n = await cache.sweep()
        assert n == 0 and cache.get("c1") is a

    async def test_safe_close_swallows_errors(self, cache):
        class _Boom:
            async def close(self):
                raise RuntimeError("boom")
        await cache.put("c1", _Boom(), "ig_t_acc", "instagram")
        # Evict must not raise even if the adapter's close() throws.
        await cache.evict("c1")
        assert cache.get("c1") is None
