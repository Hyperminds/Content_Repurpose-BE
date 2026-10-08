"""X (Twitter) API constants — endpoints, scopes, limits.

X moved to the x.com domain. The api.x.com host is the currently documented
base; api.twitter.com still resolves as a legacy alias. All platform-specific
URLs and values live here so nothing is hardcoded in business logic files.

X API v2 posting requires OAuth 2.0 Authorization Code flow WITH PKCE.
"""

# ── OAuth 2.0 Endpoints (Authorization Code + PKCE) ───────────────────────────
# Authorization dialog is still served from twitter.com; x.com also works.
AUTHORIZATION_URL = "https://twitter.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
REVOKE_URL = "https://api.x.com/2/oauth2/revoke"

# ── API v2 Endpoints ──────────────────────────────────────────────────────────
API_BASE = "https://api.x.com/2"
CREATE_TWEET_URL = f"{API_BASE}/tweets"
USERS_ME_URL = f"{API_BASE}/users/me"

# Media upload uses the X API v2 chunked flow (INIT/APPEND/FINALIZE/STATUS).
# It accepts the same OAuth 2.0 Bearer token used for posting. The legacy
# v1.1 host (upload.twitter.com) is deprecated and is no longer used.
MEDIA_UPLOAD_URL = f"{API_BASE}/media/upload"

# Chunked upload tuning
MEDIA_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB per APPEND segment
# When FINALIZE returns processing_info, poll STATUS until completion.
MEDIA_STATUS_POLL_INTERVAL = 2   # seconds between STATUS checks
MEDIA_STATUS_MAX_WAIT = 120      # max seconds to wait for async processing

# ── Scopes ────────────────────────────────────────────────────────────────────
# offline.access is required to receive a refresh_token.
REQUIRED_SCOPES = [
    "tweet.read",
    "tweet.write",
    "users.read",
    "media.write",     # required for the v2 media upload endpoint (image/video)
    "offline.access",
]

# ── PKCE ──────────────────────────────────────────────────────────────────────
# S256 is the recommended challenge method. Verifier length 43-128 chars.
PKCE_METHOD = "S256"
PKCE_VERIFIER_BYTES = 64  # url-safe base64 → ~86 chars, within the 43-128 range

# ── Token Lifecycle ───────────────────────────────────────────────────────────
# X access tokens are short-lived (2 hours). Refresh tokens rotate on each use.
ACCESS_TOKEN_SECONDS = 7200  # 2 hours

# ── Content Limits ────────────────────────────────────────────────────────────
# Standard post length. (Longer posts require a paid/verified tier; we validate
# against the widely-available limit to avoid silent truncation surprises.)
MAX_TWEET_LENGTH = 280
MAX_MEDIA_ITEMS = 4  # Up to 4 images per post

# ── Media Constraints ─────────────────────────────────────────────────────────
SUPPORTED_IMAGE_FORMATS = ["jpg", "jpeg", "png", "gif", "webp"]

# ── Timeouts (seconds) ────────────────────────────────────────────────────────
HTTP_TIMEOUT = 30.0
UPLOAD_TIMEOUT = 120.0
