from datetime import datetime, timezone
from bson import ObjectId
from app.database import bookmarks_collection


def serialize_bookmark(doc):
    """Convert MongoDB document to JSON-serializable dict."""
    return {
        "id": str(doc["_id"]),
        "platform": doc.get("platform"),
        "content": doc.get("content"),
        "input_text": doc.get("input_text", ""),
        "image_url": doc.get("image_url", ""),
        "note": doc.get("note", ""),
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
    }


async def create_bookmark(data: dict, db=None):
    doc = {
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


async def get_bookmarks(platform: str | None, db=None):
    query = {}
    if platform:
        query["platform"] = platform

    cursor = bookmarks_collection.find(query).sort("created_at", -1)
    docs = await cursor.to_list(length=200)
    return [serialize_bookmark(doc) for doc in docs]


async def delete_bookmark(bookmark_id: str, db=None):
    await bookmarks_collection.delete_one({"_id": ObjectId(bookmark_id)})
    return {"message": "Bookmark deleted"}
