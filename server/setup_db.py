"""
MongoDB Setup Script for Content Repurposer

MongoDB doesn't require explicit database/table creation.
The database and collections are created automatically on first write.

This script verifies the connection and creates indexes.

Usage:
    python setup_db.py

Prerequisites:
    1. MongoDB must be installed and running
    2. Default connection: mongodb://localhost:27017
    3. Update .env MONGODB_URL if using a different connection
"""

import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import os

load_dotenv()


async def setup():
    url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DB_NAME", "content_repurposer")

    print(f"Connecting to MongoDB at: {url}")
    print(f"Database: {db_name}")
    print("=" * 50)

    try:
        client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
        # Test connection
        await client.admin.command("ping")
        print("✓ MongoDB connection successful!")

        db = client[db_name]

        # Create indexes
        await db["bookmarks"].create_index("platform")
        await db["bookmarks"].create_index("created_at")
        await db["history"].create_index("created_at")
        print("✓ Indexes created!")

        # Show collections
        collections = await db.list_collection_names()
        print(f"✓ Collections: {collections if collections else '(will be created on first write)'}")

        print("\n" + "=" * 50)
        print("Setup complete! Run the server with:")
        print("  uvicorn app.main:app --reload")

    except Exception as e:
        print(f"✗ Connection failed: {e}")
        print("\nMake sure MongoDB is running:")
        print("  - Windows: Check 'MongoDB Server' in Services")
        print("  - Or run: net start MongoDB")


if __name__ == "__main__":
    asyncio.run(setup())
