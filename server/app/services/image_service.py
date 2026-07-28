"""
Image service — generates platform-specific images using AWS Bedrock.

Each platform gets a content-aware prompt tailored to its visual style,
audience, and native dimensions. Images are generated in parallel, encoded
from base64, uploaded to Cloudinary, and returned as CDN URLs.

Falls back to deterministic picsum URLs if Bedrock is unavailable or not
configured, so the rest of the app never breaks.
"""

import asyncio
import base64
import hashlib
import io
import json
import os

import boto3
import cloudinary
import cloudinary.uploader
from botocore.exceptions import BotoCoreError, ClientError

from app.config import USE_MOCK

# ── Cloudinary config (already used for uploads elsewhere) ───────────────────
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", ""),
    api_key=os.getenv("CLOUDINARY_API_KEY", ""),
    api_secret=os.getenv("CLOUDINARY_API_SECRET", ""),
)

# ── Bedrock config ────────────────────────────────────────────────────────────
AWS_REGION        = os.getenv("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_KEY    = os.getenv("AWS_SECRET_ACCESS_KEY", "")
BEDROCK_MODEL     = os.getenv("BEDROCK_IMAGE_MODEL", "amazon.titan-image-generator-v1")

# Lazy singleton — created once on first use
_bedrock_client = None

def _get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_KEY,
        )
    return _bedrock_client


# ── Platform visual specs ─────────────────────────────────────────────────────
PLATFORM_SPECS = {
    "linkedin": {
        "width": 1200, "height": 630,
        "style": "professional, corporate, clean white background, business photography style, "
                 "modern minimalist design, high contrast typography feel, suitable for B2B audience",
    },
    "twitter": {
        "width": 1200, "height": 675,
        "style": "bold, high-impact, eye-catching colors, modern graphic design, "
                 "dynamic composition, strong visual hierarchy, trending social media aesthetic",
    },
    "instagram": {
        "width": 1080, "height": 1080,
        "style": "vibrant, aesthetically pleasing, lifestyle photography, warm tones, "
                 "visually rich, square composition, Instagram-worthy, highly shareable",
    },
    "reddit": {
        "width": 1200, "height": 630,
        "style": "informative, clean infographic style, neutral tones, data visualization feel, "
                 "community-focused, clear and readable, no promotional feel",
    },
    "medium": {
        "width": 1200, "height": 630,
        "style": "editorial photography, sophisticated, minimalist, literary feel, "
                 "muted tones, intellectual aesthetic, long-form content banner style",
    },
    "meta": {
        "width": 1200, "height": 630,
        "style": "warm, friendly, community-oriented, approachable lifestyle photography, "
                 "diverse and inclusive, social and engaging, Facebook-native feel",
    },
    "quora": {
        "width": 1200, "height": 630,
        "style": "clean, knowledge-focused, authoritative, educational infographic style, "
                 "professional and credible, neutral background, clear visual information",
    },
}


# ── Prompt builder ────────────────────────────────────────────────────────────
def _build_image_prompt(platform: str, content_text: str) -> str:
    """
    Build a platform-specific image generation prompt from the content text.
    Extracts the core topic and wraps it in platform-appropriate visual direction.
    """
    spec = PLATFORM_SPECS[platform]

    # Extract first 200 chars as the content theme — enough for context
    theme = content_text[:200].strip().replace("\n", " ")

    prompt = (
        f"Create a {spec['width']}x{spec['height']} pixel image for a {platform} post. "
        f"The image should visually represent: {theme}. "
        f"Visual style: {spec['style']}. "
        f"High quality, photorealistic or professional graphic design. "
        f"No text overlays, no watermarks, no logos. "
        f"Composition optimized for {platform} native dimensions."
    )
    return prompt


