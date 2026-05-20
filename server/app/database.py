import os
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "content_repurposer")

client = AsyncIOMotorClient(MONGODB_URL)
db = client[DB_NAME]

# Collections
bookmarks_collection = db["bookmarks"]
history_collection = db["history"]
scheduled_posts_collection = db["scheduled_posts"]


async def init_db():
    """Create indexes for better query performance."""
    await bookmarks_collection.create_index("platform")
    await bookmarks_collection.create_index("created_at")
    await history_collection.create_index("created_at")
    await scheduled_posts_collection.create_index("user_id")
    await scheduled_posts_collection.create_index("status")
    await scheduled_posts_collection.create_index("scheduled_at")

    # Publishing system indexes
    post_history = db["post_history"]
    await post_history.create_index("user_id")
    await post_history.create_index("platform")
    await post_history.create_index("status")
    await post_history.create_index("unique_post_id", unique=True)
    await post_history.create_index([("status", 1), ("scheduled_at", 1)])
    await post_history.create_index("created_at")
