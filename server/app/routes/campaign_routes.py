"""
Campaign Studio API routes.
Isolated from existing TrendZo routes — modular and independent.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from app.utils.jwt_handler import get_current_user
from app.models.campaign_model import CampaignCreate, CampaignUpdate
from app.services.campaign_service import (
    create_campaign, get_campaigns, get_campaign, update_campaign,
    delete_campaign, update_campaign_status, get_campaign_stats, get_campaign_activity,
)
from app.services.campaign_ai_service import (
    generate_campaign_strategy, generate_campaign_days,
    get_campaign_days, get_campaign_weeks,
)
from app.database import db

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

# Store strategies in MongoDB
campaign_strategies_collection = db["campaign_strategies"]


@router.post("")
async def create(data: CampaignCreate, user: dict = Depends(get_current_user)):
    """Create a new campaign."""
    result = await create_campaign(user["user_id"], data.model_dump())
    return result


@router.get("")
async def list_campaigns(
    status: str = Query(None, description="Filter by status"),
    user: dict = Depends(get_current_user),
):
    """Get all campaigns for the current user."""
    return await get_campaigns(user["user_id"], status)


@router.get("/stats")
async def campaign_stats(user: dict = Depends(get_current_user)):
    """Get campaign statistics."""
    return await get_campaign_stats(user["user_id"])


@router.get("/{campaign_id}")
async def get_one(campaign_id: str, user: dict = Depends(get_current_user)):
    """Get a single campaign."""
    result = await get_campaign(campaign_id, user["user_id"])
    if not result:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return result


@router.put("/{campaign_id}")
async def update(campaign_id: str, data: CampaignUpdate, user: dict = Depends(get_current_user)):
    """Update a campaign."""
    result = await update_campaign(campaign_id, user["user_id"], data.model_dump(exclude_none=True))
    if not result:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return result


@router.patch("/{campaign_id}/status")
async def set_status(
    campaign_id: str,
    data: dict = Body(...),
    user: dict = Depends(get_current_user),
):
    """Update campaign status only."""
    status = data.get("status", "")
    valid = ["draft", "active", "paused", "completed", "cancelled"]
    if status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Use: {', '.join(valid)}")
    result = await update_campaign_status(campaign_id, user["user_id"], status)
    if not result:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return result


@router.delete("/{campaign_id}")
async def delete(campaign_id: str, user: dict = Depends(get_current_user)):
    """Delete a campaign."""
    success = await delete_campaign(campaign_id, user["user_id"])
    if not success:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"message": "Campaign deleted"}


@router.get("/{campaign_id}/activity")
async def activity(
    campaign_id: str,
    limit: int = Query(20),
    user: dict = Depends(get_current_user),
):
    """Get campaign activity log."""
    return await get_campaign_activity(campaign_id, user["user_id"], limit)


# ── AI Strategy Endpoints ─────────────────────────────────────────────────────

@router.post("/{campaign_id}/generate-strategy")
async def generate_strategy(campaign_id: str, user: dict = Depends(get_current_user)):
    """Generate AI campaign strategy and content plan."""
    campaign = await get_campaign(campaign_id, user["user_id"])
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    try:
        # Generate strategy
        strategy = await generate_campaign_strategy(campaign)

        # Save strategy to DB
        from datetime import datetime, timezone
        await campaign_strategies_collection.update_one(
            {"campaign_id": campaign_id},
            {"$set": {
                "campaign_id": campaign_id,
                "user_id": user["user_id"],
                **strategy,
                "generated_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )

        # Generate day-by-day plan
        await generate_campaign_days(campaign, strategy)

        return {"strategy": strategy, "message": "Strategy generated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Strategy generation failed: {str(e)}")


@router.get("/{campaign_id}/strategy")
async def get_strategy(campaign_id: str, user: dict = Depends(get_current_user)):
    """Get the AI-generated strategy for a campaign."""
    doc = await campaign_strategies_collection.find_one({"campaign_id": campaign_id, "user_id": user["user_id"]})
    if not doc:
        return None
    doc["id"] = str(doc["_id"])
    del doc["_id"]
    if doc.get("generated_at"):
        doc["generated_at"] = doc["generated_at"].isoformat()
    return doc


@router.get("/{campaign_id}/days")
async def get_days(
    campaign_id: str,
    week: int = Query(None),
    user: dict = Depends(get_current_user),
):
    """Get campaign content plan days."""
    return await get_campaign_days(campaign_id, week)


@router.get("/{campaign_id}/weeks")
async def get_weeks(campaign_id: str, user: dict = Depends(get_current_user)):
    """Get weekly summary for a campaign."""
    return await get_campaign_weeks(campaign_id)


# ── Day Content Management ────────────────────────────────────────────────────

from app.services.campaign_content_service import (
    generate_day_content, save_day_content, get_day_content,
    update_day_content, update_day_status,
)
from app.services.campaign_ai_service import campaign_days_collection as _days_col
from app.database import db as _db
campaign_content_collection = _db["campaign_content"]
from bson import ObjectId


@router.post("/{campaign_id}/days/{day_id}/generate")
async def generate_content_for_day(
    campaign_id: str, day_id: str, user: dict = Depends(get_current_user)
):
    """Generate AI content for a specific campaign day."""
    campaign = await get_campaign(campaign_id, user["user_id"])
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Get the day
    day_doc = await _days_col.find_one({"_id": ObjectId(day_id)})
    if not day_doc:
        raise HTTPException(status_code=404, detail="Day not found")

    from app.services.campaign_content_service import serialize_day
    day = serialize_day(day_doc)

    try:
        content_data = await generate_day_content(day, campaign)
        saved = await save_day_content(day_id, campaign_id, user["user_id"], content_data)
        return saved
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Content generation failed: {str(e)}")


@router.get("/{campaign_id}/days/{day_id}/content")
async def get_content_for_day(
    campaign_id: str, day_id: str, user: dict = Depends(get_current_user)
):
    """Get generated content for a specific day."""
    content = await get_day_content(day_id)
    return content


@router.put("/{campaign_id}/days/{day_id}/content")
async def update_content_for_day(
    campaign_id: str, day_id: str,
    data: dict = Body(...),
    user: dict = Depends(get_current_user),
):
    """Update content for a specific day (edit, platform change)."""
    result = await update_day_content(day_id, user["user_id"], data)
    if not result:
        raise HTTPException(status_code=404, detail="Content not found")
    return result


@router.patch("/{campaign_id}/days/{day_id}/status")
async def set_day_status(
    campaign_id: str, day_id: str,
    data: dict = Body(...),
    user: dict = Depends(get_current_user),
):
    """Update status of a campaign day (approve, skip, etc.)."""
    status = data.get("status", "")
    valid = ["planned", "approved", "skipped", "in_progress", "done"]
    if status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Use: {', '.join(valid)}")
    result = await update_day_status(day_id, user["user_id"], status)
    return result


# ── Campaign Chat Endpoints ───────────────────────────────────────────────────

from app.services.campaign_chat_service import (
    chat_with_campaign_ai, get_chat_history, clear_chat_history,
)


@router.post("/{campaign_id}/days/{day_id}/chat")
async def campaign_chat(
    campaign_id: str, day_id: str,
    data: dict = Body(...),
    user: dict = Depends(get_current_user),
):
    """Send a message to the campaign AI assistant for a specific day."""
    campaign = await get_campaign(campaign_id, user["user_id"])
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    day_doc = await _days_col.find_one({"_id": ObjectId(day_id)})
    if not day_doc:
        raise HTTPException(status_code=404, detail="Day not found")

    from app.services.campaign_content_service import serialize_day
    day = serialize_day(day_doc)

    # Get current content
    content_doc = await campaign_content_collection.find_one({"day_id": day_id})
    current_content = content_doc.get("content", "") if content_doc else ""

    user_message = data.get("message", "").strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Message is required")

    try:
        result = await chat_with_campaign_ai(
            user_id=user["user_id"],
            campaign_id=campaign_id,
            day_id=day_id,
            user_message=user_message,
            campaign=campaign,
            day=day,
            current_content=current_content,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat failed: {str(e)}")


@router.get("/{campaign_id}/days/{day_id}/chat")
async def get_chat(
    campaign_id: str, day_id: str,
    limit: int = Query(20),
    user: dict = Depends(get_current_user),
):
    """Get chat history for a campaign day."""
    return await get_chat_history(campaign_id, day_id, limit)


@router.delete("/{campaign_id}/days/{day_id}/chat")
async def clear_chat(
    campaign_id: str, day_id: str,
    user: dict = Depends(get_current_user),
):
    """Clear chat history for a campaign day."""
    await clear_chat_history(campaign_id, day_id, user["user_id"])
    return {"message": "Chat history cleared"}


# ── Memory Routes ─────────────────────────────────────────────────────────────

from app.services.campaign_memory_service import (
    get_memory, update_memory, record_approval, record_skip,
    record_regeneration, generate_ai_insights,
)


@router.get("/memory/profile")
async def get_memory_profile(user: dict = Depends(get_current_user)):
    """Get user's AI memory profile."""
    return await get_memory(user["user_id"])


