import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "content_repurposer")

client = AsyncIOMotorClient(MONGODB_URL)
db = client[DB_NAME]

# Collections
bookmarks_collection = db["bookmarks"]
history_collection = db["history"]


async def init_db():
    """Create indexes for better query performance."""
    await bookmarks_collection.create_index("platform")
    await bookmarks_collection.create_index("created_at")
    await history_collection.create_index("created_at")
