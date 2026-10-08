"""Provider-agnostic image-generation contract.

Keeps OpenRouter (or any future provider) behind a stable interface so the rest
of Trendzzo never depends on a specific vendor. A generator takes a normalized
request (prompt + model + optional size/aspect-ratio + optional reference image)
and returns a normalized ImageGenerationResult.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# Social-media-friendly aspect ratios the UI/config may request. Whether a given
# MODEL honours a given ratio is model-dependent; generators map these to the
# provider's parameters only when the selected model supports them.
ASPECT_RATIOS = {
    "1:1": (1024, 1024),
    "4:5": (1024, 1280),
    "16:9": (1280, 720),
    "9:16": (720, 1280),
}


class ImageGenerationError(Exception):
    """
    A clean, frontend-safe image-generation failure.

    `code` is a short machine-readable token (e.g. "missing_api_key",
    "invalid_model", "model_not_image_capable", "provider_http_error",
    "rate_limited", "timeout", "malformed_response", "invalid_image",
    "storage_failed", "unsupported_aspect_ratio", "unsupported_reference_image").
    `message` is safe to show the user — it NEVER contains credentials or raw
    provider stack traces.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ImageGenerationResult:
    """Normalized Trendzzo image-generation result."""

    success: bool
    image_url: Optional[str] = None
    mime_type: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    # Optional provider request id for usage/debugging (never secrets).
    request_id: Optional[str] = None
    # Diagnostics only — never includes the API key or raw image bytes.
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "image_url": self.image_url,
            "mime_type": self.mime_type,
            "model": self.model,
            "provider": self.provider,
            "request_id": self.request_id,
        }


class ImageGenerator(ABC):
    """Abstract image generator. One concrete subclass per provider."""

    #: Human/config name of the provider (e.g. "openrouter").
    provider_name: str = "unknown"

    @abstractmethod
    async def generate(
        self,
        *,
        prompt: str,
        model: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
        reference_image: Optional[bytes] = None,
        reference_image_mime: Optional[str] = None,
    ) -> ImageGenerationResult:
        """
        Generate ONE image.

        Returns raw image bytes wrapped in a result OR raises
        ImageGenerationError. The CALLER (controller) is responsible for storing
        the bytes via Trendzzo media storage and populating image_url — so
        storage stays in one place and the generator stays provider-focused.

        Implementations must:
          - validate the model is appropriate for image generation,
          - only send aspect-ratio / reference-image params the model supports,
          - never log the API key or raw image payload.
        """
        raise NotImplementedError

    @abstractmethod
    def supports_reference_images(self, model: str) -> bool:
        """Whether the given model accepts a reference/input image."""
        raise NotImplementedError

    @abstractmethod
    def supports_aspect_ratio(self, model: str, aspect_ratio: str) -> bool:
        """Whether the given model supports the requested aspect ratio."""
        raise NotImplementedError
