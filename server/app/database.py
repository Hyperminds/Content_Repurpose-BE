import os
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "content_repurposer")

# Atlas-compatible client settings:
# - serverSelectionTimeoutMS: fail fast if Atlas unreachable (5s)
# - connectTimeoutMS: max time to establish connection (10s)
# - tls=True is auto-detected from mongodb+srv:// URI
client = AsyncIOMotorClient(
    MONGODB_URL,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=10000,
)
db = client[DB_NAME]

# Collections
bookmarks_collection = db["bookmarks"]
history_collection = db["history"]
scheduled_posts_collection = db["scheduled_posts"]


async def init_db():
    """Create indexes for better query performance and scalability."""
    # Verify connection before creating indexes
    await client.admin.command("ping")

    # Bookmarks
    await bookmarks_collection.create_index("user_id")
    await bookmarks_collection.create_index("platform")
    await bookmarks_collection.create_index("created_at")

    # History
    await history_collection.create_index("user_id")
    await history_collection.create_index("created_at")

    # Scheduled posts
    await scheduled_posts_collection.create_index("user_id")
    await scheduled_posts_collection.create_index("status")
    await scheduled_posts_collection.create_index("scheduled_at")

    # Post history (publishing system)
    post_history = db["post_history"]
    await post_history.create_index("user_id")
    await post_history.create_index("platform")
    await post_history.create_index("status")
    await post_history.create_index("unique_post_id", unique=True)
    await post_history.create_index([("status", 1), ("scheduled_at", 1)])
    await post_history.create_index([("user_id", 1), ("created_at", -1)])
    await post_history.create_index([("user_id", 1), ("platform", 1), ("status", 1)])

    # Campaigns
    campaigns = db["campaigns"]
    await campaigns.create_index("user_id")
    await campaigns.create_index([("user_id", 1), ("status", 1)])
    await campaigns.create_index("created_at")

    # Campaign days
    campaign_days = db["campaign_days"]
    await campaign_days.create_index("campaign_id")
    await campaign_days.create_index([("campaign_id", 1), ("day_number", 1)])

    # Connected accounts
    connected = db["connected_accounts"]
    await connected.create_index([("user_id", 1), ("platform", 1)])
    await connected.create_index("platform_user_id")

    # Notifications
    notifications = db["notifications"]
    await notifications.create_index([("user_id", 1), ("read", 1)])
    await notifications.create_index([("user_id", 1), ("created_at", -1)])

    # AI usage tracking
    ai_usage = db["ai_usage"]
    await ai_usage.create_index("user_id")
    await ai_usage.create_index([("user_id", 1), ("created_at", -1)])
    await ai_usage.create_index("platform")
