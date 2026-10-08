"""Image-generation controller.

Orchestrates authenticated, tenant-scoped AI image generation:
  resolve tenant/user → validate input → (optional) build a prompt from a
  content idea → call the provider-agnostic ImageGenerator → store the bytes via
  Trendzzo's EXISTING media storage → record usage → return a normalized media
  reference.

Reuses existing infrastructure:
  - auth/tenant: app.utils.jwt_handler (same pattern as upload_routes)
  - media storage: Cloudinary when configured (as elsewhere) else local
    /uploads/files/... served by upload_routes
  - usage logging: ai_usage_service.log_generation + metering_utils.record_ai_usage
The OpenRouter key is used ONLY server-side and is never returned or logged.
"""

import io
import os
import uuid
from pathlib import Path

from fastapi import HTTPException, Request

from app.config import OPENROUTER_IMAGE_MODEL
from app.services.image_generation import (
    get_default_image_generator,
    ImageGenerationError,
    IMAGE_MODELS,
    resolve_image_model_id,
    ASPECT_RATIOS,
)
from app.services.image_generation.openrouter_generator import mime_to_ext
from app.services.logger import log


# Local-storage dir shared with upload_routes (same /uploads/files/<name> URLs).
_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
_UPLOAD_DIR.mkdir(exist_ok=True)

_CLOUDINARY_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
_CLOUDINARY_KEY = os.getenv("CLOUDINARY_API_KEY", "")
_CLOUDINARY_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")
_USE_CLOUDINARY = bool(_CLOUDINARY_NAME and _CLOUDINARY_KEY and _CLOUDINARY_SECRET)

_MAX_PROMPT_CHARS = 4000

# Per-platform visual direction + the most natural aspect ratio for each. The
# style text is reused from image_service.PLATFORM_SPECS so there is a single
# source of truth for platform aesthetics.
_PLATFORM_ASPECT = {
    "linkedin": "16:9",
    "twitter": "16:9",
    "instagram": "1:1",
    "reddit": "16:9",
    "medium": "16:9",
    "meta": "16:9",
    "quora": "16:9",
    "threads": "4:5",
}


# ── error mapping (clean, frontend-safe) ──────────────────────────────────────
# Provider error codes → HTTP status. Never leaks credentials or stack traces.
_CODE_STATUS = {
    "missing_api_key": 503,
    "invalid_prompt": 400,
    "invalid_model": 400,
    "model_not_image_capable": 400,
    "unsupported_aspect_ratio": 400,
    "unsupported_reference_image": 400,
    "rate_limited": 429,
    "timeout": 504,
    "provider_http_error": 502,
    "malformed_response": 502,
    "invalid_image": 502,
    "storage_failed": 500,
}


def _tenant_from_request(request: Request) -> tuple[str, str]:
    """
    Require authentication and derive (user_id, organization_id). Mirrors the
    tenant derivation used across the app. Raises 401 if not authenticated.
    """
    from app.utils.jwt_handler import get_current_user

    user = get_current_user(request)  # raises 401 if missing/invalid
    user_id = user.get("user_id") or user.get("sub") or ""
    organization_id = (
        user.get("organization_id") or user.get("org_id") or user_id or "default"
    )
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id, organization_id


