"""Content generation service — AI-powered platform-specific content adaptation.

Generates platform-tailored content variants from a single source idea.
Uses the existing shared AI client (OpenRouter) for LLM calls.

Responsibilities:
  - Adapt content tone for each platform
  - Generate platform-specific captions
  - Suggest relevant hashtags
  - Respect platform character limits
  - Return structured ContentVariant objects (never raw LLM text)

This service NEVER calls platform APIs directly. It only produces text content.
"""

import json
import time
from typing import Optional

from app.social_publishing.agent.models import AgentDecision, ContentVariant
from app.social_publishing.domain.enums import SocialPlatform
from app.services.logger import log

# Platform-specific guidance for the LLM
_PLATFORM_GUIDELINES: dict[str, dict] = {
    "linkedin": {
        "tone": "professional, thought-leadership",
        "max_chars": 3000,
        "hashtag_count": "3-5",
        "notes": "Professional audience. Article-style. No excessive emojis.",
    },
    "instagram": {
        "tone": "visual-first, engaging, conversational",
        "max_chars": 2200,
        "hashtag_count": "5-15",
        "notes": "Visual platform. Hashtags drive discovery. Short paragraphs.",
    },
    "meta": {
        "tone": "community-driven, conversational",
        "max_chars": 63206,
        "hashtag_count": "1-3",
        "notes": "Questions drive engagement. Avoid link-heavy posts.",
    },
    "twitter": {
        "tone": "punchy, concise, witty",
        "max_chars": 280,
        "hashtag_count": "1-2",
        "notes": "Extremely short. Every word counts. Thread for longer ideas.",
    },
    "threads": {
        "tone": "casual, conversational",
        "max_chars": 500,
        "hashtag_count": "0",
        "notes": "Conversational. No hashtags. Engagement-first.",
    },
    "reddit": {
        "tone": "authentic, value-driven, community-minded",
        "max_chars": 40000,
        "hashtag_count": "0",
        "notes": "No hashtags. Authentic tone. Provide value. No self-promotion.",
    },
}


