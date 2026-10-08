"""OpenRouter image generator.

Reuses the SAME OpenRouter credentials/config as text generation — it calls the
shared `app.core.ai_client.ai_client` (an AsyncOpenAI pointed at
https://openrouter.ai/api/v1 with OPENROUTER_API_KEY). There is NO second API
key and NO new key field.

OpenRouter exposes image generation through its OpenAI-compatible Chat
Completions endpoint by requesting the "image" output modality. Image-capable
models (e.g. Google's Gemini image preview) return generated images on
`choices[0].message.images[*].image_url.url` as base64 data URLs. We decode that
data URL, validate it is really an image, detect the MIME/extension, and return
the raw bytes to the caller for storage.

This module is the ONLY place OpenRouter-specific image logic lives.
"""

import base64
import binascii
import re
from typing import Optional

from openai import APIError, APIConnectionError, APITimeoutError, RateLimitError

from app.config import OPENROUTER_API_KEY, OPENROUTER_IMAGE_MODEL
from app.core.ai_client import get_ai_client
from app.services.image_generation.base import (
    ImageGenerator,
    ImageGenerationResult,
    ImageGenerationError,
    ASPECT_RATIOS,
)
from app.services.logger import log


# ── Image-model catalog ───────────────────────────────────────────────────────
# Mirrors the AVAILABLE_MODELS catalog style used for text. `image` must be True
# for a model to be accepted here; `reference` and `aspect_ratios` declare the
# model's capabilities so we never send unsupported parameters. Unknown
# "provider/model" ids are allowed through (OpenRouter validates them) but with
# conservative capability assumptions.
IMAGE_MODELS = [
    {
        "id": "google/gemini-2.5-flash-image",
        "name": "Gemini 2.5 Flash (Image)",
        "provider": "Google",
        "description": "Fast, high-quality image generation. Supports reference images.",
        "image": True,
        "reference": True,
        "aspect_ratios": ["1:1", "4:5", "16:9", "9:16"],
        "badge": "Recommended",
        "badge_color": "#10B981",
    },
]

_IMAGE_MODEL_INDEX = {m["id"]: m for m in IMAGE_MODELS}

# Models known NOT to produce images (text-only) — rejected early with a clear
# error so a misconfiguration surfaces immediately rather than as an empty reply.
_KNOWN_TEXT_ONLY = {
    "openai/gpt-4o-mini",
    "openai/gpt-4.1-nano",
    "mistralai/mistral-small-3.2-24b-instruct",
    "openrouter/free",
}

# Data-URL parser: data:<mime>;base64,<payload>
_DATA_URL_RE = re.compile(r"^data:(?P<mime>[\w.+-]+/[\w.+-]+);base64,(?P<data>.+)$", re.DOTALL)

# Minimal magic-byte signatures to validate decoded bytes are really an image
# and to detect the MIME when the provider doesn't supply a data-URL mime.
_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # followed by WEBP at offset 8 (checked below)
]

_MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}


def resolve_image_model_id(requested_model: Optional[str]) -> str:
    """
    Resolve the image model id, mirroring the text resolve_model_id contract.

    - Known catalog ids pass through.
    - Plausible "provider/model" ids pass through (OpenRouter validates them).
    - Anything else falls back to the configured default (OPENROUTER_IMAGE_MODEL).
    """
    if not requested_model or not isinstance(requested_model, str):
        return OPENROUTER_IMAGE_MODEL
    requested_model = requested_model.strip()
    if requested_model in _IMAGE_MODEL_INDEX:
        return requested_model
    if (
        "/" in requested_model
        and " " not in requested_model
        and 3 <= len(requested_model) <= 128
        and not requested_model.startswith("agent_")
    ):
        return requested_model
    return OPENROUTER_IMAGE_MODEL


def _detect_mime(data: bytes, declared: Optional[str]) -> Optional[str]:
    """Detect image MIME from magic bytes; fall back to the declared mime."""
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    for sig, mime in _MAGIC:
        if mime == "image/webp":
            continue
        if data.startswith(sig):
            return mime
    if declared in _MIME_EXT:
        return declared
    return None