async def _store_image(image_bytes: bytes, mime: str) -> str:
    """
    Store image bytes using the EXISTING media mechanism and return a URL.

    Cloudinary when configured (same account used elsewhere), else local disk at
    /uploads/files/<name> (served by upload_routes). Raises ImageGenerationError
    with code "storage_failed" on any failure.
    """
    ext = mime_to_ext(mime)
    try:
        if _USE_CLOUDINARY:
            import cloudinary
            import cloudinary.uploader

            cloudinary.config(
                cloud_name=_CLOUDINARY_NAME,
                api_key=_CLOUDINARY_KEY,
                api_secret=_CLOUDINARY_SECRET,
                secure=True,
            )
            result = cloudinary.uploader.upload(
                io.BytesIO(image_bytes),
                folder="trendzzo/generated",
                resource_type="image",
                public_id=f"ai_{uuid.uuid4().hex[:12]}",
                overwrite=False,
                unique_filename=True,
            )
            url = result.get("secure_url")
            if not url:
                raise ValueError("cloudinary returned no secure_url")
            return url

        # Local fallback — same URL shape as upload_routes._upload_local.
        fname = f"{uuid.uuid4().hex[:12]}.{ext}"
        (_UPLOAD_DIR / fname).write_bytes(image_bytes)
        return f"/uploads/files/{fname}"
    except Exception as e:
        # Never include the image payload; terse diagnostic only.
        log.warning("Generated image storage failed", backend="cloudinary" if _USE_CLOUDINARY else "local", err=type(e).__name__)
        raise ImageGenerationError("storage_failed", "Could not store the generated image.")


def _record_usage(request: Request, user_id: str, organization_id: str, model: str, success: bool) -> None:
    """Best-effort usage logging via the existing AI-usage + metering systems."""
    # Generation log (fail-safe).
    try:
        import asyncio
        from app.services.ai_usage_service import log_generation

        # Image models aren't token-priced; record a zero-token event tagged
        # platform="image" so usage dashboards still see the generation.
        asyncio.create_task(
            log_generation(
                user_id=user_id,
                platform="image",
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                generation_time_ms=0,
                content_preview="",
                organization_id=organization_id,
            )
        )
    except Exception:
        pass
    # Global metering (fail-safe).
    try:
        from app.utils.metering_utils import record_ai_usage

        record_ai_usage(request, model, 0, 0, 0.0)
    except Exception:
        pass


async def generate_image(request: Request) -> dict:
    """
    POST handler for AI image generation. Authenticated + tenant-scoped.

    Request JSON:
      { "prompt": str,                 # required (the image prompt)
        "model": str,                  # optional (defaults to configured model)
        "aspect_ratio": str,           # optional ("1:1" | "4:5" | "16:9" | "9:16")
        "content_idea": str }          # optional — used to AUTO-BUILD a prompt
                                       #   when "prompt" is absent
    Returns:
      { "success": true, "image_url": str, "mime_type": str,
        "model": str, "provider": "openrouter", "aspect_ratio": str|None }
    """
    user_id, organization_id = _tenant_from_request(request)

    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    content_idea = (body.get("content_idea") or "").strip()
    platform = (body.get("platform") or "").strip().lower()
    aspect_ratio = body.get("aspect_ratio") or None
    requested_model = body.get("model")

    # Prompt precedence (supports INDEPENDENT regeneration):
    #   1. An explicit user `prompt` is used verbatim (the user's own prompt).
    #   2. Otherwise build one from the content idea, applying the platform's
    #      visual style when a platform is given (platform-specific images).
    if not prompt and content_idea:
        prompt = build_image_prompt(content_idea, platform=platform)
    if not prompt:
        raise HTTPException(status_code=400, detail="A prompt or content_idea is required.")
    if len(prompt) > _MAX_PROMPT_CHARS:
        raise HTTPException(status_code=400, detail="Prompt is too long.")
    if aspect_ratio is not None and aspect_ratio not in ASPECT_RATIOS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported aspect_ratio. Allowed: {', '.join(ASPECT_RATIOS)}",
        )

    # MOCK MODE: follow the SAME runtime toggle as caption generation. When mock
    # is ON (toggle OFF = mock data), return a deterministic STOCK image and make
    # NO real OpenRouter call — mirroring get_mock_content() for captions.
    from app.config import get_use_mock

    if get_use_mock():
        return _mock_image_result(prompt, platform, aspect_ratio)

    model = resolve_image_model_id(requested_model)
    try:
        out = await generate_and_store(
            prompt=prompt, platform=platform, aspect_ratio=aspect_ratio, model=model
        )
    except ImageGenerationError as e:
        _record_usage(request, user_id, organization_id, model, success=False)
        status = _CODE_STATUS.get(e.code, 502)
        raise HTTPException(status_code=status, detail=e.message)

    _record_usage(request, user_id, organization_id, out.get("model") or model, success=True)
    return out


