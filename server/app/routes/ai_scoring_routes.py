"""
AI Content Scoring routes - real-time content analysis and intelligence.
"""

from fastapi import APIRouter, Body, Depends
from app.utils.jwt_handler import get_current_user
from app.services.ai_scoring_service import (
    analyze_content,
    analyze_all_platforms,
    get_overall_score,
    BEST_POSTING_TIMES,
)

router = APIRouter(prefix="/ai", tags=["ai-scoring"])


@router.post("/score")
async def score_content(data: dict = Body(...), user: dict = Depends(get_current_user)):
    """Score a single piece of content for a specific platform."""
    content = data.get("content", "")
    platform = data.get("platform", "linkedin")
    return analyze_content(content, platform)


@router.post("/score-all")
async def score_all_platforms(data: dict = Body(...), user: dict = Depends(get_current_user)):
    """Score content for all platforms at once."""
    content_map = data.get("content_map", {})
    analyses = analyze_all_platforms(content_map)
    overall = get_overall_score(analyses)
    return {"analyses": analyses, "overall": overall}


@router.get("/posting-times")
async def best_posting_times(user: dict = Depends(get_current_user)):
    """Get AI-recommended best posting times per platform."""
    return BEST_POSTING_TIMES
