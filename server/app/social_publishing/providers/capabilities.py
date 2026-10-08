"""Provider capability declarations.

Every publishing provider exposes a `ProviderCapabilities` object describing
what it can do. The publishing engine validates a job's requirements against
the resolved provider's capabilities BEFORE execution, so an unsupported
operation fails fast with a clear, provider-neutral reason instead of erroring
deep inside an integration.

Capabilities are declarative data only — they contain no execution logic.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderCapabilities:
    """
    What a provider supports.

    Content capabilities (text/image/video/links/scheduling/analytics) describe
    the kinds of posts the provider can publish. The execution-mode flags
    (browser_automation / user_confirmation) describe HOW it runs, which the UI
    and engine use to decide whether user interaction may be required.
    """

    text: bool = False
    image: bool = False
    video: bool = False
    links: bool = False
    scheduling: bool = False
    analytics: bool = False

    # Execution-mode signals (not content types)
    browser_automation: bool = False
    user_confirmation: bool = False

    def as_dict(self) -> dict:
        """Serialize for API/UI. Never contains secrets."""
        return {
            "text": self.text,
            "image": self.image,
            "video": self.video,
            "links": self.links,
            "scheduling": self.scheduling,
            "analytics": self.analytics,
            "browser_automation": self.browser_automation,
            "user_confirmation": self.user_confirmation,
        }


@dataclass(frozen=True)
class ContentRequirements:
    """
    The capabilities a specific publishing job needs.

    Derived from the post content by the engine (not the provider). The engine
    checks these against a provider's ProviderCapabilities before execution.
    """

    needs_text: bool = False
    needs_image: bool = False
    needs_video: bool = False
    needs_links: bool = False
    needs_scheduling: bool = False


def requirements_for(
    content: str,
    media_urls: list[str] | None = None,
    scheduled: bool = False,
) -> ContentRequirements:
    """
    Derive the capability requirements of a post from its content.

    Media type detection is intentionally simple (extension-based) and mirrors
    how the native integrations already classify media, so the engine's
    pre-flight check agrees with what the provider will attempt.
    """
    media_urls = media_urls or []
    has_text = bool(content and content.strip())

    needs_image = False
    needs_video = False
    video_exts = (".mp4", ".mov", ".m4v", ".avi", ".wmv")
    for url in media_urls:
        lowered = url.lower()
        if any(lowered.endswith(ext) or f"{ext}?" in lowered for ext in video_exts):
            needs_video = True
        else:
            needs_image = True

    needs_links = "http://" in content or "https://" in content if content else False

    return ContentRequirements(
        needs_text=has_text,
        needs_image=needs_image,
        needs_video=needs_video,
        needs_links=needs_links,
        needs_scheduling=scheduled,
    )


def unmet_requirements(
    caps: ProviderCapabilities, reqs: ContentRequirements
) -> list[str]:
    """
    Return the list of required capabilities the provider does NOT support.

    Empty list means the provider can handle the job. This is the single place
    capability validation is expressed, so the engine stays provider-agnostic.
    """
    missing: list[str] = []
    if reqs.needs_text and not caps.text:
        missing.append("text")
    if reqs.needs_image and not caps.image:
        missing.append("image")
    if reqs.needs_video and not caps.video:
        missing.append("video")
    if reqs.needs_links and not caps.links:
        missing.append("links")
    if reqs.needs_scheduling and not caps.scheduling:
        missing.append("scheduling")
    return missing