def _mock_image_result(prompt: str, platform: str = "", aspect_ratio: str = None) -> dict:
    """
    Deterministic STOCK (picsum) image for mock mode — no API call. Sized to the
    platform's native dimensions when known, so the preview still looks right.
    The same prompt+platform always yields the same image.
    """
    import hashlib

    try:
        from app.services.image_service import PLATFORM_SPECS

        spec = PLATFORM_SPECS.get((platform or "").lower())
    except Exception:
        spec = None
    width, height = (spec["width"], spec["height"]) if spec else (1024, 1024)
    seed = int(hashlib.md5(f"{platform}{prompt}".encode()).hexdigest()[:8], 16)
    return {
        "success": True,
        "image_url": f"https://picsum.photos/seed/{seed}/{width}/{height}",
        "mime_type": "image/jpeg",
        "model": "mock",
        "provider": "mock",
        "aspect_ratio": aspect_ratio or (_PLATFORM_ASPECT.get((platform or "").lower()) if platform else None),
    }


def build_image_prompt(content_idea: str, platform: str = "") -> str:
    """
    Turn a short content idea into a marketing-image prompt.

    When a known `platform` is given, the platform's visual style (from
    image_service.PLATFORM_SPECS) is woven in so the image is relevant to that
    platform (LinkedIn → corporate, Instagram → vibrant lifestyle, etc.). Keeps
    the user's intent, adds tasteful visual direction, and explicitly asks the
    model NOT to invent text/logos the user didn't request. The user can still
    edit the prompt client-side before generating.
    """
    idea = content_idea.strip()[:600]
    style = ""
    try:
        from app.services.image_service import PLATFORM_SPECS

        spec = PLATFORM_SPECS.get((platform or "").lower())
        if spec:
            style = f" Visual style for {platform}: {spec['style']}."
    except Exception:
        style = ""
    return (
        f"Create a premium, professional marketing visual for: {idea}.{style} "
        "Modern, clean composition with a sophisticated, human-centered aesthetic "
        "suitable for a social-media advertisement. Realistic, high quality. "
        "Do not include text, captions, watermarks, or logos unless explicitly described above."
    )


async def generate_and_store(
    *, prompt: str, platform: str = "", aspect_ratio: str = None, model: str = None
) -> dict:
    """
    Generate ONE image and store it, returning a normalized dict with the media
    URL. Shared by the HTTP endpoint and the auto per-platform batch so the
    generate→decode→store→URL path lives in one place. Raises
    ImageGenerationError on failure (callers decide how to surface it).
    """
    resolved_model = resolve_image_model_id(model)
    ratio = aspect_ratio or (_PLATFORM_ASPECT.get((platform or "").lower()) if platform else None)
    generator = get_default_image_generator()
    result = await generator.generate(prompt=prompt, model=resolved_model, aspect_ratio=ratio)
    image_bytes = result.meta.get("_image_bytes")
    if not image_bytes:
        raise ImageGenerationError("malformed_response", "No image produced.")
    url = await _store_image(image_bytes, result.mime_type or "image/png")
    return {
        "success": True,
        "image_url": url,
        "mime_type": result.mime_type,
        "model": result.model,
        "provider": result.provider,
        "aspect_ratio": ratio,
    }


def list_image_models() -> dict:
    """Return the image-model catalog + default + supported aspect ratios."""
    return {
        "models": IMAGE_MODELS,
        "default": OPENROUTER_IMAGE_MODEL,
        "aspect_ratios": list(ASPECT_RATIOS.keys()),
    }
