"""
Reddit OAuth + Auto Publishing Service.
Supports: text posts, link posts, subreddit-aware posting.
"""

import httpx
from datetime import datetime, timezone
from app.services.platform_connections import get_active_token, connected_accounts_collection

REDDIT_API_BASE = "https://oauth.reddit.com"
USER_AGENT = "ContentRepurposer/1.0"


async def publish_to_reddit(user_id: str, content: str, media_urls: list = None, account_id: str = None, subreddit: str = "test") -> dict:
    """Publish a text post to Reddit."""
    token_data = await get_active_token(user_id, "reddit", account_id)

    if not token_data:
        return {"success": False, "error": "Reddit not connected.", "needs_connection": True}

    access_token = token_data["access_token"]

    # Extract title (first line or first 100 chars)
    lines = content.strip().split("\n")
    title = lines[0][:300] if lines else content[:100]
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else content

    # Submit post
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{REDDIT_API_BASE}/api/submit",
            data={
                "kind": "self",
                "sr": subreddit,
                "title": title,
                "text": body,
                "api_type": "json",
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": USER_AGENT,
            },
        )

        if response.status_code == 200:
            data = response.json()
            errors = data.get("json", {}).get("errors", [])
            if errors:
                return {"success": False, "error": f"Reddit error: {errors[0]}"}
            post_url = data.get("json", {}).get("data", {}).get("url", "")
            post_id = data.get("json", {}).get("data", {}).get("id", "")
            return {"success": True, "platform_post_id": post_id or post_url}
        elif response.status_code == 401:
            await connected_accounts_collection.update_one(
                {"user_id": user_id, "platform": "reddit", "is_default": True},
                {"$set": {"status": "expired"}},
            )
            return {"success": False, "error": "Reddit token expired. Please reconnect.", "needs_connection": True}
        else:
            return {"success": False, "error": f"Reddit API error ({response.status_code}): {response.text[:200]}"}


def validate_reddit_content(content: str) -> dict:
    """Validate content for Reddit."""
    errors = []
    if len(content) > 40000:
        errors.append("Content exceeds Reddit's 40000 character limit")
    if not content.strip():
        errors.append("Content cannot be empty")
    # Check for title (first line)
    lines = content.strip().split("\n")
    if lines and len(lines[0]) > 300:
        errors.append("Title (first line) exceeds 300 characters")
    return {"valid": len(errors) == 0, "errors": errors}
