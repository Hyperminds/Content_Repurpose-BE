from fastapi import APIRouter, Query, Request
from app.controllers.history_controller import (
    get_history,
    delete_history_entry,
    clear_history,
)

router = APIRouter(prefix="/history", tags=["history"])


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


@router.get("")
async def list_history(
    request: Request,
    start_date: str = Query(None, description="Filter from date (ISO format)"),
    end_date: str = Query(None, description="Filter to date (ISO format)"),
    limit: int = Query(50, description="Max entries to return"),
    offset: int = Query(0, description="Offset for pagination"),
):
    user_id = _get_user_id(request)
    return await get_history(start_date, end_date, limit, offset, user_id)


@router.delete("/{history_id}")
async def remove_history_entry(history_id: str, request: Request):
    user_id = _get_user_id(request)
    return await delete_history_entry(history_id, user_id)


@router.delete("")
async def clear_all_history(request: Request):
    user_id = _get_user_id(request)
    return await clear_history(user_id)