class ContentGenerationService:
    """Generates platform-adapted content variants using AI."""

    def __init__(self, ai_client=None, model: str = "openai/gpt-4o-mini") -> None:
        self._model = model
        self._ai_client = ai_client

    async def generate_variants(
        self,
        source_content: str,
        platforms: list[str],
        tone: str = "professional",
        context: str = "",
        tenant_id: str = "",
    ) -> list[ContentVariant]:
        """
        Generate platform-specific content variants from source content.

        Args:
            source_content: The original idea/content to adapt.
            platforms: List of platform names to generate for.
            tone: Overall tone preference.
            context: Additional context (brand voice, audience, etc.)
            tenant_id: For audit logging.

        Returns:
            List of ContentVariant objects, one per platform.
        """
        start = time.monotonic()
        variants: list[ContentVariant] = []

        for platform in platforms:
            variant = await self._generate_single_variant(
                source_content, platform, tone, context
            )
            if variant:
                variants.append(variant)

        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "Content variants generated",
            platforms=platforms,
            variant_count=len(variants),
            duration_ms=elapsed_ms,
        )

        return variants

    async def suggest_hashtags(
        self, content: str, platform: str, count: int = 5
    ) -> list[str]:
        """Suggest relevant hashtags for content on a specific platform."""
        guidelines = _PLATFORM_GUIDELINES.get(platform, {})
        if guidelines.get("hashtag_count") == "0":
            return []  # Platform doesn't use hashtags

        prompt = (
            f"Suggest {count} relevant hashtags for this {platform} post. "
            f"Return ONLY a JSON array of strings (no # prefix).\n\n"
            f"Content: {content[:500]}"
        )

        raw = await self._call_llm(prompt)
        return self._parse_hashtag_response(raw, count)

    async def adapt_tone(
        self, content: str, target_tone: str, platform: str
    ) -> str:
        """Rewrite content in a specific tone for a platform."""
        guidelines = _PLATFORM_GUIDELINES.get(platform, {})
        max_chars = guidelines.get("max_chars", 5000)

        prompt = (
            f"Rewrite this content for {platform} in a {target_tone} tone. "
            f"Maximum {max_chars} characters. Return ONLY the rewritten text.\n\n"
            f"Original: {content[:1000]}"
        )

        result = await self._call_llm(prompt)
        # Truncate to platform limit as safety net
        return result[:max_chars] if result else content[:max_chars]

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _generate_single_variant(
        self,
        source_content: str,
        platform: str,
        tone: str,
        context: str,
    ) -> Optional[ContentVariant]:
        """Generate a single platform variant."""
        guidelines = _PLATFORM_GUIDELINES.get(platform, {})
        platform_tone = guidelines.get("tone", tone)
        max_chars = guidelines.get("max_chars", 5000)
        hashtag_guidance = guidelines.get("hashtag_count", "3-5")
        notes = guidelines.get("notes", "")

        prompt = self._build_generation_prompt(
            source_content, platform, platform_tone, max_chars,
            hashtag_guidance, notes, context
        )

        raw = await self._call_llm(prompt)
        return self._parse_variant_response(raw, platform, tone)

    def _build_generation_prompt(
        self,
        source: str,
        platform: str,
        tone: str,
        max_chars: int,
        hashtag_guidance: str,
        notes: str,
        context: str,
    ) -> str:
        """Build the LLM prompt for content generation."""
        context_line = f"\nBrand context: {context}" if context else ""

        return (
            f"Generate a {platform} post based on this content.\n\n"
            f"Source idea: {source[:1000]}\n"
            f"Platform: {platform}\n"
            f"Tone: {tone}\n"
            f"Max length: {max_chars} characters\n"
            f"Hashtags: {hashtag_guidance} relevant hashtags\n"
            f"Platform notes: {notes}\n"
            f"{context_line}\n\n"
            f"Respond in this exact JSON format:\n"
            f'{{"content": "your post text here", "hashtags": ["tag1", "tag2"]}}\n\n'
            f"Return ONLY valid JSON. No markdown, no explanation."
        )

    async def _call_llm(self, prompt: str) -> str:
        """Call the AI model. Returns raw text response."""
        if not self._ai_client:
            self._ai_client = _get_default_client()

        if not self._ai_client:
            # No AI client configured — return empty (graceful degradation)
            return ""

        try:
            response = await self._ai_client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "You are a social media content specialist. Respond only with the requested format."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1000,
                temperature=0.7,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            log.warning("AI content generation failed", error=str(e)[:200])
            return ""

    def _parse_variant_response(
        self, raw: str, platform: str, tone: str
    ) -> Optional[ContentVariant]:
        """Parse LLM JSON response into a ContentVariant. Handles malformed output."""
        if not raw:
            return None

        try:
            # Try to extract JSON from the response
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                # Strip markdown code fences
                lines = cleaned.split("\n")
                cleaned = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

            data = json.loads(cleaned)
            content = data.get("content", "")
            hashtags = data.get("hashtags", [])

            if not content:
                return None

            # Sanitize hashtags
            clean_hashtags = [
                tag.lstrip("#").strip()
                for tag in hashtags
                if isinstance(tag, str) and tag.strip()
            ]

            return ContentVariant(
                platform=platform,
                content=content,
                hashtags=clean_hashtags[:20],  # Cap hashtags
                tone=tone,
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            # LLM returned non-JSON — use raw text as content
            if raw.strip():
                return ContentVariant(
                    platform=platform,
                    content=raw.strip()[:_PLATFORM_GUIDELINES.get(platform, {}).get("max_chars", 5000)],
                    hashtags=[],
                    tone=tone,
                    caption_notes="AI output was not valid JSON; used raw text",
                )
            return None

    def _parse_hashtag_response(self, raw: str, max_count: int) -> list[str]:
        """Parse hashtag suggestion response."""
        if not raw:
            return []

        try:
            cleaned = raw.strip().strip("`").strip()
            if cleaned.startswith("["):
                tags = json.loads(cleaned)
                return [t.lstrip("#").strip() for t in tags if isinstance(t, str)][:max_count]
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: split by common delimiters
        parts = raw.replace("#", "").replace(",", " ").split()
        return [p.strip() for p in parts if p.strip()][:max_count]


def _get_default_client():
    """Get the shared AI client (lazy import to avoid circular deps)."""
    try:
        from app.core.ai_client import ai_client
        return ai_client
    except Exception:
        return None
