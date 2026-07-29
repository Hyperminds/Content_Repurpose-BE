"""
Social Presence Analyzer Routes
Isolated module — does not touch campaign or other existing systems.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from app.services.social_presence_service import (
    run_social_presence_analysis,
    analyze_content_batch,
    run_competitor_analysis,
    run_growth_forecast,
    run_brand_positioning,
    run_content_strategy,
    run_bio_optimization,
)
from app.utils.jwt_handler import get_current_user

router = APIRouter(prefix="/social-presence", tags=["Social Presence"])


class ProfileData(BaseModel):
    username: Optional[str] = ""
    bio: Optional[str] = ""
    display_name: Optional[str] = ""
    posts_per_week: Optional[float] = 0
    followers: Optional[int] = 0
    following: Optional[int] = 0
    profile_picture: Optional[bool] = False
    banner_image: Optional[bool] = False
    website_link: Optional[bool] = False
    pinned_post: Optional[bool] = False
    content_types: Optional[list] = []
    primary_topics: Optional[list] = []
    account_age_months: Optional[int] = 0
    has_cta: Optional[bool] = False
    custom_notes: Optional[str] = ""


class AnalyzeRequest(BaseModel):
    profiles: Dict[str, ProfileData]


class ContentItem(BaseModel):
    platform: str
    content: str
    id: Optional[str] = None


class ContentAnalyzeRequest(BaseModel):
    items: List[ContentItem]


@router.post("/analyze")
async def analyze_social_presence(
    request: AnalyzeRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Analyze connected social media profiles and return AI-powered recommendations.
    Accepts profile data for one or more platforms and returns:
    - Per-platform scores and recommendations
    - Overall social presence score
    - Improvement roadmap
    """
    if not request.profiles:
        raise HTTPException(status_code=400, detail="At least one platform profile is required")

    # Convert Pydantic models to plain dicts for the service
    profiles_data = {
        platform: profile.model_dump(exclude_none=False)
        for platform, profile in request.profiles.items()
    }

    try:
        result = await run_social_presence_analysis(profiles_data)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@router.get("/platforms")
async def get_supported_platforms(current_user: dict = Depends(get_current_user)):
    """Return the list of supported platforms and their analysis focus areas."""
    return {
        "platforms": [
            {
                "id": "linkedin",
                "label": "LinkedIn",
                "color": "#0A66C2",
                "focus": "Professional authority & thought leadership",
                "ideal_frequency": "3-5 posts/week",
            },
            {
                "id": "twitter",
                "label": "Twitter / X",
                "color": "#000000",
                "focus": "Real-time engagement & community building",
                "ideal_frequency": "1-3 tweets/day",
            },
            {
                "id": "instagram",
                "label": "Instagram",
                "color": "#E1306C",
                "focus": "Visual storytelling & brand aesthetics",
                "ideal_frequency": "4-7 posts/week",
            },
            {
                "id": "reddit",
                "label": "Reddit",
                "color": "#FF4500",
                "focus": "Community value & niche expertise",
                "ideal_frequency": "2-4 posts/week",
            },
            {
                "id": "medium",
                "label": "Medium",
                "color": "#000000",
                "focus": "Long-form thought leadership & SEO",
                "ideal_frequency": "2-4 articles/month",
            },
            {
                "id": "quora",
                "label": "Quora",
                "color": "#B92B27",
                "focus": "Expertise demonstration & credibility",
                "ideal_frequency": "3-5 answers/week",
            },
        ]
    }


@router.post("/analyze-content")
async def analyze_content_intelligence(
    request: ContentAnalyzeRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Analyze one or more content pieces for hook strength, readability, CTA quality,
    platform fit, content style detection, and AI feedback.
    Returns per-item analysis + aggregate insights panel.
    """
    if not request.items:
        raise HTTPException(status_code=400, detail="At least one content item is required")

    items = [item.model_dump() for item in request.items]

    try:
        result = await analyze_content_batch(items)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Content analysis failed: {str(e)}")


# ── Growth Strategist Models ──────────────────────────────────────────────────

class CompetitorProfile(BaseModel):
    name: str
    platform: str
    username: Optional[str] = ""
    bio: Optional[str] = ""
    posts_per_week: Optional[float] = 0
    followers: Optional[int] = 0
    content_types: Optional[list] = []
    notes: Optional[str] = ""


class CompetitorAnalysisRequest(BaseModel):
    user_profile: dict
    competitors: List[CompetitorProfile]


class GrowthForecastRequest(BaseModel):
    profile_data: dict
    platforms: List[str]


class BrandPositioningRequest(BaseModel):
    profile_data: dict


class ContentStrategyRequest(BaseModel):
    profile_data: dict
    platforms: List[str]


class BioOptimizationRequest(BaseModel):
    platform: str
    current_bio: str
    profile_context: Optional[dict] = {}


# ── Growth Strategist Endpoints ───────────────────────────────────────────────

@router.post("/competitor-analysis")
async def competitor_analysis(
    request: CompetitorAnalysisRequest,
    current_user: dict = Depends(get_current_user),
):
    """Compare user profile against competitors/creators on the same platform."""
    try:
        competitors = [c.model_dump() for c in request.competitors]
        result = await run_competitor_analysis(request.user_profile, competitors)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Competitor analysis failed: {str(e)}")


@router.post("/growth-forecast")
async def growth_forecast(
    request: GrowthForecastRequest,
    current_user: dict = Depends(get_current_user),
):
    """Predict growth opportunities, strongest platform potential, and audience alignment."""
    try:
        result = await run_growth_forecast(request.profile_data, request.platforms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Growth forecast failed: {str(e)}")


@router.post("/brand-positioning")
async def brand_positioning(
    request: BrandPositioningRequest,
    current_user: dict = Depends(get_current_user),
):
    """Analyze niche clarity, authority level, branding consistency, and generate AI suggestions."""
    try:
        result = await run_brand_positioning(request.profile_data)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Brand positioning analysis failed: {str(e)}")


@router.post("/content-strategy")
async def content_strategy(
    request: ContentStrategyRequest,
    current_user: dict = Depends(get_current_user),
):
    """Generate monthly content plan, platform strategies, posting recommendations, content mix."""
    try:
        result = await run_content_strategy(request.profile_data, request.platforms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Content strategy generation failed: {str(e)}")


@router.post("/bio-optimization")
async def bio_optimization(
    request: BioOptimizationRequest,
    current_user: dict = Depends(get_current_user),
):
    """Regenerate bio, improve headlines, optimize CTAs, improve profile positioning."""
    try:
        result = await run_bio_optimization(
            request.platform, request.current_bio, request.profile_context or {}
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Bio optimization failed: {str(e)}")
