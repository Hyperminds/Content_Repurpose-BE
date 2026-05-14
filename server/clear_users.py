import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

async def main():
    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db = client["content_repurposer"]
    result = await db["users"].delete_many({})
    print(f"Deleted {result.deleted_count} users from database.")

asyncio.run(main())
