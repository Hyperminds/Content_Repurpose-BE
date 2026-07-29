from typing import Optional
from datetime import datetime, timezone
from bson import ObjectId
from app.database import history_collection


def serialize_history(doc):
    """Convert MongoDB document to JSON-serializable dict."""
    return {
        "id": str(doc["_id"]),
        "user_id": doc.get("user_id", ""),
        "input_text": doc.get("input_text"),
        "generated_data": doc.get("generated_data"),
        "images": doc.get("images"),
        "settings_snapshot": doc.get("settings_snapshot"),
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
    }


async def add_history_entry(input_text: str, generated_data: dict, images: dict, settings: dict, user_id: str = None):
    doc = {
        "user_id": user_id or "",
        "input_text": input_text,
        "generated_data": generated_data,
        "images": images,
        "settings_snapshot": settings,
        "created_at": datetime.now(timezone.utc),
    }
    result = await history_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_history(doc)


async def get_history(start_date: Optional[str], end_date: Optional[str], limit: int, offset: int, user_id: str = None):
    query = {}
    if user_id:
        query["user_id"] = user_id

    if start_date:
        try:
            start = datetime.fromisoformat(start_date)
            query.setdefault("created_at", {})["$gte"] = start
        except ValueError:
            pass

    if end_date:
        try:
            end = datetime.fromisoformat(end_date)
            query.setdefault("created_at", {})["$lte"] = end
        except ValueError:
            pass

    cursor = history_collection.find(query).sort("created_at", -1).skip(offset).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [serialize_history(doc) for doc in docs]


async def delete_history_entry(history_id: str, user_id: str = None):
    query = {"_id": ObjectId(history_id)}
    if user_id:
        query["user_id"] = user_id
    await history_collection.delete_one(query)
    return {"message": "History entry deleted"}


async def clear_history(user_id: str = None):
    query = {}
    if user_id:
        query["user_id"] = user_id
    await history_collection.delete_many(query)
    return {"message": "All history cleared"}
