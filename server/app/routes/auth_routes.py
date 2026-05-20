from fastapi import APIRouter, Body, Depends, HTTPException
from app.controllers.auth_controller import (
    handle_signup,
    handle_verify_otp,
    handle_resend_otp,
    handle_login,
    handle_forgot_password,
    handle_reset_password,
    handle_get_profile,
    handle_get_all_users,
    handle_update_role,
    handle_delete_user,
)
from app.utils.jwt_handler import get_current_user, require_role

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup")
async def signup(data: dict = Body(...)):
    result = await handle_signup(data)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/verify-otp")
async def verify_otp(data: dict = Body(...)):
    result = await handle_verify_otp(data)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/resend-otp")
async def resend_otp(data: dict = Body(...)):
    result = await handle_resend_otp(data)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/login")
async def login(data: dict = Body(...)):
    result = await handle_login(data)
    if "error" in result:
        status = 401
        if result.get("needs_verification"):
            status = 403
        raise HTTPException(status_code=status, detail=result["error"])
    return result


@router.post("/forgot-password")
async def forgot_password(data: dict = Body(...)):
    result = await handle_forgot_password(data)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/reset-password")
async def reset_password(data: dict = Body(...)):
    result = await handle_reset_password(data)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/me")
async def get_profile(user: dict = Depends(get_current_user)):
    result = await handle_get_profile(user)
    if not result:
        raise HTTPException(status_code=404, detail="User not found")
    return result


# Admin-only routes
@router.get("/users")
async def get_all_users(user: dict = Depends(require_role(["super_admin"]))):
    return await handle_get_all_users()


@router.put("/users/{user_id}/role")
async def update_user_role(user_id: str, data: dict = Body(...), user: dict = Depends(require_role(["super_admin"]))):
    result = await handle_update_role(user_id, data)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, user: dict = Depends(require_role(["super_admin"]))):
    return await handle_delete_user(user_id)
