from fastapi import APIRouter, Query, Body
from app.controllers.bookmark_controller import (
    create_bookmark,
    get_bookmarks,
    delete_bookmark,
)

router = APIRouter(prefix="/bookmarks", tags=["bookmarks"])


@router.post("")
async def add_bookmark(request: dict = Body(...)):
    return await create_bookmark(request)


@router.get("")
async def list_bookmarks(
    platform: str = Query(None, description="Filter by platform"),
):
    return await get_bookmarks(platform)


@router.delete("/{bookmark_id}")
async def remove_bookmark(bookmark_id: str):
    return await delete_bookmark(bookmark_id)
