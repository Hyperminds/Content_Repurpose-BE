import bcrypt
from datetime import datetime, timezone
from app.models.user_model import users_collection
from app.database import db
from app.utils.jwt_handler import create_access_token
from app.utils.otp_handler import generate_otp, get_otp_expiry, is_otp_expired, send_otp_email

# Temporary collection for pending signups (not yet verified)
pending_collection = db["pending_verifications"]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


async def signup_user(name: str, email: str, password: str):
    """
    Step 1: Send OTP. Do NOT create user yet.
    Store signup data temporarily in pending_verifications.
    """
    # Check if email already registered as a verified user
    existing = await users_collection.find_one({"email": email})
    if existing:
        return {"error": "Email already registered"}

    # Generate OTP
    otp_code = generate_otp()
    otp_expiry = get_otp_expiry()

    # Store in pending (upsert - replace if they try again)
    await pending_collection.update_one(
        {"email": email},
        {"$set": {
            "name": name,
            "email": email,
            "password_hash": hash_password(password),
            "otp_code": otp_code,
            "otp_expiry": otp_expiry,
            "created_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )

    # Send OTP email
    sent = await send_otp_email(email, otp_code, name)
    if not sent:
        return {"error": "Failed to send OTP email. Please try again."}

    return {"message": "OTP sent to your email. Please verify to complete registration."}


async def verify_otp(email: str, otp_code: str):
    """
    Step 2: Verify OTP. If valid, create the actual user account.
    """
    # Find pending signup
    pending = await pending_collection.find_one({"email": email})
    if not pending:
        return {"error": "No pending registration found. Please sign up first."}

    # Check OTP
    if pending.get("otp_code") != otp_code:
        return {"error": "Invalid OTP code"}

    if is_otp_expired(pending.get("otp_expiry")):
        return {"error": "OTP has expired. Please request a new one."}

    # OTP is valid — create the real user
    user_doc = {
        "name": pending["name"],
        "email": pending["email"],
        "password_hash": pending["password_hash"],
        "role": "member",
        "is_verified": True,
        "created_at": pending.get("created_at", datetime.now(timezone.utc)),
    }

    # Check again (race condition guard)
    existing = await users_collection.find_one({"email": email})
    if existing:
        await pending_collection.delete_one({"email": email})
        return {"error": "Email already registered"}

    await users_collection.insert_one(user_doc)

    # Remove from pending
    await pending_collection.delete_one({"email": email})

    return {"message": "Email verified! Account created successfully. You can now log in."}


async def resend_otp(email: str):
    """Resend OTP for a pending signup."""
    pending = await pending_collection.find_one({"email": email})
    if not pending:
        return {"error": "No pending registration found. Please sign up first."}

    otp_code = generate_otp()
    otp_expiry = get_otp_expiry()

    await pending_collection.update_one(
        {"email": email},
        {"$set": {"otp_code": otp_code, "otp_expiry": otp_expiry}}
    )

    await send_otp_email(email, otp_code, pending.get("name", "User"))

    return {"message": "New OTP sent to your email."}


async def forgot_password(email: str):
    """Send OTP for password reset."""
    user = await users_collection.find_one({"email": email})
    if not user:
        # Don't reveal whether email exists
        return {"message": "If this email is registered, you will receive a reset code."}

    otp_code = generate_otp()
    otp_expiry = get_otp_expiry()

    # Store reset OTP in a separate collection
    password_resets_collection = db["password_resets"]
    await password_resets_collection.update_one(
        {"email": email},
        {"$set": {
            "email": email,
            "otp_code": otp_code,
            "otp_expiry": otp_expiry,
        }},
        upsert=True,
    )

    sent = await send_otp_email(email, otp_code, user.get("name", "User"))
    if not sent:
        return {"error": "Failed to send reset email. Please try again."}

    return {"message": "If this email is registered, you will receive a reset code."}


async def reset_password(email: str, otp_code: str, new_password: str):
    """Verify OTP and reset the user's password."""
    password_resets_collection = db["password_resets"]

    reset_record = await password_resets_collection.find_one({"email": email})
    if not reset_record:
        return {"error": "No password reset request found. Please request a new one."}

    if reset_record.get("otp_code") != otp_code:
        return {"error": "Invalid OTP code"}

    if is_otp_expired(reset_record.get("otp_expiry")):
        return {"error": "OTP has expired. Please request a new one."}

    # Update the user's password
    new_hash = hash_password(new_password)
    await users_collection.update_one(
        {"email": email},
        {"$set": {"password_hash": new_hash}}
    )

    # Remove the reset record
    await password_resets_collection.delete_one({"email": email})

    return {"message": "Password reset successfully. You can now log in with your new password."}


async def login_user(email: str, password: str):
    """Authenticate user and return JWT."""
    user = await users_collection.find_one({"email": email})
    if not user:
        # Check if they're in pending (signed up but not verified)
        pending = await pending_collection.find_one({"email": email})
        if pending:
            return {"error": "Account not verified. Please verify your email first.", "needs_verification": True}
        return {"error": "Invalid email or password"}

    if not verify_password(password, user.get("password_hash", "")):
        return {"error": "Invalid email or password"}

    # Create JWT token
    token = create_access_token({
        "user_id": str(user["_id"]),
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
    })

    return {
        "token": token,
        "user": {
            "id": str(user["_id"]),
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
        }
    }


async def get_user_profile(user_id: str):
    """Get user profile by ID."""
    from bson import ObjectId
    user = await users_collection.find_one({"_id": ObjectId(user_id)})
    if not user:
        return None
    return {
        "id": str(user["_id"]),
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "is_verified": user.get("is_verified", True),
        "created_at": user["created_at"].isoformat() if user.get("created_at") else None,
    }


async def get_all_users():
    """Get all users (admin only)."""
    cursor = users_collection.find().sort("created_at", -1)
    users = await cursor.to_list(length=200)
    return [
        {
            "id": str(u["_id"]),
            "name": u["name"],
            "email": u["email"],
            "role": u["role"],
            "is_verified": u.get("is_verified", True),
            "created_at": u["created_at"].isoformat() if u.get("created_at") else None,
        }
        for u in users
    ]


async def update_user_role(user_id: str, new_role: str):
    """Update user role (admin only)."""
    from bson import ObjectId
    if new_role not in ["member", "super_admin"]:
        return {"error": "Invalid role"}
    await users_collection.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"role": new_role}}
    )
    return {"message": f"User role updated to {new_role}"}


async def delete_user(user_id: str):
    """Delete a user (admin only)."""
    from bson import ObjectId
    await users_collection.delete_one({"_id": ObjectId(user_id)})
    return {"message": "User deleted"}
