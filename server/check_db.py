import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import json
from datetime import datetime
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / "app" / ".env")


def serialize(doc):
    """Make doc JSON-printable."""
    doc["_id"] = str(doc["_id"])
    if "created_at" in doc and isinstance(doc["created_at"], datetime):
        doc["created_at"] = doc["created_at"].isoformat()
    return doc


async def main():
    url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DB_NAME", "content_repurposer")
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
    db = client[db_name]

    collections = await db.list_collection_names()
    print("=" * 60)
    print("DATABASE: content_repurposer")
    print("=" * 60)
    print(f"\nCollections: {collections}\n")

    for coll_name in collections:
        coll = db[coll_name]
        count = await coll.count_documents({})
        print("-" * 60)
        print(f"Collection: {coll_name} ({count} documents)")
        print("-" * 60)

        docs = await coll.find().sort("created_at", -1).to_list(5)
        if docs:
            for i, doc in enumerate(docs, 1):
                doc = serialize(doc)
                # Truncate long fields for readability
                if "generated_data" in doc:
                    for key in doc["generated_data"]:
                        if isinstance(doc["generated_data"][key], str) and len(doc["generated_data"][key]) > 100:
                            doc["generated_data"][key] = doc["generated_data"][key][:100] + "..."
                if "content" in doc and isinstance(doc["content"], str) and len(doc["content"]) > 150:
                    doc["content"] = doc["content"][:150] + "..."
                if "input_text" in doc and isinstance(doc["input_text"], str) and len(doc["input_text"]) > 100:
                    doc["input_text"] = doc["input_text"][:100] + "..."
                print(f"\n  [{i}] {json.dumps(doc, indent=6, default=str)}")
        else:
            print("  (empty)")
        print()


if __name__ == "__main__":
    asyncio.run(main())
