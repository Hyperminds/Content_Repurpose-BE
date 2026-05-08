from fastapi import APIRouter, Request #type:ignore

from content_repuposer_BE.server.app.controllers.content_controller import generate_content 

router = APIRouter()

@router.post("/generate")
async def generate(request: Request):
    return await generate_content(request)