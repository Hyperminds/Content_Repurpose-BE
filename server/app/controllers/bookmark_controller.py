from typing import Optional
from datetime import datetime, timezone
from bson import ObjectId
from app.database import bookmarks_collection


def serialize_bookmark(doc):
    """Convert MongoDB document to JSON-serializable dict."""
    return {
        "id": str(doc["_id"]),
        "user_id": doc.get("user_id", ""),
        "platform": doc.get("platform"),
        "content": doc.get("content"),
        "input_text": doc.get("input_text", ""),
        "image_url": doc.get("image_url", ""),
        "note": doc.get("note", ""),
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
    }


async def create_bookmark(data: dict, user_id: str = None):
    doc = {
        "user_id": user_id or "",
        "platform": data.get("platform"),
        "content": data.get("content"),
        "input_text": data.get("input_text", ""),
        "image_url": data.get("image_url", ""),
        "note": data.get("note", ""),
        "created_at": datetime.now(timezone.utc),
    }
    result = await bookmarks_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_bookmark(doc)


async def get_bookmarks(platform: Optional[str], user_id: str = None):
    query = {}
    if user_id:
        query["user_id"] = user_id
    if platform:
        query["platform"] = platform

    cursor = bookmarks_collection.find(query).sort("created_at", -1)
    docs = await cursor.to_list(length=200)
    return [serialize_bookmark(doc) for doc in docs]


async def delete_bookmark(bookmark_id: str, user_id: str = None):
    query = {"_id": ObjectId(bookmark_id)}
    if user_id:
        query["user_id"] = user_id
    await bookmarks_collection.delete_one(query)
    return {"message": "Bookmark deleted"}
