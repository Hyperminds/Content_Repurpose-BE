"""
File upload routes — handles image/video/file uploads and serves them.
Stores files locally in /uploads directory.
"""

import os
import uuid
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import FileResponse
from app.utils.jwt_handler import get_current_user

router = APIRouter(prefix="/uploads", tags=["uploads"])

# Upload directory
UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_TYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif",
    "video/mp4", "application/pdf",
}
MAX_SIZE = 10 * 1024 * 1024  # 10MB


@router.post("")
async def upload_file(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """Upload a file and return its URL."""
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file.content_type}")

    # Read and check size
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")

    # Generate unique filename
    ext = file.filename.split(".")[-1] if "." in file.filename else "bin"
    filename = f"{uuid.uuid4().hex[:12]}.{ext}"

    # Save file
    filepath = UPLOAD_DIR / filename
    with open(filepath, "wb") as f:
        f.write(content)

    # Return URL
    url = f"/uploads/files/{filename}"

    return {
        "url": url,
        "filename": filename,
        "original_name": file.filename,
        "content_type": file.content_type,
        "size": len(content),
    }


@router.get("/files/{filename}")
async def serve_file(filename: str):
    """Serve an uploaded file."""
    filepath = UPLOAD_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(filepath)


@router.delete("/files/{filename}")
async def delete_file(filename: str, user: dict = Depends(get_current_user)):
    """Delete an uploaded file."""
    filepath = UPLOAD_DIR / filename
    if filepath.exists():
        os.remove(filepath)
    return {"message": "File deleted"}
