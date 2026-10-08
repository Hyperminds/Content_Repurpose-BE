"""LinkedIn API constants — endpoints, scopes, limits, versioning."""

# ── OAuth Endpoints ───────────────────────────────────────────────────────────
AUTHORIZATION_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"

# ── API Endpoints ─────────────────────────────────────────────────────────────
API_BASE = "https://api.linkedin.com/rest"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"

# ── API Versioning ────────────────────────────────────────────────────────────
# LinkedIn requires Linkedin-Version header in YYYYMM format.
# Use a recent stable version that hasn't been sunset.
API_VERSION = "202608"

# ── Scopes ────────────────────────────────────────────────────────────────────
MEMBER_SCOPES = ["openid", "profile", "email", "w_member_social"]
ORGANIZATION_SCOPES = ["openid", "profile", "email", "w_member_social", "w_organization_social"]

# ── Organization Roles that allow publishing ──────────────────────────────────
PUBLISHING_ROLES = {"ADMINISTRATOR", "DIRECT_SPONSORED_CONTENT_POSTER", "CONTENT_ADMIN"}

# ── Content Limits ────────────────────────────────────────────────────────────
MAX_COMMENTARY_LENGTH = 3000  # LinkedIn post character limit
MAX_IMAGES_PER_POST = 20  # Multi-image limit (organic)

# ── Rate Limits (conservative client-side enforcement) ────────────────────────
# LinkedIn doesn't publish exact limits; these are safe defaults.
MAX_MEMBER_POSTS_PER_DAY = 20
MAX_ORG_POSTS_PER_DAY = 50

# ── Image Constraints ─────────────────────────────────────────────────────────
SUPPORTED_IMAGE_FORMATS = ["jpg", "jpeg", "png", "gif"]
MAX_IMAGE_PIXELS = 36_152_320  # ~6000x6000

# ── Timeouts (seconds) ────────────────────────────────────────────────────────
HTTP_TIMEOUT = 30.0
UPLOAD_TIMEOUT = 120.0  # Longer timeout for binary uploads

# ── Token Lifecycle ───────────────────────────────────────────────────────────
ACCESS_TOKEN_SECONDS = 5_184_000  # 60 days
