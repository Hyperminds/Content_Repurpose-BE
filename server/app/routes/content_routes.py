from fastapi import APIRouter, Request, Response
from app.controllers.content_controller import generate_content, regenerate_platform_content
from app.services.ai_usage_service import AVAILABLE_MODELS

router = APIRouter()


@router.options("/generate")
async def generate_options():
    """Handle CORS preflight for /generate."""
    return Response(status_code=200)


@router.post("/generate")
async def generate(request: Request):
    return await generate_content(request)


@router.options("/regenerate")
async def regenerate_options():
    """Handle CORS preflight for /regenerate."""
    return Response(status_code=200)


@router.post("/regenerate")
async def regenerate(request: Request):
    """Regenerate content for a single platform."""
    return await regenerate_platform_content(request)


@router.get("/models")
async def get_available_models():
    """Return the list of available AI models for content generation."""
    return {"models": AVAILABLE_MODELS}
