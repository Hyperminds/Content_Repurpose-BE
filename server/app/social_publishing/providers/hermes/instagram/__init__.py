"""Instagram user-assisted Hermes workflow package.

Adds Instagram as a USER_ASSISTED_AGENT (Hermes/Playwright) publishing option,
ALONGSIDE the existing native Meta Graph API integration. Provider selection is
by account.provider_type, so an Instagram account can use either the native API
or Hermes without changing the publishing engine.

This package mirrors the Reddit Hermes package (constants/validation/workflow/
service) and reuses the shared Hermes infrastructure (PlaywrightHermesAdapter,
SemanticBrowser, PendingConfirmationRepository, PlatformAutomationPolicy).
Single-image posts only in this phase — no reels/carousels/stories/video.
"""