# ── Bedrock invocation per model ──────────────────────────────────────────────
def _build_request_body(prompt: str, width: int, height: int) -> dict:
    """Build the request payload for the configured Bedrock model."""

    if "titan-image" in BEDROCK_MODEL:
        return {
            "taskType": "TEXT_IMAGE",
            "textToImageParams": {"text": prompt},
            "imageGenerationConfig": {
                "numberOfImages": 1,
                "width": width,
                "height": height,
                "quality": "standard",
                "cfgScale": 8.0,
            },
        }

    if "stable-diffusion" in BEDROCK_MODEL:
        return {
            "text_prompts": [{"text": prompt, "weight": 1.0}],
            "cfg_scale": 7,
            "steps": 30,
            "width": width,
            "height": height,
        }

    if "nova-canvas" in BEDROCK_MODEL:
        return {
            "taskType": "TEXT_IMAGE",
            "textToImageParams": {"text": prompt},
            "imageGenerationConfig": {
                "numberOfImages": 1,
                "width": width,
                "height": height,
                "quality": "standard",
            },
        }

    # Generic fallback — Titan format
    return {
        "taskType": "TEXT_IMAGE",
        "textToImageParams": {"text": prompt},
        "imageGenerationConfig": {
            "numberOfImages": 1,
            "width": width,
            "height": height,
        },
    }


def _extract_image_bytes(response_body: dict) -> bytes:
    """Extract raw image bytes from the Bedrock response regardless of model."""
    # Titan / Nova Canvas
    if "images" in response_body:
        return base64.b64decode(response_body["images"][0])
    # Stable Diffusion
    if "artifacts" in response_body:
        return base64.b64decode(response_body["artifacts"][0]["base64"])
    raise ValueError(f"Unknown Bedrock response shape: {list(response_body.keys())}")


# ── Single platform image generation ─────────────────────────────────────────
async def _generate_one(platform: str, content_text: str) -> str:
    """
    Generate one platform image via Bedrock + upload to Cloudinary.
    Returns a Cloudinary CDN URL on success, or a picsum fallback on any error.
    """
    spec = PLATFORM_SPECS[platform]
    seed = int(hashlib.md5(f"{platform}{content_text}".encode()).hexdigest()[:8], 16)
    fallback = f"https://picsum.photos/seed/{seed}/{spec['width']}/{spec['height']}"

    try:
        prompt = _build_image_prompt(platform, content_text)
        body   = _build_request_body(prompt, spec["width"], spec["height"])
        client = _get_bedrock_client()

        # Run blocking boto3 call in thread pool so we don't block the event loop
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.invoke_model(
                modelId=BEDROCK_MODEL,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
        )

        response_body = json.loads(response["body"].read())
        image_bytes   = _extract_image_bytes(response_body)

        # Upload to Cloudinary
        upload_result = await loop.run_in_executor(
            None,
            lambda: cloudinary.uploader.upload(
                io.BytesIO(image_bytes),
                folder="trendzzo/generated",
                resource_type="image",
                format="jpg",
                quality="auto:good",
                public_id=f"{platform}_{seed}",
                overwrite=True,
            )
        )

        return upload_result.get("secure_url", fallback)

    except (BotoCoreError, ClientError) as e:
        print(f"[Bedrock] {platform} AWS error: {e}")
        return fallback
    except Exception as e:
        print(f"[Bedrock] {platform} error: {e}")
        return fallback


# ── Public API ────────────────────────────────────────────────────────────────
async def generate_platform_images(content_text: str) -> dict:
    """
    Generate content-specific images for all 7 platforms in parallel.

    In mock mode returns deterministic picsum URLs instantly.
    In production calls Bedrock for each platform concurrently.
    """
    platforms = list(PLATFORM_SPECS.keys())

    if USE_MOCK or not AWS_ACCESS_KEY_ID or not AWS_SECRET_KEY:
        # Deterministic fallback — same content always same images
        seed = int(hashlib.md5(content_text.encode()).hexdigest()[:8], 16)
        return {
            p: f"https://picsum.photos/seed/{seed + i + 1}"
               f"/{PLATFORM_SPECS[p]['width']}/{PLATFORM_SPECS[p]['height']}"
            for i, p in enumerate(platforms)
        }

    # Generate all platforms concurrently
    results = await asyncio.gather(
        *[_generate_one(p, content_text) for p in platforms],
        return_exceptions=False,
    )

    return dict(zip(platforms, results))