class OpenRouterImageGenerator(ImageGenerator):
    """Generate images via OpenRouter using the shared OpenRouter client."""

    provider_name = "openrouter"

    def __init__(self, client=None, timeout_s: float = 90.0):
        # Reuse the shared OpenRouter client by default (same key/base URL).
        self._client = client or get_ai_client()
        self._timeout_s = timeout_s

    # ── capability queries ──────────────────────────────────────────────────
    def _model_meta(self, model: str) -> Optional[dict]:
        return _IMAGE_MODEL_INDEX.get(model)

    def supports_reference_images(self, model: str) -> bool:
        meta = self._model_meta(model)
        # Known catalog model: use its declared flag. Unknown model: be
        # conservative and assume NO reference support (don't send blindly).
        return bool(meta and meta.get("reference"))

    def supports_aspect_ratio(self, model: str, aspect_ratio: str) -> bool:
        if aspect_ratio not in ASPECT_RATIOS:
            return False
        meta = self._model_meta(model)
        if meta is None:
            # Unknown model — allow the ratio hint (sent as a prompt suffix only).
            return True
        return aspect_ratio in (meta.get("aspect_ratios") or [])

    def _validate_image_capable(self, model: str) -> None:
        if model in _KNOWN_TEXT_ONLY:
            raise ImageGenerationError(
                "model_not_image_capable",
                f"The model '{model}' does not support image generation. "
                "Choose an image-capable model.",
            )
        meta = self._model_meta(model)
        if meta is not None and not meta.get("image"):
            raise ImageGenerationError(
                "model_not_image_capable",
                f"The model '{model}' is not configured for image generation.",
            )

    # ── main entry ────────────────────────────────────────────────────────────
    async def generate(
        self,
        *,
        prompt: str,
        model: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
        reference_image: Optional[bytes] = None,
        reference_image_mime: Optional[str] = None,
    ) -> ImageGenerationResult:
        if not OPENROUTER_API_KEY:
            raise ImageGenerationError(
                "missing_api_key",
                "AI image generation is not configured on the server.",
            )
        if not prompt or not prompt.strip():
            raise ImageGenerationError("invalid_prompt", "An image prompt is required.")

        model = resolve_image_model_id(model)
        self._validate_image_capable(model)

        # Build the user content. A ratio hint is appended to the prompt only
        # when supported (OpenRouter image models take the ratio via prompt).
        effective_prompt = prompt.strip()
        if aspect_ratio:
            if not self.supports_aspect_ratio(model, aspect_ratio):
                raise ImageGenerationError(
                    "unsupported_aspect_ratio",
                    f"The selected model does not support the {aspect_ratio} aspect ratio.",
                )
            effective_prompt = f"{effective_prompt}\n\nAspect ratio: {aspect_ratio}."

        content_parts = [{"type": "text", "text": effective_prompt}]

        # Reference image: only sent when the model supports it.
        if reference_image is not None:
            if not self.supports_reference_images(model):
                raise ImageGenerationError(
                    "unsupported_reference_image",
                    "The selected model does not support reference/input images.",
                )
            mime = reference_image_mime or "image/png"
            b64 = base64.b64encode(reference_image).decode("ascii")
            content_parts.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            )

        try:
            response = await self._client.chat.completions.create(
                model=model,
                modalities=["image", "text"],
                messages=[{"role": "user", "content": content_parts}],
                timeout=self._timeout_s,
            )
        except APITimeoutError:
            raise ImageGenerationError("timeout", "Image generation timed out. Please try again.")
        except RateLimitError:
            raise ImageGenerationError(
                "rate_limited", "Image generation is rate-limited right now. Please retry shortly."
            )
        except APIConnectionError:
            raise ImageGenerationError(
                "provider_http_error", "Could not reach the image provider. Please try again."
            )
        except APIError as e:
            # Do NOT leak the provider's raw body/credentials. Log a terse,
            # key-free line and surface a generic message.
            log.warning("OpenRouter image API error", status=getattr(e, "status_code", None))
            raise ImageGenerationError(
                "provider_http_error", "The image provider returned an error. Please try again."
            )
        except Exception:
            raise ImageGenerationError(
                "provider_http_error", "Unexpected error during image generation."
            )

        image_bytes, mime = self._extract_image(response)
        request_id = getattr(response, "id", None)
        return ImageGenerationResult(
            success=True,
            image_url=None,          # filled by the controller after storage
            mime_type=mime,
            model=model,
            provider=self.provider_name,
            request_id=request_id,
            meta={"_image_bytes": image_bytes},  # internal handoff to the caller
        )

    # ── response parsing ────────────────────────────────────────────────────
    def _extract_image(self, response) -> tuple[bytes, str]:
        """
        Pull the first generated image out of an OpenRouter chat response and
        return (bytes, mime). Raises ImageGenerationError on a malformed response
        or an invalid/undecodable image.
        """
        try:
            choices = response.choices or []
            message = choices[0].message if choices else None
        except Exception:
            message = None
        if message is None:
            raise ImageGenerationError("malformed_response", "The image provider returned no result.")

        # OpenRouter attaches generated images to message.images[*].image_url.url
        images = getattr(message, "images", None)
        data_url = None
        if images:
            first = images[0]
            # SDK may expose dicts or objects; handle both.
            if isinstance(first, dict):
                data_url = (first.get("image_url") or {}).get("url")
            else:
                iu = getattr(first, "image_url", None)
                data_url = getattr(iu, "url", None) if iu is not None else None

        if not data_url:
            raise ImageGenerationError(
                "malformed_response",
                "The provider did not return an image for this prompt/model.",
            )

        m = _DATA_URL_RE.match(data_url.strip())
        if m:
            declared_mime = m.group("mime")
            payload = m.group("data")
        else:
            # Some providers return a bare base64 payload without the data: prefix.
            declared_mime = None
            payload = data_url.strip()

        try:
            raw = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            raise ImageGenerationError("invalid_image", "The generated image data was invalid.")

        if not raw or len(raw) < 16:
            raise ImageGenerationError("invalid_image", "The generated image was empty.")

        mime = _detect_mime(raw, declared_mime)
        if mime is None:
            raise ImageGenerationError(
                "invalid_image", "The generated data was not a recognized image format."
            )
        return raw, mime


def mime_to_ext(mime: str) -> str:
    """Map an image MIME type to a file extension (defaults to png)."""
    return _MIME_EXT.get(mime, "png")
