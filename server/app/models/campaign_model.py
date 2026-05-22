"""
Campaign Studio models for TrendZo.
Uses existing MongoDB database — separate collections only.
"""

from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, Field
from enum import Enum


class CampaignStatus(str, Enum):
    draft = "draft"
    active = "active"
    paused = "paused"
    completed = "completed"
    cancelled = "cancelled"


class CampaignType(str, Enum):
    brand_awareness = "brand_awareness"
    lead_generation = "lead_generation"
    product_launch = "product_launch"
    thought_leadership = "thought_leadership"
    community_building = "community_building"
    event_promotion = "event_promotion"
    content_series = "content_series"


class PostingFrequency(str, Enum):
    daily = "daily"
    twice_daily = "twice_daily"
    every_other_day = "every_other_day"
    weekly = "weekly"
    custom = "custom"


# ── Request / Response Schemas ────────────────────────────────────────────────

class CampaignCreate(BaseModel):
    campaign_name: str = Field(..., min_length=2, max_length=100)
    campaign_goal: str = Field(..., min_length=5, max_length=500)
    campaign_type: CampaignType
    target_audience: str = Field(..., min_length=3, max_length=200)
    duration: int = Field(..., ge=1, le=365, description="Duration in days")
    selected_platforms: List[str] = Field(..., min_length=1)
    posting_frequency: PostingFrequency
    tone: str = Field(..., min_length=2, max_length=50)
    cta_goal: str = Field(..., min_length=3, max_length=200)
    start_date: str = Field(..., description="ISO date string")
    end_date: Optional[str] = None


class CampaignUpdate(BaseModel):
    campaign_name: Optional[str] = Field(None, min_length=2, max_length=100)
    campaign_goal: Optional[str] = Field(None, min_length=5, max_length=500)
    campaign_type: Optional[CampaignType] = None
    target_audience: Optional[str] = None
    duration: Optional[int] = Field(None, ge=1, le=365)
    selected_platforms: Optional[List[str]] = None
    posting_frequency: Optional[PostingFrequency] = None
    tone: Optional[str] = None
    cta_goal: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    campaign_status: Optional[CampaignStatus] = None


class CampaignResponse(BaseModel):
    id: str
    user_id: str
    campaign_name: str
    campaign_goal: str
    campaign_type: str
    target_audience: str
    duration: int
    selected_platforms: List[str]
    posting_frequency: str
    tone: str
    cta_goal: str
    start_date: Optional[str]
    end_date: Optional[str]
    campaign_status: str
    created_at: Optional[str]
    updated_at: Optional[str]
    # Computed
    days_remaining: Optional[int] = None
    progress_percent: Optional[int] = None