@router.put("/memory/profile")
async def update_memory_profile(data: dict = Body(...), user: dict = Depends(get_current_user)):
    """Manually update memory preferences."""
    allowed = ["preferred_tone", "preferred_hook_style", "emoji_preference",
               "cta_preference", "preferred_post_length"]
    updates = {k: v for k, v in data.items() if k in allowed}
    return await update_memory(user["user_id"], updates)


@router.post("/memory/insights")
async def refresh_insights(user: dict = Depends(get_current_user)):
    """Generate fresh AI insights from memory."""
    insights = await generate_ai_insights(user["user_id"])
    return {"insights": insights}


@router.post("/{campaign_id}/days/{day_id}/approve")
async def approve_day(
    campaign_id: str, day_id: str,
    user: dict = Depends(get_current_user),
):
    """Approve a day's content and record in memory."""
    day_doc = await _days_col.find_one({"_id": ObjectId(day_id)})
    if not day_doc:
        raise HTTPException(status_code=404, detail="Day not found")

    content_doc = await campaign_content_collection.find_one({"day_id": day_id})
    content_text = content_doc.get("content", "") if content_doc else ""

    # Record approval in memory
    await record_approval(
        user["user_id"],
        day_doc.get("platform", ""),
        day_doc.get("content_type", ""),
        day_doc.get("content_pillar", ""),
        content_text,
    )

    # Update day status
    result = await update_day_status(day_id, user["user_id"], "approved")
    return result


# ── Campaign Analytics Routes ─────────────────────────────────────────────────

from app.services.campaign_analytics_service import analyze_campaign_content, get_cached_analytics


@router.get("/{campaign_id}/analytics")
async def get_analytics(campaign_id: str, user: dict = Depends(get_current_user)):
    """Get cached campaign analytics."""
    cached = await get_cached_analytics(campaign_id)
    return cached


@router.post("/{campaign_id}/analytics/analyze")
async def run_analytics(campaign_id: str, user: dict = Depends(get_current_user)):
    """Run full campaign content analysis."""
    campaign = await get_campaign(campaign_id, user["user_id"])
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    try:
        result = await analyze_campaign_content(campaign_id, campaign)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")
