"""Instagram API constants — endpoints, scopes, limits.

All platform-specific URLs and values live here. Nothing is hardcoded
in business logic files.
"""

# ── OAuth Endpoints ───────────────────────────────────────────────────────────
AUTHORIZATION_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_EXCHANGE_URL = "https://api.instagram.com/oauth/access_token"
LONG_LIVED_TOKEN_URL = "https://graph.instagram.com/access_token"
TOKEN_REFRESH_URL = "https://graph.instagram.com/refresh_access_token"

# ── API Endpoints ─────────────────────────────────────────────────────────────
GRAPH_API_HOST = "https://graph.instagram.com"
API_VERSION = "v26.0"

# ── Scopes ────────────────────────────────────────────────────────────────────
REQUIRED_SCOPES = [
    "instagram_business_basic",
    "instagram_business_content_publish",
]

# ── Rate Limits ───────────────────────────────────────────────────────────────
MAX_POSTS_PER_24H = 100  # Instagram Login API limit
MAX_CAROUSEL_ITEMS = 10

# ── Token Lifecycle ───────────────────────────────────────────────────────────
SHORT_LIVED_TOKEN_SECONDS = 3600       # 1 hour
LONG_LIVED_TOKEN_SECONDS = 5_184_000   # 60 days

# ── Media Constraints ─────────────────────────────────────────────────────────
SUPPORTED_IMAGE_FORMATS = ["jpeg", "jpg"]
MAX_CAPTION_LENGTH = 2200
MAX_HASHTAGS = 30

# ── Timeouts (seconds) ────────────────────────────────────────────────────────
HTTP_TIMEOUT = 30.0
CONTAINER_POLL_INTERVAL = 10  # seconds between status checks
CONTAINER_POLL_MAX_WAIT = 300  # max seconds to wait for container processing
