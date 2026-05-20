"""
Notification service - stores in-app notifications and sends email alerts.
"""

import smtplib
import os
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from bson import ObjectId
from dotenv import load_dotenv
from app.database import db

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SMTP_EMAIL = os.getenv("SMTP_EMAIL", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip().replace(" ", "")

notifications_collection = db["notifications"]


# ============ IN-APP NOTIFICATIONS ============ #

def serialize_notification(doc):
    return {
        "id": str(doc["_id"]),
        "user_id": doc.get("user_id"),
        "title": doc.get("title", ""),
        "message": doc.get("message", ""),
        "platform": doc.get("platform", ""),
        "post_id": doc.get("post_id", ""),
        "status": doc.get("status", "unread"),
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
    }


async def create_notification(user_id: str, title: str, message: str, platform: str = "", post_id: str = ""):
    """Create an in-app notification."""
    doc = {
        "user_id": user_id,
        "title": title,
        "message": message,
        "platform": platform,
        "post_id": post_id,
        "status": "unread",
        "created_at": datetime.now(timezone.utc),
    }
    result = await notifications_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_notification(doc)


async def get_notifications(user_id: str, limit: int = 20, unread_only: bool = False):
    """Get notifications for a user."""
    query = {"user_id": user_id}
    if unread_only:
        query["status"] = "unread"
    cursor = notifications_collection.find(query).sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [serialize_notification(doc) for doc in docs]


async def get_unread_count(user_id: str) -> int:
    """Get count of unread notifications."""
    return await notifications_collection.count_documents({"user_id": user_id, "status": "unread"})


async def mark_as_read(user_id: str, notification_id: str):
    """Mark a notification as read."""
    await notifications_collection.update_one(
        {"_id": ObjectId(notification_id), "user_id": user_id},
        {"$set": {"status": "read"}},
    )
    return {"message": "Marked as read"}


async def mark_all_read(user_id: str):
    """Mark all notifications as read."""
    await notifications_collection.update_many(
        {"user_id": user_id, "status": "unread"},
        {"$set": {"status": "read"}},
    )
    return {"message": "All marked as read"}


# ============ EMAIL NOTIFICATIONS ============ #

async def send_post_published_email(to_email: str, platform: str, post_id: str, content_preview: str):
    """Send email notification when a scheduled post is published."""
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        return

    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_EMAIL
        msg["To"] = to_email
        msg["Subject"] = f"✓ Your {platform.title()} post has been published!"

        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #22C55E;">✓ Post Published Successfully</h2>
            <p>Your scheduled <strong>{platform.title()}</strong> post has been published.</p>
            <div style="background: #f4f4f5; padding: 16px; border-radius: 12px; margin: 16px 0;">
                <p style="font-size: 12px; color: #666; margin: 0 0 8px 0;">Post ID: {post_id}</p>
                <p style="margin: 0; color: #333;">{content_preview[:200]}</p>
            </div>
            <p style="color: #888; font-size: 12px;">Content Repurposer</p>
        </body>
        </html>
        """
        msg.attach(MIMEText(body, "html"))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.ehlo()
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, to_email, msg.as_string())
        server.quit()
    except Exception as e:
        print(f"[Notification] Email send failed: {e}")


async def send_post_failed_email(to_email: str, platform: str, post_id: str, error: str):
    """Send email notification when a scheduled post fails."""
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        return

    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_EMAIL
        msg["To"] = to_email
        msg["Subject"] = f"✕ Your {platform.title()} post failed to publish"

        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #EF4444;">✕ Post Publishing Failed</h2>
            <p>Your scheduled <strong>{platform.title()}</strong> post could not be published.</p>
            <div style="background: #FEF2F2; padding: 16px; border-radius: 12px; margin: 16px 0; border: 1px solid #FECACA;">
                <p style="font-size: 12px; color: #666; margin: 0 0 8px 0;">Post ID: {post_id}</p>
                <p style="margin: 0; color: #DC2626;">Error: {error}</p>
            </div>
            <p>You can retry this post from your Post History page.</p>
            <p style="color: #888; font-size: 12px;">Content Repurposer</p>
        </body>
        </html>
        """
        msg.attach(MIMEText(body, "html"))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.ehlo()
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, to_email, msg.as_string())
        server.quit()
    except Exception as e:
        print(f"[Notification] Email send failed: {e}")
