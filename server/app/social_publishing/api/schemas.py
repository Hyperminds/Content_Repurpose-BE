"""Pydantic request/response schemas for the social publishing API."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Accounts ──────────────────────────────────────────────────────────────────

class CreateAccountRequest(BaseModel):
    platform: str = Field(..., min_length=1, description="Platform identifier (e.g. 'linkedin')")
    account_name: str = Field(..., min_length=1, max_length=200)
    platform_account_id: str = Field(..., min_length=1, max_length=200)
    # Optional: when the UI adds a user-assisted (Hermes) account it sends these
    # so the account is created with the right provider and immediately usable.
    provider_type: Optional[str] = Field(None, max_length=50)
    provider_name: Optional[str] = Field(None, max_length=50)


class UpdateAccountRequest(BaseModel):
    account_name: Optional[str] = Field(None, min_length=1, max_length=200)
    # The platform handle/identifier (e.g. a Quora/Instagram username). Editable
    # so users can correct or change the account identity from the UI without
    # recreating the account. For user-assisted Hermes accounts this is the
    # profile/username hint; the real login lives in the browser profile.
    platform_account_id: Optional[str] = Field(None, min_length=1, max_length=200)
    is_active: Optional[bool] = None


class AccountResponse(BaseModel):
    id: str
    tenant_id: str
    platform: str
    account_name: str
    platform_account_id: str
    account_type: str
    is_active: bool
    connection_status: str
    provider_type: str
    provider_name: str
    has_access_token: bool
    capabilities: list[str]
    scopes: list[str]
    token_expires_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ── Posts ─────────────────────────────────────────────────────────────────────

class CreatePostRequest(BaseModel):
    account_id: str = Field(..., min_length=1)
    platform: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    media_urls: Optional[list[str]] = None
    scheduled_at: Optional[datetime] = None


class UpdatePostRequest(BaseModel):
    content: Optional[str] = Field(None, min_length=1)
    media_urls: Optional[list[str]] = None
    scheduled_at: Optional[datetime] = None


class SchedulePostRequest(BaseModel):
    scheduled_at: datetime


class PostResponse(BaseModel):
    id: str
    tenant_id: str
    account_id: str
    platform: str
    status: str
    content: str
    media_urls: list[str]
    scheduled_at: Optional[str] = None
    published_at: Optional[str] = None
    failure_reason: Optional[str] = None
    retry_count: int
    max_retries: int
    platform_post_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PostListResponse(BaseModel):
    posts: list[PostResponse]
    total: int


# ── Publishing Status ─────────────────────────────────────────────────────────

class PublishingStatusResponse(BaseModel):
    post_id: str
    status: str
    platform_post_id: Optional[str] = None
    failure_reason: Optional[str] = None
    retry_count: int
    published_at: Optional[str] = None


# ── Generic error ─────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error: str
    detail: str
