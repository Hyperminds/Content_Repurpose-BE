from fastapi import APIRouter, Query
from app.controllers.history_controller import (
    get_history,
    delete_history_entry,
    clear_history,
)

router = APIRouter(prefix="/history", tags=["history"])


@router.get("")
async def list_history(
    start_date: str = Query(None, description="Filter from date (ISO format)"),
    end_date: str = Query(None, description="Filter to date (ISO format)"),
    limit: int = Query(50, description="Max entries to return"),
    offset: int = Query(0, description="Offset for pagination"),
):
    return await get_history(start_date, end_date, limit, offset)


@router.delete("/{history_id}")
async def remove_history_entry(history_id: str):
    return await delete_history_entry(history_id)


@router.delete("")
async def clear_all_history():
    return await clear_history()
