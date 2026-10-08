"""Reddit workflow constants — URLs, limits, supported media.

Values live here so nothing is hardcoded in workflow logic.
"""

PLATFORM = "reddit"

# ── URLs ──────────────────────────────────────────────────────────────────────
REDDIT_BASE = "https://www.reddit.com"
LOGIN_URL = f"{REDDIT_BASE}/login"
# Submit composer for a specific subreddit.
def submit_url(subreddit: str) -> str:
    return f"{REDDIT_BASE}/r/{subreddit}/submit"

# A user's own submitted-posts listing — used read-only as a verification
# fallback to locate a just-created post by title when the post-submit page
# does not redirect the address bar to the /comments/ permalink.
def submitted_url(username: str) -> str:
    return f"{REDDIT_BASE}/user/{username}/submitted/"

def subreddit_url(subreddit: str) -> str:
    return f"{REDDIT_BASE}/r/{subreddit}/"

# ── Content limits (conservative; Reddit's documented UI limits) ──────────────
MAX_TITLE_LENGTH = 300
MAX_BODY_LENGTH = 40000
MIN_TITLE_LENGTH = 1

# ── Supported media (Phase 13: text + image only; video NOT reliably verifiable) ──
SUPPORTED_IMAGE_FORMATS = ["jpg", "jpeg", "png", "gif"]
SUPPORTS_VIDEO = False
MAX_MEDIA_ITEMS = 1  # single-image posts only, initially

# ── Post types ────────────────────────────────────────────────────────────────
POST_TYPE_TEXT = "text"
POST_TYPE_IMAGE = "image"
POST_TYPE_LINK = "link"

# ── Semantic locators (role, name) — accessibility-first, no pixel coords ─────
# Grouped so the workflow reads clearly and locators are easy to adjust if
# Reddit's accessibility labels change.
LOC_TITLE_INPUT = ("textbox", "Title")
LOC_BODY_INPUT = ("textbox", "Body")
LOC_POST_BUTTON = ("button", "Post")
LOC_IMAGE_TAB = ("tab", "Images & Video")     # legacy composer post-type tab
LOC_IMAGE_BUTTON = ("button", "Image")        # current composer post-type button
LOC_TEXT_TAB = ("tab", "Text")
LOC_FILE_INPUT_LABEL = "Drag and Drop or Upload Media"  # legacy accessible label
LOC_USER_MENU = ("button", "Expand user menu")

# Current Reddit composer exposes the image upload as a real (often hidden)
# <input type=file>, NOT via the old accessible label. The adapter targets this
# selector directly to attach the file.
IMAGE_FILE_INPUT_SELECTOR = "input[type=file]"

# Attach-verification signals (the composer is a web component that CLEARS the
# native <input>.files after consuming the file, so files.length is unreliable):
#   primary  — an accessible "Remove media" control appears once an image is
#              attached (deterministic, accessibility-first).
#   fallback — the composer renders a blob preview <img> per attached image.
LOC_REMOVE_MEDIA = ("button", "Remove media")
IMAGE_PREVIEW_SELECTOR = "img[src^='blob:']"

# Reddit's current composer defaults to a text post and no longer renders a
# post-type "Text" tab. The workflow clicks LOC_TEXT_TAB only if present (legacy
# UI); otherwise it verifies the text composer is already active. This short
# timeout keeps the "tab absent" path fast (the tab, when present, is immediate).
TAB_PROBE_TIMEOUT = 3

# Success signal: after submit, Reddit navigates to the created post permalink,
# whose path contains "/comments/".
PERMALINK_MARKER = "/comments/"

# The current Reddit UI is a SPA: after clicking Post it can take several seconds
# to change the address bar to the created post's /comments/ permalink. A single
# wait can resolve before that transition and read a stale (submit) URL. The
# verifier therefore POLLS current_url() for the permalink transition. These
# bound that polling — the total wait never exceeds HERMES_PUBLISH_TIMEOUT.
PERMALINK_POLL_INTERVAL_S = 2      # seconds between current_url() checks
PERMALINK_POLL_MAX_CHECKS = 30     # hard cap on checks (defense-in-depth)

# Only accept a permalink that Reddit itself is showing in the address bar
# (never scraped from arbitrary in-page links, which include unrelated
# recommendations). A trustworthy Reddit post permalink is an absolute
# reddit.com URL whose path contains "/comments/".
REDDIT_HOSTS = ("reddit.com", "www.reddit.com")

# ── Confirmation ──────────────────────────────────────────────────────────────
CONFIRM_ACTION = "confirm_reddit_publish"
