from datetime import datetime, timezone
from bson import ObjectId
from app.database import history_collection


def serialize_history(doc):
    """Convert MongoDB document to JSON-serializable dict."""
    return {
        "id": str(doc["_id"]),
        "input_text": doc.get("input_text"),
        "generated_data": doc.get("generated_data"),
        "images": doc.get("images"),
        "settings_snapshot": doc.get("settings_snapshot"),
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
    }


async def add_history_entry(input_text: str, generated_data: dict, images: dict, settings: dict, db=None):
    doc = {
        "input_text": input_text,
        "generated_data": generated_data,
        "images": images,
        "settings_snapshot": settings,
        "created_at": datetime.now(timezone.utc),
    }
    result = await history_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_history(doc)


async def get_history(start_date: str | None, end_date: str | None, limit: int, offset: int, db=None):
    query = {}

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


async def delete_history_entry(history_id: str, db=None):
    await history_collection.delete_one({"_id": ObjectId(history_id)})
    return {"message": "History entry deleted"}


async def clear_history(db=None):
    await history_collection.delete_many({})
    return {"message": "All history cleared"}
