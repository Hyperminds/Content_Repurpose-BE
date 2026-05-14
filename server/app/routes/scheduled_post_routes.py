from fastapi import APIRouter, Query, Body, Depends, HTTPException
from app.controllers.scheduled_post_controller import (
    create_scheduled_post,
    get_scheduled_posts,
    update_scheduled_post,
    delete_scheduled_post,
)
from app.utils.jwt_handler import get_current_user

router = APIRouter(prefix="/scheduled-posts", tags=["scheduled-posts"])


@router.post("")
async def create_post(data: dict = Body(...), user: dict = Depends(get_current_user)):
    result = await create_scheduled_post(data, user["user_id"])
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("")
async def list_posts(
    status: str = Query(None, description="Filter by status: draft, scheduled, publishing, published, failed"),
    user: dict = Depends(get_current_user),
):
    result = await get_scheduled_posts(user["user_id"], status)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.put("/{post_id}")
async def update_post(post_id: str, data: dict = Body(...), user: dict = Depends(get_current_user)):
    result = await update_scheduled_post(post_id, user["user_id"], data)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/{post_id}")
async def delete_post(post_id: str, user: dict = Depends(get_current_user)):
    result = await delete_scheduled_post(post_id, user["user_id"])
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
