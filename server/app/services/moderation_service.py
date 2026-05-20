"""
Content moderation service - checks content before generation/publishing.
Lightweight keyword + pattern-based filtering with flagging system.
"""

import re
from datetime import datetime, timezone
from bson import ObjectId
from app.database import db

moderation_logs_collection = db["moderation_logs"]
users_collection = db["users"]

# Categories of prohibited content
VIOLATION_CATEGORIES = {
    "explicit": [
        r"\b(porn|pornograph|xxx|nsfw|nude|naked|sex\s*tape|onlyfans\s*leak)\b",
        r"\b(hentai|erotic\s*story|sexual\s*fantasy|explicit\s*content)\b",
    ],
    "hate_speech": [
        r"\b(kill\s*all|genocide|ethnic\s*cleansing|race\s*war)\b",
        r"\b(white\s*supremac|nazi|neo-nazi|racial\s*inferior)\b",
    ],
    "violence": [
        r"\b(how\s*to\s*make\s*a?\s*bomb|build\s*explosive|weapon\s*tutorial)\b",
        r"\b(mass\s*shoot|terror\s*attack\s*plan|assassinat)\b",
    ],
    "illegal": [
        r"\b(how\s*to\s*hack|steal\s*credit\s*card|phishing\s*tutorial)\b",
        r"\b(drug\s*manufactur|cook\s*meth|synthesize\s*fentanyl)\b",
    ],
    "misinformation": [
        r"\b(fake\s*news\s*campaign|propaganda\s*bot|manipulate\s*election)\b",
        r"\b(deepfake\s*tutorial|impersonat.*celebrity|fake\s*identity)\b",
    ],
    "scam": [
        r"\b(ponzi\s*scheme|pyramid\s*scheme|nigerian\s*prince)\b",
        r"\b(crypto\s*scam|pump\s*and\s*dump|money\s*launder)\b",
    ],
}


def check_content(text: str) -> dict:
    """
    Check content against moderation rules.
    Returns {safe: True} or {safe: False, category: str, reason: str}.
    """
    if not text:
        return {"safe": True}

    text_lower = text.lower()

    for category, patterns in VIOLATION_CATEGORIES.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                return {
                    "safe": False,
                    "category": category,
                    "reason": f"Content flagged for: {category.replace('_', ' ')}",
                }

    return {"safe": True}


async def flag_user(user_id: str, category: str, content_preview: str) -> dict:
    """
    Increment user's moderation flags and take action based on count.
    Returns the action taken.
    """
    # Increment flag count
    result = await users_collection.find_one_and_update(
        {"_id": ObjectId(user_id)},
        {
            "$inc": {"moderation_flags": 1},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
        return_document=True,
    )

    if not result:
        return {"action": "none"}

    flag_count = result.get("moderation_flags", 1)

    # Determine action based on flag count
    if flag_count >= 3:
        # Suspend account
        await users_collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"moderation_status": "suspended"}},
        )
        action = "suspended"
    elif flag_count == 2:
        await users_collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"moderation_status": "warned"}},
        )
        action = "strong_warning"
    else:
        action = "warning"

    # Log the moderation event
    await moderation_logs_collection.insert_one({
        "user_id": user_id,
        "category": category,
        "content_preview": content_preview[:200],
        "action": action,
        "flag_count": flag_count,
        "created_at": datetime.now(timezone.utc),
    })

    return {"action": action, "flag_count": flag_count}


async def check_user_status(user_id: str) -> dict:
    """
    Check if user is allowed to use the platform.
    Returns {allowed: True} or {allowed: False, reason: str}.
    """
    user = await users_collection.find_one({"_id": ObjectId(user_id)})
    if not user:
        return {"allowed": False, "reason": "User not found"}

    status = user.get("moderation_status", "active")

    if status == "suspended":
        return {
            "allowed": False,
            "reason": "Your account has been suspended due to repeated policy violations. Please contact support.",
        }

    return {"allowed": True, "status": status}


async def accept_disclaimer(user_id: str) -> dict:
    """Mark that user has accepted the platform disclaimer."""
    await users_collection.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {
            "has_accepted_disclaimer": True,
            "disclaimer_accepted_at": datetime.now(timezone.utc),
        }},
    )
    return {"message": "Disclaimer accepted"}


async def get_disclaimer_status(user_id: str) -> dict:
    """Check if user has accepted the disclaimer."""
    user = await users_collection.find_one({"_id": ObjectId(user_id)})
    if not user:
        return {"accepted": False}
    return {"accepted": user.get("has_accepted_disclaimer", False)}


async def get_moderation_logs(user_id: str = None, limit: int = 50) -> list:
    """Get moderation logs (admin use)."""
    query = {}
    if user_id:
        query["user_id"] = user_id
    cursor = moderation_logs_collection.find(query).sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [
        {
            "id": str(d["_id"]),
            "user_id": d.get("user_id"),
            "category": d.get("category"),
            "content_preview": d.get("content_preview"),
            "action": d.get("action"),
            "flag_count": d.get("flag_count"),
            "created_at": d.get("created_at").isoformat() if d.get("created_at") else None,
        }
        for d in docs
    ]
