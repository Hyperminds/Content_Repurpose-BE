import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path("app/.env"))

async def fix():
    c = AsyncIOMotorClient(os.getenv("MONGODB_URL"), serverSelectionTimeoutMS=5000)
    db = c[os.getenv("DB_NAME", "content_repurposer")]
    # Reset ALL users to active with 0 flags
    result = await db["users"].update_many(
        {},
        {"$set": {"moderation_status": "active", "moderation_flags": 0}}
    )
    print(f"Reset {result.modified_count} users to active status")

asyncio.run(fix())
