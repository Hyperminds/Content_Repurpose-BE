import asyncio
import os
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / "app" / ".env")

async def main():
    url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DB_NAME", "content_repurposer")
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
    db = client[db_name]
    result = await db["users"].delete_many({})
    print(f"Deleted {result.deleted_count} users from database.")

asyncio.run(main())
