"""
Trend Analysis Routes
Realtime AI-powered trend discovery engine.
Isolated module — does not touch campaign or other existing systems.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import Optional, List
from app.services.trend_service import run_trend_analysis
from app.utils.jwt_handler import get_current_user

router = APIRouter(prefix="/trends", tags=["Trend Analysis"])

SUPPORTED_PLATFORMS = ["twitter", "reddit", "linkedin", "instagram", "medium", "quora"]

SUPPORTED_CATEGORIES = [
    "AI", "Technology", "Politics", "Sports", "Finance", "Startups",
    "Crypto", "Gaming", "Entertainment", "Marketing", "Business",
    "Education", "Science", "Health",
]


class TrendRequest(BaseModel):
    category: str = "AI"
    platforms: Optional[List[str]] = None
    search_query: Optional[str] = None


@router.post("/fetch")
async def fetch_trends(
    request: TrendRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Fetch AI-powered trend intelligence for a category across platforms.
    Returns trending topics, hashtags, platform pulse, and content opportunities.
    """
    category = request.category if request.category in SUPPORTED_CATEGORIES else "AI"
    platforms = [p for p in (request.platforms or SUPPORTED_PLATFORMS) if p in SUPPORTED_PLATFORMS]
    if not platforms:
        platforms = SUPPORTED_PLATFORMS

    try:
        result = await run_trend_analysis(
            category=category,
            platforms=platforms,
            search_query=request.search_query,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Trend analysis failed: {str(e)}")


@router.get("/categories")
async def get_categories(current_user: dict = Depends(get_current_user)):
    """Return supported trend categories."""
    return {"categories": SUPPORTED_CATEGORIES}


@router.get("/platforms")
async def get_platforms(current_user: dict = Depends(get_current_user)):
    """Return supported platforms for trend analysis."""
    return {
        "platforms": [
            {"id": "twitter",   "label": "Twitter / X", "color": "#1DA1F2"},
            {"id": "reddit",    "label": "Reddit",       "color": "#FF4500"},
            {"id": "linkedin",  "label": "LinkedIn",     "color": "#0A66C2"},
            {"id": "instagram", "label": "Instagram",    "color": "#E1306C"},
            {"id": "medium",    "label": "Medium",       "color": "#888888"},
            {"id": "quora",     "label": "Quora",        "color": "#B92B27"},
        ]
    }
