from fastapi import APIRouter, Request #type:ignore

from app.controllers.content_controller import generate_content 

router = APIRouter()

@router.post("/generate")
async def generate(request: Request):
    return await generate_content(request)