"""Materialize app-created media (a URL) into a local file Hermes can attach.

Trendzzo-generated content exposes its image as a URL — either a Cloudinary
`https://…` secure URL or the app's own local upload served at a relative
`/uploads/files/<name>` path. The Instagram user-assisted workflow, however,
attaches media via Playwright `set_input_files`, which needs a REAL local file
path on disk (validated for type/size).

This module bridges that gap: given one media URL (or an already-local path), it
produces a validated local file whose path can be handed to the Instagram
service's `media_paths`. The file lives in a DURABLE per-operation directory
(not an ephemeral temp dir), because the user-assisted flow re-prepares the same
operation during `confirm` — so the file must still exist on the confirm leg.
Cleanup happens on cancel / publish / expiry via `cleanup_operation_media`.

Security posture:
  - Only `http`/`https` URLs are fetched over the network; any other scheme is
    rejected. The app's own `/uploads/files/<name>` relative paths are read
    directly from disk (no self-directed network request).
  - Downloads are bounded by a timeout and a hard byte cap (the Hermes media
    size limit) — a stream that exceeds the cap is aborted.
  - The final file is validated with the shared `validate_media` (extension +
    size) before its path is returned; unsupported types never reach Playwright.
  - Generated filenames are non-user-derived and confined to the operation dir.
"""

import os
import re
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, unquote

from app.social_publishing.providers.hermes.constants import (
    ALLOWED_MEDIA_TYPES,
    HERMES_MAX_MEDIA_BYTES,
)
from app.social_publishing.providers.hermes.media_workspace import (
    MediaValidationError,
    validate_media,
)

# Root for durable per-operation media (sibling of the browser profiles, under
# the same Hermes-controlled area). Overridable via env for ops.
_MEDIA_ROOT = os.getenv(
    "HERMES_MEDIA_DIR",
    os.path.join(
        os.getenv(
            "HERMES_BROWSER_PROFILE_DIR",
            os.path.join(os.getenv("TEMP", "/tmp"), "trendzzo_hermes_profiles"),
        ),
        "_media",
    ),
)

# Where the app's local uploader stores files on disk (server/uploads/). Mirrors
# upload_routes.UPLOAD_DIR without importing it (avoids a route-layer dependency
# from the provider layer).
_UPLOADS_DIR = Path(__file__).resolve().parents[5] / "uploads"

# Relative URL prefix the local uploader serves from.
_LOCAL_UPLOADS_PREFIX = "/uploads/files/"

# Bounded network read for a remote image.
_FETCH_TIMEOUT_S = 20


class MediaMaterializeError(Exception):
    """Raised when a media URL cannot be turned into a valid local file."""


def _safe_op_dir(operation_id: str) -> str:
    safe_op = re.sub(r"[^a-zA-Z0-9_]", "", operation_id or "")[:48] or "op"
    return os.path.join(_MEDIA_ROOT, safe_op)


