from app.services.auth_service import (
    signup_user,
    verify_otp,
    resend_otp,
    login_user,
    forgot_password,
    reset_password,
    get_user_profile,
    get_all_users,
    update_user_role,
    delete_user,
)


async def handle_signup(data: dict):
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not name or not email or not password:
        return {"error": "Name, email, and password are required"}
    if len(password) < 6:
        return {"error": "Password must be at least 6 characters"}

    return await signup_user(name, email, password)


async def handle_verify_otp(data: dict):
    email = data.get("email", "").strip().lower()
    otp_code = data.get("otp", "").strip()

    if not email or not otp_code:
        return {"error": "Email and OTP are required"}

    return await verify_otp(email, otp_code)


async def handle_resend_otp(data: dict):
    email = data.get("email", "").strip().lower()
    if not email:
        return {"error": "Email is required"}
    return await resend_otp(email)


async def handle_login(data: dict):
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return {"error": "Email and password are required"}

    return await login_user(email, password)


async def handle_forgot_password(data: dict):
    email = data.get("email", "").strip().lower()
    if not email:
        return {"error": "Email is required"}
    return await forgot_password(email)


async def handle_reset_password(data: dict):
    email = data.get("email", "").strip().lower()
    otp_code = data.get("otp", "").strip()
    new_password = data.get("new_password", "")

    if not email or not otp_code or not new_password:
        return {"error": "Email, OTP, and new password are required"}
    if len(new_password) < 6:
        return {"error": "Password must be at least 6 characters"}

    return await reset_password(email, otp_code, new_password)


async def handle_get_profile(user: dict):
    return await get_user_profile(user.get("user_id"))


async def handle_get_all_users():
    return await get_all_users()


async def handle_update_role(user_id: str, data: dict):
    new_role = data.get("role", "")
    return await update_user_role(user_id, new_role)


async def handle_delete_user(user_id: str):
    return await delete_user(user_id)
