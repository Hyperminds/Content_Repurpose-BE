"""AI image-generation routes.

Thin layer over image_generation_controller, mirroring content_routes. All
generation requires authentication (handled in the controller) and is scoped to
the authenticated tenant/user.
"""

from fastapi import APIRouter, Request, Response

from app.controllers.image_generation_controller import generate_image, list_image_models

router = APIRouter(prefix="/ai/images", tags=["ai-images"])


@router.options("/generate")
async def generate_image_options():
    """CORS preflight for /ai/images/generate."""
    return Response(status_code=200)


@router.post("/generate")
async def generate(request: Request):
    """Generate an image from a prompt or content idea (authenticated)."""
    return await generate_image(request)


@router.get("/models")
async def image_models():
    """Return available image models, the default, and supported aspect ratios."""
    return list_image_models()