def _ext_from_name(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _ext_from_content_type(content_type: str) -> str:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    for ext, mime in ALLOWED_MEDIA_TYPES.items():
        if mime == ct:
            return ext
    return ""


def _is_local_upload_url(url: str) -> Optional[str]:
    """
    If `url` points at the app's own local uploads, return the on-disk filename
    (basename), else None. Handles both a relative '/uploads/files/x.png' and an
    absolute 'http://host/uploads/files/x.png'.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    path = parsed.path or url
    if _LOCAL_UPLOADS_PREFIX in path:
        name = path.split(_LOCAL_UPLOADS_PREFIX, 1)[1]
        name = unquote(name).strip().strip("/")
        # Only a bare filename — never a nested/traversing path.
        if name and "/" not in name and "\\" not in name and ".." not in name:
            return name
    return None


def _copy_local_upload(filename: str, dest_path: str) -> None:
    src = (_UPLOADS_DIR / filename).resolve()
    # src must stay inside the uploads dir (defense against traversal).
    try:
        inside = os.path.commonpath([str(_UPLOADS_DIR.resolve()), str(src)]) == str(
            _UPLOADS_DIR.resolve()
        )
    except ValueError:
        inside = False
    if not inside or not src.is_file():
        raise MediaMaterializeError("local upload not found")
    shutil.copyfile(src, dest_path)


def _download_remote(url: str, dest_path: str) -> str:
    """
    Stream an http(s) URL to dest_path with a timeout + hard byte cap.

    Returns the response Content-Type (for extension inference). Raises
    MediaMaterializeError on any network/size problem. Uses httpx (already a
    project dependency) with a bounded timeout and redirect following.
    """
    import httpx  # lazy import; not needed for local-only paths

    try:
        with httpx.Client(timeout=_FETCH_TIMEOUT_S, follow_redirects=True) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("Content-Type", "")
                written = 0
                with open(dest_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > HERMES_MAX_MEDIA_BYTES:
                            raise MediaMaterializeError(
                                f"remote media exceeds size limit ({HERMES_MAX_MEDIA_BYTES} bytes)"
                            )
                        f.write(chunk)
                if written <= 0:
                    raise MediaMaterializeError("remote media is empty")
                return content_type
    except MediaMaterializeError:
        raise
    except Exception as e:
        raise MediaMaterializeError(f"could not download media: {type(e).__name__}") from e


def materialize_media_url(url: str, operation_id: str, *, index: int = 0) -> str:
    """
    Turn ONE media URL (or local upload path) into a validated local file path.

    Steps: resolve local-upload vs remote → write into the durable per-operation
    dir with a safe generated name → infer+validate extension & size → return the
    absolute local path. Raises MediaMaterializeError on any failure.
    """
    if not url or not isinstance(url, str):
        raise MediaMaterializeError("missing media URL")

    op_dir = _safe_op_dir(operation_id)
    os.makedirs(op_dir, exist_ok=True)

    local_name = _is_local_upload_url(url)
    # Provisional extension from the source name (refined after download).
    provisional_ext = _ext_from_name(local_name) if local_name else _ext_from_name(
        urlparse(url).path
    )

    # Write to a temp name first; we may rename once the real extension is known.
    tmp_path = os.path.join(op_dir, f"media_{index}.part")
    content_type = ""
    if local_name is not None:
        _copy_local_upload(local_name, tmp_path)
    else:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise MediaMaterializeError(f"unsupported media URL scheme: {parsed.scheme!r}")
        content_type = _download_remote(url, tmp_path)

    # Resolve the final extension: prefer a known one from the name, else infer
    # from the response content-type.
    ext = provisional_ext if provisional_ext in ALLOWED_MEDIA_TYPES else _ext_from_content_type(
        content_type
    )
    if ext not in ALLOWED_MEDIA_TYPES:
        _safe_remove(tmp_path)
        raise MediaMaterializeError(
            f"unsupported or undetectable media type (ext={provisional_ext!r}, "
            f"content_type={content_type!r})"
        )

    # Validate size against the Hermes limit using the shared validator.
    try:
        size = os.path.getsize(tmp_path)
        validate_media(ext, size)
    except (MediaValidationError, OSError) as e:
        _safe_remove(tmp_path)
        raise MediaMaterializeError(str(e)) from e

    final_path = os.path.join(op_dir, f"media_{index}.{ext}")
    try:
        os.replace(tmp_path, final_path)
    except OSError as e:
        _safe_remove(tmp_path)
        raise MediaMaterializeError(f"could not finalize media: {type(e).__name__}") from e
    return final_path


def materialize_media_urls(urls: list[str], operation_id: str) -> list[str]:
    """Materialize each URL in order; raise on the first failure (atomic-ish)."""
    paths: list[str] = []
    for i, u in enumerate(urls or []):
        paths.append(materialize_media_url(u, operation_id, index=i))
    return paths


def cleanup_operation_media(operation_id: str) -> None:
    """Remove the durable per-operation media directory (best-effort)."""
    op_dir = _safe_op_dir(operation_id)
    shutil.rmtree(op_dir, ignore_errors=True)


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
