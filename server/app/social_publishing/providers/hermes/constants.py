"""Hermes runtime constants — timeouts and media constraints (Phase 12/13).

All timeouts are bounded: no operation may wait indefinitely. A stuck browser
must eventually return control to Trendzzo so the job can be marked UNKNOWN and
verified rather than hanging forever.

Values are read from the environment with safe defaults so ops can tune them
without code changes.
"""

import os


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# ── Timeouts (seconds) — Phase 13 ─────────────────────────────────────────────
HERMES_CONNECTION_TIMEOUT: int = _int_env("HERMES_CONNECTION_TIMEOUT", 30)
HERMES_NAVIGATION_TIMEOUT: int = _int_env("HERMES_NAVIGATION_TIMEOUT", 45)
HERMES_ACTION_TIMEOUT: int = _int_env("HERMES_ACTION_TIMEOUT", 30)
HERMES_PUBLISH_TIMEOUT: int = _int_env("HERMES_PUBLISH_TIMEOUT", 120)

# Hard ceiling for a whole publish operation (defense against a wedged runtime).
HERMES_OPERATION_TIMEOUT: int = _int_env("HERMES_OPERATION_TIMEOUT", 300)

# ── Media constraints — Phase 12 ──────────────────────────────────────────────
# Max media file size Hermes will handle (bytes).
HERMES_MAX_MEDIA_BYTES: int = _int_env("HERMES_MAX_MEDIA_BYTES", 50 * 1024 * 1024)

# Allowed media extensions and their MIME types. Anything not listed is rejected.
ALLOWED_MEDIA_TYPES: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "mp4": "video/mp4",
    "mov": "video/quicktime",
}
