"""
Medium OAuth + Auto Publishing Service.
Supports: article publishing, drafts, tags, canonical URLs.
"""

import httpx
from datetime import datetime, timezone
from app.services.platform_connections import get_active_token, connected_accounts_collection

MEDIUM_API_BASE = "https://api.medium.com/v1"


async def publish_to_medium(user_id: str, content: str, media_urls: list = None, account_id: str = None, tags: list = None, publish_status: str = "public") -> dict:
    """Publish an article to Medium."""
    token_data = await get_active_token(user_id, "medium", account_id)

    if not token_data:
        return {"success": False, "error": "Medium not connected.", "needs_connection": True}

    access_token = token_data["access_token"]
    medium_user_id = token_data["platform_user_id"]

    if not medium_user_id:
        # Fetch user ID from Medium API
        async with httpx.AsyncClient() as client:
            me_response = await client.get(
                f"{MEDIUM_API_BASE}/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if me_response.status_code == 200:
                medium_user_id = me_response.json().get("data", {}).get("id", "")
            else:
                return {"success": False, "error": "Failed to get Medium user info."}

    # Extract title (first line) and body
    lines = content.strip().split("\n")
    title = lines[0].strip("# ").strip() if lines else "Untitled"
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else content

    # Build article payload
    payload = {
        "title": title,
        "contentFormat": "markdown",
        "content": f"# {title}\n\n{body}",
        "publishStatus": publish_status,  # "public", "draft", "unlisted"
    }
    if tags:
        payload["tags"] = tags[:5]  # Medium allows max 5 tags

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{MEDIUM_API_BASE}/users/{medium_user_id}/posts",
            json=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )

        if response.status_code in (200, 201):
            data = response.json().get("data", {})
            return {"success": True, "platform_post_id": data.get("id", ""), "url": data.get("url", "")}
        elif response.status_code == 401:
            await connected_accounts_collection.update_one(
                {"user_id": user_id, "platform": "medium", "is_default": True},
                {"$set": {"status": "expired"}},
            )
            return {"success": False, "error": "Medium token expired. Please reconnect.", "needs_connection": True}
        else:
            return {"success": False, "error": f"Medium API error ({response.status_code}): {response.text[:200]}"}


def validate_medium_content(content: str) -> dict:
    """Validate content for Medium."""
    errors = []
    if not content.strip():
        errors.append("Content cannot be empty")
    if len(content) < 50:
        errors.append("Article too short. Medium articles should be substantial.")
    return {"valid": len(errors) == 0, "errors": errors}
