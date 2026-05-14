"""
User document schema for MongoDB.

{
    "_id": ObjectId,
    "name": str,
    "email": str (unique),
    "password_hash": str,
    "role": "super_admin" | "member",
    "is_verified": bool,
    "otp_code": str | None,
    "otp_expiry": datetime | None,
    "created_at": datetime
}
"""

from app.database import db

users_collection = db["users"]


async def init_users_collection():
    """Create indexes for users collection."""
    await users_collection.create_index("email", unique=True)
