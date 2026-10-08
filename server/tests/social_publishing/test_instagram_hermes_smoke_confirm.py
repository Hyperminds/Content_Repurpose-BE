"""Focused tests for the Instagram Hermes smoke CLI `confirm` subcommand.

These tests cover ONLY the CLI wiring in scripts/instagram_hermes_smoke.py:
  - `confirm` calls the EXISTING production path
    InstagramUserAssistedService.confirm(tenant_id, confirmation_id)
    (no second publish implementation)
  - it passes the CLI args through unchanged
  - it prints safe fields only and a clear success/failure verdict for each
    outcome (published / unknown / not_found / failed)

No real browser, no Playwright, no network: the service and the pending repo
are replaced with fakes. The CLI never bypasses the confirmation mechanism — it
only drives the service, which is faked here.
"""

import argparse
import importlib

import pytest

smoke = importlib.import_module("scripts.instagram_hermes_smoke")


class _FakePending:
    def __init__(self, operation_id="op-xyz", account_id="acc-1"):
        self.operation_id = operation_id
        self.account_id = account_id


class _FakeRepo:
    """Stand-in for PendingConfirmationRepository used by the CLI for context."""
    _pending = _FakePending()
    find_calls = []

    def __init__(self):
        pass

    async def find(self, confirmation_id, tenant_id):
        type(self).find_calls.append((confirmation_id, tenant_id))
        return self._pending


class _FakeService:
    """Stand-in for InstagramUserAssistedService — records confirm() args."""
    result = {"status": "published", "external_url": "https://www.instagram.com/p/ABC123/",
              "external_post_id": "ABC123", "platform": "instagram"}
    confirm_calls = []

    def __init__(self, *a, **k):
        pass

    async def confirm(self, *, tenant_id, confirmation_id):
        type(self).confirm_calls.append({"tenant_id": tenant_id,
                                         "confirmation_id": confirmation_id})
        return self.result


@pytest.fixture
def _patched(monkeypatch):
    """Patch the names the CLI imports INSIDE cmd_confirm.

    cmd_confirm imports InstagramUserAssistedService and
    PendingConfirmationRepository from their home modules at call time, so we
    patch those source modules.
    """
    import app.social_publishing.providers.hermes.instagram.service as service_mod
    import app.social_publishing.providers.hermes.pending_confirmation_repository as repo_mod

    _FakeService.confirm_calls = []
    _FakeRepo.find_calls = []
    _FakeRepo._pending = _FakePending()
    _FakeService.result = {"status": "published",
                           "external_url": "https://www.instagram.com/p/ABC123/",
                           "external_post_id": "ABC123", "platform": "instagram"}

    monkeypatch.setattr(service_mod, "InstagramUserAssistedService", _FakeService)
    monkeypatch.setattr(repo_mod, "PendingConfirmationRepository", _FakeRepo)
    return service_mod, repo_mod


def _args(tenant="t1", confirmation_id="c-123"):
    return argparse.Namespace(tenant=tenant, confirmation_id=confirmation_id)


class TestSmokeConfirmCLI:
    async def test_calls_production_confirm_with_cli_args(self, _patched):
        await smoke.cmd_confirm(_args(tenant="tenantA", confirmation_id="conf-9"))
        # It called the EXISTING production confirm path exactly once, with the
        # CLI-provided tenant + confirmation id (no mutation, no second path).
        assert _FakeService.confirm_calls == [
            {"tenant_id": "tenantA", "confirmation_id": "conf-9"}
        ]

    async def test_published_prints_success_and_permalink(self, _patched, capsys):
        await smoke.cmd_confirm(_args())
        out = capsys.readouterr().out
        assert "status            = published" in out
        assert "[SUCCESS]" in out
        assert "https://www.instagram.com/p/ABC123/" in out
        assert "CONFIRM_STATUS=published" in out
        # Safe context fields sourced from the pending row.
        assert "operation_id      = op-xyz" in out
        assert "account_id        = acc-1" in out
        assert "platform          = instagram" in out
        assert "provider          = hermes" in out

    async def test_unknown_prints_uncertain_verdict(self, _patched, capsys):
        _FakeService.result = {"status": "unknown",
                               "message": "Instagram publish result is uncertain"}
        await smoke.cmd_confirm(_args())
        out = capsys.readouterr().out
        assert "[UNKNOWN]" in out
        assert "uncertain" in out.lower()
        assert "CONFIRM_STATUS=unknown" in out

    async def test_not_found_prints_not_published(self, _patched, capsys):
        _FakeService.result = {"status": "not_found"}
        _FakeRepo._pending = None  # no pending row for this id/tenant
        await smoke.cmd_confirm(_args())
        out = capsys.readouterr().out
        assert "[NOT PUBLISHED]" in out
        assert "CONFIRM_STATUS=not_found" in out

    async def test_failed_prints_not_published_with_reason(self, _patched, capsys):
        _FakeService.result = {"status": "failed", "message": "Could not submit the Instagram post"}
        await smoke.cmd_confirm(_args())
        out = capsys.readouterr().out
        assert "[NOT PUBLISHED]" in out
        assert "status=failed" in out
        assert "Could not submit the Instagram post" in out
        assert "CONFIRM_STATUS=failed" in out

    async def test_prints_no_cookie_or_token_fields(self, _patched, capsys):
        # Safety: the confirm output must never contain secret-ish fields even if
        # the service result accidentally carried them.
        _FakeService.result = {"status": "published", "external_url": None,
                               "platform": "instagram", "cookie": "SECRET",
                               "access_token": "SECRET"}
        await smoke.cmd_confirm(_args())
        out = capsys.readouterr().out
        assert "SECRET" not in out
        assert "cookie" not in out.lower()
        assert "token" not in out.lower()
