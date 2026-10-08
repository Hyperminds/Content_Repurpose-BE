"""Facebook API constants — endpoints, scopes, limits."""

# ── OAuth Endpoints ───────────────────────────────────────────────────────────
API_VERSION = "v26.0"
AUTHORIZATION_URL = f"https://www.facebook.com/{API_VERSION}/dialog/oauth"
TOKEN_URL = f"https://graph.facebook.com/{API_VERSION}/oauth/access_token"

# ── Graph API ─────────────────────────────────────────────────────────────────
GRAPH_API_BASE = f"https://graph.facebook.com/{API_VERSION}"

# ── Scopes (permissions) ──────────────────────────────────────────────────────
REQUIRED_SCOPES = [
    "pages_manage_posts",
    "pages_manage_metadata",
    "pages_read_engagement",
    "pages_show_list",
]

# ── Page Tasks ────────────────────────────────────────────────────────────────
# The user must have CREATE_CONTENT task to publish to a Page
PUBLISHING_TASK = "CREATE_CONTENT"

# ── Content Limits ────────────────────────────────────────────────────────────
MAX_MESSAGE_LENGTH = 63206  # Facebook's documented limit
SCHEDULE_MIN_MINUTES = 10
SCHEDULE_MAX_DAYS = 75

# ── Rate Limits (conservative client-side) ────────────────────────────────────
MAX_POSTS_PER_PAGE_PER_DAY = 25

# ── Media Constraints ─────────────────────────────────────────────────────────
SUPPORTED_PHOTO_FORMATS = ["jpg", "jpeg", "gif", "png"]

# ── Timeouts (seconds) ────────────────────────────────────────────────────────
HTTP_TIMEOUT = 30.0
UPLOAD_TIMEOUT = 120.0
