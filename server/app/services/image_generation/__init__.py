"""Provider-agnostic AI image generation.

Public surface:
  - ImageGenerator            : abstract base (provider interface)
  - OpenRouterImageGenerator  : OpenRouter implementation (reuses the shared
                                OpenRouter client/key — no second API key)
  - ImageGenerationResult     : normalized Trendzzo result
  - ImageGenerationError      : clean, frontend-safe error type
  - get_default_image_generator() : factory returning the configured generator

OpenRouter-specific logic lives ONLY in openrouter_generator.py. The rest of the
app depends on the ImageGenerator interface and the normalized result.
"""

from app.services.image_generation.base import (
    ImageGenerator,
    ImageGenerationResult,
    ImageGenerationError,
    ASPECT_RATIOS,
)
from app.services.image_generation.openrouter_generator import (
    OpenRouterImageGenerator,
    IMAGE_MODELS,
    resolve_image_model_id,
)


def get_default_image_generator() -> ImageGenerator:
    """Return the configured image generator (OpenRouter today)."""
    return OpenRouterImageGenerator()


__all__ = [
    "ImageGenerator",
    "ImageGenerationResult",
    "ImageGenerationError",
    "ASPECT_RATIOS",
    "OpenRouterImageGenerator",
    "IMAGE_MODELS",
    "resolve_image_model_id",
    "get_default_image_generator",
]
