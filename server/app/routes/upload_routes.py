"""
File upload routes.
Uses Cloudinary when credentials are configured (production/staging),
falls back to local disk storage for local development.
"""

import os
import uuid
import io
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import FileResponse
from app.utils.jwt_handler import get_current_user
from app.config import APP_ENV

router = APIRouter(prefix="/uploads", tags=["uploads"])

ALLOWED_TYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif",
    "video/mp4", "application/pdf",
}
MAX_SIZE = 10 * 1024 * 1024  # 10 MB

# ── Cloudinary config ─────────────────────────────────────────────────────────
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY    = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")

USE_CLOUDINARY = bool(CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET)

if USE_CLOUDINARY:
    import cloudinary
    import cloudinary.uploader
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True,
    )

# ── Local fallback ────────────────────────────────────────────────────────────
UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


async def _upload_to_cloudinary(content: bytes, filename: str, content_type: str) -> str:
    """Upload bytes to Cloudinary and return the secure URL."""
    resource_type = "video" if content_type == "video/mp4" else "image" if content_type.startswith("image/") else "raw"
    result = cloudinary.uploader.upload(
        io.BytesIO(content),
        public_id=f"trendzzo/{uuid.uuid4().hex[:12]}",
        resource_type=resource_type,
        overwrite=False,
        unique_filename=True,
    )
    return result["secure_url"]


async def _upload_local(content: bytes, filename: str) -> str:
    """Save bytes to local disk and return the relative URL."""
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
    fname = f"{uuid.uuid4().hex[:12]}.{ext}"
    filepath = UPLOAD_DIR / fname
    with open(filepath, "wb") as f:
        f.write(content)
    return f"/uploads/files/{fname}"


@router.post("")
async def upload_file(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Upload a file. Returns a URL (Cloudinary or local)."""
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file.content_type}")

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 10 MB)")

    try:
        if USE_CLOUDINARY:
            url = await _upload_to_cloudinary(content, file.filename, file.content_type)
        else:
            url = await _upload_local(content, file.filename)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

    return {
        "url": url,
        "original_name": file.filename,
        "content_type": file.content_type,
        "size": len(content),
        "storage": "cloudinary" if USE_CLOUDINARY else "local",
    }


@router.get("/files/{filename}")
async def serve_file(filename: str):
    """Serve a locally-stored file (dev only)."""
    filepath = UPLOAD_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(filepath)


@router.delete("/files/{filename}")
async def delete_file(filename: str, user: dict = Depends(get_current_user)):
    """Delete a locally-stored file."""
    filepath = UPLOAD_DIR / filename
    if filepath.exists():
        os.remove(filepath)
    return {"message": "File deleted"}
