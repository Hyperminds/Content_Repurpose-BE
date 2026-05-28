"""
File Storage Abstraction for TrendZo.
Provides a unified interface for file uploads that can be backed by:
- Local filesystem (development)
- AWS S3 (production)
- Cloudinary (media optimization)
- Supabase Storage (alternative)

Switch backends via STORAGE_BACKEND env var.
"""

import os
import uuid
import shutil
from pathlib import Path
from datetime import datetime, timezone
from app.config import APP_ENV
from app.services.logger import log

STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")  # local | s3 | cloudinary | supabase
UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Max file size: 10MB
MAX_FILE_SIZE = 10 * 1024 * 1024
ALLOWED_TYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif",
    "video/mp4", "application/pdf",
}


class StorageError(Exception):
    pass


async def upload_file(file_bytes: bytes, filename: str, content_type: str, user_id: str = "") -> dict:
    """
    Upload a file using the configured storage backend.
    Returns: {url, filename, size, content_type, storage_backend}
    """
    if len(file_bytes) > MAX_FILE_SIZE:
        raise StorageError(f"File too large. Max size: {MAX_FILE_SIZE // (1024*1024)}MB")

    if content_type not in ALLOWED_TYPES:
        raise StorageError(f"Unsupported file type: {content_type}")

    # Generate unique filename
    ext = Path(filename).suffix or ".bin"
    unique_name = f"{uuid.uuid4().hex[:12]}{ext}"

    if STORAGE_BACKEND == "local":
        return await _upload_local(file_bytes, unique_name, content_type)
    elif STORAGE_BACKEND == "s3":
        return await _upload_s3(file_bytes, unique_name, content_type, user_id)
    elif STORAGE_BACKEND == "cloudinary":
        return await _upload_cloudinary(file_bytes, unique_name, content_type)
    elif STORAGE_BACKEND == "supabase":
        return await _upload_supabase(file_bytes, unique_name, content_type, user_id)
    else:
        return await _upload_local(file_bytes, unique_name, content_type)


async def delete_file(filename: str) -> bool:
    """Delete a file from storage."""
    if STORAGE_BACKEND == "local":
        filepath = UPLOAD_DIR / filename
        if filepath.exists():
            filepath.unlink()
            return True
        return False
    # TODO: Implement for other backends
    return False


async def get_file_url(filename: str) -> str:
    """Get the public URL for a file."""
    if STORAGE_BACKEND == "local":
        return f"/uploads/{filename}"
    # TODO: Return CDN URLs for cloud backends
    return f"/uploads/{filename}"


# ── Backend implementations ───────────────────────────────────────────────────

async def _upload_local(file_bytes: bytes, filename: str, content_type: str) -> dict:
    """Save file to local filesystem."""
    filepath = UPLOAD_DIR / filename
    filepath.write_bytes(file_bytes)
    log.info(f"File uploaded (local): {filename}", size=len(file_bytes))
    return {
        "url": f"/uploads/{filename}",
        "filename": filename,
        "size": len(file_bytes),
        "content_type": content_type,
        "storage_backend": "local",
    }


async def _upload_s3(file_bytes: bytes, filename: str, content_type: str, user_id: str) -> dict:
    """Upload to AWS S3. Requires boto3 and AWS credentials."""
    # TODO: Implement when ready for production
    # import boto3
    # s3 = boto3.client('s3')
    # bucket = os.getenv("S3_BUCKET")
    # key = f"uploads/{user_id}/{filename}"
    # s3.put_object(Bucket=bucket, Key=key, Body=file_bytes, ContentType=content_type)
    # url = f"https://{bucket}.s3.amazonaws.com/{key}"
    raise StorageError("S3 backend not configured. Set AWS credentials in .env")


async def _upload_cloudinary(file_bytes: bytes, filename: str, content_type: str) -> dict:
    """Upload to Cloudinary. Requires cloudinary SDK."""
    # TODO: Implement when ready
    raise StorageError("Cloudinary backend not configured.")


async def _upload_supabase(file_bytes: bytes, filename: str, content_type: str, user_id: str) -> dict:
    """Upload to Supabase Storage."""
    # TODO: Implement when ready
    raise StorageError("Supabase Storage backend not configured.")
