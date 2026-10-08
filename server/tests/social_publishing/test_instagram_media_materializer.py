"""Tests for the Instagram media materializer (app URL -> local file).

Covers:
  - local /uploads/files/<name> path copied from the uploads dir
  - absolute http(s) URL downloaded (httpx mocked)
  - extension inferred from Content-Type when the URL has none
  - unsupported type rejected
  - oversize download aborted
  - non-http scheme rejected
  - traversal/invalid local name rejected
  - cleanup removes the per-operation dir

No real network and no real uploads dir are required: the uploads dir and the
media root are redirected to tmp paths, and httpx.Client is faked.
"""

import os

import pytest

import app.social_publishing.providers.hermes.instagram.media_materializer as mm
from app.social_publishing.providers.hermes.instagram.media_materializer import (
    MediaMaterializeError,
    materialize_media_url,
    materialize_media_urls,
    cleanup_operation_media,
)


@pytest.fixture
def _dirs(tmp_path, monkeypatch):
    """Redirect the media root and uploads dir into tmp."""
    media_root = tmp_path / "media"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(mm, "_MEDIA_ROOT", str(media_root), raising=False)
    monkeypatch.setattr(mm, "_UPLOADS_DIR", uploads, raising=False)
    return media_root, uploads


# ── Fake httpx streaming client ────────────────────────────────────────────────

class _FakeStreamResponse:
    def __init__(self, chunks, headers, status=200):
        self._chunks = chunks
        self.headers = headers
        self._status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")

    def iter_bytes(self, chunk_size=65536):
        for c in self._chunks:
            yield c


class _FakeClient:
    def __init__(self, chunks, headers, status=200, **kwargs):
        self._chunks = chunks
        self._headers = headers
        self._status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url):
        return _FakeStreamResponse(self._chunks, self._headers, self._status)


def _patch_httpx(monkeypatch, chunks, headers, status=200):
    import httpx

    def _factory(*a, **k):
        return _FakeClient(chunks, headers, status)

    monkeypatch.setattr(httpx, "Client", _factory)


# ── Local uploads path ──────────────────────────────────────────────────────────

