from fastapi import APIRouter, Query, Body, Request
from app.controllers.bookmark_controller import (
    create_bookmark,
    get_bookmarks,
    delete_bookmark,
)

router = APIRouter(prefix="/bookmarks", tags=["bookmarks"])


def _get_user_id(request: Request) -> str:
    """Extract user_id from auth header if present."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            from app.utils.jwt_handler import decode_access_token
            payload = decode_access_token(auth.split(" ")[1])
            return payload.get("user_id", "")
        except Exception:
            pass
    return ""


@router.post("")
async def add_bookmark(request: Request, data: dict = Body(...)):
    user_id = _get_user_id(request)
    return await create_bookmark(data, user_id)


@router.get("")
async def list_bookmarks(
    request: Request,
    platform: str = Query(None, description="Filter by platform"),
):
    user_id = _get_user_id(request)
    return await get_bookmarks(platform, user_id)


@router.delete("/{bookmark_id}")
async def remove_bookmark(bookmark_id: str, request: Request):
    user_id = _get_user_id(request)
    return await delete_bookmark(bookmark_id, user_id)
