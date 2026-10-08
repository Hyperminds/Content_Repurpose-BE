"""Unit tests for OpenRouter image generation.

All OpenRouter calls are MOCKED — no network calls are made. Covers:
 1. Existing OpenRouter API-key configuration is reused (no second key).
 2. Image-generation request construction.
 3. Model configuration (default + resolve).
 4. Successful image response parsing.
 5. Base64 decoding.
 6. MIME/extension detection.
 7. Media-storage integration (local path).
 8. Invalid/malformed model response.
 9. OpenRouter API failure (timeout / rate limit / http error).
10. Missing API key.
11. Tenant isolation (auth required; tenant derived from JWT).
12. Existing content-generation behavior remains intact (ai_client unchanged).
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.image_generation import (
    OpenRouterImageGenerator,
    ImageGenerationError,
    resolve_image_model_id,
    IMAGE_MODELS,
)
from app.services.image_generation.openrouter_generator import mime_to_ext, _detect_mime


# ── fixtures / helpers ────────────────────────────────────────────────────────

# Smallest valid PNG (1x1) — real magic bytes so image validation passes.
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMCAoG5mWcGAAAAAElFTkSuQmCC"
)
_JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 32
_WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 16


def _fake_response(data_url: str | None, *, response_id="gen-123"):
    """Build a fake OpenRouter chat-completions response with an images payload."""
    msg = MagicMock()
    if data_url is None:
        msg.images = None
    else:
        img = {"type": "image_url", "image_url": {"url": data_url}}
        msg.images = [img]
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    resp.id = response_id
    return resp


def _client_returning(data_url):
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_fake_response(data_url))
    return client


def _data_url(mime, raw):
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


# ── 1. API-key reuse (no second key) ──────────────────────────────────────────

def test_reuses_existing_openrouter_key_no_second_key():
    # The generator must use the SHARED ai_client built from OPENROUTER_API_KEY.
    import app.core.ai_client as ai
    import app.services.image_generation.openrouter_generator as og

    # It imports OPENROUTER_API_KEY (not a new var) and get_ai_client.
    assert hasattr(og, "OPENROUTER_API_KEY")
    gen = OpenRouterImageGenerator()
    # Default client is the shared singleton.
    assert gen._client is ai.get_ai_client()


# ── 2 + 3. request construction + model configuration ─────────────────────────

async def test_request_construction_and_default_model():
    client = _client_returning(_data_url("image/png", _PNG_1x1))
    gen = OpenRouterImageGenerator(client=client)
    with patch.object(og_module(), "OPENROUTER_API_KEY", "sk-test"):
        res = await gen.generate(prompt="a cat", model=None)
    assert res.success
    # Default model resolved from config.
    from app.config import OPENROUTER_IMAGE_MODEL
    assert res.model == OPENROUTER_IMAGE_MODEL
    # The call requested the image modality and sent the prompt text.
    _, kwargs = client.chat.completions.create.call_args
    assert kwargs["model"] == OPENROUTER_IMAGE_MODEL
    assert "image" in kwargs["modalities"]
    parts = kwargs["messages"][0]["content"]
    assert any(p.get("type") == "text" and "a cat" in p.get("text", "") for p in parts)


def test_resolve_image_model_id():
    from app.config import OPENROUTER_IMAGE_MODEL
    assert resolve_image_model_id(None) == OPENROUTER_IMAGE_MODEL
    assert resolve_image_model_id("") == OPENROUTER_IMAGE_MODEL
    assert resolve_image_model_id("garbage model") == OPENROUTER_IMAGE_MODEL
    # Catalog + plausible provider/model pass through.
    assert resolve_image_model_id(IMAGE_MODELS[0]["id"]) == IMAGE_MODELS[0]["id"]
    assert resolve_image_model_id("vendor/some-image-model") == "vendor/some-image-model"


# ── 4 + 5. successful parse + base64 decode ───────────────────────────────────

async def test_successful_response_parsing_and_decode():
    client = _client_returning(_data_url("image/png", _PNG_1x1))
    gen = OpenRouterImageGenerator(client=client)
    with patch.object(og_module(), "OPENROUTER_API_KEY", "sk-test"):
        res = await gen.generate(prompt="hello", model=IMAGE_MODELS[0]["id"])
    assert res.success is True
    assert res.provider == "openrouter"
    assert res.mime_type == "image/png"
    assert res.meta["_image_bytes"] == _PNG_1x1  # decoded bytes handed back
    assert res.request_id == "gen-123"


async def test_bare_base64_without_data_url_prefix():
    # Some providers return a bare base64 string; still decoded + validated.
    bare = base64.b64encode(_PNG_1x1).decode()
    client = _client_returning(bare)
    gen = OpenRouterImageGenerator(client=client)
    with patch.object(og_module(), "OPENROUTER_API_KEY", "sk-test"):
        res = await gen.generate(prompt="x", model=IMAGE_MODELS[0]["id"])
    assert res.success and res.mime_type == "image/png"


# ── 6. MIME / extension detection ─────────────────────────────────────────────

def test_mime_and_extension_detection():
    assert _detect_mime(_PNG_1x1, None) == "image/png"
    assert _detect_mime(_JPEG_MAGIC, None) == "image/jpeg"
    assert _detect_mime(_WEBP, None) == "image/webp"
    assert _detect_mime(b"not-an-image", "image/png") == "image/png"  # declared fallback
    assert _detect_mime(b"not-an-image", None) is None
    assert mime_to_ext("image/jpeg") == "jpg"
    assert mime_to_ext("image/png") == "png"
    assert mime_to_ext("image/weird") == "png"  # default


# ── 7. media-storage integration ──────────────────────────────────────────────

async def test_storage_writes_local_and_returns_url(tmp_path, monkeypatch):
    import app.controllers.image_generation_controller as ctrl

    monkeypatch.setattr(ctrl, "_UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(ctrl, "_USE_CLOUDINARY", False)
    url = await ctrl._store_image(_PNG_1x1, "image/png")
    assert url.startswith("/uploads/files/")
    fname = url.rsplit("/", 1)[-1]
    assert fname.endswith(".png")
    assert (tmp_path / fname).read_bytes() == _PNG_1x1


async def test_storage_failure_raises_clean_error(monkeypatch):
    import app.controllers.image_generation_controller as ctrl

    monkeypatch.setattr(ctrl, "_USE_CLOUDINARY", False)

    class _BadDir:
        def __truediv__(self, other):
            raise OSError("disk full")

    monkeypatch.setattr(ctrl, "_UPLOAD_DIR", _BadDir())
    with pytest.raises(ImageGenerationError) as ei:
        await ctrl._store_image(_PNG_1x1, "image/png")
    assert ei.value.code == "storage_failed"


# ── 8. invalid / malformed response ───────────────────────────────────────────

async def test_malformed_response_no_image():
    client = _client_returning(None)  # message.images is None
    gen = OpenRouterImageGenerator(client=client)
    with patch.object(og_module(), "OPENROUTER_API_KEY", "sk-test"):
        with pytest.raises(ImageGenerationError) as ei:
            await gen.generate(prompt="x", model=IMAGE_MODELS[0]["id"])
    assert ei.value.code == "malformed_response"


async def test_invalid_base64_image():
    client = _client_returning("data:image/png;base64,!!!!not-base64!!!!")
    gen = OpenRouterImageGenerator(client=client)
    with patch.object(og_module(), "OPENROUTER_API_KEY", "sk-test"):
        with pytest.raises(ImageGenerationError) as ei:
            await gen.generate(prompt="x", model=IMAGE_MODELS[0]["id"])
    assert ei.value.code == "invalid_image"


async def test_model_not_image_capable_rejected():
    gen = OpenRouterImageGenerator(client=_client_returning(_data_url("image/png", _PNG_1x1)))
    with patch.object(og_module(), "OPENROUTER_API_KEY", "sk-test"):
        with pytest.raises(ImageGenerationError) as ei:
            await gen.generate(prompt="x", model="openai/gpt-4o-mini")
    assert ei.value.code == "model_not_image_capable"


# ── 9. OpenRouter API failures ────────────────────────────────────────────────

async def test_provider_timeout_maps_clean():
    from openai import APITimeoutError
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))
    gen = OpenRouterImageGenerator(client=client)
    with patch.object(og_module(), "OPENROUTER_API_KEY", "sk-test"):
        with pytest.raises(ImageGenerationError) as ei:
            await gen.generate(prompt="x", model=IMAGE_MODELS[0]["id"])
    assert ei.value.code == "timeout"


async def test_provider_rate_limit_maps_clean():
    from openai import RateLimitError
    resp = MagicMock(); resp.status_code = 429
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(
        side_effect=RateLimitError("rate", response=resp, body=None)
    )
    gen = OpenRouterImageGenerator(client=client)
    with patch.object(og_module(), "OPENROUTER_API_KEY", "sk-test"):
        with pytest.raises(ImageGenerationError) as ei:
            await gen.generate(prompt="x", model=IMAGE_MODELS[0]["id"])
    assert ei.value.code == "rate_limited"


# ── 10. missing API key ───────────────────────────────────────────────────────

async def test_missing_api_key():
    gen = OpenRouterImageGenerator(client=_client_returning(_data_url("image/png", _PNG_1x1)))
    with patch.object(og_module(), "OPENROUTER_API_KEY", ""):
        with pytest.raises(ImageGenerationError) as ei:
            await gen.generate(prompt="x", model=IMAGE_MODELS[0]["id"])
    assert ei.value.code == "missing_api_key"


# ── 11. tenant isolation (auth required) ──────────────────────────────────────

async def test_endpoint_requires_authentication():
    import app.controllers.image_generation_controller as ctrl

    request = MagicMock()
    # No Authorization header → get_current_user raises 401.
    request.headers = {}
    with pytest.raises(HTTPException) as ei:
        await ctrl.generate_image(request)
    assert ei.value.status_code == 401


async def test_tenant_derived_from_jwt(monkeypatch):
    import app.controllers.image_generation_controller as ctrl

    captured = {}

    async def _fake_store(image_bytes, mime):
        return "/uploads/files/abc.png"

    class _Gen:
        async def generate(self, **kw):
            from app.services.image_generation import ImageGenerationResult
            return ImageGenerationResult(
                success=True, mime_type="image/png", model="google/gemini-2.5-flash-image-preview",
                provider="openrouter", meta={"_image_bytes": _PNG_1x1},
            )

    def _fake_record(request, user_id, org, model, success):
        captured["user_id"] = user_id
        captured["org"] = org

    monkeypatch.setattr(ctrl, "_store_image", _fake_store)
    monkeypatch.setattr(ctrl, "get_default_image_generator", lambda: _Gen())
    monkeypatch.setattr(ctrl, "_record_usage", _fake_record)
    monkeypatch.setattr("app.config.get_use_mock", lambda: False)  # force REAL path

    request = MagicMock()
    request.headers = {"Authorization": "Bearer tok"}

    async def _json():
        return {"prompt": "a professional workspace"}

    request.json = _json

    def _fake_user(req):
        return {"user_id": "u-1", "organization_id": "org-9"}

    monkeypatch.setattr("app.utils.jwt_handler.get_current_user", _fake_user)
    out = await ctrl.generate_image(request)
    assert out["success"] and out["image_url"] == "/uploads/files/abc.png"
    assert captured == {"user_id": "u-1", "org": "org-9"}  # tenant threaded through


# ── 12. existing content-generation behavior intact ───────────────────────────

def test_shared_ai_client_untouched():
    # The image feature must not replace/rewrap the shared text client.
    import app.core.ai_client as ai
    from openai import AsyncOpenAI
    assert isinstance(ai.get_ai_client(), AsyncOpenAI)
    # content_service still imports the same shared client symbol.
    import app.services.content_service as cs
    assert hasattr(cs, "client")


# ── 13. platform-specific prompt building ─────────────────────────────────────

def test_platform_specific_prompt_includes_platform_style():
    from app.controllers.image_generation_controller import build_image_prompt

    li = build_image_prompt("promote our CRM", platform="linkedin")
    ig = build_image_prompt("promote our CRM", platform="instagram")
    # Each embeds that platform's distinct visual style.
    assert "linkedin" in li.lower() and "professional" in li.lower()
    assert "instagram" in ig.lower() and ("vibrant" in ig.lower() or "lifestyle" in ig.lower())
    assert li != ig  # platform-specific
    # Unknown/empty platform → generic (no style clause, still a valid prompt).
    generic = build_image_prompt("promote our CRM")
    assert "promote our CRM" in generic


# ── 14. auto per-platform batch (parallel, fail-safe, flag-gated) ─────────────

async def test_generate_platform_images_disabled_returns_stock(monkeypatch):
    import app.services.image_service as isvc

    monkeypatch.setattr("app.config.ENABLE_AI_IMAGE_GENERATION", True, raising=False)
    # Simulate flag OFF at the read site.
    import app.config as cfg
    monkeypatch.setattr(cfg, "ENABLE_AI_IMAGE_GENERATION", False, raising=False)
    out = await isvc.generate_platform_images("hello world")
    assert set(out.keys()) == set(isvc.PLATFORM_SPECS.keys())
    assert all(url.startswith("https://picsum.photos/") for url in out.values())


async def test_generate_platform_images_uses_ai_and_falls_back(monkeypatch):
    import app.services.image_service as isvc
    import app.config as cfg

    monkeypatch.setattr(cfg, "ENABLE_AI_IMAGE_GENERATION", True, raising=False)
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", "sk-test", raising=False)

    calls = {}

    async def _fake_generate_and_store(*, prompt, platform="", aspect_ratio=None, model=None):
        calls[platform] = prompt
        # Instagram fails → must fall back to stock for IG only.
        if platform == "instagram":
            raise RuntimeError("boom")
        return {"success": True, "image_url": f"https://cdn.test/{platform}.png"}

    monkeypatch.setattr(
        "app.controllers.image_generation_controller.generate_and_store",
        _fake_generate_and_store,
    )
    out = await isvc.generate_platform_images("promote our CRM")
    # Every platform present; AI url for successes, stock fallback for instagram.
    assert out["linkedin"] == "https://cdn.test/linkedin.png"
    assert out["instagram"].startswith("https://picsum.photos/")
    # Prompts were platform-specific.
    assert "linkedin" in calls["linkedin"].lower()


# ── 15. endpoint applies platform + independent prompt ────────────────────────

async def test_endpoint_independent_prompt_takes_precedence(monkeypatch):
    import app.controllers.image_generation_controller as ctrl

    seen = {}

    async def _fake_gas(*, prompt, platform="", aspect_ratio=None, model=None):
        seen["prompt"] = prompt
        seen["platform"] = platform
        return {"success": True, "image_url": "/uploads/files/x.png", "model": "m", "provider": "openrouter"}

    monkeypatch.setattr(ctrl, "generate_and_store", _fake_gas)
    monkeypatch.setattr(ctrl, "_record_usage", lambda *a, **k: None)
    monkeypatch.setattr("app.config.get_use_mock", lambda: False)  # force REAL path
    monkeypatch.setattr("app.utils.jwt_handler.get_current_user", lambda req: {"user_id": "u1", "organization_id": "o1"})

    request = MagicMock()
    request.headers = {"Authorization": "Bearer t"}

    async def _json():
        return {"prompt": "my own clear prompt", "platform": "linkedin"}

    request.json = _json
    out = await ctrl.generate_image(request)
    assert out["success"]
    # The user's independent prompt is sent verbatim (not caption-derived).
    assert seen["prompt"] == "my own clear prompt"
    assert seen["platform"] == "linkedin"


# ── 16. mock toggle gates image generation (parity with content) ──────────────

async def test_endpoint_mock_mode_returns_stock_no_api_call(monkeypatch):
    import app.controllers.image_generation_controller as ctrl

    # Mock ON → must NOT call the generator; returns a stock image.
    called = {"n": 0}

    async def _should_not_run(**kw):
        called["n"] += 1
        return {}

    monkeypatch.setattr(ctrl, "generate_and_store", _should_not_run)
    monkeypatch.setattr("app.config.get_use_mock", lambda: True)
    monkeypatch.setattr("app.utils.jwt_handler.get_current_user", lambda req: {"user_id": "u1", "organization_id": "o1"})

    request = MagicMock()
    request.headers = {"Authorization": "Bearer t"}

    async def _json():
        return {"content_idea": "promote CRM", "platform": "instagram"}

    request.json = _json
    out = await ctrl.generate_image(request)
    assert out["success"] is True
    assert out["provider"] == "mock"
    assert out["image_url"].startswith("https://picsum.photos/")
    assert called["n"] == 0  # NO real generation call in mock mode


async def test_batch_mock_mode_returns_stock(monkeypatch):
    import app.services.image_service as isvc
    import app.config as cfg

    monkeypatch.setattr(cfg, "ENABLE_AI_IMAGE_GENERATION", True, raising=False)
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(cfg, "get_use_mock", lambda: True)

    # If this were called, it would raise — proving no real path is taken.
    async def _boom(**kw):
        raise AssertionError("real generation must not run in mock mode")

    monkeypatch.setattr(
        "app.controllers.image_generation_controller.generate_and_store", _boom
    )
    out = await isvc.generate_platform_images("hello")
    assert all(url.startswith("https://picsum.photos/") for url in out.values())


# helper to patch the module-level OPENROUTER_API_KEY used inside generate()
def og_module():
    import app.services.image_generation.openrouter_generator as og
    return og