class TestLocalUpload:
    def test_relative_uploads_path_is_copied(self, _dirs):
        _, uploads = _dirs
        (uploads / "pic.png").write_bytes(b"\x89PNG" + b"0" * 100)
        out = materialize_media_url("/uploads/files/pic.png", "op-local-1")
        assert os.path.isfile(out)
        assert out.endswith("media_0.png")
        assert os.path.getsize(out) == 104

    def test_absolute_local_upload_url_is_copied(self, _dirs):
        _, uploads = _dirs
        (uploads / "shot.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 50)
        out = materialize_media_url(
            "http://127.0.0.1:8000/uploads/files/shot.jpg", "op-local-2"
        )
        assert os.path.isfile(out) and out.endswith("media_0.jpg")

    def test_missing_local_upload_rejected(self, _dirs):
        with pytest.raises(MediaMaterializeError):
            materialize_media_url("/uploads/files/nope.png", "op-local-3")

    def test_traversal_local_name_treated_as_remote_scheme_reject(self, _dirs):
        # "/uploads/files/../secret" is NOT a safe bare name, so it is not treated
        # as a local upload; as a relative string with no scheme it is rejected.
        with pytest.raises(MediaMaterializeError):
            materialize_media_url("/uploads/files/../secret.png", "op-local-4")


# ── Remote download ───────────────────────────────────────────────────────────

class TestRemoteDownload:
    def test_https_url_with_extension(self, _dirs, monkeypatch):
        _patch_httpx(monkeypatch, [b"\xff\xd8\xff", b"0" * 40], {"Content-Type": "image/jpeg"})
        out = materialize_media_url("https://cdn.example.com/a/b.jpg", "op-r-1")
        assert os.path.isfile(out) and out.endswith("media_0.jpg")

    def test_extension_inferred_from_content_type(self, _dirs, monkeypatch):
        # URL path has no usable extension; Content-Type drives the ext.
        _patch_httpx(monkeypatch, [b"\x89PNG", b"0" * 40], {"Content-Type": "image/png"})
        out = materialize_media_url("https://cdn.example.com/image", "op-r-2")
        assert out.endswith("media_0.png")

    def test_unsupported_content_type_rejected(self, _dirs, monkeypatch):
        _patch_httpx(monkeypatch, [b"%PDF", b"0" * 40], {"Content-Type": "application/pdf"})
        with pytest.raises(MediaMaterializeError):
            materialize_media_url("https://cdn.example.com/doc", "op-r-3")

    def test_oversize_download_aborted(self, _dirs, monkeypatch):
        monkeypatch.setattr(mm, "HERMES_MAX_MEDIA_BYTES", 100, raising=False)
        big = [b"0" * 60, b"0" * 60]  # 120 bytes > 100 cap
        _patch_httpx(monkeypatch, big, {"Content-Type": "image/png"})
        with pytest.raises(MediaMaterializeError):
            materialize_media_url("https://cdn.example.com/big.png", "op-r-4")

    def test_http_error_rejected(self, _dirs, monkeypatch):
        _patch_httpx(monkeypatch, [b"x"], {"Content-Type": "image/png"}, status=500)
        with pytest.raises(MediaMaterializeError):
            materialize_media_url("https://cdn.example.com/err.png", "op-r-5")

    def test_non_http_scheme_rejected(self, _dirs):
        with pytest.raises(MediaMaterializeError):
            materialize_media_url("ftp://host/x.png", "op-r-6")
        with pytest.raises(MediaMaterializeError):
            materialize_media_url("file:///etc/passwd", "op-r-7")

    def test_empty_url_rejected(self, _dirs):
        with pytest.raises(MediaMaterializeError):
            materialize_media_url("", "op-r-8")


# ── Batch + cleanup ─────────────────────────────────────────────────────────────

class TestBatchAndCleanup:
    def test_materialize_urls_and_cleanup(self, _dirs, monkeypatch):
        _, uploads = _dirs
        (uploads / "one.png").write_bytes(b"\x89PNG" + b"0" * 20)
        out = materialize_media_urls(["/uploads/files/one.png"], "op-batch-1")
        assert len(out) == 1 and os.path.isfile(out[0])
        op_dir = os.path.dirname(out[0])
        assert os.path.isdir(op_dir)
        cleanup_operation_media("op-batch-1")
        assert not os.path.exists(op_dir)

    def test_cleanup_is_idempotent_and_safe_when_absent(self, _dirs):
        # No dir yet — cleanup must not raise.
        cleanup_operation_media("op-never-created")


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE: create_instagram_post media_urls -> materialize -> prepare
# ══════════════════════════════════════════════════════════════════════════════

import app.config as _cfg
from app.social_publishing.api import instagram_routes as ig_routes


@pytest.fixture
def _flags_on(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_HERMES_PROVIDER", True, raising=False)
    monkeypatch.setattr(_cfg, "ENABLE_HERMES_INSTAGRAM", True, raising=False)


def _user():
    return {"org_id": "tenantZ", "sub": "user-1"}


class TestCreateInstagramPostRoute:
    async def test_media_urls_materialized_and_passed_to_prepare(self, _flags_on, monkeypatch):
        captured = {}

        def _fake_materialize(urls, operation_id):
            captured["urls"] = list(urls)
            captured["op"] = operation_id
            return [f"/local/{operation_id}/media_0.png"]

        async def _fake_prepare(**kwargs):
            captured["prepare_kwargs"] = kwargs
            return {"status": "action_required", "confirmation_id": "c-1",
                    "operation_id": kwargs["operation_id"],
                    "preview": {"caption": kwargs["caption"], "media_count": 1}}

        cleanup_called = {"n": 0}
        monkeypatch.setattr(ig_routes, "materialize_media_urls", _fake_materialize)
        monkeypatch.setattr(ig_routes, "cleanup_operation_media",
                            lambda op: cleanup_called.__setitem__("n", cleanup_called["n"] + 1))
        monkeypatch.setattr(ig_routes, "resolve_tenant_id", lambda user: "tenantZ")
        monkeypatch.setattr(ig_routes._service, "prepare", _fake_prepare)

        req = ig_routes.CreateInstagramPostRequest(
            account_id="acc-1", caption="hi",
            media_urls=["https://cdn.example.com/x.png"],
        )
        res = await ig_routes.create_instagram_post(req, _user())

        assert res["status"] == "action_required"
        # URLs were materialized and the LOCAL paths were handed to prepare.
        assert captured["urls"] == ["https://cdn.example.com/x.png"]
        assert captured["prepare_kwargs"]["media_paths"] == [
            f"/local/{captured['op']}/media_0.png"
        ]
        # ACTION_REQUIRED => files kept for the later confirm (no cleanup).
        assert cleanup_called["n"] == 0

    async def test_media_urls_cleaned_up_when_prepare_not_action_required(self, _flags_on, monkeypatch):
        async def _fake_prepare(**kwargs):
            return {"status": "failed", "message": "login required"}

        cleanup_ops = []
        monkeypatch.setattr(ig_routes, "materialize_media_urls",
                            lambda urls, op: [f"/local/{op}/media_0.png"])
        monkeypatch.setattr(ig_routes, "cleanup_operation_media", lambda op: cleanup_ops.append(op))
        monkeypatch.setattr(ig_routes, "resolve_tenant_id", lambda user: "tenantZ")
        monkeypatch.setattr(ig_routes._service, "prepare", _fake_prepare)

        req = ig_routes.CreateInstagramPostRequest(
            account_id="acc-1", caption="hi", media_urls=["https://cdn.example.com/x.png"],
            operation_id="op-fixed",
        )
        res = await ig_routes.create_instagram_post(req, _user())
        assert res["status"] == "failed"
        # Non-action-required => materialized media cleaned up immediately.
        assert cleanup_ops == ["op-fixed"]

    async def test_materialize_failure_returns_failed_and_cleans_up(self, _flags_on, monkeypatch):
        def _boom(urls, op):
            raise ig_routes.MediaMaterializeError("unsupported type")

        cleanup_ops = []
        prepare_called = {"n": 0}

        async def _fake_prepare(**kwargs):
            prepare_called["n"] += 1
            return {"status": "action_required"}

        monkeypatch.setattr(ig_routes, "materialize_media_urls", _boom)
        monkeypatch.setattr(ig_routes, "cleanup_operation_media", lambda op: cleanup_ops.append(op))
        monkeypatch.setattr(ig_routes, "resolve_tenant_id", lambda user: "tenantZ")
        monkeypatch.setattr(ig_routes._service, "prepare", _fake_prepare)

        req = ig_routes.CreateInstagramPostRequest(
            account_id="acc-1", caption="hi", media_urls=["https://cdn.example.com/x.pdf"],
            operation_id="op-bad",
        )
        res = await ig_routes.create_instagram_post(req, _user())
        assert res["status"] == "failed"
        assert "Could not prepare the Instagram image" in res["message"]
        assert cleanup_ops == ["op-bad"]       # partial media cleaned
        assert prepare_called["n"] == 0        # never reached prepare

    async def test_no_media_returns_failed(self, _flags_on, monkeypatch):
        monkeypatch.setattr(ig_routes, "resolve_tenant_id", lambda user: "tenantZ")
        req = ig_routes.CreateInstagramPostRequest(account_id="acc-1", caption="hi")
        res = await ig_routes.create_instagram_post(req, _user())
        assert res["status"] == "failed"
        assert "image is required" in res["message"].lower()

    async def test_media_paths_passthrough_not_cleaned(self, _flags_on, monkeypatch):
        # Dev tooling supplies local media_paths directly: no materialize, no cleanup.
        seen = {}

        async def _fake_prepare(**kwargs):
            seen["media_paths"] = kwargs["media_paths"]
            return {"status": "action_required", "confirmation_id": "c-9"}

        cleanup_ops = []
        monkeypatch.setattr(ig_routes, "cleanup_operation_media", lambda op: cleanup_ops.append(op))
        monkeypatch.setattr(ig_routes, "resolve_tenant_id", lambda user: "tenantZ")
        monkeypatch.setattr(ig_routes._service, "prepare", _fake_prepare)

        req = ig_routes.CreateInstagramPostRequest(
            account_id="acc-1", caption="hi", media_paths=["C:/tmp/local.png"],
        )
        res = await ig_routes.create_instagram_post(req, _user())
        assert res["status"] == "action_required"
        assert seen["media_paths"] == ["C:/tmp/local.png"]
        assert cleanup_ops == []
